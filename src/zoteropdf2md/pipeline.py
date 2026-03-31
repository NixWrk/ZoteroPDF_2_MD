from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable

from .attachments import resolve_pdf_attachments
from .export_modes import ExportMode, get_export_mode_spec, parse_export_mode
from .history import append_history
from .llm_bundle import LlmBundleResult, create_llm_bundle
from .marker_runner import MarkerRunner
from .models import PipelineSummary, ResolvedAttachment
from .output_state import detect_existing_results, normalize_source_path
from .paths import resolve_zotero_data_dir
from .runtime_temp import cleanup_runtime_temp_root, runtime_temp_root
from .single_file_html import drop_repeated_phrases, inline_images_from_html_file
from .staging import (
    DEFAULT_MAX_BASE_LEN,
    FILENAME_MAP_NAME,
    cleanup_staging_dir,
    expected_output_artifact_path,
    stage_resolved_pdfs,
    write_filename_map,
)
from .zotero import ZoteroRepository
from .zotero_html_attachment import attach_single_file_html, check_zotero_write_access
from .zotero_pending import (
    build_pending_entry,
    enqueue_pending_attachments,
    load_pending_attachments,
    retry_pending_attachments,
)


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
    # Comma-separated export modes, e.g. "classic" or "classic,llm_bundle".
    # Multiple modes sharing the same marker_output_format run with one Marker call.
    export_mode: str = ExportMode.CLASSIC.value

    @property
    def export_modes_list(self) -> list[ExportMode]:
        return [parse_export_mode(m.strip()) for m in self.export_mode.split(",") if m.strip()]


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


def _log_elapsed(log: Callable[[str], None] | None, stage: str, started_at: float) -> None:
    if log is None:
        return
    log(f"[timer] {stage}: {perf_counter() - started_at:.2f}s")


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
    artifact_extension: str = ".md",
    temp_root: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> PdfDiscoveryResult:
    discover_started_at = perf_counter()

    started_at = perf_counter()
    zotero_dir = resolve_zotero_data_dir(zotero_data_dir)
    out_dir = Path(output_dir).expanduser().resolve()
    _log_elapsed(log, "discover.resolve_paths", started_at)

    started_at = perf_counter()
    repo = ZoteroRepository(zotero_dir, snapshot_temp_root=temp_root)
    _log_elapsed(log, "discover.open_repository", started_at)

    started_at = perf_counter()
    collection = repo.get_collection_by_key(collection_key)
    _log_elapsed(log, "discover.get_collection", started_at)

    started_at = perf_counter()
    collection_ids = repo.get_descendant_collection_ids(collection.collection_id, include_subcollections)
    _log_elapsed(log, "discover.get_descendant_collection_ids", started_at)

    started_at = perf_counter()
    attachment_records = repo.get_attachment_records(collection_ids)
    _log_elapsed(log, "discover.get_attachment_records", started_at)

    started_at = perf_counter()
    resolved, unresolved = resolve_pdf_attachments(zotero_dir, attachment_records)
    _log_elapsed(log, "discover.resolve_pdf_attachments", started_at)

    started_at = perf_counter()
    existing_in_output = detect_existing_results(
        out_dir,
        [r.source_pdf_path for r in resolved],
        artifact_extension=artifact_extension,
    )
    _log_elapsed(log, "discover.detect_existing_results", started_at)

    candidates = [
        PdfCandidate(
            resolved_attachment=r,
            already_in_output=(normalize_source_path(r.source_pdf_path) in existing_in_output),
        )
        for r in resolved
    ]
    _log_elapsed(log, "discover.total", discover_started_at)

    return PdfDiscoveryResult(
        collection_name=collection.full_name,
        collection_key=collection.key,
        attachments_total=len(attachment_records),
        unresolved_total=len(unresolved),
        candidates=candidates,
    )


def _clean_md_repeated_phrases(md_path: Path, log: Callable[[str], None]) -> None:
    """Read an MD file, remove repeated-phrase hallucinations, write back if changed."""
    try:
        original = md_path.read_text(encoding="utf-8", errors="replace")
        cleaned = drop_repeated_phrases(original)
        if cleaned != original:
            md_path.write_text(cleaned, encoding="utf-8")
            log(f"Repetitions removed: {md_path.name} (-{len(original) - len(cleaned)} chars)")
    except Exception as exc:
        log(f"Warning: repetition cleanup failed for {md_path.name}: {exc}")


def run_pipeline(
    options: PipelineOptions,
    runner: MarkerRunner,
    log: Callable[[str], None],
    is_cancelled: Callable[[], bool],
) -> PipelineSummary:
    pipeline_started_at = perf_counter()
    output_dir = Path(options.output_dir).expanduser().resolve()
    runtime_tmp_root = runtime_temp_root(output_dir)

    # Support comma-separated multi-mode (e.g. "classic,llm_bundle").
    # All modes in one pipeline call must share the same marker_output_format.
    export_modes_list = options.export_modes_list
    primary_spec = get_export_mode_spec(export_modes_list[0])
    artifact_extension = primary_spec.artifact_extension
    marker_output_format = primary_spec.marker_output_format

    zotero_dir_for_mode: Path | None = None
    zotero_write_lock_detected = False

    try:
        started_at = perf_counter()
        output_dir.mkdir(parents=True, exist_ok=True)
        _log_elapsed(log, "pipeline.prepare_output_dir", started_at)

        started_at = perf_counter()
        discovery = discover_collection_pdfs(
            zotero_data_dir=options.zotero_data_dir,
            collection_key=options.collection_key,
            include_subcollections=options.include_subcollections,
            output_dir=options.output_dir,
            artifact_extension=artifact_extension,
            temp_root=runtime_tmp_root,
            log=log,
        )
        _log_elapsed(log, "pipeline.discover_collection_pdfs", started_at)

        log(f"Selected collection: {discovery.collection_name} ({discovery.collection_key})")
        log(f"Attachment records in scope: {discovery.attachments_total}")
        log(f"Resolved PDF attachments: {len(discovery.candidates)}")
        log(f"Export mode: {options.export_mode}")
        if discovery.unresolved_total:
            log(f"Skipped/unresolved attachments: {discovery.unresolved_total}")

        resolved = [c.resolved_attachment for c in discovery.candidates]
        if not resolved:
            raise RuntimeError("No local PDF attachments found for selected collection.")

        if options.selected_source_pdf_paths:
            started_at = perf_counter()
            selected_norm = {
                normalize_source_path(Path(path))
                for path in options.selected_source_pdf_paths
            }
            before = len(resolved)
            resolved = [
                item for item in resolved
                if normalize_source_path(item.source_pdf_path) in selected_norm
            ]
            _log_elapsed(log, "pipeline.apply_gui_selection_filter", started_at)
            log(f"Selected in GUI: {len(resolved)} of {before}")

        if not resolved:
            raise RuntimeError("No PDFs selected for processing.")

        skipped_existing = 0
        started_at = perf_counter()
        existing_in_output = detect_existing_results(
            output_dir,
            [r.source_pdf_path for r in resolved],
            artifact_extension=artifact_extension,
        )
        _log_elapsed(log, "pipeline.detect_existing_results", started_at)

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
            started_at = perf_counter()
            before = len(resolved)
            resolved = [
                item for item in resolved
                if normalize_source_path(item.source_pdf_path) not in skip_existing_set
            ]
            skipped_existing = before - len(resolved)
            _log_elapsed(log, "pipeline.apply_skip_existing", started_at)
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
                export_mode=options.export_mode,
            )

        if ExportMode.ZOTERO in export_modes_list:
            started_at = perf_counter()
            zotero_dir_for_mode = resolve_zotero_data_dir(options.zotero_data_dir)
            try:
                check_zotero_write_access(zotero_dir_for_mode)
            except RuntimeError as exc:
                if "locked for writing" in str(exc).lower():
                    zotero_write_lock_detected = True
                    log(
                        "Zotero write lock detected before conversion. "
                        "HTML results will be queued in output pending file for retry."
                    )
                else:
                    raise
            _log_elapsed(log, "pipeline.zotero_preflight_write_access", started_at)

        if is_cancelled():
            raise RuntimeError("Cancelled before staging.")

        started_at = perf_counter()
        stage = stage_resolved_pdfs(resolved, output_dir, options.max_base_len, temp_root=runtime_tmp_root)
        _log_elapsed(log, "pipeline.stage_resolved_pdfs", started_at)
        log(
            "Staging prepared: "
            f"requested_max={stage.requested_max_base_len}, "
            f"max_by_output_path={stage.max_base_len_by_output_path}, "
            f"effective_max={stage.effective_max_base_len}, "
            f"files={len(stage.staged_files)}"
        )

        started_at = perf_counter()
        filename_map_path = write_filename_map(output_dir, stage.staged_files)
        _log_elapsed(log, "pipeline.write_filename_map", started_at)
        log(f"Filename map: {filename_map_path}")

        started_at = perf_counter()
        env = _build_env(options)
        _log_elapsed(log, "pipeline.build_env", started_at)
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

            started_at = perf_counter()
            batch_result = runner.run_batch(
                input_dir=stage.staging_dir,
                output_dir=output_dir,
                skip_existing=batch_skip_existing,
                disable_multiprocessing=options.disable_batch_multiprocessing,
                output_format=marker_output_format,
                env=env,
                log=log,
            )
            _log_elapsed(log, "pipeline.marker_batch", started_at)
            log(f"marker batch exit_code={batch_result.exit_code}")

            pending = []
            for staged_file in stage.staged_files:
                artifact_path = expected_output_artifact_path(output_dir, staged_file.alias_base_name, artifact_extension)
                if artifact_path.exists():
                    converted_total += 1
                    continue
                pending.append(staged_file)

            if pending:
                log(f"Fallback conversion for missing outputs: {len(pending)}")

            fallback_started_at = perf_counter()
            for staged_file in pending:
                if is_cancelled():
                    raise RuntimeError("Cancelled during fallback conversion.")

                single_started_at = perf_counter()
                single_result = runner.run_single(
                    pdf_path=staged_file.alias_pdf_path,
                    output_dir=output_dir,
                    output_format=marker_output_format,
                    env=env,
                    log=log,
                )
                _log_elapsed(log, f"pipeline.marker_single.{staged_file.alias_pdf_path.name}", single_started_at)
                artifact_path = expected_output_artifact_path(output_dir, staged_file.alias_base_name, artifact_extension)
                if single_result.exit_code == 0 and artifact_path.exists():
                    converted_total += 1
            if pending:
                _log_elapsed(log, "pipeline.fallback_total", fallback_started_at)

            started_at = perf_counter()
            converted_staged_files = []
            converted_source_paths: list[Path] = []
            for staged_file in stage.staged_files:
                artifact_path = expected_output_artifact_path(output_dir, staged_file.alias_base_name, artifact_extension)
                if artifact_path.exists():
                    converted_staged_files.append(staged_file)
                    converted_source_paths.append(staged_file.source_pdf_path)
            _log_elapsed(log, "pipeline.collect_converted_results", started_at)

            llm_bundle_result: LlmBundleResult | None = None
            zotero_html_attached_total = 0
            zotero_html_failed_total = 0
            zotero_html_queued_total = 0
            zotero_pending_total = 0
            history_paths = list(converted_source_paths)

            # Level 3: clean repetition hallucinations from markdown outputs.
            if marker_output_format == "markdown" and converted_staged_files:
                started_at = perf_counter()
                for staged_file in converted_staged_files:
                    md_path = expected_output_artifact_path(
                        output_dir, staged_file.alias_base_name, ".md"
                    )
                    if md_path.is_file():
                        _clean_md_repeated_phrases(md_path, log)
                _log_elapsed(log, "pipeline.clean_md_repetitions", started_at)

            if converted_source_paths and ExportMode.LLM in export_modes_list:
                started_at = perf_counter()
                llm_bundle_result = create_llm_bundle(
                    output_dir=output_dir,
                    collection_name=discovery.collection_name,
                    staged_files=stage.staged_files,
                    converted_source_paths=converted_source_paths,
                )
                _log_elapsed(log, "pipeline.llm_bundle", started_at)
                log(
                    "LLM bundle created: "
                    f"{llm_bundle_result.bundle_dir} "
                    f"(md={llm_bundle_result.markdown_files}, images={llm_bundle_result.image_files})"
                )

            if converted_source_paths and ExportMode.ZOTERO in export_modes_list:
                started_at = perf_counter()
                zotero_dir = zotero_dir_for_mode or resolve_zotero_data_dir(options.zotero_data_dir)
                source_to_resolved = {normalize_source_path(r.source_pdf_path): r for r in resolved}
                history_paths = []

                def queue_entries_from(staged_items: list, error_message: str) -> None:
                    nonlocal zotero_html_queued_total, zotero_pending_total, zotero_html_failed_total
                    queue_batch = []
                    for item in staged_items:
                        source_norm = normalize_source_path(item.source_pdf_path)
                        resolved_item = source_to_resolved.get(source_norm)
                        if resolved_item is None:
                            zotero_html_failed_total += 1
                            log(f"Zotero queue skipped, source mapping missing: {item.source_pdf_path}")
                            continue
                        html_path = expected_output_artifact_path(output_dir, item.alias_base_name, ".html")
                        if not html_path.is_file():
                            zotero_html_failed_total += 1
                            log(f"Zotero queue skipped, HTML not found: {html_path}")
                            continue
                        parent_item_id = resolved_item.attachment.parent_item_id or resolved_item.attachment.item_id
                        queue_batch.append(
                            build_pending_entry(
                                source_pdf_path=item.source_pdf_path,
                                html_path=html_path,
                                parent_item_id=parent_item_id,
                                last_error=error_message,
                            )
                        )
                    if queue_batch:
                        added, total = enqueue_pending_attachments(output_dir, queue_batch)
                        zotero_html_queued_total += len(queue_batch)
                        zotero_pending_total = total
                        log(
                            "Queued pending Zotero attachments: "
                            f"queued_now={len(queue_batch)}, "
                            f"new_unique={added}, pending_total={total}"
                        )

                if zotero_write_lock_detected:
                    queue_entries_from(
                        converted_staged_files,
                        "Zotero database locked for writing during run",
                    )
                else:
                    for idx, staged_file in enumerate(converted_staged_files):
                        source_norm = normalize_source_path(staged_file.source_pdf_path)
                        resolved_item = source_to_resolved.get(source_norm)
                        if resolved_item is None:
                            zotero_html_failed_total += 1
                            log(f"Zotero attach skipped, source mapping missing: {staged_file.source_pdf_path}")
                            continue

                        html_path = expected_output_artifact_path(output_dir, staged_file.alias_base_name, ".html")
                        if not html_path.is_file():
                            zotero_html_failed_total += 1
                            log(f"Zotero attach skipped, HTML not found: {html_path}")
                            continue

                        try:
                            inline_result = inline_images_from_html_file(html_path)
                            parent_item_id = resolved_item.attachment.parent_item_id or resolved_item.attachment.item_id
                            attach_result = attach_single_file_html(
                                zotero_data_dir=zotero_dir,
                                parent_item_id=parent_item_id,
                                source_pdf_path=staged_file.source_pdf_path,
                                html_content=inline_result.html,
                            )
                            zotero_html_attached_total += 1
                            history_paths.append(staged_file.source_pdf_path)
                            log(
                                "Zotero attachment created: "
                                f"parent={attach_result.parent_item_id}, "
                                f"itemID={attach_result.item_id}, "
                                f"key={attach_result.item_key}, "
                                f"inlined_images={inline_result.inlined_images}"
                            )
                        except RuntimeError as exc:
                            if "locked for writing" in str(exc).lower():
                                queue_entries_from(
                                    converted_staged_files[idx:],
                                    str(exc),
                                )
                                break
                            zotero_html_failed_total += 1
                            log(f"Zotero attachment failed for {staged_file.source_pdf_path}: {exc}")
                        except Exception as exc:
                            zotero_html_failed_total += 1
                            log(f"Zotero attachment failed for {staged_file.source_pdf_path}: {exc}")

                _log_elapsed(log, "pipeline.zotero_attach_html", started_at)
                zotero_pending_total = len(load_pending_attachments(output_dir))

            if history_paths:
                started_at = perf_counter()
                history_path = append_history(history_paths, output_dir)
                _log_elapsed(log, "pipeline.append_history", started_at)
                log(f"History updated: {history_path}")

            marker_failed_total = len(stage.staged_files) - len(converted_source_paths)
            failed_total = marker_failed_total + zotero_html_failed_total

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
                export_mode=options.export_mode,
                llm_bundle_dir=None if llm_bundle_result is None else llm_bundle_result.bundle_dir,
                llm_bundle_markdown_files=0 if llm_bundle_result is None else llm_bundle_result.markdown_files,
                llm_bundle_image_files=0 if llm_bundle_result is None else llm_bundle_result.image_files,
                zotero_html_attached_total=zotero_html_attached_total,
                zotero_html_failed_total=zotero_html_failed_total,
                zotero_html_queued_total=zotero_html_queued_total,
                zotero_pending_total=zotero_pending_total,
            )
        finally:
            cleanup_started_at = perf_counter()
            cleanup_staging_dir(stage.staging_dir)
            log("Staging folder cleaned up.")
            _log_elapsed(log, "pipeline.cleanup_staging", cleanup_started_at)
    finally:
        cleanup_runtime_temp_root(runtime_tmp_root)
        log(f"Runtime temp cleaned: {runtime_tmp_root}")
        _log_elapsed(log, "pipeline.total", pipeline_started_at)


def retry_pending_zotero_exports(
    zotero_data_dir: str,
    output_dir: str,
    log: Callable[[str], None],
) -> None:
    summary = retry_pending_attachments(
        zotero_data_dir=zotero_data_dir,
        output_dir=output_dir,
        log=log,
    )
    log(
        "Pending retry summary: "
        f"attempted={summary.attempted}, "
        f"attached={summary.attached}, "
        f"kept_pending={summary.kept_pending}, "
        f"dropped_missing_html={summary.dropped_missing_html}, "
        f"failed_non_lock={summary.failed_non_lock}, "
        f"lock_blocked={summary.lock_blocked}"
    )
    log(f"Pending queue file: {summary.queue_path}")
