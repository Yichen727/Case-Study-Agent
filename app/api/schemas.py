"""Bounded REST contracts using the existing search requirement vocabulary."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.search.intent import MAX_QUERY_BYTES, Requirement, RequirementKind, normalize_query

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchCondition(APIModel):
    kind: RequirementKind
    description: str = Field(min_length=1, max_length=220)
    terms: list[Term] = Field(default_factory=list, max_length=8,
                             description="Alternative evidence phrases for ONE condition; any term may match. Use separate conditions to require both.")
    minimum: float | None = Field(default=None, ge=0, allow_inf_nan=False,
                                 description="Optional lower bound for quantified_outcomes only, e.g. 2000000 views.")

    @field_validator("description")
    @classmethod
    def clean_description(cls, value):
        if not value.strip():
            raise ValueError("A condition description is required.")
        return value.strip()

    @model_validator(mode="after")
    def supported_condition(self):
        if self.kind in {"client", "services", "pillar", "content", "link_type", "source"} and not self.terms:
            raise ValueError("This condition kind requires at least one evidence term.")
        if self.minimum is not None and self.kind != "quantified_outcomes":
            raise ValueError("Only quantified_outcomes supports a minimum.")
        if self.kind == "link_type" and any(term not in {"READ_MORE", "WATCH_VIDEO"} for term in self.terms):
            raise ValueError("Link terms must be READ_MORE or WATCH_VIDEO.")
        return self

    def as_requirement(self):
        return Requirement(**self.model_dump())


class SearchRequest(APIModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_BYTES,
                       description="Full search brief, including all must-haves and exclusions. Maximum 6000 UTF-8 bytes.")
    mandatory_conditions: list[SearchCondition] | None = Field(default=None, max_length=8,
        description="Additional mandatory checks; never replaces requirements or exclusions in query.")
    preferred_conditions: list[SearchCondition] | None = Field(default=None, max_length=6,
        description="Additional preferences. Missing preferred evidence does not become a hard failure.")
    limit: int = Field(default=5, ge=1, le=10, strict=True)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value):
        value = value.strip()
        if not normalize_query(value) or len(value.encode("utf-8")) > MAX_QUERY_BYTES:
            raise ValueError("Provide a nonempty query within 6000 UTF-8 bytes.")
        return value


class Evidence(APIModel):
    source: str
    quote: str


class ConditionCheck(APIModel):
    requirement: str
    status: Literal["MET", "NOT_MET", "UNKNOWN"]
    quote: str | None
    source: str


class SearchResult(APIModel):
    case_id: str
    case_name: str
    client: str | None
    match_level: Literal["High Match", "Partial Match", "Not a Match"]
    match_reason: str
    supporting_evidence: list[Evidence]
    source_file: str
    slide_number: int
    review_status: Literal["CONFIRMED", "NEEDS_REVIEW"]
    condition_checks: list[ConditionCheck]
    preferred_checks: list[ConditionCheck]
    uncertainties: list[str]


class SearchResponse(APIModel):
    results: list[SearchResult]
    search_mode: Literal["hybrid", "keyword"]
    notices: list[str]


class ErrorResponse(APIModel):
    error: str
    message: str
