import shutil
from pathlib import Path
from uuid import uuid4

from zoteropdf2md.single_file_html import inline_images_from_html_file, polish_html_document


def _make_temp_dir() -> Path:
    path = Path(".tmp_local2") / f"test_single_file_html_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_inline_images_from_html_file() -> None:
    tmp_path = _make_temp_dir()
    try:
        html_path = tmp_path / "doc.html"
        image_path = tmp_path / "img.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        html_path.write_text('<html><body><img src="img.png"></body></html>', encoding="utf-8")

        result = inline_images_from_html_file(html_path)

        assert result.inlined_images == 1
        assert "data:image/png;base64," in result.html
        assert "img.png" not in result.html
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_inline_images_adds_readability_and_repairs_common_text_artifacts() -> None:
    tmp_path = _make_temp_dir()
    try:
        html_path = tmp_path / "doc.html"
        html_path.write_text(
            "<html><head><meta charset='utf-8'/></head><body><p>A&lt;sup&gt;1&lt;/sup&gt; РІР‚вЂќ Р’В©</p></body></html>",
            encoding="utf-8",
        )

        result = inline_images_from_html_file(html_path)

        assert 'data-z2m-style="readable"' in result.html
        assert '<main id="marker-doc">' in result.html
        assert "A<sup>1</sup>" in result.html
        assert "&lt;sup&gt;" not in result.html
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_polish_html_document_autolinks_plain_web_urls() -> None:
    html = (
        "<html><body>"
        "<p>See https://example.com/paper.pdf.</p>"
        "<p>Portal: www.nature.com/reprints</p>"
        "<p><a href=\"#ref-1\">[1]</a></p>"
        "<pre>https://do-not-link.example</pre>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert 'href="https://example.com/paper.pdf"' in polished
    assert 'href="https://www.nature.com/reprints"' in polished
    assert '<a href="#ref-1">[1]</a>' in polished
    assert "<pre>https://do-not-link.example</pre>" in polished


def test_polish_html_document_links_sup_citations_to_references() -> None:
    html = (
        "<html><body>"
        "<p>Finding<sup>1,2</sup> is robust.</p>"
        "<h4>References</h4>"
        "<ul><li>First ref.</li><li>Second ref.</li></ul>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert '<li id="ref-1">' in polished
    assert '<li id="ref-2">' in polished
    assert (
        '<sup><a href="#ref-1" class="z2m-ref-link">1</a>,'
        '<a href="#ref-2" class="z2m-ref-link">2</a></sup>'
    ) in polished


def test_polish_html_document_fixes_spaced_sup_and_backslash_artifacts() -> None:
    html = (
        "<html><body>"
        "<p>Fig. 2 \\ | \\ Pipeline. \\ The \\ key steps. < sup>3</ sup></p>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert " \\ | \\" not in polished
    assert "\\ The \\" not in polished
    assert "<sup>3</sup>" in polished


def test_polish_html_document_repairs_split_url_before_autolink() -> None:
    html = (
        "<html><body>"
        "<p>Preprint at https://doi.org/10.48550/ arXiv.2408.06292</p>"
        "</body></html>"
    )
    polished = polish_html_document(html)
    assert 'href="https://doi.org/10.48550/arXiv.2408.06292"' in polished


def test_polish_html_document_links_bracket_citations_to_references() -> None:
    """[N] bracket-style citations (common in IEEE papers) must become anchors."""
    html = (
        "<html><body>"
        "<p>Device performance [1], [3] and follow-up [2].</p>"
        "<h4>References</h4>"
        "<ul>"
        "<li>Ref one.</li>"
        "<li>Ref two.</li>"
        "<li>Ref three.</li>"
        "</ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert '<a href="#ref-1" class="z2m-ref-link">[1]</a>' in polished
    assert '<a href="#ref-2" class="z2m-ref-link">[2]</a>' in polished
    assert '<a href="#ref-3" class="z2m-ref-link">[3]</a>' in polished
    # References list items must carry IDs
    assert 'id="ref-1"' in polished
    assert 'id="ref-3"' in polished


def test_polish_html_document_bracket_citations_not_linked_inside_references() -> None:
    """The [N] markers inside the references list itself must NOT become double-links."""
    html = (
        "<html><body>"
        "<p>See [1] for details.</p>"
        "<h4>References</h4>"
        "<ul><li>[1] Smith et al. 2020.</li></ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    # The in-text [1] BEFORE the heading should be linked.
    assert '<a href="#ref-1" class="z2m-ref-link">[1]</a>' in polished
    # The [1] INSIDE the list item (after the heading) must NOT be wrapped again.
    # It will have a z2m-ref-num span prepended, but the [1] text itself stays plain.
    ref_section_start = polished.index("References")
    assert '<a href="#ref-1"' not in polished[ref_section_start:]
