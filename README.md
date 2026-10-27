# Case Study Agent (MVP scaffold)

A small, testable starting point for searching approved case studies in a PowerPoint master deck.

## Current capabilities
- Upload a `.pptx` file in Streamlit
- Parse slide text and hyperlinks locally
- Identify case-study slides by a first-line `CASE STUDY` marker
- Extract title, pillar, case name, BRIEF / CREATIVE / IMPACT sections
- Store case-level and slide-level records in SQLite
- Keyword search using SQLite FTS5 (with a LIKE fallback)
- Show source slide numbers, extracted evidence, and missing metadata
- Let a user add missing metadata with provenance

## Important limitation
Original-slide export is intentionally not implemented in this scaffold. `python-pptx` does not provide a complete, reliable native slide-cloning API. Validate a PowerPoint/Open XML or PowerPoint-automation strategy against real company decks before promising fidelity.

## Setup
Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows PowerShell:
.venv\\Scripts\\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
streamlit run app/ui/streamlit_app.py
```

## Suggested first test
1. Use a copy of an approved master deck.
2. Upload it in the UI.
3. Inspect the parsed cases and slide associations.
4. Test missing metadata edits.
5. Test search using exact Client / Pillar / service terms and natural-language queries.
6. Create a separate export PoC before integrating slide export.

## Project structure
- `app/ingestion/`: PPTX parsing
- `app/db/`: SQLite schema and persistence
- `app/search/`: keyword retrieval
- `app/services/`: application-level orchestration
- `app/export/`: export boundary / future implementation
- `app/ui/`: Streamlit interface
- `tests/`: basic automated tests
