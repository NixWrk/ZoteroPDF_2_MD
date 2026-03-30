from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .history import append_history
from .paths import resolve_zotero_data_dir
from .single_file_html import inline_images_from_html_file
from .zotero_html_attachment import attach_single_file_html, check_zotero_write_access


PENDING_ATTACHMENTS_FILE = "_zotero_pending_attachments.json"


@dataclass(frozen=True)
class PendingZoteroAttachment:
    source_pdf_path: str
    html_path: str
    parent_item_id: int
    queued_at_utc: str
    last_error: str | None = None


@dataclass(frozen=True)
class PendingRetrySummary:
    attempted: int
    attached: int
    kept_pending: int
    dropped_missing_html: int
    failed_non_lock: int
    lock_blocked: bool
    queue_path: Path


def _norm_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _queue_path(output_dir: Path) -> Path:
    return output_dir / PENDING_ATTACHMENTS_FILE


def load_pending_attachments(output_dir: Path) -> list[PendingZoteroAttachment]:
    path = _queue_path(output_dir)
    if not path.is_file():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if not isinstance(raw, list):
        return []

    out: list[PendingZoteroAttachment] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source_pdf_path", "")).strip()
        html_path = str(row.get("html_path", "")).strip()
        queued_at = str(row.get("queued_at_utc", "")).strip()
        if not source or not html_path or not queued_at:
            continue
        try:
            parent_item_id = int(row.get("parent_item_id"))
        except Exception:
            continue
        last_error = row.get("last_error")
        out.append(
            PendingZoteroAttachment(
                source_pdf_path=source,
                html_path=html_path,
                parent_item_id=parent_item_id,
                queued_at_utc=queued_at,
                last_error=None if last_error is None else str(last_error),
            )
        )
    return out


def save_pending_attachments(output_dir: Path, entries: list[PendingZoteroAttachment]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _queue_path(output_dir)
    if not entries:
        if path.exists():
            path.unlink()
        return path

    payload = [
        {
            "source_pdf_path": e.source_pdf_path,
            "html_path": e.html_path,
            "parent_item_id": e.parent_item_id,
            "queued_at_utc": e.queued_at_utc,
            "last_error": e.last_error,
        }
        for e in entries
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def enqueue_pending_attachments(output_dir: Path, entries: list[PendingZoteroAttachment]) -> tuple[int, int]:
    existing = load_pending_attachments(output_dir)
    merged: dict[str, PendingZoteroAttachment] = {}

    for row in existing:
        key = f"{_norm_path(row.source_pdf_path)}||{_norm_path(row.html_path)}||{row.parent_item_id}"
        merged[key] = row

    added = 0
    for row in entries:
        key = f"{_norm_path(row.source_pdf_path)}||{_norm_path(row.html_path)}||{row.parent_item_id}"
        if key not in merged:
            merged[key] = row
            added += 1
        else:
            prev = merged[key]
            # Keep the earliest queued_at, preserve latest non-empty error.
            earliest = prev.queued_at_utc if prev.queued_at_utc <= row.queued_at_utc else row.queued_at_utc
            merged[key] = PendingZoteroAttachment(
                source_pdf_path=prev.source_pdf_path,
                html_path=prev.html_path,
                parent_item_id=prev.parent_item_id,
                queued_at_utc=earliest,
                last_error=row.last_error or prev.last_error,
            )

    ordered = sorted(
        merged.values(),
        key=lambda e: (e.queued_at_utc, _norm_path(e.source_pdf_path), _norm_path(e.html_path)),
    )
    save_pending_attachments(output_dir, ordered)
    return added, len(ordered)


def build_pending_entry(source_pdf_path: Path, html_path: Path, parent_item_id: int, last_error: str | None = None) -> PendingZoteroAttachment:
    return PendingZoteroAttachment(
        source_pdf_path=str(source_pdf_path),
        html_path=str(html_path),
        parent_item_id=parent_item_id,
        queued_at_utc=datetime.now(timezone.utc).isoformat(),
        last_error=last_error,
    )


def retry_pending_attachments(
    zotero_data_dir: str | Path,
    output_dir: str | Path,
    log: Callable[[str], None],
) -> PendingRetrySummary:
    out_dir = Path(output_dir).expanduser().resolve(strict=False)
    queue_path = _queue_path(out_dir)
    pending = load_pending_attachments(out_dir)
    if not pending:
        return PendingRetrySummary(
            attempted=0,
            attached=0,
            kept_pending=0,
            dropped_missing_html=0,
            failed_non_lock=0,
            lock_blocked=False,
            queue_path=queue_path,
        )

    zotero_dir = resolve_zotero_data_dir(zotero_data_dir)
    lock_blocked = False
    try:
        check_zotero_write_access(zotero_dir)
    except RuntimeError:
        lock_blocked = True
        return PendingRetrySummary(
            attempted=0,
            attached=0,
            kept_pending=len(pending),
            dropped_missing_html=0,
            failed_non_lock=0,
            lock_blocked=True,
            queue_path=queue_path,
        )

    remaining: list[PendingZoteroAttachment] = []
    attached_source_paths: list[Path] = []
    attempted = 0
    attached = 0
    dropped_missing_html = 0
    failed_non_lock = 0

    for idx, row in enumerate(pending):
        html_path = Path(row.html_path).expanduser().resolve(strict=False)
        source_pdf_path = Path(row.source_pdf_path).expanduser().resolve(strict=False)
        if not html_path.is_file():
            dropped_missing_html += 1
            log(f"Pending dropped (HTML missing): {html_path}")
            continue

        attempted += 1
        try:
            inline_result = inline_images_from_html_file(html_path)
            attach_result = attach_single_file_html(
                zotero_data_dir=zotero_dir,
                parent_item_id=row.parent_item_id,
                source_pdf_path=source_pdf_path,
                html_content=inline_result.html,
            )
            attached += 1
            attached_source_paths.append(source_pdf_path)
            log(
                "Pending attached: "
                f"parent={attach_result.parent_item_id}, "
                f"itemID={attach_result.item_id}, key={attach_result.item_key}, "
                f"inlined_images={inline_result.inlined_images}"
            )
        except RuntimeError as exc:
            if "locked for writing" in str(exc).lower():
                lock_blocked = True
                remaining.append(
                    PendingZoteroAttachment(
                        source_pdf_path=row.source_pdf_path,
                        html_path=row.html_path,
                        parent_item_id=row.parent_item_id,
                        queued_at_utc=row.queued_at_utc,
                        last_error=str(exc),
                    )
                )
                remaining.extend(pending[idx + 1 :])
                break
            failed_non_lock += 1
            log(f"Pending attach failed: {source_pdf_path}: {exc}")
            remaining.append(
                PendingZoteroAttachment(
                    source_pdf_path=row.source_pdf_path,
                    html_path=row.html_path,
                    parent_item_id=row.parent_item_id,
                    queued_at_utc=row.queued_at_utc,
                    last_error=str(exc),
                )
            )
        except Exception as exc:
            failed_non_lock += 1
            log(f"Pending attach failed: {source_pdf_path}: {exc}")
            remaining.append(
                PendingZoteroAttachment(
                    source_pdf_path=row.source_pdf_path,
                    html_path=row.html_path,
                    parent_item_id=row.parent_item_id,
                    queued_at_utc=row.queued_at_utc,
                    last_error=str(exc),
                )
            )

    save_pending_attachments(out_dir, remaining)

    if attached_source_paths:
        history_path = append_history(attached_source_paths, out_dir)
        log(f"History updated: {history_path}")

    return PendingRetrySummary(
        attempted=attempted,
        attached=attached,
        kept_pending=len(remaining),
        dropped_missing_html=dropped_missing_html,
        failed_non_lock=failed_non_lock,
        lock_blocked=lock_blocked,
        queue_path=queue_path,
    )

