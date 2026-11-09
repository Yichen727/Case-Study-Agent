"""Remove template labels from searchable text without changing source material."""
from functools import lru_cache
import re


MEDIA_PLACEHOLDER = re.compile(
    r"^[^\S\n]*REPLACE\s+WITH\s+(?:IMAGE|VIDEO)"
    r"(?:\s*/\s*(?:IMAGE|VIDEO))?[^\S\n]*[.!]?[^\S\n]*$", re.I | re.M)


@lru_cache(maxsize=2048)
def case_text(text: str | None) -> str:
    """Discard standalone media instructions, preserving actual project prose."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\v", "\n")
    return MEDIA_PLACEHOLDER.sub("", text)
