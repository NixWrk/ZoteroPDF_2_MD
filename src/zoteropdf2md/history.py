from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .output_state import normalize_source_path


@dataclass(frozen=True)
class HistoryRecord:
    source_pdf_path: str
    output_dir: str
    processed_at_utc: str


def get_history_file_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        base_dir = Path(appdata) / "ZoteroPDF_2_MD"
    else:
        base_dir = Path.home() / ".zoteropdf2md"

    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "processing_history.csv"


def load_history() -> list[HistoryRecord]:
    history_path = get_history_file_path()
    if not history_path.is_file():
        return []

    out: list[HistoryRecord] = []
    with history_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            source = (row.get("source_pdf_path") or "").strip()
            output_dir = (row.get("output_dir") or "").strip()
            processed = (row.get("processed_at_utc") or "").strip()
            if not source or not output_dir or not processed:
                continue
            out.append(
                HistoryRecord(
                    source_pdf_path=source,
                    output_dir=output_dir,
                    processed_at_utc=processed,
                )
            )
    return out


def append_history(source_pdf_paths: list[Path], output_dir: Path) -> Path:
    history_path = get_history_file_path()
    fieldnames = ["source_pdf_path", "output_dir", "processed_at_utc"]

    exists = history_path.is_file()
    now_utc = datetime.now(timezone.utc).isoformat()

    with history_path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()

        for source_pdf_path in source_pdf_paths:
            writer.writerow(
                {
                    "source_pdf_path": str(source_pdf_path),
                    "output_dir": str(output_dir),
                    "processed_at_utc": now_utc,
                }
            )

    return history_path


def find_processed_elsewhere(source_pdf_paths: list[Path], current_output_dir: Path) -> dict[str, HistoryRecord]:
    current_output_norm = os.path.normcase(str(current_output_dir.resolve()))
    target_norms = {normalize_source_path(path): path for path in source_pdf_paths}

    latest_by_source: dict[str, HistoryRecord] = {}

    for record in load_history():
        source_norm = normalize_source_path(Path(record.source_pdf_path))
        if source_norm not in target_norms:
            continue

        record_output_norm = os.path.normcase(str(Path(record.output_dir).expanduser().resolve(strict=False)))
        if record_output_norm == current_output_norm:
            continue

        prev = latest_by_source.get(source_norm)
        if prev is None or record.processed_at_utc > prev.processed_at_utc:
            latest_by_source[source_norm] = record

    return latest_by_source
