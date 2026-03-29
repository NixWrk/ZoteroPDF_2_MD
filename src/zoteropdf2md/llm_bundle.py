from __future__ import annotations

import os
import re
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .models import StagedFile
from .naming import make_unique_filename, sanitize_filename_component, shorten_filename_component


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff"}
_MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass(frozen=True)
class LlmBundleResult:
    bundle_dir: Path
    markdown_files: int
    image_files: int


def _normalize_path(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _safe_collection_dir_name(collection_name: str) -> str:
    safe = sanitize_filename_component(collection_name, fallback="collection")
    return shorten_filename_component(safe, 120)


def _is_external_ref(target: str) -> bool:
    lowered = target.lower()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("data:")
        or lowered.startswith("mailto:")
        or lowered.startswith("#")
    )


def _extract_target_path(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target:
        return ""
    return target.split()[0]


def create_llm_bundle(
    output_dir: Path,
    collection_name: str,
    staged_files: list[StagedFile],
    converted_source_paths: list[Path],
) -> LlmBundleResult:
    bundle_dir = output_dir / _safe_collection_dir_name(collection_name)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    converted_norms = {_normalize_path(path) for path in converted_source_paths}
    used_names: set[str] = set()
    copied_asset_names: dict[str, str] = {}

    md_count = 0
    image_count = 0

    for staged in staged_files:
        source_norm = _normalize_path(staged.source_pdf_path)
        if source_norm not in converted_norms:
            continue

        md_src = output_dir / staged.alias_base_name / f"{staged.alias_base_name}.md"
        if not md_src.is_file():
            continue

        md_filename = make_unique_filename(staged.source_pdf_path.stem, ".md", used_names)
        md_target = bundle_dir / md_filename

        text = md_src.read_text(encoding="utf-8", errors="replace")

        def replace_image(match: re.Match[str]) -> str:
            nonlocal image_count
            alt = match.group(1)
            raw_target = match.group(2)
            target_path = _extract_target_path(raw_target)
            if not target_path or _is_external_ref(target_path):
                return match.group(0)

            source_asset = (md_src.parent / urllib.parse.unquote(target_path)).resolve(strict=False)
            if not source_asset.is_file():
                return match.group(0)

            if source_asset.suffix.lower() not in _IMAGE_SUFFIXES:
                return match.group(0)

            source_key = _normalize_path(source_asset)
            existing_name = copied_asset_names.get(source_key)
            if existing_name is None:
                stem = f"{Path(md_filename).stem}__{source_asset.stem}"
                existing_name = make_unique_filename(stem, source_asset.suffix.lower(), used_names)
                shutil.copy2(source_asset, bundle_dir / existing_name)
                copied_asset_names[source_key] = existing_name
                image_count += 1

            return f"![{alt}]({existing_name})"

        updated = _MARKDOWN_IMAGE_PATTERN.sub(replace_image, text)
        md_target.write_text(updated, encoding="utf-8")
        md_count += 1

    return LlmBundleResult(bundle_dir=bundle_dir, markdown_files=md_count, image_files=image_count)

