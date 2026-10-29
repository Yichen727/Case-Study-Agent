"""Resolve full-path company Office links through read-only Microsoft Graph APIs."""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import monotonic
from urllib.parse import parse_qs, quote, unquote, urlsplit

import requests

from app.ingestion.errors import IngestionError
from app.ingestion.validation import MAX_FILE_BYTES

GRAPH = "https://graph.microsoft.com/v1.0"
FETCH_BUDGET_SECONDS = 180


@dataclass(frozen=True)
class CloudLink:
    original_url: str
    host: str
    site_path: str
    file_path: str
    source_type: str

    @property
    def filename(self):
        return PurePosixPath(self.file_path).name


def parse_cloud_link(value, settings):
    value = value.strip()
    if not value or len(value)>4096 or any(ord(char)<32 for char in value):
        raise IngestionError("INVALID_URL")
    try:
        url = urlsplit(value)
        if url.scheme!="https" or not url.hostname or url.username or url.password or url.fragment or url.port not in (None,443):
            raise ValueError()
    except ValueError:
        raise IngestionError("INVALID_URL") from None
    host = url.hostname.lower()
    if host not in settings.sharepoint_hosts or not host.endswith(".sharepoint.com"):
        raise IngestionError("UNSUPPORTED_SOURCE")
    query = parse_qs(url.query)
    if any(key.casefold() in {"access_token","refresh_token","id_token","authorization"} for key in query):
        raise IngestionError("INVALID_URL")
    path = unquote(url.path)
    if path.startswith("/:p:/r/"):
        path = path[len("/:p:/r"):]
    elif path.startswith("/:"):
        raise IngestionError("INVALID_URL")
    if path.lower().endswith("/onedrive.aspx") and len(query.get("id",[]))==1:
        path = query["id"][0]
    parts = path.split("/")
    if not path.startswith("/") or "\\" in path or any(part in {".",".."} for part in parts) or any(ord(c)<32 for c in path):
        raise IngestionError("INVALID_URL")
    if not path.lower().endswith(".pptx"):
        raise IngestionError("INVALID_URL")
    if len(parts)>3 and parts[1].lower() in {"sites","teams","personal"}:
        site = "/".join(parts[:3])
    else:
        site = ""
    return CloudLink(value,host,site,path,"ONEDRIVE" if host.endswith("-my.sharepoint.com") else "SHAREPOINT")


def _check_status(response):
    code = {401:"AUTH_REQUIRED",403:"ACCESS_DENIED",404:"FILE_NOT_FOUND",410:"FILE_NOT_FOUND"}.get(response.status_code)
    if code:
        raise IngestionError(code)
    if response.status_code not in {200,302,303,307,308}:
        raise IngestionError("FETCH_FAILED")


class MicrosoftSource:
    def __init__(self, auth, settings):
        self.auth,self.settings = auth,settings
        self.session = requests.Session()
        self.session.trust_env = False
        self.deadline = monotonic()+FETCH_BUDGET_SECONDS

    def close(self):
        self.session.close()

    def _get(self, path, **kwargs):
        self.auth.check_active()
        if monotonic()>=self.deadline:
            raise IngestionError("FETCH_FAILED")
        try:
            response = self.session.get(GRAPH+path,headers={"Authorization":"Bearer "+self.auth.token()},
                                        timeout=(5,10),allow_redirects=False,**kwargs)
        except requests.RequestException:
            raise IngestionError("FETCH_FAILED") from None
        try:
            _check_status(response)
        except IngestionError:
            response.close()
            raise
        return response

    def _json(self, path):
        with self._get(path) as response:
            if response.status_code!=200:
                raise IngestionError("FETCH_FAILED")
            try:
                value = response.json()
                if not isinstance(value,dict):
                    raise ValueError()
                return value
            except ValueError:
                raise IngestionError("FETCH_FAILED") from None

    def resolve(self, link):
        try:
            return self._resolve(link)
        except (KeyError,TypeError,ValueError,AttributeError):
            raise IngestionError("FETCH_FAILED") from None

    def _resolve(self, link):
        site_path = f"/sites/{link.host}"+(":"+quote(link.site_path,safe="/") if link.site_path else "")
        site = self._json(site_path+"?$select=id")
        site_id = quote(site["id"],safe=",")
        drives = self._json(f"/sites/{site_id}/drives?$select=id,webUrl,driveType")
        choices = []
        for drive in drives.get("value",[]):
            web = urlsplit(drive.get("webUrl",""))
            root = unquote(web.path).rstrip("/")
            if web.hostname==link.host and link.file_path.casefold().startswith(root.casefold()+"/"):
                choices.append((root,drive["id"]))
        if not choices:
            raise IngestionError("FILE_NOT_FOUND")
        root,drive_id = max(choices,key=lambda value:len(value[0]))
        relative = link.file_path[len(root)+1:]
        item = self._json(f"/drives/{quote(drive_id,safe='')}/root:/{quote(relative,safe='/')}?$select=id,name,file,size,eTag,parentReference")
        if not isinstance(item.get("file"),dict) or not item.get("name","").lower().endswith(".pptx"):
            raise IngestionError("INVALID_FILE")
        if item.get("size",0)>MAX_FILE_BYTES:
            raise IngestionError("INVALID_FILE")
        return {"file_id":item["id"],"drive_id":drive_id,"filename":item["name"],"version":item.get("eTag"),
                "source_file_id":drive_id+":"+item["id"],"source_type":link.source_type,"original_source_url":link.original_url}

    def download(self, item, destination, progress=None):
        destination = Path(destination)
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary = destination.with_suffix(".part")
        path = f"/drives/{quote(item['drive_id'],safe='')}/items/{quote(item['file_id'],safe='')}/content"
        try:
            response = self._get(path,stream=True)
            for _ in range(4):
                if response.status_code==200:
                    break
                location = response.headers.get("Location","")
                response.close()
                url = urlsplit(location)
                host = url.hostname or ""
                allowed = (host in self.settings.sharepoint_hosts and host.endswith(".sharepoint.com")) or host.endswith(".files.1drv.com")
                if url.scheme!="https" or not allowed or url.username or url.password or url.port not in (None,443):
                    raise IngestionError("FETCH_FAILED")
                self.auth.check_active()
                if monotonic()>=self.deadline:
                    raise IngestionError("FETCH_FAILED")
                # The Graph bearer is never forwarded to a signed download URL.
                response = self.session.get(location,headers={},stream=True,timeout=(5,10),allow_redirects=False)
                _check_status(response)
            with response:
                if response.status_code!=200:
                    raise IngestionError("FETCH_FAILED")
                size = 0
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(1024*1024):
                        self.auth.check_active()
                        if monotonic()>=self.deadline:
                            raise IngestionError("FETCH_FAILED")
                        size += len(chunk)
                        if size>MAX_FILE_BYTES:
                            raise IngestionError("INVALID_FILE")
                        file.write(chunk)
                        if progress:
                            progress("Downloading presentation",size,max(size,int(response.headers.get("Content-Length",size))))
            current = self._json(f"/drives/{quote(item['drive_id'],safe='')}/items/{quote(item['file_id'],safe='')}?$select=id,eTag")
            if item["version"] is not None and current.get("eTag")!=item["version"]:
                raise IngestionError("FETCH_FAILED")
            temporary.replace(destination)
            return destination
        except IngestionError:
            temporary.unlink(missing_ok=True)
            raise
        except (requests.RequestException,OSError,ValueError):
            temporary.unlink(missing_ok=True)
            raise IngestionError("FETCH_FAILED") from None
