from pathlib import Path

from zoteropdf2md.llm_bundle import create_llm_bundle
from zoteropdf2md.models import StagedFile


def test_llm_bundle_flattens_markdown_and_images(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    source_pdf = tmp_path / "Source Doc.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    alias_base = "source_doc_alias"
    artifact_dir = output_dir / alias_base
    artifact_dir.mkdir()
    (artifact_dir / f"{alias_base}.md").write_text("![fig](img1.png)\n", encoding="utf-8")
    (artifact_dir / "img1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    staged = StagedFile(
        source_pdf_path=source_pdf,
        alias_pdf_path=tmp_path / "stage.pdf",
        alias_base_name=alias_base,
        source_base_len=len(source_pdf.stem),
        alias_base_len=len(alias_base),
        was_shortened=False,
        materialization="copy",
    )

    result = create_llm_bundle(
        output_dir=output_dir,
        collection_name="Parent / Child",
        staged_files=[staged],
        converted_source_paths=[source_pdf],
    )

    assert result.markdown_files == 1
    assert result.image_files == 1
    md_files = list(result.bundle_dir.glob("*.md"))
    assert len(md_files) == 1
    updated_md = md_files[0].read_text(encoding="utf-8")
    assert "![fig](img1.png)" not in updated_md
    assert ".png" in updated_md
    assert len(list(result.bundle_dir.glob("*.png"))) == 1
