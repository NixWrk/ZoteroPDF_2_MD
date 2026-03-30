# ZoteroPDF_2_MD

GUI app to process PDF attachments from a local Zotero collection with multiple export modes based on Marker.

## What It Does

1. Opens local Zotero data (`zotero.sqlite` + `storage`) without API keys.
2. Lets you choose a collection in GUI.
3. Resolves local PDF attachments from that collection (optionally including subcollections).
4. Stages files with deterministic short aliases for Windows path safety.
5. Runs `marker` batch conversion and falls back to `marker_single` for missing outputs.
6. Supports 3 export modes:
   - `classic`: original marker markdown output structure
   - `llm_bundle`: flat folder with mixed `.md` + images (collection-named folder)
   - `zotero_single_html`: marker HTML -> single-file HTML (images inlined as base64) -> attach back to Zotero parent item
7. Writes a filename map CSV for traceability.

## Requirements

- Windows with local Zotero desktop data available.
- Python 3.10+.
- `marker` and `marker_single` available in `PATH`.

## Run

From repository root:

```powershell
python app.py
```

## GUI Workflow

1. Use **Detected Zotero profile** (auto-detected from `profiles.ini` + profile `prefs.js`).
2. If needed, set **Zotero data folder** manually (profile `.../zotero` directory).
3. Click **Load collections**.
4. Select a collection.
5. Set **Output folder**.
6. Choose **Export mode**:
   - `Classic (MD in separate folders)`
   - `LLM bundle (flat folder: md + images)`
   - `Zotero single-file HTML attachment`
7. Optional settings:
   - include subcollections
   - skip existing outputs
   - CUDA env setup
   - max source base-name length for aliasing
8. Click **Run**.

For `zotero_single_html` mode:
- If Zotero write lock is active, HTML results are queued in output pending file instead of failing the whole run.
- Use **Retry pending Zotero** button later to attach queued HTML files.

## Output

- `classic`: marker output folders like `<output_dir>/<alias_base>/<alias_base>.md`
- `llm_bundle`: collection folder `<output_dir>/<collection_name>/` with flattened markdown + images
- `zotero_single_html`: marker HTML used as intermediate, then a single-file HTML is attached to Zotero parent item as stored attachment
- Pending queue file for locked Zotero writes: `<output_dir>/_zotero_pending_attachments.json`
- Metadata JSON from Marker for each file
- Filename map CSV: `<output_dir>/_source_filename_map.csv`

## Notes

- Source PDF files are never modified.
- `zotero_single_html` writes new attachment records into local `zotero.sqlite`.
- If Zotero DB is locked for writing, close Zotero and retry Zotero mode.
- `attachments:` linked-base-dir paths are currently skipped in MVP.
- If batch mode fails due multiprocessing environment issues, app uses single-file fallback automatically.

## Project Layout

- `app.py` - local entrypoint.
- `src/zoteropdf2md/gui.py` - Tkinter GUI.
- `src/zoteropdf2md/zotero.py` - read-only Zotero DB queries.
- `src/zoteropdf2md/attachments.py` - attachment path resolution.
- `src/zoteropdf2md/staging.py` - aliasing, staging, map CSV.
- `src/zoteropdf2md/marker_runner.py` - marker process orchestration.
- `src/zoteropdf2md/pipeline.py` - end-to-end conversion pipeline.
