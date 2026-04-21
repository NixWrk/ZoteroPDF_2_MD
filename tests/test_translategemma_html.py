import re
from pathlib import Path

import pytest

from zoteropdf2md.translategemma import (
    _apply_abbrev_mask,
    _apply_post_reassembly_guards,
    _apply_wide_paragraph_recovery,
    _apply_formula_mask,
    _is_identity_residual,
    _is_translator_refusal,
    _mark_author_line_notranslate,
    _restore_abbrev_mask,
    _restore_formula_mask,
    _sanitize_generation_config_for_greedy,
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


def test_sanitize_generation_config_for_greedy_sets_sampling_defaults() -> None:
    class DummyConfig:
        do_sample = True
        top_p = 0.95
        top_k = 64
        temperature = 0.7
        typical_p = 0.8

    cfg = DummyConfig()
    _sanitize_generation_config_for_greedy(cfg)

    assert cfg.do_sample is False
    assert cfg.top_p == 1.0
    assert cfg.top_k == 50
    assert cfg.temperature == 1.0
    assert cfg.typical_p == 1.0


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


def test_try_batch_translate_accepts_formula_token_case_variants() -> None:
    segments = [r"Hello \frac{a}{b}.", "World."]

    def token_case_variant_translate(text: str) -> str:
        return (
            text
            .replace("@@Z2MF0@@", "@@z2mf0@@")
            .replace("Hello", "Привет")
            .replace("World", "Мир")
        )

    result = _try_batch_translate(segments, token_case_variant_translate)

    assert result is not None
    assert r"\frac{a}{b}" in result[0]
    assert "Привет" in result[0]
    assert "Мир" in result[1]


def test_try_batch_translate_recovers_when_formula_tokens_are_altered() -> None:
    segments = [r"Hello \frac{a}{b} and \omega.", "World."]

    def drop_formula_token_translate(text: str) -> str:
        text = re.sub(r"@@Z2MF1@@", "", text, count=1, flags=re.IGNORECASE)
        return text.replace("Hello", "Привет").replace("World", "Мир")

    result, reason = _try_batch_translate_with_reason(segments, drop_formula_token_translate)

    assert result is not None
    assert "ok_lenient_formula_recovered" in reason
    assert r"\frac{a}{b}" in result[0]
    assert r"\omega" in result[0]
    assert "Мир" in result[1]


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
        return (
            text
            .replace("First paragraph text.", "Первый абзац текста.")
            .replace("Second paragraph text.", "Второй абзац текста.")
            .replace("Third paragraph text.", "Третий абзац текста.")
        )

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=fake_translate,
    )

    assert translated_segments == 3
    assert "<p>Первый абзац текста.</p>" in translated
    assert "<p>Второй абзац текста.</p>" in translated
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


def test_try_batch_translate_recovers_when_abbrev_tokens_are_partially_dropped() -> None:
    segments = ["LC sensor uses VNA calibration.", "Control sample."]

    def dropping_abbrev_token_translate(text: str) -> str:
        if "<z2m-i1/>" in text:
            # Batch path: damage one abbrev token to force local recovery.
            return re.sub(r"@@Z2M_A1@@", "", text, count=1, flags=re.IGNORECASE)
        # Local single-segment recovery path keeps abbrev tokens and translates body.
        return (
            text
            .replace("sensor uses", "датчик использует")
            .replace("calibration", "калибровку")
            .replace("Control sample.", "Контрольный образец.")
        )

    result, reason = _try_batch_translate_with_reason(segments, dropping_abbrev_token_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery") or "ok_lenient_abbrev_recovered" in reason
    assert "identity_residual=1" in reason or "ok_lenient_abbrev_recovered" in reason
    assert "LC" in result[0]
    assert "VNA" in result[0]


def test_try_batch_translate_recovers_when_all_abbrev_tokens_are_dropped() -> None:
    segments = ["LC sensor uses VNA calibration.", "Control sample."]

    def drop_all_abbrev_tokens_translate(text: str) -> str:
        if "<z2m-i1/>" in text:
            text = re.sub(r"@@Z2M_A0@@", "", text, flags=re.IGNORECASE)
            text = re.sub(r"@@Z2M_A1@@", "", text, flags=re.IGNORECASE)
            return text.replace("uses", "использует")
        return (
            text
            .replace("sensor uses", "датчик использует")
            .replace("calibration", "калибровку")
            .replace("Control sample.", "Контрольный образец.")
        )

    result, reason = _try_batch_translate_with_reason(segments, drop_all_abbrev_tokens_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery") or "ok_lenient_abbrev_recovered" in reason
    assert "identity_residual=1" in reason or "ok_lenient_abbrev_recovered" in reason
    assert "LC" in result[0]
    assert "VNA" in result[0]


def test_try_batch_translate_accepts_case_variants_for_abbrev_tokens() -> None:
    segments = ["GAI sensor uses VNA calibration.", "Control sample."]

    def token_case_variant_translate(text: str) -> str:
        return (
            text
            .replace("@@Z2M_A0@@", "@@z2m_a0@@")
            .replace("@@Z2M_A1@@", "@@z2m_a1@@")
            .replace("uses", "использует")
        )

    result = _try_batch_translate(segments, token_case_variant_translate)

    assert result is not None
    assert "GAI" in result[0]
    assert "VNA" in result[0]
    assert "использует" in result[0]


def test_try_batch_translate_keeps_lc_abbreviation() -> None:
    segments = ["A novel LC sensor operating at 5 MHz.", "Control sample."]

    def lc_expanding_translate(text: str) -> str:
        return text.replace("LC", "Индуктивно-ёмкостная цепь")

    result = _try_batch_translate(segments, lc_expanding_translate)

    assert result is not None
    assert "LC" in result[0]
    assert "Индуктивно-ёмкостная цепь" not in result[0]


def test_try_windowed_batch_translate_recovers_from_abbrev_token_loss_locally() -> None:
    segments = [
        "LC sensor calibration.",
        "VNA setup details.",
        "Control segment.",
    ]

    def abbrev_token_losing_translate(text: str) -> str:
        marker_count = len(re.findall(r"<z2m-i\d+\s*/>", text, flags=re.IGNORECASE))
        if marker_count > 1:
            return re.sub(
                r"@@Z2M_A0@@",
                "",
                text,
                count=1,
                flags=re.IGNORECASE,
            )
        return (
            text
            .replace("calibration", "калибровку")
            .replace("setup details", "параметры настройки")
            .replace("Control segment.", "Контрольный сегмент.")
        )

    result, reason = _try_windowed_batch_translate_with_reason(
        segments,
        abbrev_token_losing_translate,
        window_segments=3,
        overlap_segments=0,
        max_window_chars=4096,
    )

    assert result is not None
    assert reason == "ok"
    assert result[0] != ""
    assert result[1] != segments[1]
    assert result[2] != segments[2]


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
        return re.sub(r"@@Z2M_A0@@", "", text, count=1, flags=re.IGNORECASE)

    translated = _translate_text_segment(
        segment,
        translate_text=token_losing_translate,
        cache={},
        max_chunk_chars=512,
    )

    assert translated == segment


def test_translate_text_segment_retries_all_caps_table_caption_with_normalized_case() -> None:
    segment = "TABLE I PARAMETERS FOR TWO ANTENNAS"
    calls: list[str] = []

    def caption_translate(text: str) -> str:
        calls.append(text)
        if text == "Table I parameters for two antennas":
            return "Таблица I параметры двух антенн"
        return text

    translated = _translate_text_segment(
        segment,
        translate_text=caption_translate,
        cache={},
        max_chunk_chars=512,
    )

    assert translated == "Таблица I параметры двух антенн"
    assert "Table I parameters for two antennas" in calls


def test_try_batch_translate_recovers_all_caps_table_caption_identity_residual() -> None:
    segments = [
        "TABLE IV COMPARISON OF STATE OF ARTS",
        "Control segment.",
    ]

    def caption_identity_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return (
                "<z2m-i1/>TABLE IV COMPARISON OF STATE OF ARTS"
                "<z2m-i2/>Контрольный сегмент переведен."
            )
        if text == "Table IV comparison of state of arts":
            return "Таблица IV сравнение существующих решений"
        return text.replace("Control segment.", "Контрольный сегмент переведен.")

    result, reason = _try_batch_translate_with_reason(segments, caption_identity_translate)

    assert result is not None
    assert reason.startswith("ok_lenient_abbrev_recovered") or reason.startswith("ok_leak_recovery")
    assert result[0] == "Таблица IV сравнение существующих решений"
    assert result[1] == "Контрольный сегмент переведен."


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


def test_try_batch_translate_recovers_when_internal_marker_leaks_into_segment() -> None:
    segments = [
        "First segment should be recovered.",
        "Second segment can stay from batch.",
    ]

    def marker_leak_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return (
                "<z2m-i1/>Batch output with leaked marker <z2m-i99>tail</z2m-i99>"
                "<z2m-i2/>Второй сегмент из батча."
            )
        return (
            text
            .replace("First segment should be recovered.", "Первый сегмент восстановлен локально.")
            .replace("Second segment can stay from batch.", "Второй сегмент из батча.")
        )

    result, reason = _try_batch_translate_with_reason(segments, marker_leak_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery")
    assert "marker_leak=1" in reason
    assert result[0] == "Первый сегмент восстановлен локально."
    assert result[1] == "Второй сегмент из батча."
    assert "<z2m-" not in "".join(result)


def test_tag_mask_preserves_anchors_in_recovery() -> None:
    segments = [
        'Segment with (<a href="#section-three">section three</a>-d) anchor.',
        "Second batch segment.",
    ]

    def marker_leak_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return (
                "<z2m-i1/>Output with leaked marker <z2m-i7>tail"
                "<z2m-i2/>Второй сегмент из батча."
            )
        if "@@Z2M_T0@@" in text:
            return "Восстановленный сегмент со ссылкой (@@Z2M_T0@@-D)."
        return text

    result, reason = _try_batch_translate_with_reason(segments, marker_leak_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery")
    assert "marker_leak=1" in reason
    assert '(<a href="#section-three">section three</a>-d)' in result[0]
    assert result[1] == "Второй сегмент из батча."


def test_try_batch_translate_recovers_neighbor_duplicates_locally() -> None:
    segments = [
        "Alpha segment source text should remain unique after recovery.",
        "Beta segment source text should remain unique after recovery.",
    ]
    duplicate_text = (
        "Одинаковый длинный фрагмент для проверки duplicate guard в соседних "
        "сегментах после reassembly."
    )

    def duplicate_batch_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return f"<z2m-i1/>{duplicate_text}<z2m-i2/>{duplicate_text}"
        return (
            text
            .replace(
                "Alpha segment source text should remain unique after recovery.",
                "Альфа сегмент восстановлен локально.",
            )
            .replace(
                "Beta segment source text should remain unique after recovery.",
                "Бета сегмент восстановлен локально.",
            )
        )

    result, reason = _try_batch_translate_with_reason(segments, duplicate_batch_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery")
    assert "duplicate_leak=2" in reason
    assert result[0] == "Альфа сегмент восстановлен локально."
    assert result[1] == "Бета сегмент восстановлен локально."


def test_try_batch_translate_recovers_identity_residual_segment() -> None:
    segments = [
        "This paragraph remains in English and should be recovered by guard logic.",
        "Second segment can stay translated.",
    ]

    def identity_residual_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return (
                "<z2m-i1/>This paragraph remains in English and should be recovered by guard logic."
                "<z2m-i2/>Второй сегмент уже переведен."
            )
        return (
            text
            .replace(
                "This paragraph remains in English and should be recovered by guard logic.",
                "Этот абзац восстановлен локально и переведен на русский.",
            )
            .replace("Second segment can stay translated.", "Второй сегмент уже переведен.")
        )

    result, reason = _try_batch_translate_with_reason(segments, identity_residual_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery")
    assert "identity_residual=1" in reason
    assert result[0] == "Этот абзац восстановлен локально и переведен на русский."
    assert result[1] == "Второй сегмент уже переведен."


def test_guard_trailing_ellipsis_recovers() -> None:
    segments = [
        'This segment includes (<a href="#section-three">section three</a>-d)',
        "Next segment keeps context alive.",
    ]

    def ellipsis_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return (
                "<z2m-i1/>Этот сегмент внезапно обрывается..."
                "<z2m-i2/>Следующий сегмент уже переведен."
            )
        if "@@Z2M_T0@@" in text:
            return "Этот сегмент восстановлен корректно (@@Z2M_T0@@-D)."
        return text

    result, reason = _try_batch_translate_with_reason(segments, ellipsis_translate)

    assert result is not None
    assert reason.startswith("ok_leak_recovery")
    assert "trailing_ellipsis_artifact=1" in reason
    assert not result[0].rstrip().endswith("...")
    assert '(<a href="#section-three">section three</a>-d)' in result[0]
    assert result[1] == "Следующий сегмент уже переведен."


def test_try_windowed_batch_translate_recovers_cross_window_duplicate_boundary() -> None:
    segments = [
        "S1 source paragraph one.",
        "S2 source paragraph two.",
        "S3 source paragraph three.",
        "S4 source paragraph four.",
    ]
    duplicate_boundary_text = (
        "Длинный дублированный фрагмент на границе окон для проверки window guard "
        "после объединения результата."
    )

    def cross_window_duplicate_translate(text: str) -> str:
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            if "S1 source paragraph one." in text:
                return (
                    "<z2m-i1/>RU1 уникальный сегмент первого окна."
                    f"<z2m-i2/>{duplicate_boundary_text}"
                )
            if "S3 source paragraph three." in text:
                return (
                    f"<z2m-i1/>{duplicate_boundary_text}"
                    "<z2m-i2/>RU4 уникальный сегмент второго окна."
                )
        return (
            text
            .replace("S1 source paragraph one.", "RU1 уникальный сегмент первого окна.")
            .replace("S2 source paragraph two.", "RU2 локально восстановлен.")
            .replace("S3 source paragraph three.", "RU3 локально восстановлен.")
            .replace("S4 source paragraph four.", "RU4 уникальный сегмент второго окна.")
        )

    result, reason = _try_windowed_batch_translate_with_reason(
        segments,
        cross_window_duplicate_translate,
        window_segments=2,
        overlap_segments=0,
        max_window_chars=4096,
    )

    assert result is not None
    assert reason.startswith("ok_window_leak_recovery")
    assert "duplicate_leak=2" in reason
    assert result[0] == "RU1 уникальный сегмент первого окна."
    assert result[1] == "RU2 локально восстановлен."
    assert result[2] == "RU3 локально восстановлен."
    assert result[3] == "RU4 уникальный сегмент второго окна."

def test_regression_heading_multi_inline_split() -> None:
    html = (
        "<html><body>"
        "<h1>A novel <i>LC</i> and <b>VNA</b> sensor</h1>"
        "<p>Body text.</p>"
        "</body></html>"
    )

    translated, _ = translate_html_text_nodes(html, translate_text=lambda text: text)

    assert "<i>LC</i>" in translated
    assert "<b>VNA</b>" in translated
    assert "@@Z2M_HSEP@@" not in translated


def test_identity_final_pass_recovers_single_inline_heading() -> None:
    html = (
        "<html><body>"
        "<h2><i>B. Signal Generator and Controller</i></h2>"
        "<p>Body text.</p>"
        "</body></html>"
    )
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        if "<z2m-i1/>" in text:
            return text
        return text.replace(
            "B. Signal Generator and Controller",
            "B. Генератор сигнала и контроллер",
        )

    translated, _ = translate_html_text_nodes(html, translate_text=fake_translate)

    assert "B. Генератор сигнала и контроллер" in translated
    assert calls[0] >= 2


def test_identity_paragraph_recovery_uses_single_batch_for_full_paragraph() -> None:
    source_segments = [
        "Signal amplifier enhances quality in the receiver path.",
        "A control chip drives the signal generator for telemetry.",
    ]
    translated_segments = list(source_segments)
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text:
            return (
                "<z2m-i1/>Усилитель сигнала повышает качество в тракте приемника."
                "<z2m-i2/>Управляющая микросхема ведет генератор сигнала для телеметрии."
            )
        return text

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[1, 1],
    )

    assert calls[0] == 1
    assert counts.get("identity_residual_paragraph") == 1
    assert result[0].startswith("Усилитель сигнала")
    assert result[1].startswith("Управляющая микросхема")


def test_identity_mixed_paragraph_recovers_only_identity_segment() -> None:
    source_segments = [
        "Signal amplifier enhances quality in the receiver path.",
        "Second segment source text.",
    ]
    translated_segments = [
        "Signal amplifier enhances quality in the receiver path.",
        "Второй сегмент уже переведен.",
    ]
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        return text.replace(
            "Signal amplifier enhances quality in the receiver path.",
            "Усилитель сигнала повышает качество в тракте приемника.",
        )

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[1, 1],
    )

    assert calls[0] == 1
    assert counts.get("identity_residual") == 1
    assert "identity_residual_paragraph" not in counts
    assert result[0].startswith("Усилитель сигнала")
    assert result[1] == "Второй сегмент уже переведен."


def test_is_identity_residual_detects_long_english_run_in_mixed_segment() -> None:
    source = (
        "An amplifier enhances the signal with a constant power output "
        "when the load impedance is matched."
    )
    mixed = (
        "Часть фразы уже переведена, но An amplifier enhances the signal "
        "with a constant power output when the load impedance is matched."
    )
    assert _is_identity_residual(source, mixed)


def test_identity_contiguous_run_recovery_handles_partial_paragraph_group() -> None:
    source_segments = [
        "A signal amplifier enhances the signal and stabilizes power output for telemetry operation.",
        "A half-wave rectifier recovers the envelope amplitude for microcontroller processing.",
        "This segment is already translated and should stay as is.",
    ]
    translated_segments = [
        source_segments[0],
        source_segments[1],
        "Этот сегмент уже переведен и должен остаться как есть.",
    ]
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text and "<z2m-i3/>" not in text:
            return (
                "<z2m-i1/>Усилитель сигнала усиливает сигнал и стабилизирует мощность для телеметрии."
                "<z2m-i2/>Полуволновой выпрямитель восстанавливает амплитуду огибающей для микроконтроллера."
            )
        return text

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[1, 1, 1],
    )

    assert calls[0] >= 1
    assert counts.get("identity_residual_paragraph") == 1
    assert result[0].startswith("Усилитель сигнала")
    assert result[1].startswith("Полуволновой выпрямитель")
    assert result[2] == "Этот сегмент уже переведен и должен остаться как есть."


def test_identity_terminal_escalates_to_group_context_recovery() -> None:
    source_segments = [
        "Context sentence before the hard segment.",
        "The block samples output amplitude quickly.",
        "Context sentence after the hard segment.",
    ]
    translated_segments = [
        "Контекстное предложение перед сложным сегментом.",
        source_segments[1],
        "Контекстное предложение после сложного сегмента.",
    ]
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        if "<z2m-i1/>" in text and "<z2m-i2/>" in text and "<z2m-i3/>" in text:
            return (
                "<z2m-i1/>Контекстное предложение перед сложным сегментом."
                "<z2m-i2/>Блок быстро измеряет амплитуду выходного сигнала."
                "<z2m-i3/>Контекстное предложение после сложного сегмента."
            )
        if "zz2mtargetstartzz" in text and "zz2mtargetendzz" in text:
            return text.replace(
                "The block samples output amplitude quickly.",
                "Блок быстро измеряет амплитуду выходного сигнала.",
            )
        return text

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[9, 9, 9],
    )

    assert calls[0] >= 2
    assert counts.get("identity_context_recovery") == 1
    assert counts.get("identity_terminal") in (None, 0)
    assert result[1].startswith("Блок быстро измеряет")


def test_identity_forced_recovery_handles_stubborn_single_segment() -> None:
    source_segments = [
        "Currently the maximal ADC input from the half wave rectifier is 1 V.",
    ]
    translated_segments = [source_segments[0]]
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        if "zz2mforcestartzz" in text and "zz2mforceendzz" in text:
            return (
                "zz2mforcestartzz"
                "В настоящее время максимальный вход @@Z2M_A0@@ от полуволнового выпрямителя составляет 1 В."
                "zz2mforceendzz"
            )
        return text

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[1],
    )

    assert calls[0] >= 2
    assert counts.get("identity_forced_recovery") == 1
    assert counts.get("identity_terminal") in (None, 0)
    assert result[0].startswith("В настоящее время максимальный вход ADC")


def test_identity_terminal_does_not_loop() -> None:
    source_segments = ["Signal amplifier enhances quality in the receiver path."]
    translated_segments = ["Signal amplifier enhances quality in the receiver path."]
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        return text

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[1],
    )

    assert calls[0] >= 1
    assert counts.get("identity_residual") == 1
    assert counts.get("identity_terminal") == 1
    assert result[0] == source_segments[0]


def test_wide_recovery_fixes_marker_leak_shape_with_inline_anchor() -> None:
    source_parts = [
        "<p>",
        "which is discussed in (",
        '<a href="#section-III">Section III</a>',
        "-D), we solved equations.",
        "</p>",
    ]
    translated_parts = [
        "<p>",
        "который обсуждается в (...",
        '<a href="#section-III">Section III</a>',
        "-D), мы решили уравнения.",
        "</p>",
    ]

    def fake_translate(text: str) -> str:
        return (
            text
            .replace("which is discussed in (", "который обсуждается в (")
            .replace("-D), we solved equations.", "-D), мы решили уравнения.")
        )

    result_parts, counts = _apply_wide_paragraph_recovery(
        source_parts=source_parts,
        translated_parts=translated_parts,
        translatable_indices=[1, 3],
        source_segments=[source_parts[1], source_parts[3]],
        paragraph_groups=[1, 1],
        paragraph_part_ranges={1: (0, 4)},
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
    )

    assert counts.get("wide_paragraph_recovery") == 1
    joined = "".join(result_parts)
    assert "(...<a href=\"#section-III\">" not in joined
    assert "(<a href=\"#section-III\">Section III</a>-D)" in joined


def test_trailing_ellipsis_is_stripped_after_failed_local_retry() -> None:
    source_segments = [
        "We define the frequency range.",
        "Then process telemetry.",
    ]
    translated_segments = [
        "Мы определяем диапазон частоты...",
        "Затем обрабатываем телеметрию.",
    ]

    def fake_translate(text: str) -> str:
        return text if text.endswith("...") else text + "..."

    result, counts = _apply_post_reassembly_guards(
        source_segments=source_segments,
        translated_segments=translated_segments,
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
        context_label="test",
        segment_groups=[1, 1],
    )

    assert counts.get("trailing_ellipsis_artifact") == 1
    assert counts.get("trailing_ellipsis_stripped") == 1
    assert not result[0].rstrip().endswith("...")


def test_heading_identity_recovery_uses_context_markers() -> None:
    html = (
        "<html><body>"
        "<h2><i>B. Signal Generator and Controller</i></h2>"
        "<p>This paragraph provides context for the heading retry path.</p>"
        "</body></html>"
    )
    calls = [0]

    def fake_translate(text: str) -> str:
        calls[0] += 1
        if "<z2m-i1/>" in text:
            return text
        if "[[hstart]]" in text and "[[hend]]" in text:
            return text.replace(
                "B. Signal Generator and Controller",
                "B. Генератор сигнала и контроллер",
            )
        return text

    translated, _ = translate_html_text_nodes(html, translate_text=fake_translate)

    assert "B. Генератор сигнала и контроллер" in translated
    assert calls[0] >= 2


def test_wide_recovery_handles_contiguous_identity_run_in_paragraph() -> None:
    source_parts = [
        "<p>",
        "A control chip drives telemetry ",
        '<a href="#fig-8">Fig. 8</a>',
        " and signal amplifier enhances quality ",
        '<a href="#fig-9">Fig. 9</a>',
        " while software processes output.",
        "</p>",
    ]
    translated_parts = [
        "<p>",
        "A control chip drives telemetry ",
        '<a href="#fig-8">Fig. 8</a>',
        " and signal amplifier enhances quality ",
        '<a href="#fig-9">Fig. 9</a>',
        " в то время как программное обеспечение обрабатывает выход.",
        "</p>",
    ]

    def fake_translate(text: str) -> str:
        return (
            text
            .replace("A control chip drives telemetry", "Управляющая микросхема обеспечивает телеметрию")
            .replace("signal amplifier enhances quality", "усилитель сигнала повышает качество")
            .replace("while software processes output.", "в то время как программное обеспечение обрабатывает выход.")
        )

    result_parts, counts = _apply_wide_paragraph_recovery(
        source_parts=source_parts,
        translated_parts=translated_parts,
        translatable_indices=[1, 3, 5],
        source_segments=[source_parts[1], source_parts[3], source_parts[5]],
        paragraph_groups=[1, 1, 1],
        paragraph_part_ranges={1: (0, 6)},
        translate_text=fake_translate,
        cache={},
        max_chunk_chars=1800,
    )

    assert counts.get("wide_paragraph_recovery") == 1
    assert result_parts[1].startswith("Управляющая микросхема")
    assert "усилитель сигнала повышает качество" in result_parts[3]


def test_translate_html_text_nodes_reports_en_residual_warning() -> None:
    html = (
        "<html><body>"
        "<p>First identity paragraph.</p>"
        "<p>Second identity paragraph.</p>"
        "</body></html>"
    )
    warnings: list[str] = []

    translated, translated_segments = translate_html_text_nodes(
        html,
        translate_text=lambda text: text,
        on_warning=warnings.append,
    )

    assert translated_segments == 0
    assert translated
    assert warnings
    assert any(item.startswith("identity_terminal_count=") for item in warnings)
    assert any(item.startswith("wide_paragraph_recovery_count=") for item in warnings)
    assert any(item.startswith("wide_recovery_split_fail_count=") for item in warnings)
    assert warnings[-1].startswith("en_residual_segments=")

