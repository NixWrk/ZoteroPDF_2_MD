from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path

from .staging import FILENAME_MAP_NAME


def _normalize_path_str(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _normalize_path(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _source_hash_suffix(source_pdf_path: Path) -> str:
    digest = hashlib.sha1(source_pdf_path.stem.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"_{digest}".lower()


def _build_output_md_index(output_dir: Path) -> set[str]:
    names: set[str] = set()
    if not output_dir.is_dir():
        return names

    for child in output_dir.iterdir():
        if not child.is_dir():
            continue
        md_path = child / f"{child.name}.md"
        if md_path.is_file():
            names.add(child.name.lower())
    return names


def _load_existing_from_filename_map(output_dir: Path, output_md_dirs: set[str]) -> set[str]:
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

            alias_base = Path(alias_pdf_path).stem.lower()
            if alias_base in output_md_dirs:
                existing.add(_normalize_path_str(source_path))

    return existing


def _has_exact_legacy_output(output_md_dirs: set[str], source_pdf_path: Path) -> bool:
    return source_pdf_path.stem.lower() in output_md_dirs


def _has_hash_alias_output(output_md_dirs: set[str], source_pdf_path: Path) -> bool:
    suffix = _source_hash_suffix(source_pdf_path)
    for dirname in output_md_dirs:
        if dirname.endswith(suffix):
            return True
    return False


def detect_existing_results(output_dir: Path, source_pdf_paths: list[Path]) -> set[str]:
    output_md_dirs = _build_output_md_index(output_dir)

    existing = _load_existing_from_filename_map(output_dir, output_md_dirs)

    for source_pdf_path in source_pdf_paths:
        normalized = _normalize_path(source_pdf_path)
        if normalized in existing:
            continue

        if _has_exact_legacy_output(output_md_dirs, source_pdf_path):
            existing.add(normalized)
            continue

        if _has_hash_alias_output(output_md_dirs, source_pdf_path):
            existing.add(normalized)

    return existing


def is_source_already_converted(output_dir: Path, source_pdf_path: Path, existing_set: set[str] | None = None) -> bool:
    normalized = _normalize_path(source_pdf_path)
    if existing_set is None:
        existing_set = detect_existing_results(output_dir, [source_pdf_path])
    return normalized in existing_set


def normalize_source_path(source_pdf_path: Path) -> str:
    return _normalize_path(source_pdf_path)
