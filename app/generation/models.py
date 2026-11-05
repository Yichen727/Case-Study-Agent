"""Structured drafts; completeness is separate from factual verification."""
from datetime import datetime, timezone
import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CASE_FIELDS = ("title", "client", "services", "pillar", "brief", "creative", "impact")
BODY_FIELDS = ("brief", "creative", "impact")
CaseField = Literal["title", "client", "services", "pillar", "brief", "creative", "impact"]
ProjectStage = Literal["COMPLETED", "IN_PROGRESS", "CONCEPT", "UNKNOWN"]
MIN_BODY_WORDS, MAX_BODY_WORDS = 120, 250


def now():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value):
    if isinstance(value, str):
        value = " ".join(value.split())
        if value.casefold() in {"", "none", "null", "unknown", "n/a"}:
            return None
    return value


def word_count(text):
    """English tokens; hyphenated words and apostrophes each count once."""
    return len(re.findall(r"\d+(?:[.,]\d+)*%?|[A-Za-z]+(?:[\u2019'\-][A-Za-z0-9]+)*", text or ""))


class Content(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    title: str | None = Field(default=None, max_length=160)
    client: str | None = Field(default=None, max_length=200)
    services: str | None = Field(default=None, max_length=600)
    pillar: str | None = Field(default=None, max_length=200)
    brief: str | None = Field(default=None, max_length=6000)
    creative: str | None = Field(default=None, max_length=6000)
    impact: str | None = Field(default=None, max_length=6000)

    @field_validator("*", mode="before")
    @classmethod
    def normalize(cls, value):
        return clean_text(value)

    @property
    def body_words(self):
        return sum(word_count(getattr(self, name)) for name in BODY_FIELDS)

    @property
    def export_title(self):
        return (self.title or "").upper()

    @property
    def missing_fields(self):
        return [name for name in CASE_FIELDS if getattr(self, name) is None]


class SuggestedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None = Field(max_length=6000)
    evidence: list[str] = Field(max_length=6, description="Short verbatim excerpts from the user's original description, supplied prior evidence or latest reply. Never quote generated prose.")


class Suggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: SuggestedField
    client: SuggestedField
    services: SuggestedField
    pillar: SuggestedField
    brief: SuggestedField
    creative: SuggestedField
    impact: SuggestedField
    project_stage: ProjectStage
    unavailable_fields: list[CaseField] = Field(max_length=7, description="Only fields explicitly declared unavailable by the user. Include the user quote in that field's evidence, even if value is null.")


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["USER_PROVIDED", "AI_DRAFTED", "USER_EDITED"]
    evidence: list[str] = Field(default_factory=list)


class DraftImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    filename: str = Field(min_length=1, max_length=200)
    format: Literal["PNG", "JPEG"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    @property
    def extension(self):
        return "png" if self.format == "PNG" else "jpg"


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 3
    draft_id: str = Field(default_factory=lambda: uuid4().hex)
    revision: int = 1
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    original_description: str = Field(max_length=12000)
    clarification: str | None = Field(default=None, max_length=6000)
    clarification_used: bool = False
    follow_ups: list[Annotated[str, Field(max_length=6000)]] = Field(default_factory=list)
    details_skipped: bool = False
    content: Content = Field(default_factory=Content)
    images: list[DraftImage] = Field(default_factory=list, max_length=4)
    provenance: dict[CaseField, Provenance] = Field(default_factory=dict)
    project_stage: ProjectStage = "UNKNOWN"
    unavailable_fields: list[CaseField] = Field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    request_count: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def restore_legacy_reply(self):
        # Older files store a single reply. Retain it without treating the old
        # clarification_used flag as a permanent conversation lock.
        if self.clarification and not self.follow_ups:
            self.follow_ups = [self.clarification]
        self.schema_version = 3
        return self

    @property
    def clarification_fields(self):
        return self.content.missing_fields

    @property
    def clarification_question(self):
        if self.details_skipped:
            return None
        fields = self.clarification_fields
        if fields:
            return "Still missing: " + ", ".join(name.title() for name in fields) + "."
        if self.content.body_words < MIN_BODY_WORDS:
            return "The draft feels a little brief. A few more details about the background or creative approach could help."
        return None

    @property
    def state(self):
        return "AWAITING_DETAILS" if self.clarification_question else "DRAFT_READY"

    @property
    def notices(self):
        notices = []
        if self.content.body_words < MIN_BODY_WORDS:
            notices.append("INSUFFICIENT_INFORMATION")
        if self.content.body_words > MAX_BODY_WORDS:
            notices.append("BODY_TOO_LONG")
        if self.content.title and len(self.content.title) > 40:
            notices.append("TITLE_LENGTH_CHECK")
        return notices
