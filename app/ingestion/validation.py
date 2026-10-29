"""Validate PPTX packages without extracting them to the filesystem."""
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from app.ingestion.errors import IngestionError

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_PACKAGE_PARTS = 10_000
MAX_XML_BYTES = 8 * 1024 * 1024
PRESENTATION_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def validate_pptx(path: str | Path) -> None:
    path = Path(path)
    try:
        if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            raise IngestionError("INVALID_FILE")
        with ZipFile(path) as package:
            entries = package.infolist()
            names = {entry.filename for entry in entries}
            if (
                len(entries) > MAX_PACKAGE_PARTS or len(names) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_UNCOMPRESSED_BYTES
            ):
                raise IngestionError("INVALID_FILE")
            for entry in entries:
                name = entry.filename
                if name.startswith("/") or "\\" in name or ".." in PurePosixPath(name).parts or entry.flag_bits & 1:
                    raise IngestionError("INVALID_FILE")
                if name.endswith((".xml", ".rels")):
                    if entry.file_size > MAX_XML_BYTES:
                        raise IngestionError("INVALID_FILE")
                    xml = package.read(name)
                    declaration_scan = xml.replace(b"\x00", b"").upper()
                    if b"<!DOCTYPE" in declaration_scan or b"<!ENTITY" in declaration_scan:
                        raise IngestionError("INVALID_FILE")
            required = {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}
            if not required.issubset(names):
                raise IngestionError("INVALID_FILE")
            types = ET.fromstring(package.read("[Content_Types].xml"))
            if not any(part.get("PartName") == "/ppt/presentation.xml" and part.get("ContentType") == PRESENTATION_TYPE for part in types):
                raise IngestionError("INVALID_FILE")
            root = ET.fromstring(package.read("ppt/presentation.xml"))
            if root.tag != f"{{{PRESENTATION_NS}}}presentation":
                raise IngestionError("INVALID_FILE")
            root_rels = ET.fromstring(package.read("_rels/.rels"))
            if not any(rel.get("Type", "").endswith("/officeDocument")
                       and rel.get("Target", "").lstrip("/") == "ppt/presentation.xml"
                       and rel.get("TargetMode") != "External" for rel in root_rels):
                raise IngestionError("INVALID_FILE")
            ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
    except IngestionError:
        raise
    except OSError as exc:
        raise IngestionError("FETCH_FAILED") from exc
    except (BadZipFile, ET.ParseError, KeyError, ValueError, RuntimeError) as exc:
        raise IngestionError("INVALID_FILE") from exc
