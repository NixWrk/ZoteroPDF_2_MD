from pathlib import Path

import pytest

from zoteropdf2md.translategemma import (
    _split_references_tail,
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


def test_split_references_tail_detects_and_splits_references_section() -> None:
    html = (
        "<html><body>"
        "<p>Body text.</p>"
        "<h4>References</h4>"
        "<ul><li>One</li></ul>"
        "</body></html>"
    )
    head, tail = _split_references_tail(html)
    assert "<p>Body text.</p>" in head
    assert "<h4>References</h4>" not in head
    assert tail.startswith("<h4>References</h4>")


def test_split_references_tail_returns_full_html_when_absent() -> None:
    html = "<html><body><p>No refs here.</p></body></html>"
    head, tail = _split_references_tail(html)
    assert head == html
    assert tail == ""
