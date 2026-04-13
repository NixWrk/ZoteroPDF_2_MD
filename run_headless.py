"""Headless pipeline runner — processes specific Zotero collections without the GUI.

Usage:
    python run_headless.py

Outputs to  md_output/<run_name>/  next to this file.
"""
from __future__ import annotations

import sys
import io
from pathlib import Path

# Force UTF-8 output so Marker's unicode log lines don't crash on cp1251 consoles.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zoteropdf2md.marker_runner import MarkerRunner
from zoteropdf2md.paths import discover_zotero_profiles
from zoteropdf2md.pipeline import PipelineOptions, run_pipeline
from zoteropdf2md.single_file_html import polish_html_document

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROFILE_NAME = "Meine"          # Zotero profile to use
EXPORT_MODE  = "zotero_single_html"
TRANSLATE    = True
TARGET_LANG  = "ru"
SKIP_EXISTING = False           # force re-run even if output already exists

RUNS: list[dict] = [
    {
        "collection_key": "LSZKA7Z9",
        "output_subdir":  "intracranial",
        "include_subcollections": True,
    },
    {
        "collection_key": "ETB2AQMX",
        "output_subdir":  "llm_medicine",
        "include_subcollections": True,
    },
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    print(msg, flush=True)


def _repolish_output_dir(output_dir: Path) -> None:
    """Re-apply polish_html_document to every EN and RU HTML in *output_dir*.

    This guarantees that all layout fixes (equation rows, ref numbering, etc.)
    are present in the final files even if the pipeline's own polish step ran
    with an older cached version of the code.
    """
    html_files = list(output_dir.rglob("*.html"))
    if not html_files:
        return
    _log(f"\nPost-polish: {len(html_files)} HTML file(s) in {output_dir.name}")
    for p in html_files:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            polished = polish_html_document(raw)
            if polished != raw:
                p.write_text(polished, encoding="utf-8")
                _log(f"  refreshed: {p.name}")
        except Exception as exc:
            _log(f"  WARN: could not re-polish {p.name}: {exc}")


def main() -> None:
    profiles = discover_zotero_profiles()
    profile = next((p for p in profiles if p.name == PROFILE_NAME), profiles[0])
    _log(f"Using Zotero profile: {profile.name}  ({profile.zotero_data_dir})")

    runner = MarkerRunner(marker_cmd="marker", marker_single_cmd="marker_single")

    for run_cfg in RUNS:
        output_dir = ROOT / "md_output" / run_cfg["output_subdir"]
        _log(f"\n{'='*60}")
        _log(f"Collection key : {run_cfg['collection_key']}")
        _log(f"Output dir     : {output_dir}")
        _log(f"Export mode    : {EXPORT_MODE}")
        _log(f"Translate      : {TRANSLATE} -> {TARGET_LANG}")
        _log(f"{'='*60}")

        options = PipelineOptions(
            zotero_data_dir=str(profile.zotero_data_dir),
            collection_key=run_cfg["collection_key"],
            include_subcollections=run_cfg["include_subcollections"],
            output_dir=str(output_dir),
            skip_existing=SKIP_EXISTING,
            use_cuda=True,
            cuda_device_index=0,
            export_mode=EXPORT_MODE,
            translate_html_with_gemma=TRANSLATE,
            translation_target_language_code=TARGET_LANG,
            translation_source_language="Auto",
        )

        summary = run_pipeline(
            options=options,
            runner=runner,
            log=_log,
            is_cancelled=lambda: False,
        )
        _log(f"\nSummary: {summary}")
        _repolish_output_dir(output_dir)

    _log("\nAll runs complete.")


if __name__ == "__main__":
    main()
