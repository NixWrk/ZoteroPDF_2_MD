from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import zoteropdf2md.pipeline as pipeline_mod
from zoteropdf2md.models import AttachmentRecord, ResolvedAttachment
from zoteropdf2md.output_state import normalize_source_path


class _RunnerMustNotBeCalled:
    def run_batch(self, **kwargs):  # noqa: ANN003
        raise AssertionError("run_batch must not be called in this test")

    def run_single(self, **kwargs):  # noqa: ANN003
        raise AssertionError("run_single must not be called in this test")


def _resolved(source_pdf_path: Path) -> ResolvedAttachment:
    return ResolvedAttachment(
        attachment=AttachmentRecord(
            item_id=1,
            attachment_key="ABC12345",
            parent_item_id=100,
            link_mode=1,
            path=f"storage:{source_pdf_path.name}",
            content_type="application/pdf",
        ),
        source_pdf_path=source_pdf_path,
    )


def _local_test_dir(name: str) -> Path:
    root = Path("md_output") / "_test_tmp" / "pipeline_selection" / f"{name}_{uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def test_run_pipeline_ignores_selection_without_pdf_and_continues(
    monkeypatch,
) -> None:
    tmp_path = _local_test_dir("selection_fallback")
    output_dir = tmp_path / "out"
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    discovery = pipeline_mod.PdfDiscoveryResult(
        collection_name="Test Collection",
        collection_key="COLL1",
        attachments_total=2,
        unresolved_total=1,
        candidates=[
            pipeline_mod.PdfCandidate(
                resolved_attachment=_resolved(source_pdf),
                already_in_output=False,
            )
        ],
    )

    monkeypatch.setattr(
        pipeline_mod,
        "discover_collection_pdfs",
        lambda **kwargs: discovery,  # noqa: ARG005
    )
    monkeypatch.setattr(
        pipeline_mod,
        "detect_existing_results",
        lambda output_dir, source_pdf_paths, artifact_extension=".md": {  # noqa: ARG005
            normalize_source_path(source_pdf)
        },
    )

    logs: list[str] = []
    options = pipeline_mod.PipelineOptions(
        zotero_data_dir=str(tmp_path),
        collection_key="COLL1",
        include_subcollections=True,
        output_dir=str(output_dir),
        selected_source_pdf_paths=[str(tmp_path / "note_without_pdf.txt")],
    )

    summary = pipeline_mod.run_pipeline(
        options=options,
        runner=_RunnerMustNotBeCalled(),
        log=logs.append,
        is_cancelled=lambda: False,
    )

    assert summary.pdfs_resolved == 1
    assert summary.staged_total == 0
    assert summary.skipped_existing == 1
    assert any("Selected entries without local PDF were skipped: 1" in line for line in logs)
    assert any("Selection did not include local PDFs." in line for line in logs)


def test_run_pipeline_returns_empty_summary_when_collection_has_no_pdfs(
    monkeypatch,
) -> None:
    tmp_path = _local_test_dir("no_pdfs")
    output_dir = tmp_path / "out"

    discovery = pipeline_mod.PdfDiscoveryResult(
        collection_name="No PDF Collection",
        collection_key="COLL2",
        attachments_total=3,
        unresolved_total=2,
        candidates=[],
    )

    monkeypatch.setattr(
        pipeline_mod,
        "discover_collection_pdfs",
        lambda **kwargs: discovery,  # noqa: ARG005
    )

    logs: list[str] = []
    options = pipeline_mod.PipelineOptions(
        zotero_data_dir=str(tmp_path),
        collection_key="COLL2",
        include_subcollections=True,
        output_dir=str(output_dir),
    )

    summary = pipeline_mod.run_pipeline(
        options=options,
        runner=_RunnerMustNotBeCalled(),
        log=logs.append,
        is_cancelled=lambda: False,
    )

    assert summary.pdfs_resolved == 0
    assert summary.staged_total == 0
    assert summary.converted_total == 0
    assert summary.failed_total == 0
    assert any(
        "No local PDF attachments found for selected collection. Nothing to process."
        in line
        for line in logs
    )
