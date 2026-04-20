import re
from pathlib import Path

import pytest

from zoteropdf2md.translategemma import (
    _apply_abbrev_mask,
    _apply_formula_mask,
    _is_translator_refusal,
    _mark_author_line_notranslate,
    _restore_abbrev_mask,
    _restore_formula_mask,
    _try_batch_translate,
    _try_batch_translate_with_reason,
    _translate_text_segment,
    _try_windowed_batch_translate,
    _try_windowed_batch_translate_with_reason,
    language_name_for_code,
    normalize_language_code,
    translate_html_text_nodes,
    translated_html_output_path,
)


def test_normalize_language_code_supports_code_and_name() -> None:
    assert normalize_language_code("ru") == "ru"
    assert normalize_language_code("Russian") == "ru"
    assert language_name_for_code("ru") == "Russian"


def test_translated_html_output_path_uses_language_suffix() -> None:
    source = Path("D:/tmp/paper.html")
    assert translated_html_output_path(source, "de").name == "paper.de.html"


def test_translate_html_text_nodes_preserves_markup_and_skips_code_blocks() -> None:
    html = (
        "<html><body>"
        "<h1>Hello</h1>"
        "<p>World text.</p>"
        "<script>const msg='Hello';</script>"
        "<style>.x{content:'World'}</style>"
        "<pre>Hello code block</pre>"
        "</body></html>"
    )

    def fake_translate(text: str) -> str:
        return text.replace("Hello", "Privet").replace("World", "Mir")

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=32,
    )

    assert "<h1>Privet</h1>" in translated
    assert "<p>Mir text.</p>" in translated
    assert "<script>const msg='Hello';</script>" in translated
    assert "<style>.x{content:'World'}</style>" in translated
    assert "<pre>Hello code block</pre>" in translated
    assert translated_segments >= 2


def test_translate_html_text_nodes_tries_full_then_fallback_chunks() -> None:
    html = "<p>" + ("alpha beta gamma. " * 80) + "</p>"
    calls: list[str] = []

    def fake_translate(text: str) -> str:
        calls.append(text)
        if len(text) > 300:
            raise RuntimeError("context window exceeded")
        return text.upper()

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=420,
    )

    assert calls
    assert len(calls[0]) > 300
    assert any(len(item) <= 300 for item in calls[1:])
    assert "<p>" in translated and "</p>" in translated
    assert "ALPHA BETA GAMMA." in translated
    assert translated_segments >= 1


def test_translate_html_text_nodes_preserves_non_context_errors() -> None:
    def failing_translate(text: str) -> str:
        raise RuntimeError("unexpected translation failure")

    with pytest.raises(RuntimeError, match="unexpected translation failure"):
        translate_html_text_nodes(
            "<p>Hello</p>",
            translate_text=failing_translate,
            max_chunk_chars=512,
        )


def test_translate_html_text_nodes_reports_progress_for_translatable_segments() -> None:
    html = (
        "<html><body>"
        "<p>First paragraph.</p>"
        "<pre>Skip this block.</pre>"
        "<p>Second paragraph.</p>"
        "</body></html>"
    )
    events: list[tuple[int, int]] = []

    def fake_translate(text: str) -> str:
        return text.upper()

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=512,
        on_progress=lambda done, total: events.append((done, total)),
    )

    assert translated_segments == 2
    assert "<p>FIRST PARAGRAPH.</p>" in translated
    assert "<p>SECOND PARAGRAPH.</p>" in translated
    assert events
    assert events[-1] == (2, 2)


# ---------------------------------------------------------------------------
# Translator refusal detection
# ---------------------------------------------------------------------------

def test_is_translator_refusal_detects_russian_meta_commentary() -> None:
    assert _is_translator_refusal(
        'Невозможно перевести "LC" без дополнительного контекста.'
    )
    assert _is_translator_refusal("Я не могу перевести этот термин.")
    assert _is_translator_refusal(
        "Пожалуйста, предоставьте больше информации."
    )


def test_is_translator_refusal_detects_english_meta_commentary() -> None:
    assert _is_translator_refusal("I cannot translate this abbreviation.")
    assert _is_translator_refusal(
        "Please provide more context to translate this correctly."
    )


def test_is_translator_refusal_accepts_normal_translation() -> None:
    assert not _is_translator_refusal("Пассивный беспроводной датчик давления.")
    assert not _is_translator_refusal("Измерение внутричерепного давления.")


def test_translate_html_text_nodes_falls_back_to_original_on_refusal() -> None:
    """When the model returns a refusal the original text must be preserved."""

    def refusing_translate(text: str) -> str:
        return f'Невозможно перевести "{text}" без дополнительного контекста.'

    html = "<html><body><p>LC sensor</p></body></html>"
    translated, _ = translate_html_text_nodes(
        html,
        translate_text=refusing_translate,
        max_chunk_chars=512,
    )

    assert "LC sensor" in translated
    assert "Невозможно" not in translated


# ---------------------------------------------------------------------------
# References section preservation
# ---------------------------------------------------------------------------

def test_translate_html_text_nodes_skips_references_section() -> None:
    """The References section must be copied verbatim, not translated."""
    html = (
        "<html><body>"
        "<p>Main text here.</p>"
        "<h4>References</h4>"
        "<ul>"
        "<li>[1] Smith, J. et al. Paper title. Journal, 2020.</li>"
        "<li>[2] Jones, A. Another paper. Conf. Proc., 2021.</li>"
        "</ul>"
        "</body></html>"
    )

    def fake_translate(text: str) -> str:
        return text.upper()

    translated, _ = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=512,
    )

    # Body text before references should be translated.
    assert "MAIN TEXT HERE." in translated
    # Author names and titles inside references must remain unchanged.
    assert "Smith, J. et al. Paper title." in translated
    assert "Jones, A. Another paper." in translated
    # The heading word itself must survive unchanged too.
    assert "References" in translated


# ---------------------------------------------------------------------------
# Author-line protection
# ---------------------------------------------------------------------------

def test_mark_author_line_notranslate_adds_attribute() -> None:
    """The first <p> after <h1> should gain translate="no"."""
    html = (
        "<html><body>"
        "<h1>A Novel Circuit</h1>"
        "<p>Fa Wang, Member, IEEE, and John Webster</p>"
        "<p><i>Abstract</i>—We present...</p>"
        "</body></html>"
    )
    marked = _mark_author_line_notranslate(html)

    assert 'translate="no"' in marked
    # The Abstract paragraph must NOT be marked.
    abstract_idx = marked.index("Abstract")
    assert 'translate="no"' not in marked[abstract_idx - 20: abstract_idx]


def test_mark_author_line_notranslate_skips_abstract_as_first_p() -> None:
    """If the first <p> after <h1> IS the abstract paragraph, don't mark it."""
    html = (
        "<html><body>"
        "<h1>Title</h1>"
        "<p><i>Abstract</i>—Short abstract text here.</p>"
        "</body></html>"
    )
    marked = _mark_author_line_notranslate(html)
    assert 'translate="no"' not in marked


def test_translate_html_text_nodes_respects_translate_no_attribute() -> None:
    """Elements with translate="no" must be left in their original language."""
    html = (
        "<html><body>"
        "<p translate=\"no\">Fa Wang, Member, IEEE</p>"
        "<p>Normal translatable text.</p>"
        "</body></html>"
    )

    def fake_translate(text: str) -> str:
        return text.upper()

    translated, _ = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=512,
    )

    assert "Fa Wang, Member, IEEE" in translated
    assert "NORMAL TRANSLATABLE TEXT." in translated


def test_translate_html_text_nodes_strips_appended_source_echo() -> None:
    html = "<html><body><p>Hello world.</p></body></html>"

    def fake_translate(text: str) -> str:
        return f"Привет мир.\n\n{text}"

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=512,
    )

    assert translated_segments == 1
    assert "Привет мир." in translated
    assert "Hello world." not in translated


def test_translate_html_text_nodes_heading_separator_does_not_leak() -> None:
    html = (
        "<html><body>"
        "<h1>A Novel Passive Wireless <i>LC</i> Sensor</h1>"
        "<p>Second paragraph.</p>"
        "</body></html>"
    )

    translated, _ = translate_html_text_nodes(
        html,
        translate_text=lambda text: text,
        max_chunk_chars=512,
    )

    assert "<i>LC</i>" in translated
    assert "@@Z2M_HSEP@@" not in translated


# ---------------------------------------------------------------------------
# Batch translation helpers
# ---------------------------------------------------------------------------

def test_apply_formula_mask_replaces_spans_with_tokens() -> None:
    text = r"Coupling L_{\rm m} and current I_1 flow."
    masked, fmap = _apply_formula_mask(text)
    assert "L_{" not in masked
    assert "I_1" not in masked
    assert len(fmap) == 2
    restored = _restore_formula_mask(masked, fmap)
    assert restored == text


def test_try_batch_translate_returns_none_for_single_segment() -> None:
    """One segment is not worth a batch call."""
    assert _try_batch_translate(["hello"], lambda t: t.upper()) is None


def test_try_batch_translate_translates_multiple_segments() -> None:
    """Two segments are joined, translated once, split back."""
    calls: list[str] = []

    def fake_translate(text: str) -> str:
        calls.append(text)
        return text.replace("Hello", "Привет").replace("World", "Мир")

    result = _try_batch_translate(["Hello.", "World."], fake_translate)

    assert result == ["Привет.", "Мир."]
    assert len(calls) == 1  # Only one model call


def test_try_batch_translate_returns_none_on_non_trailing_id_mismatch() -> None:
    """Middle-id loss must still fail (lenient applies only to safe cases)."""
    def eating_translate(text: str) -> str:
        # Remove a middle id marker to force non-trailing mismatch.
        return re.sub(r"<z2m-i2\s*/>", "", text, flags=re.IGNORECASE)

    result = _try_batch_translate(["Hello.", "World.", "Again."], eating_translate)
    assert result is None


def test_try_batch_translate_lenient_for_trailing_single_id() -> None:
    """Two-segment window may recover if the model drops the trailing id."""
    def eating_translate(text: str) -> str:
        return re.sub(r"<z2m-i2\s*/>", "", text, flags=re.IGNORECASE)

    result = _try_batch_translate(["Hello.", "World."], eating_translate)
    assert result == ["Hello.World.", "World."]


def test_try_batch_translate_returns_none_on_exception() -> None:
    def failing(text: str) -> str:
        raise RuntimeError("OOM")

    result = _try_batch_translate(["A", "B"], failing)
    assert result is None


def test_try_batch_translate_preserves_formulas() -> None:
    r"""Formula spans must survive a translate call that would corrupt LaTeX."""
    def corrupting_translate(text: str) -> str:
        return (
            text
            .replace("\\", "X")
            .replace("_", "U")
            .replace("Hello", "Привет")
        )

    segments = [r"Hello \frac{a}{b}.", r"\omega = 2\pi f."]
    result = _try_batch_translate(segments, corrupting_translate)

    assert result is not None
    assert r"\frac{a}{b}" in result[0]
    assert r"\omega = 2\pi f." in result[1]
    assert "Привет" in result[0]


def test_try_windowed_batch_translate_makes_multiple_calls_for_many_segments() -> None:
    calls: list[str] = []

    def fake_translate(text: str) -> str:
        calls.append(text)
        return text.replace("A", "X")

    segments = [f"A{i}" for i in range(12)]
    result = _try_windowed_batch_translate(
        segments,
        fake_translate,
        window_segments=4,
        overlap_segments=1,
        max_window_chars=2048,
    )

    assert result == [f"X{i}" for i in range(12)]
    assert len(calls) == 3


def test_try_windowed_batch_translate_recovers_via_bisect_without_retry() -> None:
    calls = {"count": 0}

    def flaky_translate(text: str) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            return re.sub(r"<z2m-i2\s*/>", "", text, flags=re.IGNORECASE)
        return text.replace("A", "X")

    segments = [f"A{i}" for i in range(8)]
    result = _try_windowed_batch_translate(
        segments,
        flaky_translate,
        window_segments=4,
        overlap_segments=1,
        max_window_chars=4096,
    )

    assert result == [f"X{i}" for i in range(8)]
    assert calls["count"] == 4


def test_try_windowed_batch_translate_uses_bisect_after_batch_failure() -> None:
    calls = {"count": 0}

    def size_sensitive_translate(text: str) -> str:
        calls["count"] += 1
        marker_count = len(re.findall(r"<z2m-i\d+\s*/>", text, flags=re.IGNORECASE))
        if marker_count > 2:
            return re.sub(r"<z2m-i\d+\s*/>", "", text, count=1, flags=re.IGNORECASE)
        return text.replace("A", "X")

    segments = [f"A{i}" for i in range(4)]
    result = _try_windowed_batch_translate(
        segments,
        size_sensitive_translate,
        window_segments=4,
        overlap_segments=0,
        max_window_chars=4096,
    )

    assert result == [f"X{i}" for i in range(4)]
    assert calls["count"] == 3


def test_try_batch_translate_lenient_recovery_for_large_window_single_missing_id() -> None:
    segments = [f"A{i}" for i in range(1, 11)]

    def marker_loss_translate(text: str) -> str:
        text = re.sub(r"<z2m-i5\s*/>", "", text, flags=re.IGNORECASE)
        return text.replace("A", "X")

    result = _try_batch_translate(segments, marker_loss_translate)

    assert result is not None
    assert result[4] == "A5"
    assert result[0] == "X1"
    assert result[-1] == "X10"


def test_try_batch_translate_lenient_recovery_for_trailing_eos_suffix() -> None:
    segments = [f"A{i}" for i in range(1, 7)]

    def trailing_loss_translate(text: str) -> str:
        cutoff = re.search(r"<z2m-i5\s*/>", text, flags=re.IGNORECASE)
        if cutoff is not None:
            text = text[:cutoff.start()]
        return text.replace("A", "X")

    result, reason = _try_batch_translate_with_reason(segments, trailing_loss_translate)

    assert result is not None
    assert reason == "ok_lenient_trailing_eos k=2"
    assert result[0] == "X1"
    assert result[3] == "X4"
    assert result[4] == "A5"
    assert result[5] == "A6"


def test_try_windowed_batch_translate_leaf_fallback_returns_full_result() -> None:
    segments = [f"A{i}" for i in range(4)]

    def drop_tail_ids_translate(text: str) -> str:
        marker_count = len(re.findall(r"<z2m-i\d+\s*/>", text, flags=re.IGNORECASE))
        if marker_count >= 4:
            text = re.sub(r"<z2m-i3\s*/>", "", text, flags=re.IGNORECASE)
            text = re.sub(r"<z2m-i4\s*/>", "", text, flags=re.IGNORECASE)
        return text.replace("A", "X")

    result, reason = _try_windowed_batch_translate_with_reason(
        segments,
        drop_tail_ids_translate,
        window_segments=4,
        overlap_segments=0,
        max_window_chars=4096,
    )

    assert result == [f"X{i}" for i in range(4)]
    assert reason == "ok"


def test_try_windowed_batch_translate_always_broken_batch_uses_leaf_path() -> None:
    segments = [f"A{i}" for i in range(6)]

    def always_mismatch_for_batches(text: str) -> str:
        marker_count = len(re.findall(r"<z2m-i\d+\s*/>", text, flags=re.IGNORECASE))
        if marker_count > 1:
            return re.sub(r"<z2m-i\d+\s*/>", "", text, count=1, flags=re.IGNORECASE)
        return text.replace("A", "X")

    result = _try_windowed_batch_translate(
        segments,
        always_mismatch_for_batches,
        window_segments=6,
        overlap_segments=0,
        max_window_chars=4096,
    )

    assert result == [f"X{i}" for i in range(6)]


def test_translate_html_text_nodes_uses_single_model_call_for_multi_paragraph() -> None:
    """Batch mode must translate multiple paragraphs with exactly one model call."""
    html = (
        "<html><body>"
        "<p>First paragraph text.</p>"
        "<p>Second paragraph text.</p>"
        "<p>Third paragraph text.</p>"
        "</body></html>"
    )
    calls: list[str] = []

    def fake_translate(text: str) -> str:
        calls.append(text)
        return text.upper()

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
    )

    assert translated_segments == 3
    assert "<p>FIRST PARAGRAPH TEXT.</p>" in translated
    assert "<p>SECOND PARAGRAPH TEXT.</p>" in translated
    assert len(calls) == 1  # Single batch call


def test_translate_html_text_nodes_preserves_inline_boundary_spaces() -> None:
    html = "<html><body><p>Hello <em>world</em> !</p><p><em>Hello</em> world</p></body></html>"
    translated, _ = translate_html_text_nodes(html, translate_text=lambda t: t)
    assert "<p>Hello <em>world</em> !</p>" in translated
    assert "<p><em>Hello</em> world</p>" in translated


def test_translate_html_text_nodes_handles_id_marker_loss_without_global_fallback() -> None:
    """Id marker loss should stay local (leaf fallback), not global per-segment."""
    html = "<html><body><p>Hello.</p><p>World.</p><p>Again.</p></body></html>"
    call_count = [0]
    fallback_reasons: list[str] = []

    def eating_translate(text: str) -> str:
        call_count[0] += 1
        # Remove middle id marker (non-trailing mismatch) to force bisect+leaf path.
        result = re.sub(r"<z2m-i2\s*/>", "", text, flags=re.IGNORECASE)
        return (
            result
            .replace("Hello", "Привет")
            .replace("World", "Мир")
            .replace("Again", "Снова")
        )

    translated, _ = translate_html_text_nodes(
        html,
        translate_text=eating_translate,
        on_batch_fallback=lambda reason: fallback_reasons.append(reason),
    )

    # Fallback must be local inside the batch cascade, not document-wide.
    assert call_count[0] >= 3
    assert fallback_reasons == []
    assert "Привет" in translated
    assert "Мир" in translated
    assert "Снова" in translated


def test_translate_html_text_nodes_preserves_formula_fragments() -> None:
    html = (
        "<html><body>"
        "<p>Inductive coupling factor is L_{\\rm m}, and a current I_1 flows.</p>"
        "<p>Z_{1} = \\frac{V_{1}}{I_{1}} = j\\omega L_{1}</p>"
        "</body></html>"
    )

    def fake_translate(text: str) -> str:
        return (
            text
            .replace("Inductive coupling factor is", "Коэффициент связи")
            .replace("and a current", "и ток")
            .replace("flows", "протекает")
            .replace("\\", "BROKEN_SLASH")
            .replace("_", "BROKEN_UNDERSCORE")
            .replace("j", "JJ")
        )

    translated, _ = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
        max_chunk_chars=512,
    )

    assert "L_{\\rm m}" in translated
    assert "I_1" in translated
    assert "Z_{1} = \\frac{V_{1}}{I_{1}} = j\\omega L_{1}" in translated
    assert "BROKEN_SLASH" not in translated
    assert "BROKEN_UNDERSCORE" not in translated


# ---------------------------------------------------------------------------
# Abbreviation mask
# ---------------------------------------------------------------------------

def test_apply_abbrev_mask_replaces_uppercase_sequences() -> None:
    text = "The IEEE standard for VNA measurements uses ADC chips."
    masked, amap = _apply_abbrev_mask(text)

    assert "IEEE" not in masked
    assert "VNA" not in masked
    assert "ADC" not in masked
    assert len(amap) == 3

    restored = _restore_abbrev_mask(masked, amap)
    assert restored == text


def test_apply_abbrev_mask_leaves_single_letters_and_lowercase() -> None:
    text = "Variable T is measured in K at time t."
    masked, amap = _apply_abbrev_mask(text)

    # Single uppercase letters must NOT be masked
    assert "T" in masked
    assert "K" in masked
    assert not amap


def test_apply_abbrev_mask_handles_roman_numerals() -> None:
    text = "See Section II and Section III for details."
    masked, amap = _apply_abbrev_mask(text)

    assert "II" not in masked
    assert "III" not in masked

    restored = _restore_abbrev_mask(masked, amap)
    assert restored == text


def test_try_batch_translate_translates_text_around_abbreviations() -> None:
    """Batch path must preserve abbreviations via hard mask, not prompt goodwill."""

    def abbreviation_expanding_translate(text: str) -> str:
        # Simulate a model that eagerly expands abbreviations when it sees them.
        # With hard masking wired in, raw abbreviations should not be visible here.
        return (
            text
            .replace("uses", "использует")
            .replace("GAI", "ГАИ")
            .replace("VNA", "векторный анализатор цепей")
        )

    segments = ["The GAI sensor uses VNA calibration.", "Standard test method."]
    result = _try_batch_translate(segments, abbreviation_expanding_translate)

    assert result is not None
    assert "GAI" in result[0]
    assert "VNA" in result[0]
    assert "ГАИ" not in result[0]
    assert "векторный анализатор цепей" not in result[0]
    assert "использует" in result[0]


def test_try_batch_translate_reports_abbrev_placeholder_mismatch() -> None:
    segments = ["LC sensor uses VNA calibration.", "Control sample."]

    def dropping_abbrev_token_translate(text: str) -> str:
        # Drop one abbreviation token while keeping id markers intact.
        return re.sub(r'<z2m-a\s+id\s*=\s*"1"\s*/?>', "", text, count=1, flags=re.IGNORECASE)

    result, reason = _try_batch_translate_with_reason(segments, dropping_abbrev_token_translate)

    assert result is None
    assert "abbrev_placeholder_mismatch" in reason


def test_translate_text_segment_preserves_abbrev_in_single_segment_path() -> None:
    segment = "ICP monitoring uses VNA calibration."

    def abbreviation_expanding_translate(text: str) -> str:
        return (
            text
            .replace("uses", "использует")
            .replace("ICP", "внутричерепное давление")
            .replace("VNA", "векторный анализатор цепей")
        )

    translated = _translate_text_segment(
        segment,
        translate_text=abbreviation_expanding_translate,
        cache={},
        max_chunk_chars=512,
    )

    assert "ICP" in translated
    assert "VNA" in translated
    assert "внутричерепное давление" not in translated
    assert "векторный анализатор цепей" not in translated
    assert "использует" in translated


def test_translate_text_segment_falls_back_when_abbrev_token_is_lost() -> None:
    segment = "LC sensor."

    def token_losing_translate(text: str) -> str:
        return re.sub(r'<z2m-a\s+id\s*=\s*"0"\s*/?>', "", text, count=1, flags=re.IGNORECASE)

    translated = _translate_text_segment(
        segment,
        translate_text=token_losing_translate,
        cache={},
        max_chunk_chars=512,
    )

    assert translated == segment


def test_apply_abbrev_mask_does_not_mask_long_uppercase_section_titles() -> None:
    """All-caps section title words (>5 letters) must NOT be masked.

    In IEEE-style papers section headings are written in all-caps:
    e.g. I. INTRODUCTION, V. CONCLUSION.  These must be translatable by the
    model so the translated document has Russian headings.
    """
    text = "I. INTRODUCTION\n\nV. CONCLUSION\n\nIII. PROPOSED DESIGN"
    masked, amap = _apply_abbrev_mask(text)

    # Words longer than 5 letters must pass through unchanged.
    assert "INTRODUCTION" in masked
    assert "CONCLUSION" in masked
    assert "PROPOSED" in masked
    assert "DESIGN" in masked

    # Roman numerals (short sequences) ARE expected to be masked — that's fine.
    # The key requirement is that long section-title words are NOT masked.


def test_apply_abbrev_mask_still_protects_short_abbreviations() -> None:
    """Short abbreviations (≤5 chars) must still be masked after the length limit."""
    text = "The MEMS sensor and IEEE standard use VNA calibration with ADC chips."
    masked, amap = _apply_abbrev_mask(text)

    assert "MEMS" not in masked
    assert "IEEE" not in masked
    assert "VNA" not in masked
    assert "ADC" not in masked
    assert len(amap) == 4

    restored = _restore_abbrev_mask(masked, amap)
    assert restored == text
