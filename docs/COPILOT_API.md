# Copilot Studio search adapter

This optional API calls the same `app.search.hybrid_search.search_cases()` used by Streamlit. The UI, ingestion, metadata review and original-slide export stay unchanged. This adapter exposes search only.

## Prepare and start locally

Run these commands from the project root, using the same virtual environment as the app:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-api.txt
```

First use Streamlit to import presentations and let search preparation finish. The API uses the existing `data/case_studies.sqlite3` and existing vectors. It does not create or migrate the database, ingest files, run background tasks, or build case embeddings.

Generate a separate random API credential locally:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save the generated value as `CASE_STUDY_API_KEY` in the existing root `.env`. This is a new credential for this adapter, **not** `OPENAI_API_KEY`. The key must contain 32-128 letters, digits, hyphens or underscores. Environment variables override `.env`. Never commit the key, paste it into chat/model instructions, or include it in a URL. Restart the API after changing it.

Keep the existing OpenAI settings for intent interpretation and query embeddings. No model provider or credentials are migrated to Microsoft by adding this adapter.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

The API refuses to start without a valid configured credential. It listens on loopback with this command; nothing is published or tunneled. Interactive documentation routes are disabled. Stop with Ctrl+C. Streamlit can continue running separately.

## Test a request

In a second PowerShell window, enter the API credential using the hidden prompt:

```powershell
$searchKey = Read-Host "Case Study API key" -AsSecureString
$body = @{
    query = "Find cases about AI-generated content for a hospitality brand."
    limit = 5
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/search" `
    -Headers @{ "X-API-Key" = [System.Net.NetworkCredential]::new("", $searchKey).Password } `
    -ContentType "application/json" -Body $body

Remove-Variable searchKey
```

`POST /api/search` accepts JSON:

```json
{
  "query": "Find AI-generated films for a hospitality brand; exclude NHS clients.",
  "mandatory_conditions": [
    {
      "kind": "ai_production",
      "description": "AI was used to produce the work"
    },
    {
      "kind": "content",
      "description": "Hospitality or hotel work",
      "terms": ["hospitality", "hotel"]
    }
  ],
  "preferred_conditions": [
    {
      "kind": "quantified_outcomes",
      "description": "Prefer at least two million reported viewers",
      "terms": ["viewers"],
      "minimum": 2000000
    }
  ],
  "limit": 5
}
```

- Only `query` is required: nonempty, at most 6,000 UTF-8 bytes.
- `limit` defaults to 5 and must be an integer from 1 to 10. It caps all returned results, including Partial Match and Not a Match.
- Condition arrays can be omitted, `null` or empty. Maximum 8 mandatory and 6 preferred conditions.
- Structured conditions **add to** interpreted requirements and exclusions in the full query. They do not replace them. Put exclusions and unsupported conditions in the query.
- Supported `kind` values: `client`, `services`, `pillar`, `content`, `ai_production`, `interactive`, `quantified_outcomes`, `no_additional_filming`, `link_type`, `source`.
- Each condition needs a description. `terms` contains up to eight alternative evidence phrases of up to 100 characters each. A term match is OR within one condition; use separate conditions when both are required. The `client`, `services`, `pillar`, `content`, `link_type` and `source` kinds require terms.
- Broad `ai_production` and `interactive` checks can omit terms. AI dubbing or a named tool must use specific terms. `link_type` terms are `READ_MORE` or `WATCH_VIDEO`.
- `minimum` is an optional nonnegative numeric lower bound for `quantified_outcomes` only. Upper bounds and other unsupported restrictions belong in the full query, where existing rules keep them unconfirmed.

The response is an object with `results`, `search_mode` (`hybrid` or `keyword`) and `notices`. Each result contains:

| Field | Meaning |
| --- | --- |
| `case_id` | Stable identity of this source slide; retain it even when names match. |
| `case_name`, `client` | Existing metadata; client can be null or Unknown. |
| `match_level`, `match_reason` | Existing classification and explanation. |
| `supporting_evidence` | Original/extracted/verified excerpts, each with `source` and `quote`. |
| `source_file`, `slide_number` | Presentation filename and one-based original slide number. |
| `review_status` | `CONFIRMED` or `NEEDS_REVIEW`; completeness does not mean AI metadata was independently verified. |
| `condition_checks`, `preferred_checks` | Requirement, `MET` / `NOT_MET` / `UNKNOWN`, quote and source. |
| `uncertainties` | Evidence gaps, missing metadata and unverified AI fields. |

No numeric similarity scores, local snapshot paths or credentials are returned. Same-name cases are not merged. Missing preferences do not become hard constraints. An unverified AI-inferred client cannot alone prove a mandatory client condition.

If interpretation or semantic retrieval is unavailable, the existing engine may return HTTP 200 with keyword results and explanatory notices. An uninterpreted free-text brief cannot yield High Match; supplied structured conditions are still checked. No matches or an initialized empty library returns HTTP 200 with an empty list.

Errors use `{"error": "CODE", "message": "Safe explanation"}`:

| HTTP | Code | Action |
| --- | --- | --- |
| 401 | `UNAUTHORIZED` | Supply the correct `X-API-Key` header. |
| 422 | `INVALID_REQUEST` | Check the JSON contract and bounds. |
| 503 | `LIBRARY_UNAVAILABLE` | Prepare the current library in Streamlit; check database availability. The API will not migrate it. |
| 503 | `SEARCH_UNAVAILABLE` | Retry after the search service recovers. |
| 500 | `SEARCH_FAILED` | Report the failure to the project maintainer. |

Search can write the existing query/embedding caches and usage counters. It does not modify source PPTX files, case records, metadata or case-search documents. Initial tests should be light and sequential: this remains the MVP's SQLite-backed service with a shared library, not a multi-user permission system.

## Connect to Copilot Studio

The checked-in [copilot-openapi.json](copilot-openapi.json) is generated Swagger / OpenAPI **2.0**, with a header API-key security definition and the single `SearchCaseStudies` operation. Its default address is localhost for local inspection, not a cloud-reachable deployment.

1. Arrange an organization-approved test HTTPS endpoint that routes to this API and is reachable from your Power Platform environment. Copilot Studio cannot call your PC's `127.0.0.1`. Keep application API-key authentication enabled and restrict test access. This change does not deploy the app or open a public tunnel.
2. Regenerate the specification with the actual HTTPS origin, without a path, credential or query string:

   ```powershell
   .\.venv\Scripts\python.exe -m app.api.openapi --server-url https://your-approved-api-host.example
   ```

3. In Copilot Studio, open the agent's **Tools**, choose **Add tool > New tool > REST API**, and import `docs/copilot-openapi.json`. If this preview tool is unavailable in your tenant, use the same Swagger file to create a Power Platform custom connector and add its search action to the agent.
4. Configure **API key** authentication in the connector/connection: header location, name `X-API-Key`. Supply the actual credential through the platform's credential interface, not an ordinary tool input, conversation, URL or agent instructions. Restrict the connection and agent to the approved test group.
5. Enable `SearchCaseStudies`. Map the employee's full brief to `query`, leave structured conditions empty initially, and use `limit=5`. Keep the full brief even when structured conditions are later supplied. Display classification, evidence and uncertainties without upgrading UNKNOWN conditions or merging equal names.
6. Test a known AI case, a mandatory condition with missing evidence, no results, and a rejected credential. Only publish more widely after the company approves access, hosting and data processing.

Tenant permissions, connector/DLP policies, licenses and a reachable HTTPS deployment are still required. The direct REST API tool is currently documented as preview; actual tenant import and end-to-end Copilot execution must be verified there. The OpenAI data-processing path remains the one described in the main README.

Microsoft references: [REST API tools and authentication](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api), [custom connectors from OpenAPI 2.0](https://learn.microsoft.com/en-us/connectors/custom-connectors/define-openapi-definition).

## Automated checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q
```

Tests use temporary libraries and source files, stub model/network boundaries, and exercise the real repository, hybrid search and classification. They cover authentication, request bounds, identity isolation, mandatory/preferred evidence, unavailable dependencies, safe errors, schema generation and unchanged original records/files. They do not call OpenAI or use company case data.
