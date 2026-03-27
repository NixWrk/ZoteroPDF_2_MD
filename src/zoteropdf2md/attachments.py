from __future__ import annotations

import urllib.parse
from pathlib import Path

from .models import AttachmentRecord, ResolvedAttachment


def _looks_like_pdf(record: AttachmentRecord) -> bool:
    content_type = (record.content_type or "").lower()
    path = (record.path or "").lower()
    return (
        content_type == "application/pdf"
        or path.endswith(".pdf")
        or path.startswith("storage:")
    )


def _resolve_record_path(zotero_data_dir: Path, record: AttachmentRecord) -> Path | None:
    raw_path = (record.path or "").strip()
    if not raw_path:
        return None

    if raw_path.startswith("storage:"):
        filename = raw_path.removeprefix("storage:").strip("/\\")
        if not filename:
            return None
        return zotero_data_dir / "storage" / record.attachment_key / filename

    if raw_path.startswith("file://"):
        parsed = urllib.parse.urlparse(raw_path)
        local_path = urllib.parse.unquote(parsed.path)
        if parsed.netloc and not local_path.startswith("/"):
            local_path = f"//{parsed.netloc}/{local_path}"
        # Normalize /C:/... into C:/... for Windows
        if len(local_path) >= 3 and local_path[0] == "/" and local_path[2] == ":":
            local_path = local_path[1:]
        return Path(local_path)

    if raw_path.startswith("attachments:"):
        # Base-dir linked files require additional prefs parsing; skip for MVP.
        return None

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate

    return None


def resolve_pdf_attachments(
    zotero_data_dir: Path,
    records: list[AttachmentRecord],
) -> tuple[list[ResolvedAttachment], list[AttachmentRecord]]:
    resolved: list[ResolvedAttachment] = []
    unresolved: list[AttachmentRecord] = []

    for record in records:
        if not _looks_like_pdf(record):
            continue

        source = _resolve_record_path(zotero_data_dir, record)
        if source is None or not source.exists() or source.suffix.lower() != ".pdf":
            unresolved.append(record)
            continue

        resolved.append(
            ResolvedAttachment(
                attachment=record,
                source_pdf_path=source,
            )
        )

    return resolved, unresolved
