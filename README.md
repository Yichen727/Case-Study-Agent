# Case Study Agent

Find relevant case-study slides, choose their order, and export an editable PowerPoint deck.

This guide is for employees running the tool locally on Windows or macOS. Use presentations approved for this tool.

## Setup

Extract the complete installation ZIP to a folder, then use the launcher for your system:

| System | Start the app |
| --- | --- |
| Windows 10/11, 64-bit | Double-click **start.bat**. |
| macOS, Intel or Apple Silicon | Double-click **start.command**. |

If the internal package includes the approved company `.env`, no API key setup is needed. Otherwise, configure the key below for AI features. Do not run the app from inside the ZIP viewer.

Both launchers find 64-bit Python 3.10 or later, create a private `.venv`, install requirements and start the app. Windows can install Python 3.13 through WinGet. macOS can install it through an existing Homebrew installation; if neither Python nor Homebrew is available, install approved Python from [python.org](https://www.python.org/downloads/macos/) once, or ask IT.

Initial setup needs internet access and may take several minutes. Later launches reuse the environment; dependency installation runs again when the requirements, Python runtime or platform changes. Do not copy `.venv` between computers.

The provided installation ZIP preserves the executable permission needed by `start.command`. If another ZIP tool removes it, run `chmod +x start.command` once in Terminal from the project folder. If macOS blocks the file, follow your organization's app-approval process; the launcher does not disable macOS security settings.

If company policy blocks installation, ask IT to install Python or approve the required packages. The launcher leaves error messages visible.

For manual setup on Windows, open PowerShell in the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the project owner supplied the approved company `.env`, keep it beside `run.py`. Otherwise, ask for the approved API configuration and create `.env` there:

```dotenv
OPENAI_API_KEY=your-approved-api-key
OPENAI_METADATA_MODEL=gpt-5.4-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

The approved shared company `.env` may be included in installation ZIPs for authorized employees. Keep it out of Git/GitHub and public distribution; `.gitignore` does not exclude it from manually created ZIPs. AI features send extracted case text and search queries to OpenAI; the original PPTX file is not uploaded to OpenAI.

Start with **start.bat** on Windows or **start.command** on macOS. For manual startup on Windows:

```powershell
.\.venv\Scripts\python.exe run.py
```

For manual setup and startup on macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

The app opens in your browser, usually at `http://localhost:8501`. If it does not open automatically, use the address shown in the console. Keep the console open while using the app; press **Ctrl+C** to stop it before launching another copy.

Without an API key, local uploads, keyword search and manual metadata editing remain available.

To prepare an internal ZIP with the approved shared `.env`, run `python package_app.py --include-env`. The resulting `dist/Case Study Agent - Local.zip` contains both launchers, the application and the company template, with macOS executable permissions preserved. `dist` is ignored by Git. Without `--include-env`, credentials are omitted. The package excludes `.venv`, `.git`, the case database and presentation snapshots; recipients import approved decks locally to build their case library.

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

The file includes the company template's first four pages, your cases in the chosen order, and the template's final page. For example, two selected cases produce seven slides. Keep the supplied template in [assets/templates](assets/templates/README.md) when moving the app to another computer.

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
| Startup reports a missing Python module | Close the app and run `start.bat` or `start.command` from the updated project folder to install the current requirements. |
| Import fails | Open the file in PowerPoint, save a valid `.pptx` copy and upload it again. Check the 500 MB limit. |
| No High Match results | Expand Partial Match and review the evidence gaps. Refine the brief if needed. |
| AI or search preparation fails | Check the internet connection and ask the project owner to check the API key or quota. Use the relevant Retry button. |
| Original presentation snapshot is missing | Upload the original deck again, select cases from that imported deck and generate again. |

Keep the project's **data** folder: it contains your case library and original presentation copies needed for export.

Download files you want to keep before using **Clear finished tasks**. This removes generated PPTX files from the app; imported cases and original presentation copies are retained.
