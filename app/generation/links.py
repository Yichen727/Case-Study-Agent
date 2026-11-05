"""Extract user-supplied web URLs locally; never fetch or resolve them."""
from hashlib import sha256
import json
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

VIDEO_DOMAINS = ("youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "dai.ly", "tiktok.com")
URL_PATTERN = re.compile(r'''(?<![\w:/])https?://[^\s<>"']+''', re.I)


class CaseLinkError(ValueError):
    pass


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise CaseLinkError("Choose a valid HTTP or HTTPS website link from your project notes.")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (parsed.scheme.lower() not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or "." not in host or len(host) > 253
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))):
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        netloc = host + (f":{port}" if port is not None else "")
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, parsed.query, parsed.fragment))
    except (ValueError, UnicodeError):
        raise CaseLinkError("Choose a valid HTTP or HTTPS website link from your project notes.") from None


class DraftLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str = Field(max_length=2048)
    link_type: Literal["WEB", "VIDEO"]
    source: Literal["DESCRIPTION", "FOLLOW_UP"]
    source_index: int | None = Field(default=None, ge=0)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return validate_url(value)

    @property
    def label(self):
        return "WATCH VIDEO HERE." if self.link_type == "VIDEO" else "READ MORE HERE."


def extract_links(text, *, source="DESCRIPTION", source_index=None):
    links, seen = [], set()
    if not isinstance(text, str):
        return links
    for match in URL_PATTERN.finditer(text or ""):
        candidate = match.group().rstrip(".,;:!?，。；：！？")
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
            while candidate.endswith(closing) and candidate.count(closing) > candidate.count(opening):
                candidate = candidate[:-1]
        candidate = candidate.rstrip(".,;:!?，。；：！？")
        try:
            url = validate_url(candidate)
        except CaseLinkError:
            continue
        if url in seen:
            continue
        seen.add(url)
        host = urlsplit(url).hostname
        video = any(host == domain or host.endswith("." + domain) for domain in VIDEO_DOMAINS)
        links.append(DraftLink(url=url, link_type="VIDEO" if video else "WEB", source=source, source_index=source_index))
    return links


def merge_links(existing, additions):
    """Preserve user type overrides and the first recorded source for each URL."""
    result, seen = list(existing), {link.url for link in existing}
    for link in additions:
        if link.url not in seen:
            result.append(link)
            seen.add(link.url)
    return result


def link_hash(link):
    value = {"url": link.url, "link_type": link.link_type} if link else None
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
