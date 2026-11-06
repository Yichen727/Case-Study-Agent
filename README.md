# Case Study Agent

Find and arrange existing case-study slides, or draft a new case from your notes, then export editable PowerPoint files.

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

Without an API key, local uploads, keyword search, manual metadata editing and editing/exporting saved new-case drafts remain available.

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

The export uses original editable slides, with any **applied text edits** replacing only their body area. Metadata corrections do not rewrite slide content. Check the downloaded deck in PowerPoint before sharing.

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

## 5. Create a new case

1. Open **Create case** and describe the client, project background, creative work and actual results. Optionally use **Upload Image** below the description to add up to four PNG/JPEG images (10 MB each), then click **Create draft**.
2. **Additional details** stays available for further facts or wording refinements, with no conversation-round limit. Missing information or a body under 120 words prompts you to add detail; **Skip for now** dismisses that prompt without closing the input. Unavailable facts stay blank; planned goals are not presented as achieved results.
3. Check and edit the seven fields. Brief, Creative and Impact share a **120–250-word** range with no fixed section proportions. Short drafts are allowed and marked as insufficient information.
4. Click **Save draft** to keep edits, or **Generate PPTX** to save and confirm the current wording, then **Download PPTX**. Slide titles are exported in uppercase; the **Slide title** preview shows the final text. Keep that title to 40 characters or fewer and body text to 250 words or fewer before export.

This produces **one editable slide** using `assets/templates/Single Case Template.pptx`. Client and Services are saved with the draft but do not appear on the slide. Images fill positions 1–4 in the displayed order, with centred cropping and no stretching; unused positions stay blank. Video embedding is not supported. Check the wording and slide in PowerPoint before sharing.

Use **Retry** after a draft is generated to request fresh AI wording from the same project facts and submitted additions. Manual field edits and attached images are kept. A failed retry leaves the saved draft unchanged; after a successful retry, generate the PPTX again.

You can add or remove images after generation. Click an image's **×** to remove it; remove and upload again to change the order or replace a picture. Changes are saved with the draft and require a new **Generate PPTX**. Images are processed locally and are not sent to the text model. Very large images (over 20 million pixels) should be resized before upload.

Website URLs in your description or submitted additions appear under **Optional link**. The first is selected automatically; choose another or **No link**. **Link type** controls the footer label: **Website → READ MORE HERE.**, **Video → WATCH VIDEO HERE.** YouTube and Vimeo links are recognised automatically; you can adjust the type for other sites. The app does not open or read these pages.

Link choices are saved locally and retained by Retry and History. After changing a link or its type, generate the PPTX again. The slide contains one clickable footer link; the URL and label do not count towards the body word limit.

The latest saved draft is restored when you reopen the app. Edits survive tab changes within the current session; use Save draft or Generate PPTX to preserve them after a refresh. After editing, generate again to enable a current download. **Start new case** opens a blank input and keeps earlier drafts.

Click the **History** icon beside **Create case** to view saved drafts by title, Client and last saved time. Click a title to save current field edits and open that draft with its saved images without an AI request. Each entry's **×** permanently removes that draft, attached images and local generated PPTX files; deletion is disabled while the draft is being processed.

New drafts and their slides are stored in `data/generated_cases` and retained by **Clear finished tasks**. They are separate from the case library and cannot yet be searched or combined with existing slides.

Each submitted message or **Retry** makes one AI request. Skip, save, image upload/removal, navigation and PPTX generation make none. Submitted additions are saved with the draft; older drafts can also continue the conversation.

## 6. Edit selected case text

1. In **Selected cases**, enter an **Editing request**, for example: “For all selected cases, remove the section headings and use two paragraphs at about half the current length.” Use **case 2** or **cases 1 and 3** to target selection numbers; otherwise the default is all selected cases. Click **Send request**.
2. Review **Before / Preview** and the supporting text. You can edit the preview before clicking **Apply changes**, or choose **Discard**. Ambiguous references may prompt one short clarification.
3. Applied text is marked **Edited version** and saved locally. Further requests start from the applied wording; **Restore original** removes that case's edits. Original presentations, library metadata and search evidence stay unchanged.

Click **Generate PPTX** to export applied text alongside unchanged original slides in the current order. The company template's first four pages come before your cases and its remaining pages come after them. Titles, media, layout, fonts and supported footer links are retained; only the supported body area is replaced. Text stays editable. Long edits may need shortening to fit the original body area; check the result in PowerPoint.

Apply, further changes or **Restore original** require a new **Generate PPTX**. Generation freezes the wording and order at the time you click; changes made while it runs do not alter that file. A result containing an outdated text revision cannot be downloaded as the current version. Restore changes future exports, not files already downloaded. Pending previews are not exported.

Requests use one AI call for a small batch of up to ten slides, subject to a text-size limit. For large requests, specify fewer selection numbers. Slides without a safely editable body, including bodies with inline hyperlinks, are left unchanged with an explanation. After restarting, reselect the same cases to see their saved edits.

Applied text and small derived page XML files are stored in `data/selected_edits`; original source snapshots stay unchanged. PPTX generation is local and makes no AI request. **Clear finished tasks** removes generated selected-case decks while retaining applied edits. Installation ZIPs exclude all local case data, images, drafts and derived pages.

## Common issues

| Issue | What to do |
| --- | --- |
| Startup reports a missing Python module | Close the app and run `start.bat` or `start.command` from the updated project folder to install the current requirements. |
| Import fails | Open the file in PowerPoint, save a valid `.pptx` copy and upload it again. Check the 500 MB limit. |
| No High Match results | Expand Partial Match and review the evidence gaps. Refine the brief if needed. |
| AI or search preparation fails | Check the internet connection and ask the project owner to check the API key or quota. Use the relevant Retry button. |
| Original presentation snapshot is missing | Upload the original deck again, select cases from that imported deck and generate again. |

Keep the project's **data** folder: it contains your case library and original presentation copies needed for export.

Download selected-case decks you want to keep before using **Clear finished tasks**. This removes those generated decks; imported cases, original presentation copies, and new-case drafts/slides are retained.
