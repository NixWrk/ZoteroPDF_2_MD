# ZoteroPDF_2_MD

GUI app to convert PDF attachments from a local Zotero collection into Markdown using Marker.

## What It Does

1. Opens local Zotero data (`zotero.sqlite` + `storage`) without API keys.
2. Lets you choose a collection in GUI.
3. Resolves local PDF attachments from that collection (optionally including subcollections).
4. Stages files with deterministic short aliases for Windows path safety.
5. Runs `marker` batch conversion and falls back to `marker_single` for missing outputs.
6. Writes a filename map CSV for traceability.

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

1. Set **Zotero data folder** (profile `.../zotero` directory).
2. Click **Load collections**.
3. Select a collection.
4. Set **Output folder**.
5. Optional settings:
   - include subcollections
   - skip existing outputs
   - CUDA env setup
   - max source base-name length for aliasing
6. Click **Run**.

## Output

- Markdown output folders: `<output_dir>/<alias_base>/<alias_base>.md`
- Metadata JSON from Marker for each file.
- Filename map CSV: `<output_dir>/_source_filename_map.csv`

## Notes

- Source Zotero files are never modified.
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