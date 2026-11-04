"""Structured drafts; completeness is separate from factual verification."""
from datetime import datetime, timezone
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    def missing_fields(self):
        return [name for name in CASE_FIELDS if getattr(self, name) is None]


class SuggestedField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None = Field(max_length=6000)
    evidence: list[str] = Field(max_length=6, description="Short verbatim excerpts from the user's original description or one reply. Never quote the generated draft.")


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


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    draft_id: str = Field(default_factory=lambda: uuid4().hex)
    revision: int = 1
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    original_description: str = Field(max_length=12000)
    clarification: str | None = Field(default=None, max_length=6000)
    clarification_used: bool = False
    content: Content = Field(default_factory=Content)
    provenance: dict[CaseField, Provenance] = Field(default_factory=dict)
    project_stage: ProjectStage = "UNKNOWN"
    unavailable_fields: list[CaseField] = Field(default_factory=list)
    model: str = ""
    prompt_version: str = ""
    request_count: int = Field(default=0, ge=0, le=2)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @property
    def clarification_fields(self):
        if self.clarification_used:
            return []
        return [name for name in self.content.missing_fields if name not in self.unavailable_fields]

    @property
    def clarification_question(self):
        labels = {"title": "the main project idea", "client": "the commissioning client",
                  "services": "the services delivered", "pillar": "the case-study pillar",
                  "brief": "the background and objective", "creative": "the creative approach and execution",
                  "impact": "any actual results (or say if results are not available yet)"}
        fields = self.clarification_fields
        return "Please add " + "; ".join(labels[name] for name in fields) + ". You can skip anything unavailable." if fields else None

    @property
    def state(self):
        return "AWAITING_DETAILS" if self.clarification_fields else "DRAFT_READY"

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
