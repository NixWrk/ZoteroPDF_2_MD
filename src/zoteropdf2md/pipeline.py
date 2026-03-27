from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .attachments import resolve_pdf_attachments
from .marker_runner import MarkerRunner
from .models import PipelineSummary
from .paths import resolve_zotero_data_dir
from .staging import (
    DEFAULT_MAX_BASE_LEN,
    cleanup_staging_dir,
    expected_output_md_path,
    stage_resolved_pdfs,
    write_filename_map,
)
from .zotero import ZoteroRepository


@dataclass(frozen=True)
class PipelineOptions:
    zotero_data_dir: str
    collection_key: str
    include_subcollections: bool
    output_dir: str
    skip_existing: bool = True
    use_cuda: bool = True
    model_cache_dir: str | None = None
    max_base_len: int = DEFAULT_MAX_BASE_LEN
    disable_batch_multiprocessing: bool = False
    cleanup_staging: bool = True


def _build_env(options: PipelineOptions) -> dict[str, str]:
    env = os.environ.copy()
    if options.use_cuda:
        env["TORCH_DEVICE"] = "cuda"
        env["CUDA_VISIBLE_DEVICES"] = "0"

    if options.model_cache_dir:
        cache_dir = Path(options.model_cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        env["MODEL_CACHE_DIR"] = str(cache_dir)

    return env


def run_pipeline(
    options: PipelineOptions,
    runner: MarkerRunner,
    log: Callable[[str], None],
    is_cancelled: Callable[[], bool],
) -> PipelineSummary:
    zotero_dir = resolve_zotero_data_dir(options.zotero_data_dir)
    output_dir = Path(options.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    repo = ZoteroRepository(zotero_dir)
    collection = repo.get_collection_by_key(options.collection_key)
    collection_ids = repo.get_descendant_collection_ids(
        collection.collection_id,
        options.include_subcollections,
    )

    log(f"Selected collection: {collection.full_name} ({collection.key})")
    log(f"Collection scope IDs: {len(collection_ids)}")

    attachment_records = repo.get_attachment_records(collection_ids)
    log(f"Attachment records in scope: {len(attachment_records)}")

    resolved, unresolved = resolve_pdf_attachments(zotero_dir, attachment_records)
    log(f"Resolved PDF attachments: {len(resolved)}")
    if unresolved:
        log(f"Skipped/unresolved attachments: {len(unresolved)}")

    if not resolved:
        raise RuntimeError("No local PDF attachments found for selected collection.")

    if is_cancelled():
        raise RuntimeError("Cancelled before staging.")

    stage = stage_resolved_pdfs(resolved, output_dir, options.max_base_len)
    log(
        "Staging prepared: "
        f"requested_max={stage.requested_max_base_len}, "
        f"max_by_output_path={stage.max_base_len_by_output_path}, "
        f"effective_max={stage.effective_max_base_len}, "
        f"files={len(stage.staged_files)}"
    )

    filename_map_path = write_filename_map(output_dir, stage.staged_files)
    log(f"Filename map: {filename_map_path}")

    env = _build_env(options)
    if env.get("MODEL_CACHE_DIR"):
        log(f"MODEL_CACHE_DIR={env['MODEL_CACHE_DIR']}")
    if env.get("TORCH_DEVICE"):
        log(f"TORCH_DEVICE={env['TORCH_DEVICE']}")

    converted_total = 0
    failed_total = 0

    try:
        if is_cancelled():
            raise RuntimeError("Cancelled before conversion.")

        batch_result = runner.run_batch(
            input_dir=stage.staging_dir,
            output_dir=output_dir,
            skip_existing=options.skip_existing,
            disable_multiprocessing=options.disable_batch_multiprocessing,
            env=env,
            log=log,
        )
        log(f"marker batch exit_code={batch_result.exit_code}")

        pending = []
        skipped_existing = 0
        for staged_file in stage.staged_files:
            md_path = expected_output_md_path(output_dir, staged_file.alias_base_name)
            if md_path.exists():
                converted_total += 1
                continue
            if options.skip_existing and md_path.exists():
                skipped_existing += 1
                continue
            pending.append(staged_file)

        if pending:
            log(f"Fallback conversion for missing outputs: {len(pending)}")

        for staged_file in pending:
            if is_cancelled():
                raise RuntimeError("Cancelled during fallback conversion.")

            single_result = runner.run_single(
                pdf_path=staged_file.alias_pdf_path,
                output_dir=output_dir,
                env=env,
                log=log,
            )
            md_path = expected_output_md_path(output_dir, staged_file.alias_base_name)
            if single_result.exit_code == 0 and md_path.exists():
                converted_total += 1
            else:
                failed_total += 1

        # Refresh final count in case batch completed some after initial check.
        converted_total = 0
        for staged_file in stage.staged_files:
            md_path = expected_output_md_path(output_dir, staged_file.alias_base_name)
            if md_path.exists():
                converted_total += 1

        failed_total = len(stage.staged_files) - converted_total

        summary = PipelineSummary(
            collection_key=collection.key,
            collection_name=collection.full_name,
            attachments_total=len(attachment_records),
            pdfs_resolved=len(resolved),
            staged_total=len(stage.staged_files),
            converted_total=converted_total,
            skipped_existing=skipped_existing,
            failed_total=failed_total,
            output_dir=output_dir,
            filename_map_path=filename_map_path,
        )

        return summary
    finally:
        if options.cleanup_staging:
            cleanup_staging_dir(stage.staging_dir)
            log("Staging folder cleaned up.")
        else:
            log(f"Staging folder kept: {stage.staging_dir}")
