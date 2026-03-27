from __future__ import annotations

import csv
import os
from pathlib import Path

from .staging import FILENAME_MAP_NAME


def _normalize_path_str(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _normalize_path(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _load_existing_from_filename_map(output_dir: Path) -> set[str]:
    map_path = output_dir / FILENAME_MAP_NAME
    if not map_path.is_file():
        return set()

    existing: set[str] = set()
    with map_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            source_path = (row.get("source_pdf_path") or "").strip()
            alias_pdf_path = (row.get("alias_pdf_path") or "").strip()
            if not source_path or not alias_pdf_path:
                continue

            alias_base = Path(alias_pdf_path).stem
            md_path = output_dir / alias_base / f"{alias_base}.md"
            if md_path.exists():
                existing.add(_normalize_path_str(source_path))

    return existing


def _has_legacy_output(output_dir: Path, source_pdf_path: Path) -> bool:
    base = source_pdf_path.stem
    md_path = output_dir / base / f"{base}.md"
    return md_path.exists()


def detect_existing_results(output_dir: Path, source_pdf_paths: list[Path]) -> set[str]:
    existing = _load_existing_from_filename_map(output_dir)

    for source_pdf_path in source_pdf_paths:
        if _has_legacy_output(output_dir, source_pdf_path):
            existing.add(_normalize_path(source_pdf_path))

    return existing


def is_source_already_converted(output_dir: Path, source_pdf_path: Path, existing_set: set[str] | None = None) -> bool:
    normalized = _normalize_path(source_pdf_path)
    if existing_set is None:
        existing_set = detect_existing_results(output_dir, [source_pdf_path])
    return normalized in existing_set


def normalize_source_path(source_pdf_path: Path) -> str:
    return _normalize_path(source_pdf_path)
