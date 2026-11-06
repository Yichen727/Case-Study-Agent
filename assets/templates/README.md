# Company export template

Keep the approved local template here as **Case Study Tool Base Template.pptx**.
The company-provided copy is installed locally and ignored by Git. Supply it separately when setting up another workstation.

Exports keep template pages 1-4, insert selected case slides in the user's order, then keep template page 5 and any later template pages. Applied edits replace only a supported body area on a derived slide; unedited cases retain their original slide XML. The template must have at least five slides and the same slide dimensions as the source cases. The current approved template has five slides.

The application reads this file without editing it. Replacing it changes the export cache identity. If it changes during generation, generate again. An absent or invalid template blocks generation with a clear message; it does not silently produce a deck without the company pages.

## Single new-case template

Keep **Single Case Template.pptx** here for the **Create case** workflow. It produces one editable slide, with the uppercase title, pillar, Brief, Creative and Impact filled into the approved layout. Client and Services stay in the saved draft. Zero to four images occupy the numbered areas; unused areas stay empty. An optional READ MORE HERE. / WATCH VIDEO HERE. footer uses `#FF0069`. It is separate from the five-page company deck template above; existing-case edits keep their own source layout.

The supplied shape names, IDs and section labels are required. The app copies the file and preserves its layout and embedded fonts; it never edits the original. Include both approved templates when distributing the app. Do not include Office lock files such as `~$Single Case Template.pptx`.
