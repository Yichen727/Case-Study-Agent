from dataclasses import dataclass, field
from typing import Mapping, Optional


REQUIRED_CASE_FIELDS = ("title", "pillar", "client", "services", "brief", "creative", "impact")


def missing_value(value: object) -> bool:
    return not isinstance(value, str) or value.strip().casefold() in {"", "none", "unknown"}


def case_processing_status(case: Mapping[str, object]) -> str:
    """Completeness is independent of whether fields came from extraction or AI."""
    return "NEEDS_REVIEW" if any(missing_value(case.get(name)) for name in REQUIRED_CASE_FIELDS) else "CONFIRMED"


def case_display_status(case: Mapping[str, object]) -> str:
    return "Confirmed" if case_processing_status(case) == "CONFIRMED" else "Needs Review"


@dataclass
class SourceRecord:
    source_id: str
    filename: str
    source_snapshot_reference: str
    content_sha256: str
    imported_at: str
    source_type: str = "LOCAL"
    original_source_url: Optional[str] = None
    source_file_id: Optional[str] = None
    source_version: Optional[str] = None
    ingestion_status: str = "UPLOADED"


@dataclass
class FieldProvenance:
    source_kind: str
    evidence_reference: Optional[str] = None
    verification_status: str = "EXTRACTED"


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
    original_slide_id: Optional[int] = None
    client: Optional[str] = None
    services: Optional[str] = None


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
    review_status: str = "NEEDS_REVIEW"
    source_id: Optional[str] = None
    original_slide_id: Optional[int] = None
    field_provenance: dict[str, FieldProvenance] = field(default_factory=dict)
