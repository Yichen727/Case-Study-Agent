"""Generate the small Swagger 2.0 contract required by Copilot Studio."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.api.main import API_KEY_HEADER, ERROR_RESPONSES, SEARCH_DESCRIPTION
from app.api.schemas import ErrorResponse, SearchRequest, SearchResponse


def _swagger_schema(value):
    """Translate our model schemas, whose only unions are nullable fields."""
    if isinstance(value, list):
        return [_swagger_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {key: _swagger_schema(item) for key, item in value.items() if key != "$defs"}
    if "anyOf" in value:
        alternatives = value.pop("anyOf")
        nonnull = [item for item in alternatives if item != {"type": "null"}]
        if len(alternatives) != 2 or len(nonnull) != 1:
            raise ValueError("Only nullable fields are supported in this Swagger contract.")
        value = {**nonnull[0], **value, "x-nullable": True}
    return value


def build_copilot_spec(server_url="http://127.0.0.1:8000"):
    server = urlsplit(server_url)
    if (not server.hostname or server.username is not None or server.password is not None
            or server.query or server.fragment or server.path not in {"", "/"}
            or server.scheme not in {"http", "https"}
            or (server.scheme == "http" and server.hostname not in {"127.0.0.1", "localhost"})):
        raise ValueError("Use an HTTPS server origin, or HTTP localhost for local testing; no credentials, path or query.")
    definitions = {}
    for model in (SearchRequest, SearchResponse, ErrorResponse):
        schema = model.model_json_schema(ref_template="#/definitions/{model}")
        definitions.update({name: _swagger_schema(item) for name, item in schema.get("$defs", {}).items()})
        definitions[model.__name__] = _swagger_schema(schema)
    return {
        "swagger": "2.0",
        "info": {"title": "Case Study Search API", "version": "1.0.0",
                 "description": "Search the existing case-study library and return evidence-backed slide matches."},
        "host": server.netloc, "basePath": "/", "schemes": [server.scheme],
        "consumes": ["application/json"], "produces": ["application/json"],
        "securityDefinitions": {"ApiKey": {"type": "apiKey", "in": "header", "name": API_KEY_HEADER}},
        "security": [{"ApiKey": []}],
        "paths": {"/api/search": {"post": {
            "operationId": "SearchCaseStudies", "summary": "Search case-study slides",
            "description": SEARCH_DESCRIPTION,
            "parameters": [{"name": "body", "in": "body", "required": True,
                            "schema": {"$ref": "#/definitions/SearchRequest"}}],
            "responses": {
                "200": {"description": "Ranked slide matches, including evidence gaps and search notices.",
                        "schema": {"$ref": "#/definitions/SearchResponse"}},
                **{str(status): {"description": response["description"],
                                "schema": {"$ref": "#/definitions/ErrorResponse"}}
                   for status, response in ERROR_RESPONSES.items()},
            },
        }}},
        "definitions": definitions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path, default=Path("docs/copilot-openapi.json"))
    args = parser.parse_args()
    spec = build_copilot_spec(args.server_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"OpenAPI 2.0 specification written to {args.output}")


if __name__ == "__main__":
    main()
