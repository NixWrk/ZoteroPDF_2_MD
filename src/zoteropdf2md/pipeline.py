from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .attachments import resolve_pdf_attachments
from .history import append_history
from .marker_runner import MarkerRunner
from .models import PipelineSummary, ResolvedAttachment
from .output_state import detect_existing_results, normalize_source_path
from .paths import resolve_zotero_data_dir
from .staging import (
    DEFAULT_MAX_BASE_LEN,
    FILENAME_MAP_NAME,
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
    selected_source_pdf_paths: list[str] | None = None
    skip_existing_source_pdf_paths: list[str] | None = None


@dataclass(frozen=True)
class PdfCandidate:
    resolved_attachment: ResolvedAttachment
    already_in_output: bool


@dataclass(frozen=True)
class PdfDiscoveryResult:
    collection_name: str
    collection_key: str
    attachments_total: int
    unresolved_total: int
    candidates: list[PdfCandidate]


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


def discover_collection_pdfs(
    zotero_data_dir: str,
    collection_key: str,
    include_subcollections: bool,
    output_dir: str,
) -> PdfDiscoveryResult:
    zotero_dir = resolve_zotero_data_dir(zotero_data_dir)
    out_dir = Path(output_dir).expanduser().resolve()

    repo = ZoteroRepository(zotero_dir)
    collection = repo.get_collection_by_key(collection_key)
    collection_ids = repo.get_descendant_collection_ids(collection.collection_id, include_subcollections)

    attachment_records = repo.get_attachment_records(collection_ids)
    resolved, unresolved = resolve_pdf_attachments(zotero_dir, attachment_records)

    existing_in_output = detect_existing_results(out_dir, [r.source_pdf_path for r in resolved])
    candidates = [
        PdfCandidate(
            resolved_attachment=r,
            already_in_output=(normalize_source_path(r.source_pdf_path) in existing_in_output),
        )
        for r in resolved
    ]

    return PdfDiscoveryResult(
        collection_name=collection.full_name,
        collection_key=collection.key,
        attachments_total=len(attachment_records),
        unresolved_total=len(unresolved),
        candidates=candidates,
    )


def run_pipeline(
    options: PipelineOptions,
    runner: MarkerRunner,
    log: Callable[[str], None],
    is_cancelled: Callable[[], bool],
) -> PipelineSummary:
    output_dir = Path(options.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    discovery = discover_collection_pdfs(
        zotero_data_dir=options.zotero_data_dir,
        collection_key=options.collection_key,
        include_subcollections=options.include_subcollections,
        output_dir=options.output_dir,
    )

    log(f"Selected collection: {discovery.collection_name} ({discovery.collection_key})")
    log(f"Attachment records in scope: {discovery.attachments_total}")
    log(f"Resolved PDF attachments: {len(discovery.candidates)}")
    if discovery.unresolved_total:
        log(f"Skipped/unresolved attachments: {discovery.unresolved_total}")

    resolved = [c.resolved_attachment for c in discovery.candidates]
    if not resolved:
        raise RuntimeError("No local PDF attachments found for selected collection.")

    if options.selected_source_pdf_paths:
        selected_norm = {
            normalize_source_path(Path(path))
            for path in options.selected_source_pdf_paths
        }
        before = len(resolved)
        resolved = [
            item for item in resolved
            if normalize_source_path(item.source_pdf_path) in selected_norm
        ]
        log(f"Selected in GUI: {len(resolved)} of {before}")

    if not resolved:
        raise RuntimeError("No PDFs selected for processing.")

    skipped_existing = 0
    existing_in_output = detect_existing_results(output_dir, [r.source_pdf_path for r in resolved])
    skip_existing_set: set[str] = set()
    if options.skip_existing_source_pdf_paths is not None:
        skip_existing_set = {
            normalize_source_path(Path(path))
            for path in options.skip_existing_source_pdf_paths
        }
        skip_existing_set &= existing_in_output
    elif options.skip_existing:
        skip_existing_set = set(existing_in_output)

    if skip_existing_set:
        before = len(resolved)
        resolved = [
            item for item in resolved
            if normalize_source_path(item.source_pdf_path) not in skip_existing_set
        ]
        skipped_existing = before - len(resolved)
        if skipped_existing:
            log(f"Already present in output folder, skipped before run: {skipped_existing}")

    if not resolved:
        filename_map_path = output_dir / FILENAME_MAP_NAME
        return PipelineSummary(
            collection_key=discovery.collection_key,
            collection_name=discovery.collection_name,
            attachments_total=discovery.attachments_total,
            pdfs_resolved=len(discovery.candidates),
            staged_total=0,
            converted_total=0,
            skipped_existing=skipped_existing,
            failed_total=0,
            output_dir=output_dir,
            filename_map_path=filename_map_path,
        )

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

    try:
        if is_cancelled():
            raise RuntimeError("Cancelled before conversion.")

        # If skip logic was already resolved per-file in GUI, don't pass --skip_existing
        # to marker, or it will skip files that user explicitly chose to reprocess.
        batch_skip_existing = options.skip_existing and options.skip_existing_source_pdf_paths is None

        batch_result = runner.run_batch(
            input_dir=stage.staging_dir,
            output_dir=output_dir,
            skip_existing=batch_skip_existing,
            disable_multiprocessing=options.disable_batch_multiprocessing,
            env=env,
            log=log,
        )
        log(f"marker batch exit_code={batch_result.exit_code}")

        pending = []
        for staged_file in stage.staged_files:
            md_path = expected_output_md_path(output_dir, staged_file.alias_base_name)
            if md_path.exists():
                converted_total += 1
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

        converted_source_paths: list[Path] = []
        for staged_file in stage.staged_files:
            md_path = expected_output_md_path(output_dir, staged_file.alias_base_name)
            if md_path.exists():
                converted_source_paths.append(staged_file.source_pdf_path)

        if converted_source_paths:
            history_path = append_history(converted_source_paths, output_dir)
            log(f"History updated: {history_path}")

        failed_total = len(stage.staged_files) - len(converted_source_paths)

        return PipelineSummary(
            collection_key=discovery.collection_key,
            collection_name=discovery.collection_name,
            attachments_total=discovery.attachments_total,
            pdfs_resolved=len(discovery.candidates),
            staged_total=len(stage.staged_files),
            converted_total=len(converted_source_paths),
            skipped_existing=skipped_existing,
            failed_total=failed_total,
            output_dir=output_dir,
            filename_map_path=filename_map_path,
        )
    finally:
        if options.cleanup_staging:
            cleanup_staging_dir(stage.staging_dir)
            log("Staging folder cleaned up.")
        else:
            log(f"Staging folder kept: {stage.staging_dir}")
