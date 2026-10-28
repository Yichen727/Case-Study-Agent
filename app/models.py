from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedSlide:
    slide_number: int
    text: str
    hyperlinks: list[str] = field(default_factory=list)
    hyperlink_labels: list[dict[str, str]] = field(default_factory=list)
    is_case_study: bool = False
    pillar: Optional[str] = None
    case_name: Optional[str] = None
    brief: Optional[str] = None
    creative: Optional[str] = None
    impact: Optional[str] = None


@dataclass
class ParsedCase:
    case_id: str
    title: str
    pillar: Optional[str]
    client: Optional[str]
    services: Optional[str]
    brief: Optional[str]
    creative: Optional[str]
    impact: Optional[str]
    source_deck: str
    slide_numbers: list[int]
    source_links: list[str] = field(default_factory=list)
    source_link_labels: list[dict[str, str]] = field(default_factory=list)
    linked_page_text: Optional[str] = None
    review_status: str = "needs_review"


# Fields that must be present for a case to be considered complete.
REQUIRED_CASE_FIELDS = (
    "pillar",
    "client",
    "services",
    "brief",
    "creative",
    "impact",
)


def determine_case_status(case: ParsedCase) -> str:
    """Return confirmed when all required fields contain non-empty values."""
    for field_name in REQUIRED_CASE_FIELDS:
        value = getattr(case, field_name, None)

        if value is None:
            return "needs_review"

        if isinstance(value, str) and not value.strip():
            return "needs_review"

    return "confirmed"
