from pathlib import Path

import pytest

from zoteropdf2md.translategemma import (
    _apply_formula_mask,
    _is_translator_refusal,
    _mark_author_line_notranslate,
    _restore_formula_mask,
    _try_batch_translate,
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


def test_try_batch_translate_returns_none_on_separator_mismatch() -> None:
    """If the model drops the separator the result is discarded."""
    def eating_translate(text: str) -> str:
        # Remove the separator — simulate model eating it
        return text.replace("<z2m-sep/>", "")

    result = _try_batch_translate(["Hello.", "World."], eating_translate)
    assert result is None


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


def test_translate_html_text_nodes_falls_back_on_separator_loss() -> None:
    """When the model eats the separator, fall back to per-segment translation."""
    html = "<html><body><p>Hello.</p><p>World.</p></body></html>"
    call_count = [0]

    def eating_translate(text: str) -> str:
        call_count[0] += 1
        # Remove separators — forces fallback
        result = text.replace("<z2m-sep/>", "").replace("<Z2M-SEP/>", "")
        return result.replace("Hello", "Привет").replace("World", "Мир")

    translated, _ = translate_html_text_nodes(html, translate_text=eating_translate)

    # Fallback: 2 per-segment calls (after 1 failed batch attempt)
    assert call_count[0] >= 2
    assert "Привет" in translated
    assert "Мир" in translated


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
