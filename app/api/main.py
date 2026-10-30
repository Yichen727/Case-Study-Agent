"""Authenticated search only: no ingestion, migrations, index jobs or UI imports."""
from contextlib import closing
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app import config
from app.api.schemas import ErrorResponse, SearchRequest, SearchResponse, SearchResult
from app.db.migrations import SCHEMA_VERSION
from app.search.embedding_service import SearchServiceError
from app.search.hybrid_search import search_cases

logger = logging.getLogger(__name__)
API_KEY_HEADER = "X-API-Key"
SEARCH_DESCRIPTION = (
    "Search existing case-study slides using the full user brief. Returns ranked matches, "
    "original evidence, mandatory-check outcomes and uncertainty. Additional structured "
    "conditions supplement the brief. Keep distinct case_id values even when names match. "
    "UNKNOWN is not proof that a mandatory condition is satisfied. Does not edit slides or metadata."
)
ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Missing or invalid API key."},
    422: {"model": ErrorResponse, "description": "Invalid search request."},
    503: {"model": ErrorResponse, "description": "Search library or service unavailable."},
    500: {"model": ErrorResponse, "description": "Unexpected search failure."},
}


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def require_library(db_path):
    """Read-only preflight avoids creating or migrating an employee's database."""
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("Library schema is not ready.")


def create_app(*, db_path: str | Path | None = None):
    values = {**dotenv_values(config.ENV_FILE), **os.environ}
    api_key = values.get("CASE_STUDY_API_KEY") or ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", api_key):
        raise RuntimeError("Set CASE_STUDY_API_KEY to a random 32-128 character URL-safe key before starting the API.")
    library = Path(db_path if db_path is not None else config.DB_PATH).resolve()
    header = APIKeyHeader(name=API_KEY_HEADER, scheme_name="ApiKey", auto_error=False)

    def authenticate(provided: str | None = Depends(header)):
        if provided is None or not secrets.compare_digest(provided.encode(), api_key.encode()):
            raise APIError(401, "UNAUTHORIZED", "A valid API key is required.")

    # No unauthenticated documentation routes or permissive browser CORS policy.
    app = FastAPI(title="Case Study Search API", version="1.0.0", debug=False,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _exc: RequestValidationError):
        # Do not echo the prompt, request body, headers or validation internals.
        return JSONResponse(status_code=422, content={"error": "INVALID_REQUEST",
                            "message": "Invalid search request. Check query, conditions and limit."})

    @app.post("/api/search", response_model=SearchResponse, dependencies=[Depends(authenticate)],
              operation_id="SearchCaseStudies", summary="Search case-study slides",
              description=SEARCH_DESCRIPTION, responses=ERROR_RESPONSES)
    def search(payload: SearchRequest):
        try:
            require_library(library)
            matches = search_cases(library, payload.query, payload.limit,
                mandatory_conditions=[item.as_requirement() for item in payload.mandatory_conditions or []],
                preferred_conditions=[item.as_requirement() for item in payload.preferred_conditions or []])
            results = [SearchResult(
                case_id=case["case_id"], case_name=case["title"], client=case["client"],
                match_level=case["match_level"], match_reason=case["match_reason"],
                supporting_evidence=case["evidence"], source_file=case["source_deck"],
                slide_number=int(case["slide_numbers"]), review_status=case["review_status"],
                condition_checks=case["condition_checks"], preferred_checks=case["preferred_checks"],
                uncertainties=case["uncertainties"],
            ) for case in matches]
            return SearchResponse(results=results, search_mode=matches.mode, notices=matches.notices)
        except sqlite3.Error:
            raise APIError(503, "LIBRARY_UNAVAILABLE",
                           "The case library is unavailable. Prepare it in the Streamlit app and retry.") from None
        except SearchServiceError:
            raise APIError(503, "SEARCH_UNAVAILABLE", "Search is temporarily unavailable. Please retry.") from None
        except Exception as exc:
            logger.error("Search request failed (%s).", type(exc).__name__)
            raise APIError(500, "SEARCH_FAILED", "The search could not be completed. Please retry.") from None

    return app
