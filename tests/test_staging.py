from pathlib import Path

from zoteropdf2md.models import AttachmentRecord, ResolvedAttachment
from zoteropdf2md.staging import get_max_base_len_for_output_dir, stage_resolved_pdfs


def test_max_base_len_is_positive_for_short_output_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    assert get_max_base_len_for_output_dir(output_dir) > 0


def test_stage_resolved_pdfs_uses_runtime_temp_root(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    temp_root = tmp_path / "runtime_temp"
    temp_root.mkdir()

    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    resolved = [
        ResolvedAttachment(
            attachment=AttachmentRecord(
                item_id=1,
                attachment_key="ABC123",
                parent_item_id=None,
                link_mode=1,
                path=str(pdf_path),
                content_type="application/pdf",
            ),
            source_pdf_path=pdf_path,
        )
    ]

    stage = stage_resolved_pdfs(
        resolved_attachments=resolved,
        output_dir=output_dir,
        requested_max_base_len=96,
        temp_root=temp_root,
    )

    assert stage.staging_dir.parent == temp_root
