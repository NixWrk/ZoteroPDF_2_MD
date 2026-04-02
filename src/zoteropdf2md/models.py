from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Collection:
    collection_id: int
    key: str
    name: str
    parent_collection_id: int | None
    full_name: str


@dataclass(frozen=True)
class AttachmentRecord:
    item_id: int
    attachment_key: str
    parent_item_id: int | None
    link_mode: int | None
    path: str | None
    content_type: str | None


@dataclass(frozen=True)
class ResolvedAttachment:
    attachment: AttachmentRecord
    source_pdf_path: Path


@dataclass(frozen=True)
class StagedFile:
    source_pdf_path: Path
    alias_pdf_path: Path
    alias_base_name: str
    source_base_len: int
    alias_base_len: int
    was_shortened: bool
    materialization: str


@dataclass(frozen=True)
class PipelineSummary:
    collection_key: str
    collection_name: str
    attachments_total: int
    pdfs_resolved: int
    staged_total: int
    converted_total: int
    skipped_existing: int
    failed_total: int
    output_dir: Path
    filename_map_path: Path
    export_mode: str = "classic"
    llm_bundle_dir: Path | None = None
    llm_bundle_markdown_files: int = 0
    llm_bundle_image_files: int = 0
    zotero_html_attached_total: int = 0
    zotero_html_failed_total: int = 0
    zotero_html_queued_total: int = 0
    zotero_pending_total: int = 0
    translated_html_total: int = 0
    translated_html_failed_total: int = 0
    translated_html_language_code: str = ""
    translated_html_language_name: str = ""
