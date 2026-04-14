import shutil
from pathlib import Path
from uuid import uuid4

from zoteropdf2md.single_file_html import (
    _add_figure_anchors,
    _add_section_anchors,
    _link_figure_refs,
    _link_section_refs,
    inline_images_from_html_file,
    polish_html_document,
)


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


def test_polish_html_document_converts_block_math_to_tex_delimiters() -> None:
    r"""<math display="block"> with LaTeX content must become \[...\]."""
    html = (
        "<html><body>"
        r'<p><math display="block">Z_{1} = \frac{V_1}{I_1}</math></p>'
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert r"\[Z_{1} = \frac{V_1}{I_1}\]" in polished
    assert "<math" not in polished


def test_polish_html_document_converts_inline_math_to_tex_delimiters() -> None:
    r"""<math display="inline"> with LaTeX content must become \(...\)."""
    html = (
        "<html><body>"
        r'<p>The value <math display="inline">x^2</math> is positive.</p>'
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert r"\(x^2\)" in polished
    assert "<math" not in polished


def test_polish_html_document_positions_equation_number_right() -> None:
    """Equation numbers like (1) must be extracted into a flex-row wrapper div."""
    html = (
        '<html><body>'
        '<p block-type="Equation">\\[Z_1 = j\\omega L_1\\]\n   (1)</p>'
        '</body></html>'
    )
    polished = polish_html_document(html)

    assert 'class="z2m-equation-row"' in polished
    assert '<span class="z2m-eq-num">(1)</span>' in polished
    assert '<span class="z2m-eq-lhs">' in polished
    assert '\\[Z_1 = j\\omega L_1\\]' in polished


def test_polish_html_document_demotes_block_math_in_text_paragraph() -> None:
    """\\[...\\] inside a Marker Equation paragraph that has surrounding prose text
    must be converted to inline \\(...\\) so it does not force a line break."""
    html = (
        '<html><body>'
        '<p block-type="Equation">Fig. 4 shows \\[Z_1\\] is a function of x.</p>'
        '</body></html>'
    )
    polished = polish_html_document(html)

    assert '\\(Z_1\\)' in polished
    assert '\\[Z_1\\]' not in polished


def test_polish_html_document_wraps_existing_ref_number_in_span() -> None:
    """References already numbered 'N. Author' by Marker must get z2m-ref-num span."""
    html = (
        "<html><body>"
        "<p>See [1] for details.</p>"
        "<h4>References</h4>"
        "<ul>"
        "<li>1. Smith et al., Nature 2020.</li>"
        "<li>2. Jones et al., Science 2021.</li>"
        "</ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    ref_section = polished[polished.index("References"):]
    assert '<span class="z2m-ref-num">1.</span>' in ref_section
    assert '<span class="z2m-ref-num">2.</span>' in ref_section


def test_polish_html_document_strips_bracket_ref_prefix_from_references() -> None:
    """References that start with [N] must not produce '1. [1] Author' double-numbering."""
    html = (
        "<html><body>"
        "<p>See [1] for more.</p>"
        "<h4>References</h4>"
        "<ul>"
        "<li>[1] Smith et al., Nature 2020.</li>"
        "<li>[2] Jones et al., Science 2021.</li>"
        "</ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    # z2m-ref-num span must be present
    assert 'class="z2m-ref-num"' in polished
    # The literal "[1]" must NOT appear inside a list item after the heading
    # (it was stripped and replaced by the z2m-ref-num span).
    ref_section = polished[polished.index("References"):]
    # Check no "1. [1]" double numbering
    assert "1.</span> [1]" not in ref_section
    assert "1.</span> [2]" not in ref_section


def test_polish_html_document_recovers_bare_citations_as_sup() -> None:
    """Marker sometimes drops <sup> and glues citation numbers to text."""
    html = (
        "<html><body>"
        "<p>can mitigate these issues17,68 and potential69,70.</p>"
        "<h4>References</h4>"
        "<ul>" + "".join(f"<li>Ref {i}.</li>" for i in range(1, 71)) + "</ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert '<sup>' in polished
    assert 'href="#ref-17"' in polished
    assert 'href="#ref-68"' in polished
    assert 'href="#ref-69"' in polished
    assert 'href="#ref-70"' in polished


def test_polish_html_document_recovers_spaced_bare_citations() -> None:
    """Space-separated bare citations before punctuation must also become <sup>."""
    html = (
        "<html><body>"
        "<p>relatively understudied 80,152,166. Robust methods will be essential 81.</p>"
        "<h4>References</h4>"
        "<ul>" + "".join(f"<li>Ref {i}.</li>" for i in range(1, 171)) + "</ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert 'href="#ref-80"' in polished
    assert 'href="#ref-152"' in polished
    assert 'href="#ref-166"' in polished


def test_polish_html_document_recovers_dot_separated_citations() -> None:
    """Marker OCR artefact: dots instead of commas in citation lists."""
    html = (
        "<html><body>"
        "<p>mitigate these issues17.68. Specific education</p>"
        "<h4>References</h4>"
        "<ul>" + "".join(f"<li>Ref {i}.</li>" for i in range(1, 71)) + "</ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert 'href="#ref-17"' in polished
    assert 'href="#ref-68"' in polished


def test_polish_html_document_restores_sup_from_byte_tokens() -> None:
    """Gemma byte-token artifacts followed by citation numbers → <sup>."""
    html = (
        "<html><body>"
        "<p>кодирование<0xE2><0x82><0xA9>1,2 текст.</p>"
        "<h4>References</h4>"
        "<ul><li>Ref one.</li><li>Ref two.</li></ul>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert "<0x" not in polished
    assert '<sup>' in polished
    assert 'href="#ref-1"' in polished
    assert 'href="#ref-2"' in polished


# ---------------------------------------------------------------------------
# Section anchors and links
# ---------------------------------------------------------------------------

def test_add_section_anchors_injects_id_into_roman_headings() -> None:
    html = (
        "<html><body>"
        "<h2>II. Method</h2>"
        "<h2>III. Results</h2>"
        "</body></html>"
    )
    result, found = _add_section_anchors(html)

    assert found == {"II", "III"}
    assert 'id="section-II"' in result
    assert 'id="section-III"' in result
    assert "<h2" in result


def test_add_section_anchors_skips_heading_with_existing_id() -> None:
    html = '<h3 id="my-id">IV. Discussion</h3>'
    result, found = _add_section_anchors(html)

    assert found == {"IV"}
    assert 'id="my-id"' in result
    # Must NOT add a second id
    assert result.count('id=') == 1


def test_link_section_refs_wraps_matching_roman_refs() -> None:
    html = "<p>See Section II for details and Section III for more.</p>"
    linked = _link_section_refs(html, {"II", "III"})

    assert 'href="#section-II"' in linked
    assert 'href="#section-III"' in linked
    assert "z2m-section-link" in linked


def test_link_section_refs_skips_unknown_sections() -> None:
    html = "<p>See Section V for details.</p>"
    linked = _link_section_refs(html, {"II"})

    assert 'href=' not in linked


def test_link_section_refs_skips_inside_existing_anchor() -> None:
    html = '<p>See <a href="#x">Section II info</a>.</p>'
    linked = _link_section_refs(html, {"II"})

    # Should not nest <a> inside <a>
    assert linked.count("<a") == 1


def test_polish_html_document_adds_section_anchor_links() -> None:
    html = (
        "<html><body>"
        "<h2>II. Method</h2>"
        "<p>As described in Section II above.</p>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert 'id="section-II"' in polished
    assert 'href="#section-II"' in polished
    assert "z2m-section-link" in polished


# ---------------------------------------------------------------------------
# Figure anchors and links
# ---------------------------------------------------------------------------

def test_add_figure_anchors_injects_id_into_caption_paragraphs() -> None:
    html = (
        "<html><body>"
        "<p>Fig. 1. A diagram showing results.</p>"
        "<p>Fig. 2. Another illustration.</p>"
        "</body></html>"
    )
    result, found = _add_figure_anchors(html)

    assert found == {"1", "2"}
    assert 'id="fig-1"' in result
    assert 'id="fig-2"' in result


def test_add_figure_anchors_handles_russian_caption() -> None:
    html = "<p>Рис. 3. Схема устройства.</p>"
    result, found = _add_figure_anchors(html)

    assert "3" in found
    assert 'id="fig-3"' in result


def test_add_figure_anchors_skips_paragraph_with_existing_id() -> None:
    html = '<p id="already">Fig. 4. Caption text.</p>'
    result, found = _add_figure_anchors(html)

    assert "4" in found
    assert result.count('id=') == 1


def test_link_figure_refs_wraps_matching_refs() -> None:
    html = "<p>As shown in Fig. 1 and Fig. 2 below.</p>"
    linked = _link_figure_refs(html, {"1", "2"})

    assert 'href="#fig-1"' in linked
    assert 'href="#fig-2"' in linked
    assert "z2m-fig-link" in linked


def test_link_figure_refs_skips_caption_dots() -> None:
    """'Fig. 3.' (with trailing dot) is a caption, not an in-text ref."""
    html = '<p id="fig-3">Fig. 3. Caption text.</p>'
    linked = _link_figure_refs(html, {"3"})

    # The caption itself must not be wrapped
    assert linked.count('<a') == 0


def test_link_figure_refs_skips_unknown_figures() -> None:
    html = "<p>See Fig. 9 for details.</p>"
    linked = _link_figure_refs(html, {"1", "2"})

    assert 'href=' not in linked


def test_link_figure_refs_skips_inside_existing_anchor() -> None:
    html = '<p>See <a href="#x">Fig. 1 data</a>.</p>'
    linked = _link_figure_refs(html, {"1"})

    assert linked.count("<a") == 1


def test_polish_html_document_adds_figure_anchor_links() -> None:
    html = (
        "<html><body>"
        "<p>The circuit is shown in Fig. 1.</p>"
        "<p>Fig. 1. Circuit schematic.</p>"
        "</body></html>"
    )
    polished = polish_html_document(html)

    assert 'id="fig-1"' in polished
    assert 'href="#fig-1"' in polished
    assert "z2m-fig-link" in polished


def test_link_figure_refs_handles_fig_transliteration() -> None:
    """'Фиг. 3' (translation of 'Fig. 3' by some models) must get a link."""
    html = "<p>Как показано на Фиг. 3 в данной работе.</p>"
    linked = _link_figure_refs(html, {"3"})
    assert 'href="#fig-3"' in linked
    assert "z2m-fig-link" in linked


def test_add_figure_anchors_handles_fig_transliteration_in_caption() -> None:
    """A caption starting with 'Фиг. 3.' must get id='fig-3'."""
    html = "<p>Фиг. 3. Схема устройства.</p>"
    result, found = _add_figure_anchors(html)
    assert "3" in found
    assert 'id="fig-3"' in result


def test_link_section_refs_handles_russian_case_forms() -> None:
    """Genitive (Раздела) and locative (Разделе) case forms must create links."""
    html = "<p>Описано в Разделе II и результаты Раздела III представлены ниже.</p>"
    linked = _link_section_refs(html, {"II", "III"})
    assert 'href="#section-II"' in linked
    assert 'href="#section-III"' in linked
