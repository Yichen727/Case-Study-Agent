"""Original OOXML slide/relationship copying; never recreates shapes."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import posixpath
import shutil
from tempfile import NamedTemporaryFile
from urllib.parse import unquote, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as ET

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


@dataclass(frozen=True)
class SlideReference:
    source_path: Path
    original_slide_id: int


class OriginalSlideExportError(ValueError):
    def __init__(self, message, code="EXPORT_FAILED"):
        self.code = code
        super().__init__(message)


def _xml(data):
    return ET.fromstring(data, parser=ET.XMLParser(resolve_entities=False, no_network=True))


def _bytes(root):
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def _rels_name(part):
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _resolve(part, target):
    if urlsplit(target).scheme or "\\" in target:
        raise OriginalSlideExportError("Unsupported internal part address.")
    name = unquote(target)
    name = name.lstrip("/") if name.startswith("/") else posixpath.join(posixpath.dirname(part), name)
    name = posixpath.normpath(name)
    if name.startswith("../"):
        raise OriginalSlideExportError("A relationship leaves the presentation package.")
    return name


class _Source:
    def __init__(self, package):
        self.zip = package
        self.names = set(package.namelist())
        types = _xml(package.read("[Content_Types].xml"))
        self.overrides = {n.get("PartName").lstrip("/"): n.get("ContentType") for n in types if n.tag.endswith("Override")}
        self.defaults = {n.get("Extension"): n.get("ContentType") for n in types if n.tag.endswith("Default")}
        self.presentation = _xml(package.read("ppt/presentation.xml"))
        self.presentation_rels = self.relationships("ppt/presentation.xml")
        rels_by_id = {n.get("Id"): n for n in self.presentation_rels}
        self.slides = {
            int(n.get("id")): _resolve("ppt/presentation.xml", rels_by_id[n.get(f"{{{R}}}id")].get("Target"))
            for n in self.presentation.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId")
        }
        size = self.presentation.find(f"{{{P}}}sldSz")
        self.size = (size.get("cx"), size.get("cy"))

    def relationships(self, part):
        name = _rels_name(part)
        return _xml(self.zip.read(name)) if name in self.names else []

    def content_type(self, part):
        return self.overrides.get(part) or self.defaults[part.rsplit(".", 1)[-1]]


class _PartCopier:
    def __init__(self, output, sources, selected):
        self.output = output
        self.sources = sources
        self.selected = selected
        self.mapping = {}
        self.types = {}
        self.used_master_layout_ids = set()
        self.next_metadata_id = 2147483648

    def metadata_id(self):
        while self.next_metadata_id in self.used_master_layout_ids:
            self.next_metadata_id += 1
        value = self.next_metadata_id
        self.used_master_layout_ids.add(value)
        self.next_metadata_id += 1
        return value

    def copy(self, source_path, part):
        key = (source_path, part)
        if key in self.mapping:
            return self.mapping[key]
        source = self.sources[source_path]
        destination = part
        stem, extension = posixpath.splitext(part)
        counter = 2
        while destination in self.types:
            destination = f"{stem}_{counter}{extension}"
            counter += 1
        self.mapping[key] = destination  # Register before following cyclic master/layout/notes links.
        self.types[destination] = source.content_type(part)
        if self.types[destination].endswith(".slideMaster+xml"):
            master = _xml(source.zip.read(part))
            for node in master.findall(f"{{{P}}}sldLayoutIdLst/{{{P}}}sldLayoutId"):
                identifier = int(node.get("id"))
                if identifier in self.used_master_layout_ids:
                    node.set("id", str(self.metadata_id()))
                else:
                    self.used_master_layout_ids.add(identifier)
            self.output.writestr(destination, _bytes(master))
        else:
            with source.zip.open(part) as reader, self.output.open(destination, "w") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
        rels = ET.Element(f"{{{REL}}}Relationships", nsmap={None: REL})
        for original in source.relationships(part):
            relation = deepcopy(original)
            if relation.get("TargetMode") != "External":
                target = _resolve(part, relation.get("Target"))
                if relation.get("Type").endswith("/slide") and (source_path, target) not in self.selected:
                    raise OriginalSlideExportError("Include the destination of an internal slide link before exporting.","EXPORT_LINK_TARGET_MISSING")
                copied = self.copy(source_path, target)
                relation.set("Target", posixpath.relpath(copied, posixpath.dirname(destination)))
            rels.append(relation)
        if len(rels):
            self.output.writestr(_rels_name(destination), _bytes(rels))
        return destination

    def copy_duplicate_slide(self, source_path, part):
        # Each output occurrence owns its slide and notes; media/layouts stay shared.
        private = [(source_path,part)]
        source = self.sources[source_path]
        private.extend((source_path,_resolve(part,rel.get("Target"))) for rel in source.relationships(part)
                       if rel.get("Type").endswith("/notesSlide") and rel.get("TargetMode")!="External")
        previous = {key:self.mapping.pop(key) for key in private if key in self.mapping}
        try:
            return self.copy(source_path,part)
        finally:
            self.mapping.update(previous)


def presentation_slide_ids(path):
    """Slide IDs in presentation order, without reconstructing slide content."""
    with ZipFile(path) as package:
        return list(_Source(package).slides)


def _merge_embedded_fonts(presentation, sources, copier, relationship):
    """Reuse identical embedded faces; never silently replace a conflicting font."""
    merged = ET.Element(f"{{{P}}}embeddedFontLst")
    entries, fingerprints = {}, {}
    for path, source in sources.items():
        rels = {rel.get("Id"): rel for rel in source.presentation_rels}
        for original in source.presentation.findall(f"{{{P}}}embeddedFontLst/{{{P}}}embeddedFont"):
            name = original.find(f"{{{P}}}font").get("typeface").casefold()
            if name not in entries:
                entry = deepcopy(original)
                for face in list(entry):
                    if face.tag != f"{{{P}}}font":
                        entry.remove(face)
                entries[name] = entry
                merged.append(entry)
            for face in original:
                if face.tag == f"{{{P}}}font":
                    continue
                rel = rels[face.get(f"{{{R}}}id")]
                part = _resolve("ppt/presentation.xml", rel.get("Target"))
                fingerprint = hashlib.sha256(source.zip.read(part)).digest()
                key = (name, face.tag)
                if key in fingerprints:
                    if fingerprints[key] != fingerprint:
                        raise OriginalSlideExportError("These decks embed different versions of the same font.",
                                                       "EXPORT_EMBEDDED_FONTS")
                    continue
                fingerprints[key] = fingerprint
                copied = deepcopy(face)
                copied.set(f"{{{R}}}id", relationship(rel.get("Type"), copier.copy(path, part)))
                entries[name].append(copied)
    order = ("font", "regular", "bold", "italic", "boldItalic")
    for entry in entries.values():
        entry[:] = sorted(entry,key=lambda node:order.index(ET.QName(node).localname))
    previous = presentation.find(f"{{{P}}}embeddedFontLst")
    if previous is not None:
        position = list(presentation).index(previous)
        presentation.remove(previous)
    else:
        following = {"custShowLst", "photoAlbum", "custDataLst", "kinsoku", "defaultTextStyle", "modifyVerifier", "extLst"}
        position = next((i for i, node in enumerate(presentation) if ET.QName(node).localname in following),len(presentation))
    if len(merged):
        presentation.insert(position, merged)


def export_slides(slides: list[SlideReference], output_path: str | Path) -> Path:
    if not slides:
        raise OriginalSlideExportError("Select at least one slide.")
    output_path = Path(output_path).resolve()
    source_paths = list(dict.fromkeys(Path(ref.source_path).resolve() for ref in slides))
    if output_path in source_paths:
        raise OriginalSlideExportError("The export must not replace a source presentation.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with ExitStack() as stack:
            sources = {path: _Source(stack.enter_context(ZipFile(path))) for path in source_paths}
            first = sources[source_paths[0]]
            if any(source.size != first.size for source in sources.values()):
                raise OriginalSlideExportError("Presentations have different slide sizes; automatic scaling is not supported.","EXPORT_SIZE_MISMATCH")
            selected = []
            for ref in slides:
                path = Path(ref.source_path).resolve()
                if ref.original_slide_id not in sources[path].slides:
                    raise OriginalSlideExportError("The original slide is missing from its source snapshot.","EXPORT_SLIDE_MISSING")
                selected.append((path, sources[path].slides[ref.original_slide_id]))
            temporary = stack.enter_context(NamedTemporaryFile(dir=output_path.parent, suffix=".pptx", delete=False))
            with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as output:
                copier = _PartCopier(output, sources, set(selected))
                presentation = deepcopy(first.presentation)
                main_rels = ET.Element(f"{{{REL}}}Relationships", nsmap={None: REL})

                def relationship(kind, target, external=False):
                    rid = f"rId{len(main_rels) + 1}"
                    attrs = {"Id": rid, "Type": kind, "Target": target if external else posixpath.relpath(target, "ppt")}
                    if external:
                        attrs["TargetMode"] = "External"
                    ET.SubElement(main_rels, f"{{{REL}}}Relationship", **attrs)
                    return rid

                rid_mapping = {}
                for rel in first.presentation_rels:
                    if rel.get("Type").rsplit("/", 1)[-1] in {"slide", "slideMaster", "notesMaster", "font"}:
                        continue
                    external = rel.get("TargetMode") == "External"
                    target = rel.get("Target") if external else copier.copy(source_paths[0], _resolve("ppt/presentation.xml", rel.get("Target")))
                    rid_mapping[rel.get("Id")] = relationship(rel.get("Type"), target, external)
                for node in presentation.iter():
                    if node.get(f"{{{R}}}id") in rid_mapping:
                        node.set(f"{{{R}}}id", rid_mapping[node.get(f"{{{R}}}id")])
                _merge_embedded_fonts(presentation, sources, copier, relationship)
                for name in ("sldMasterIdLst", "notesMasterIdLst", "sldIdLst", "custShowLst", "extLst"):
                    child = presentation.find(f"{{{P}}}{name}")
                    if child is not None:
                        presentation.remove(child)
                slide_list = ET.Element(f"{{{P}}}sldIdLst")
                seen = set()
                for index, (path, part) in enumerate(selected, 256):
                    target = copier.copy_duplicate_slide(path,part) if (path,part) in seen else copier.copy(path,part)
                    seen.add((path,part))
                    rid = relationship(R + "/slide", target)
                    ET.SubElement(slide_list, f"{{{P}}}sldId", id=str(index), **{f"{{{R}}}id": rid})
                master_list = ET.Element(f"{{{P}}}sldMasterIdLst")
                notes_list = ET.Element(f"{{{P}}}notesMasterIdLst")
                for part, content_type in list(copier.types.items()):
                    if content_type.endswith(".slideMaster+xml"):
                        rid = relationship(R + "/slideMaster", part)
                        ET.SubElement(master_list, f"{{{P}}}sldMasterId", id=str(copier.metadata_id()), **{f"{{{R}}}id": rid})
                    elif content_type.endswith(".notesMaster+xml"):
                        rid = relationship(R + "/notesMaster", part)
                        ET.SubElement(notes_list, f"{{{P}}}notesMasterId", **{f"{{{R}}}id": rid})
                presentation.insert(0, master_list)
                if len(notes_list):
                    presentation.insert(1, notes_list)
                # Slide list follows all master lists, before slide/notes dimensions.
                position = next((i for i, child in enumerate(presentation) if child.tag == f"{{{P}}}sldSz"), len(presentation))
                presentation.insert(position, slide_list)
                output.writestr("ppt/presentation.xml", _bytes(presentation))
                output.writestr("ppt/_rels/presentation.xml.rels", _bytes(main_rels))
                types = ET.Element(f"{{{CT}}}Types", nsmap={None: CT})
                ET.SubElement(types, f"{{{CT}}}Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
                all_types = {**copier.types, "ppt/presentation.xml": first.content_type("ppt/presentation.xml")}
                for part, content_type in all_types.items():
                    ET.SubElement(types, f"{{{CT}}}Override", PartName="/" + part, ContentType=content_type)
                output.writestr("[Content_Types].xml", _bytes(types))
                roots = ET.Element(f"{{{REL}}}Relationships", nsmap={None: REL})
                ET.SubElement(roots, f"{{{REL}}}Relationship", Id="rId1", Type=R + "/officeDocument", Target="ppt/presentation.xml")
                output.writestr("_rels/.rels", _bytes(roots))
        Path(temporary.name).replace(output_path)
        return output_path
    except Exception:
        if temporary is not None:
            Path(temporary.name).unlink(missing_ok=True)
        raise
