from __future__ import annotations

import csv
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .models import ResolvedAttachment, StagedFile
from .runtime_temp import make_temp_dir, runtime_temp_root


WINDOWS_PATH_LIMIT = 260
MIN_BASE_LEN = 16
DEFAULT_MAX_BASE_LEN = 96
FILENAME_MAP_NAME = "_source_filename_map.csv"


@dataclass(frozen=True)
class StageResult:
    staging_dir: Path
    staged_files: list[StagedFile]
    requested_max_base_len: int
    effective_max_base_len: int
    max_base_len_by_output_path: int


def get_max_base_len_for_output_dir(output_dir: Path) -> int:
    # marker writes both:
    #   <out_dir>\<base>\<base>.md
    #   <out_dir>\<base>\<base>_meta.json
    resolved_out = str(output_dir.resolve())
    return ((WINDOWS_PATH_LIMIT - 1) - len(resolved_out) - 12) // 2


def _make_short_base_name(original_base: str, max_len: int) -> str:
    if len(original_base) <= max_len:
        return original_base

    digest = hashlib.sha1(original_base.encode("utf-8")).hexdigest()[:8]
    head_len = max_len - len(digest) - 1
    if head_len < 1:
        raise ValueError("Max base length is too small for shortening.")

    shortened = f"{original_base[:head_len].rstrip(' .')}_{digest}"
    return shortened if shortened else f"doc_{digest}"


def _ensure_unique_base_name(candidate: str, used_names: set[str], max_len: int) -> str:
    key = candidate.lower()
    if key not in used_names:
        return candidate

    index = 2
    while True:
        suffix = f"_{index}"
        trimmed = candidate[: max_len - len(suffix)].rstrip(" .")
        if not trimmed:
            trimmed = "doc"
        with_suffix = f"{trimmed}{suffix}"
        key = with_suffix.lower()
        if key not in used_names:
            return with_suffix
        index += 1


def _link_or_copy(source_pdf: Path, target_pdf: Path) -> str:
    try:
        os.link(source_pdf, target_pdf)
        return "hardlink"
    except OSError:
        shutil.copy2(source_pdf, target_pdf)
        return "copy"


def stage_resolved_pdfs(
    resolved_attachments: list[ResolvedAttachment],
    output_dir: Path,
    requested_max_base_len: int,
    temp_root: Path | None = None,
) -> StageResult:
    max_by_output_dir = get_max_base_len_for_output_dir(output_dir)
    effective_max_len = min(requested_max_base_len, max_by_output_dir)

    if effective_max_len < MIN_BASE_LEN:
        raise ValueError(
            "Output path is too long for safe marker output. Use a shorter output directory."
        )

    runtime_root = temp_root if temp_root is not None else runtime_temp_root(output_dir)
    staging_dir = make_temp_dir(runtime_root, prefix="zotero_pdf_stage_")

    used_names: set[str] = set()
    staged_files: list[StagedFile] = []

    for resolved in resolved_attachments:
        source_base = resolved.source_pdf_path.stem
        alias_base = _make_short_base_name(source_base, effective_max_len)
        alias_base = _ensure_unique_base_name(alias_base, used_names, effective_max_len)
        used_names.add(alias_base.lower())

        alias_pdf_path = staging_dir / f"{alias_base}.pdf"
        materialization = _link_or_copy(resolved.source_pdf_path, alias_pdf_path)

        staged_files.append(
            StagedFile(
                source_pdf_path=resolved.source_pdf_path,
                alias_pdf_path=alias_pdf_path,
                alias_base_name=alias_base,
                source_base_len=len(source_base),
                alias_base_len=len(alias_base),
                was_shortened=alias_base != source_base,
                materialization=materialization,
            )
        )

    return StageResult(
        staging_dir=staging_dir,
        staged_files=staged_files,
        requested_max_base_len=requested_max_base_len,
        effective_max_base_len=effective_max_len,
        max_base_len_by_output_path=max_by_output_dir,
    )


def write_filename_map(output_dir: Path, staged_files: list[StagedFile]) -> Path:
    map_path = output_dir / FILENAME_MAP_NAME
    fieldnames = [
        "source_pdf_path",
        "alias_pdf_path",
        "source_base_len",
        "alias_base_len",
        "was_shortened",
        "materialization",
    ]

    merged_rows: dict[str, dict[str, str | int]] = {}
    if map_path.is_file():
        with map_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                source_path = (row.get("source_pdf_path") or "").strip()
                alias_pdf_path = (row.get("alias_pdf_path") or "").strip()
                if not source_path:
                    continue
                alias_base = Path(alias_pdf_path).stem.lower()
                key = f"{os.path.normcase(source_path)}||{alias_base}"
                merged_rows[key] = {
                    "source_pdf_path": source_path,
                    "alias_pdf_path": alias_pdf_path,
                    "source_base_len": (row.get("source_base_len") or "").strip(),
                    "alias_base_len": (row.get("alias_base_len") or "").strip(),
                    "was_shortened": (row.get("was_shortened") or "").strip(),
                    "materialization": (row.get("materialization") or "").strip(),
                }

    for staged in staged_files:
        source_path = str(staged.source_pdf_path)
        alias_base = staged.alias_base_name.lower()
        key = f"{os.path.normcase(source_path)}||{alias_base}"
        merged_rows[key] = {
            "source_pdf_path": source_path,
            "alias_pdf_path": str(staged.alias_pdf_path),
            "source_base_len": staged.source_base_len,
            "alias_base_len": staged.alias_base_len,
            "was_shortened": "yes" if staged.was_shortened else "no",
            "materialization": staged.materialization,
        }

    ordered_rows = sorted(merged_rows.values(), key=lambda row: str(row["source_pdf_path"]).lower())

    with map_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ordered_rows)

    return map_path


def cleanup_staging_dir(staging_dir: Path) -> None:
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)


def expected_output_artifact_path(output_dir: Path, alias_base_name: str, artifact_extension: str) -> Path:
    normalized_ext = artifact_extension if artifact_extension.startswith(".") else f".{artifact_extension}"
    return output_dir / alias_base_name / f"{alias_base_name}{normalized_ext.lower()}"


def expected_output_md_path(output_dir: Path, alias_base_name: str) -> Path:
    return expected_output_artifact_path(output_dir, alias_base_name, ".md")
