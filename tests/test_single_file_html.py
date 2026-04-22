import shutil
from pathlib import Path
from uuid import uuid4

from zoteropdf2md.single_file_html import (
    _add_figure_anchors,
    _add_section_anchors,
    _fix_orphaned_sup_tags,
    _fix_subscript_equation_spill,
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


def test_polish_html_document_inserts_space_after_z2m_links() -> None:
    html = (
        "<html><body>"
        "<p>См. <a href=\"#ref-30\" class=\"z2m-ref-link\">[30]</a>Мы применили фильтр.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)
    assert '</a> Мы применили фильтр.' in polished


def test_polish_html_document_fixes_lc_sensor_heading_artifact() -> None:
    html = (
        "<html><body>"
        "<h1>Новая схема для пассивной беспроводной системы <i>LC</i> датчик</h1>"
        "</body></html>"
    )

    polished = polish_html_document(html)
    assert "<i>LC</i>-датчика" in polished


def test_polish_html_document_normalizes_table_caption_style() -> None:
    html = (
        "<html><body>"
        "<p>TABLE I PARAMETERS FOR TWO ANTENNAS</p>"
        "<p>TABLE II. PARAMETERS OF SENSOR</p>"
        "<p>Таблица III параметры антенны</p>"
        "<p>Таблица IV: COMPARISON OF STATE OF ARTS.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "<p>Таблица I. Parameters for two antennas.</p>" in polished
    assert "<p>Таблица II. Parameters of sensor.</p>" in polished
    assert "<p>Таблица III. Параметры антенны.</p>" in polished
    assert "<p>Таблица IV. Comparison of state of arts.</p>" in polished


def test_polish_html_document_repairs_sentence_split_by_figure_block() -> None:
    html = (
        "<html><body>"
        "<p>The amplitude value is sent back to the microcontroller for signal processing</p>"
        "<figure><img src=\"f8.png\"/></figure>"
        "<p>at the same time, it is also sent to a NI DAQ.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert (
        "The amplitude value is sent back to the microcontroller for signal processing "
        "at the same time, it is also sent to a NI DAQ."
    ) in polished
    assert polished.count("at the same time, it is also sent to a NI DAQ.") == 1
    assert "<figure><img src=\"f8.png\"/></figure>" in polished


def test_polish_html_document_repairs_sentence_split_by_image_paragraph() -> None:
    html = (
        "<html><body>"
        "<p>The sensor was positioned close to the antenna</p>"
        "<p><img src=\"fig9.png\"/></p>"
        "<p>At the same time, the signal was monitored continuously.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert (
        "The sensor was positioned close to the antenna "
        "At the same time, the signal was monitored continuously."
    ) in polished
    assert polished.count("At the same time, the signal was monitored continuously.") == 1
    assert "<p><img src=\"fig9.png\"/></p>" in polished


def test_polish_html_document_repairs_sentence_split_by_caption_paragraph() -> None:
    html = (
        "<html><body>"
        "<p>The first network is trained by the second to generate synthetic images that cannot be distinguished from real ones, enabling the production of</p>"
        "<p>Fig. 1 | Overview of the GAI development pipeline.</p>"
        "<p>highly detailed, realistic images.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert (
        "The first network is trained by the second to generate synthetic images that cannot be distinguished from real ones, enabling the production of "
        "highly detailed, realistic images."
    ) in polished
    assert polished.count("highly detailed, realistic images.") == 1
    assert "<p>Fig. 1 | Overview of the GAI development pipeline.</p>" in polished


def test_polish_html_document_repairs_sentence_split_by_image_and_caption_paragraphs() -> None:
    html = (
        "<html><body>"
        "<p>Also, this is an analog</p>"
        "<p><img src=\"fig1.jpg\"/></p>"
        "<p id=\"fig-1\">Fig. 1. The passive sensor model.</p>"
        "<p>circuit with limited frequency resolution.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "Also, this is an analog circuit with limited frequency resolution." in polished
    assert polished.count("circuit with limited frequency resolution.") == 1
    assert "<p><img src=\"fig1.jpg\"/></p>" in polished
    assert "<p id=\"fig-1\">Fig. 1. The passive sensor model.</p>" in polished


def test_polish_html_document_repairs_sentence_split_by_long_figure_chain() -> None:
    html = (
        "<html><body>"
        "<p>This</p>"
        "<p><img src=\"f4.jpg\"/></p>"
        "<p id=\"fig-4\">Fig. 4. Passive sensor model.</p>"
        "<p><img src=\"f5.jpg\"/></p>"
        "<p id=\"fig-5\">Fig. 5. Impedance and phase frequency response.</p>"
        "<p><img src=\"f6.jpg\"/></p>"
        "<p id=\"fig-6\">Fig. 6. Measurement principle.</p>"
        "<p>equivalent resistor changes the system's impedance.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "This equivalent resistor changes the system's impedance." in polished
    assert polished.count("equivalent resistor changes the system's impedance.") == 1
    assert "<p id=\"fig-6\">Fig. 6. Measurement principle.</p>" in polished


def test_polish_html_document_repairs_sentence_split_with_table_caption_gap() -> None:
    html = (
        "<html><body>"
        "<p>In our final prototype,</p>"
        "<p><img src=\"fig9.jpg\"/></p>"
        "<p id=\"fig-9\">Fig. 9. Final sensor mounted on the PCB.</p>"
        "<p>Table I. Parameters for two antennas.</p>"
        "<p>an integrated half-wave rectifier measures the output envelope.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert (
        "In our final prototype, an integrated half-wave rectifier measures the output envelope."
    ) in polished
    assert polished.count("an integrated half-wave rectifier measures the output envelope.") == 1
    assert "<p>Таблица I. Parameters for two antennas.</p>" in polished


def test_polish_html_document_repairs_sentence_split_across_table_and_formula_note() -> None:
    html = (
        "<html><body>"
        "<p>Fig. 16 shows the k factor for two antenna with distance varying based on</p>"
        "<h4>TABLE III Antenna Parameters</h4>"
        "<table><tbody><tr><td>Parameter</td><td>Value</td></tr></tbody></table>"
        "<p>\\(f_{brain}\\) is the function which describes localized tissue properties.</p>"
        "<p>sizes. A small antenna features higher k factor at close distance.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert (
        "Fig. 16 shows the k factor for two antenna with distance varying based on "
        "sizes. A small antenna features higher k factor at close distance."
    ) in polished
    assert "\\(f_{brain}\\) is the function which describes localized tissue properties." in polished
    assert "<h4>TABLE III Antenna Parameters</h4>" in polished
    assert "<table><tbody><tr><td>Parameter</td><td>Value</td></tr></tbody></table>" in polished


def test_polish_html_document_repairs_sentence_split_when_right_starts_with_comma() -> None:
    html = (
        "<html><body>"
        "<p>distance varying based on</p>"
        "<p><img src=\"fig16.jpg\"/></p>"
        "<p id=\"fig-16\">Fig. 16. Signal strength vs distance.</p>"
        "<p>, given the antennas' and sensor's sizes.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "distance varying based on, given the antennas' and sensor's sizes." in polished
    assert "based on , given" not in polished


def test_polish_html_document_repairs_sentence_split_when_right_starts_uppercase() -> None:
    html = (
        "<html><body>"
        "<p>To facilitate observation, data is sampled by the Usb-6009 Data</p>"
        "<p><img src=\"fig10.jpg\"/></p>"
        "<p id=\"fig-10\">Fig. 10. Measurement setup.</p>"
        "<p>Acquisition Card (National Instruments).</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert (
        "To facilitate observation, data is sampled by the Usb-6009 Data "
        "Acquisition Card (National Instruments)."
    ) in polished
    assert polished.count("Acquisition Card (National Instruments).") == 1


def test_polish_html_document_repairs_sentence_split_with_dehyphenation() -> None:
    html = (
        "<html><body>"
        "<p>SPI bytes times 34 bytes per regis-</p>"
        "<p><img src=\"fig12.jpg\"/></p>"
        "<p id=\"fig-12\">Fig. 12. Resonant frequency shift.</p>"
        "<p>ter (32 bytes data per register) times 6 registers.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "SPI bytes times 34 bytes per register (32 bytes data per register) times 6 registers." in polished
    assert "regis-ter" not in polished


def test_polish_html_document_does_not_merge_after_finished_sentence() -> None:
    html = (
        "<html><body>"
        "<p>The amplitude value is sent back to the microcontroller for signal processing.</p>"
        "<figure><img src=\"f8.png\"/></figure>"
        "<p>The system then records data in a text file.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "<p>The amplitude value is sent back to the microcontroller for signal processing.</p>" in polished
    assert "<p>The system then records data in a text file.</p>" in polished


def test_polish_html_document_does_not_merge_across_regular_middle_paragraph() -> None:
    html = (
        "<html><body>"
        "<p>The first network is trained by the second to generate synthetic images</p>"
        "<p>This is just a normal paragraph, not a figure caption.</p>"
        "<p>that cannot be distinguished from real ones.</p>"
        "</body></html>"
    )

    polished = polish_html_document(html)

    assert "<p>The first network is trained by the second to generate synthetic images</p>" in polished
    assert "<p>that cannot be distinguished from real ones.</p>" in polished


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


# ---------------------------------------------------------------------------
# Subscript / superscript equation-spill fix
# ---------------------------------------------------------------------------

def test_fix_subscript_equation_spill_moves_frac_outside_subscript() -> None:
    r"""\\gamma_{ij=\\frac{a}{b}} → \\gamma_{ij}=\\frac{a}{b}"""
    latex = r"\gamma_{ij=\frac{2a_ib_j}{a_i^2+b_j^2}}"
    result = _fix_subscript_equation_spill(latex)
    assert result == r"\gamma_{ij}=\frac{2a_ib_j}{a_i^2+b_j^2}"


def test_fix_subscript_equation_spill_handles_nested_braces() -> None:
    r"""Nested fraction braces inside the spill must be preserved."""
    latex = r"\alpha_{n=\frac{x+1}{y-1}}"
    result = _fix_subscript_equation_spill(latex)
    assert result == r"\alpha_{n}=\frac{x+1}{y-1}"


def test_fix_subscript_equation_spill_leaves_normal_subscripts_untouched() -> None:
    r"""\\sum_{i=1}^{n} must not be modified (= is a summation limit, not a spill)."""
    latex = r"\sum_{i=1}^{n} x_i"
    result = _fix_subscript_equation_spill(latex)
    assert result == latex


def test_fix_subscript_equation_spill_handles_superscript() -> None:
    r"""Same fix applies to ^{…=\\frac{…}}."""
    latex = r"\phi^{k=\frac{a}{b}}"
    result = _fix_subscript_equation_spill(latex)
    assert result == r"\phi^{k}=\frac{a}{b}"


def test_fix_subscript_equation_spill_leaves_html_unchanged_when_no_match() -> None:
    html = "<p>Normal text with no LaTeX.</p>"
    assert _fix_subscript_equation_spill(html) == html


def test_polish_html_document_fixes_subscript_equation_spill() -> None:
    r"""End-to-end: spill inside an equation paragraph is repaired."""
    html = (
        "<html><body>"
        r'<p block-type="Equation">\[\gamma_{ij=\frac{2a_ib_j}{a_i^2+b_j^2}}\]</p>'
        "</body></html>"
    )
    polished = polish_html_document(html)
    assert r"\gamma_{ij}=\frac" in polished
    assert r"\gamma_{ij=\frac" not in polished


# ---------------------------------------------------------------------------
# _fix_orphaned_sup_tags
# ---------------------------------------------------------------------------

def test_fix_orphaned_sup_simple() -> None:
    """<sup>. text</sup> with long content is unwrapped."""
    html = "<p>understudied <sup>. However, researchers are applying foundation models to tasks that could improve healthcare quality.</sup> More.</p>"
    result = _fix_orphaned_sup_tags(html)
    assert "<sup>." not in result
    assert "However, researchers" in result
    # The wrapping sup is removed
    assert result.count("<sup>") == 0


def test_fix_orphaned_sup_preserves_inner_citation() -> None:
    """Inner <sup><a>N</a></sup> citation survives unwrapping of broken outer sup."""
    html = (
        '<p>studied <sup>. Researchers apply models '
        '<sup><a href="#ref-5" class="z2m-ref-link">5</a></sup>'
        ' to many tasks.</sup> Next sentence.</p>'
    )
    result = _fix_orphaned_sup_tags(html)
    assert "<sup>." not in result
    # Inner citation sup is preserved
    assert 'href="#ref-5"' in result
    assert "<sup>" in result  # inner citation sup still present
    assert "Researchers apply models" in result


def test_fix_orphaned_sup_leaves_valid_citation_unchanged() -> None:
    """Short <sup> with digit content (real citation) is not touched."""
    html = '<p>pressure<sup><a href="#ref-3">3</a></sup>. Next.</p>'
    result = _fix_orphaned_sup_tags(html)
    assert result == html


def test_fix_orphaned_sup_leaves_short_period_sup_unchanged() -> None:
    """<sup>. X</sup> shorter than 30 chars without nested sup is left alone."""
    html = "<p>text<sup>. ok</sup> more</p>"
    result = _fix_orphaned_sup_tags(html)
    assert result == html


def test_fix_orphaned_sup_multiple_in_document() -> None:
    """Multiple broken sups in one document are all repaired."""
    broken = (
        '<sup>. First broken sentence with enough chars to trigger fix.</sup>'
        '<sup>. Second broken sentence with enough chars to trigger fix.</sup>'
    )
    result = _fix_orphaned_sup_tags(broken)
    assert "<sup>." not in result
    assert "First broken" in result
    assert "Second broken" in result
