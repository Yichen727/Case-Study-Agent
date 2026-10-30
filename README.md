# Case Study Agent

Find relevant case-study slides, choose their order, and export an editable PowerPoint deck.

This guide is for employees running the tool locally on Windows. Use presentations approved for this tool.

## Setup

You need Python 3.10 or later and an internet connection for AI features.

Open PowerShell in the project folder and install the app:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Ask the project owner for an approved OpenAI API key. Create a file named `.env` beside `run.py` and add:

```dotenv
OPENAI_API_KEY=your-approved-api-key
OPENAI_METADATA_MODEL=gpt-5.4-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

Keep the key private. Do not share or commit `.env`. AI features send extracted case text and search queries to OpenAI; the original PPTX file is not uploaded to OpenAI.

Start the app:

```powershell
.\.venv\Scripts\python.exe run.py
```

Open the local address shown in PowerShell, usually `http://localhost:8501`. Keep PowerShell open while using the app; press **Ctrl+C** to stop it.

Without an API key, local uploads, keyword search and manual metadata editing remain available.

## 1. Import presentations

1. Download any presentations stored online to your computer first.
2. Under **Import presentations**, select one or more `.pptx` files, up to 500 MB each.
3. Click **Parse and save decks** and follow the **Processing** status. You can use existing cases while imports run.

Each recognised case-study slide becomes a separate case, including slides with the same name. Uploading an identical deck again does not add duplicate cases or overwrite manual corrections. View the library in **All cases**.

## 2. Search and select

Describe your brief in **Search cases**, including must-haves or exclusions, then click **Search**. Enter adds a new line.

For example: *Show me cases about AI-generated content for a hospitality brand.*

- **High Match** is expanded by default; click its heading to collapse it.
- Expand **Partial Match** to review related cases with evidence gaps.
- Open **Matching evidence** to check the reasons and supporting text.
- Tick **Select case** to add an individual slide.
- **Select all High Match** adds all High Match results across the current search's pages, keeping existing choices.
- **Clear all selections** removes every selection, including choices kept from earlier searches.

Selections stay available as you change result pages or search again, within the current app session.

## 3. Arrange and export

1. Open **Selected cases**, or click the selected-case count above the search results.
2. Drag cases into the required order. Click **×** on a case to remove it.
3. Click **Generate PPTX** above the list. This confirms the current order and starts generation.
4. When ready, click **Download PPTX** beside it.

If you change the selection or order, generate a new file before downloading. Existing files retain the order used when they were generated.

The export uses the original editable slides. Metadata corrections do not rewrite slide content. Check the downloaded deck in PowerPoint before sharing.

## 4. Review missing metadata

Open **Needs Review**, choose a case, correct its fields and click **Save metadata**. Use **Other metadata** for the title, pillar, Brief, Creative and Impact. **Show all cases** also lets you correct complete records.

- **Confirmed** means the required fields are filled; it does not mean AI suggestions have been independently verified.
- **Needs Review** means at least one required field is missing or Unknown. These cases can still be searched and exported.

Under **Retry automatic enrichment**:

| Button | Purpose |
| --- | --- |
| **Retry AI enrichment** | Infer missing Client / Services from slide text. Requires an API key and may reuse a previous result. |
| **Retry web metadata** | Revisit the case's READ MORE or other existing links to extract metadata. |

Retries apply to the selected case's entire source deck and preserve manual corrections. They do not generate missing Brief, Creative or Impact text.

## Common issues

| Issue | What to do |
| --- | --- |
| Import fails | Open the file in PowerPoint, save a valid `.pptx` copy and upload it again. Check the 500 MB limit. |
| No High Match results | Expand Partial Match and review the evidence gaps. Refine the brief if needed. |
| AI or search preparation fails | Check the internet connection and ask the project owner to check the API key or quota. Use the relevant Retry button. |
| Original presentation snapshot is missing | Upload the original deck again, select cases from that imported deck and generate again. |

Keep the project's **data** folder: it contains your case library and original presentation copies needed for export.

Download files you want to keep before using **Clear finished tasks**. This removes generated PPTX files from the app; imported cases and original presentation copies are retained.
