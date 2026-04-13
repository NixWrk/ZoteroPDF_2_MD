from __future__ import annotations

import base64
import mimetypes
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


_IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)(\2)', re.IGNORECASE)
_HEAD_OPEN_PATTERN = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_META_CHARSET_PATTERN = re.compile(r"<meta\s+charset\s*=\s*['\"]?utf-8['\"]?\s*/?>", re.IGNORECASE)
_HTML_OPEN_PATTERN = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_BODY_PATTERN = re.compile(r"(<body\b[^>]*>)(.*?)(</body>)", re.IGNORECASE | re.DOTALL)
_TAG_SPLIT_PATTERN = re.compile(r"(<[^>]+>)")
_OPEN_TAG_PATTERN = re.compile(r"^<\s*([a-zA-Z0-9:_-]+)")
_CLOSE_TAG_PATTERN = re.compile(r"^<\s*/\s*([a-zA-Z0-9:_-]+)")
_ESCAPED_INLINE_TAG_PATTERN = re.compile(r"&lt;(/?)(sup|sub)&gt;", re.IGNORECASE)
_SPACED_INLINE_TAG_PATTERN = re.compile(r"<\s*(/?)\s*(sup|sub)\s*>", re.IGNORECASE)
_EMPTY_PARAGRAPH_PATTERN = re.compile(r"<p>\s*(?:&nbsp;|\u00a0)?\s*</p>", re.IGNORECASE)
_EXCESSIVE_BREAKS_PATTERN = re.compile(r"(?:<br\s*/?>\s*){4,}", re.IGNORECASE)
_URL_PATTERN = re.compile(r"(?P<url>(?:https?://|www\.)[^\s<>\"]+)", re.IGNORECASE)
_REFERENCES_HEADING_PATTERN = re.compile(
    r"<h([1-6])\b[^>]*>\s*(?:<[^>]+>\s*)*"
    r"(?:References|Bibliography|Литература|Список литературы|Источники|Referenzen|参考文献|参考资料)"
    r"\s*(?:</[^>]+>\s*)*</h\1>",
    re.IGNORECASE | re.DOTALL,
)
_LI_OPEN_PATTERN = re.compile(r"<li\b([^>]*)>", re.IGNORECASE)
_LI_BLOCK_PATTERN = re.compile(r"<li\b([^>]*)>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_LI_ID_PATTERN = re.compile(r'\bid\s*=\s*["\']ref-(\d+)["\']', re.IGNORECASE)
_SUP_PATTERN = re.compile(r"<sup>(.*?)</sup>", re.IGNORECASE | re.DOTALL)
_SUP_NUMBER_PATTERN = re.compile(r"\d+")
_BRACKET_CITATION_PATTERN = re.compile(r'(?<!\\)\[(\d+)\]')
_SKIP_AUTOLINK_TAGS = {"script", "style", "code", "pre", "math", "svg", "a"}
_LEADING_REF_NUMBER_PATTERN = re.compile(r"^\s*(?:<[^>]+>\s*)*\d+\.\s+", re.IGNORECASE)
_BRACKET_REF_NUM_STRIP_PATTERN = re.compile(r'^\s*\[(\d+)\]\s*')
_MATH_TAG_PATTERN = re.compile(r"<math(\b[^>]*)>(.*?)</math>", re.IGNORECASE | re.DOTALL)
_EQUATION_PARA_PATTERN = re.compile(
    r'(<p\b[^>]*block-type="Equation"[^>]*>)(.*?)(</p>)',
    re.IGNORECASE | re.DOTALL,
)
_DISPLAY_MATH_IN_PARA_PATTERN = re.compile(r'\\\[(.*?)\\\]', re.DOTALL)
_TRAILING_EQ_NUM_PATTERN = re.compile(r'\(\d+\)\s*$')
_LATEX_TAG_PATTERN = re.compile(r'\\tag\{(\d+)\}')
_SLASH_PIPE_ARTIFACT_PATTERN = re.compile(r"\s*\\\s*\|\s*\\\s*")
# SentencePiece byte-fallback tokens emitted by Gemma when it encounters Unicode
# near translation boundaries: e.g. <0xE2><0x82><0xA9> instead of a real character.
# When followed by citation numbers they represent a dropped <sup> tag.
_BYTE_TOKEN_ARTIFACT_PATTERN = re.compile(r'(?:<0x[0-9A-Fa-f]{2}>)+')
_BYTE_TOKEN_CITATION_PATTERN = re.compile(r'(?:<0x[0-9A-Fa-f]{2}>)+(\d[\d,\u2013\u2014\-]*)')
# Bare citation numbers glued to words: "issues17,68" or "potential69,70"
# Marker sometimes fails to recognise superscript and dumps citations as body text.
# We look for: word-char immediately followed by 2+ numbers separated by commas,
# where none of the numbers exceeds a reasonable reference count (≤999).
_BARE_CITATION_PATTERN = re.compile(
    r'(?<=[A-Za-zА-Яа-яёЁ])(\d{1,3}(?:,\s?\d{1,3})+)(?=[\s.,;:!?)<\]]|$)'
)
_LEADING_SPACED_BACKSLASH_PATTERN = re.compile(r"(^|\s)\\\s+")
_TRAILING_SPACED_BACKSLASH_PATTERN = re.compile(r"\s+\\(?=\s|$)")
_BROKEN_URL_SPLIT_PATTERN = re.compile(
    r"((?:https?://|www\.)[^\s<>\"]+?/)\s+([A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)",
    re.IGNORECASE,
)

_LATEX_LABEL_PATTERN = re.compile(r"\\label\{[^{}]*\}")
_LATEX_TEXTBF_PATTERN = re.compile(r"\\textbf\{([^{}]*)\}")
_LATEX_ITALIC_PATTERN = re.compile(r"\\(?:textit|emph)\{([^{}]*)\}")
_LATEX_TEXTRM_PATTERN = re.compile(r"\\textrm\{([^{}]*)\}")
_LATEX_TEXT_PATTERN = re.compile(r"\\text\{([^{}]*)\}")

# Matches a phrase of 2-7 words repeated 2+ additional times back-to-back.
# Example: "the property of the property of the property of" → "the property of"
_REPEATED_PHRASE_PATTERN = re.compile(
    r"\b((?:\w+\s+){2,7}\w+)(?:\s+\1){2,}",
    re.IGNORECASE,
)

_MATHJAX_SCRIPT = (
    '<script>'
    'MathJax={'
    'tex:{inlineMath:[["$","$"],["\\\\(","\\\\)"]],displayMath:[["$$","$$"],["\\\\[","\\\\]"]]},'
    'svg:{fontCache:"global"}'
    '};'
    '</script>\n'
    '<script id="MathJax-script" async '
    'src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>'
)

_DEFAULT_READABILITY_STYLE = """
<style data-z2m-style="readable">
  :root { color-scheme: light; }
  body {
    margin: 0;
    padding: 22px;
    font-family: "Segoe UI", "Arial", sans-serif;
    line-height: 1.62;
    color: #1f2937;
    background: linear-gradient(180deg, #f5f8fb 0%, #edf2f7 100%);
  }
  #marker-doc {
    max-width: 980px;
    margin: 0 auto;
    background: #ffffff;
    border: 1px solid #dbe5ef;
    border-radius: 12px;
    box-shadow: 0 8px 22px rgba(15, 23, 42, 0.08);
    padding: 30px 36px;
  }
  h1, h2, h3, h4, h5, h6 {
    color: #0f172a;
    line-height: 1.28;
    margin-top: 1.15em;
    margin-bottom: 0.5em;
  }
  p {
    margin: 0.6em 0;
    word-break: break-word;
  }
  a {
    color: #0b57d0;
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  a:hover {
    color: #1d4ed8;
  }
  .z2m-ref-link {
    text-decoration: none;
  }
  .z2m-ref-num {
    font-weight: 600;
    margin-right: 0.3em;
  }
  ul, ol { margin: 0.65em 0 0.75em 1.3em; }
  li { margin: 0.28em 0; }
  blockquote {
    margin: 0.9em 0;
    padding: 0.55em 0.9em;
    border-left: 4px solid #60a5fa;
    background: #f8fbff;
    color: #0b355c;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 1.1em 0;
    font-size: 0.96rem;
  }
  th, td {
    border: 1px solid #dbe3ec;
    padding: 0.45em 0.58em;
    vertical-align: top;
  }
  th {
    background: #f4f8fc;
    font-weight: 600;
  }
  pre, code {
    font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
    font-size: 0.93em;
  }
  pre {
    background: #f7fafc;
    border: 1px solid #dbe3ec;
    border-radius: 8px;
    padding: 0.85em 0.95em;
    overflow-x: auto;
  }
  code {
    background: #f3f7fb;
    border-radius: 4px;
    padding: 0.08em 0.25em;
  }
  img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 0.9em auto;
    border: 1px solid #d9e0e7;
    border-radius: 6px;
  }
  math[display="block"] {
    overflow-x: auto;
    display: block;
    margin: 0.6em 0;
  }
  math {
    overflow-x: auto;
  }
  p[block-type="Equation"] {
    text-align: center;
    margin: 0.8em 0;
  }
  .z2m-equation-row {
    display: flex;
    align-items: center;
    margin: 0.8em 0;
  }
  .z2m-equation-row > p[block-type="Equation"] {
    flex: 1;
    margin: 0;
    padding: 0;
  }
  .z2m-eq-lhs,
  .z2m-eq-num {
    flex: 0 0 3.5em;
    font-size: 0.92em;
    color: #374151;
  }
  .z2m-eq-num { text-align: right; }
  @media (max-width: 960px) {
    body { padding: 10px; }
    #marker-doc { padding: 16px 15px; border-radius: 8px; }
    table { display: block; overflow-x: auto; white-space: nowrap; }
  }
</style>
""".strip()

_MOJIBAKE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("вЂ”", "—"),
    ("вЂ“", "–"),
    ("вЂ™", "’"),
    ("вЂњ", "“"),
    ("вЂќ", "”"),
    ("В©", "©"),
)


@dataclass(frozen=True)
class InlineHtmlResult:
    html: str
    inlined_images: int


def _is_inline_or_remote(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("data:")
        or lowered.startswith("mailto:")
        or lowered.startswith("#")
        or lowered.startswith("javascript:")
    )


def _to_data_url(file_path: Path) -> str | None:
    mime, _ = mimetypes.guess_type(file_path.name)
    if not mime or not mime.startswith("image/"):
        return None
    blob = file_path.read_bytes()
    encoded = base64.b64encode(blob).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _inject_default_styles(html: str) -> str:
    if 'data-z2m-style="readable"' in html:
        return html

    if _HEAD_CLOSE_PATTERN.search(html):
        return _HEAD_CLOSE_PATTERN.sub(f"{_DEFAULT_READABILITY_STYLE}\n</head>", html, count=1)

    if _HTML_OPEN_PATTERN.search(html):
        return _HTML_OPEN_PATTERN.sub(
            lambda m: f"{m.group(0)}\n<head>\n{_DEFAULT_READABILITY_STYLE}\n</head>",
            html,
            count=1,
        )

    return f"<head>\n{_DEFAULT_READABILITY_STYLE}\n</head>\n{html}"


def _inject_utf8_charset(html: str) -> str:
    if _META_CHARSET_PATTERN.search(html):
        return html

    if _HEAD_OPEN_PATTERN.search(html):
        return _HEAD_OPEN_PATTERN.sub(
            lambda m: f'{m.group(0)}\n<meta charset="utf-8">',
            html,
            count=1,
        )

    if _HTML_OPEN_PATTERN.search(html):
        return _HTML_OPEN_PATTERN.sub(
            lambda m: f'{m.group(0)}\n<head>\n<meta charset="utf-8">\n</head>',
            html,
            count=1,
        )

    return f'<head>\n<meta charset="utf-8">\n</head>\n{html}'


def _wrap_body_in_container(html: str) -> str:
    if 'id="marker-doc"' in html:
        return html

    def replace(match: re.Match[str]) -> str:
        body_open, body_inner, body_close = match.groups()
        return f'{body_open}\n  <main id="marker-doc">\n{body_inner}\n  </main>\n{body_close}'

    return _BODY_PATTERN.sub(replace, html, count=1)


def drop_repeated_phrases(text: str) -> str:
    """Collapse runs where a phrase of 3–8 words repeats 3+ times consecutively.

    Works on plain text and HTML alike (the pattern only matches word sequences,
    so it never fires inside tag attributes or markup).  Iterates until stable to
    handle nested / chained repetitions.
    """
    prev = None
    result = text
    while result != prev:
        prev = result
        result = _REPEATED_PHRASE_PATTERN.sub(r"\1", result)
    return result


def _fix_latex_text_commands(html: str) -> str:
    html = _LATEX_LABEL_PATTERN.sub("", html)
    html = _LATEX_TEXTBF_PATTERN.sub(r"<strong>\1</strong>", html)
    html = _LATEX_ITALIC_PATTERN.sub(r"<em>\1</em>", html)
    html = _LATEX_TEXTRM_PATTERN.sub(r"\1", html)
    html = _LATEX_TEXT_PATTERN.sub(r"\1", html)
    return html


def _inject_mathjax(html: str) -> str:
    if 'MathJax-script' in html:
        # Replace whatever MathJax was injected (e.g. by Marker with wrong delimiters)
        # with our correctly-configured version.
        html = re.sub(
            r'<script[^>]*id="MathJax-script"[^>]*/?>.*?(?:</script>)?',
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = re.sub(
            r'<script[^>]*>[^<]*MathJax\s*=[^<]*</script>',
            "",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if not _HEAD_CLOSE_PATTERN.search(html):
        html = _inject_default_styles(html)
    # Use a lambda so re.sub does NOT process backslashes in the replacement string.
    return _HEAD_CLOSE_PATTERN.sub(lambda _: f"{_MATHJAX_SCRIPT}\n</head>", html, count=1)


def _unescape_inline_sup_sub(html: str) -> str:
    return _ESCAPED_INLINE_TAG_PATTERN.sub(r"<\1\2>", html)


def _normalize_spaced_inline_sup_sub_tags(html: str) -> str:
    def replace(match: re.Match[str]) -> str:
        slash = match.group(1) or ""
        tag = (match.group(2) or "").lower()
        return f"<{slash}{tag}>"

    return _SPACED_INLINE_TAG_PATTERN.sub(replace, html)


def _fix_common_mojibake(html: str) -> str:
    fixed = html
    for bad, good in _MOJIBAKE_REPLACEMENTS:
        fixed = fixed.replace(bad, good)
    return fixed


def _cleanup_marker_escape_artifacts(html: str) -> str:
    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    for part in parts:
        if not part:
            continue
        if part.startswith("<"):
            _update_skip_stack(part, skip_stack)
            out.append(part)
            continue
        if skip_stack:
            out.append(part)
            continue
        cleaned = _SLASH_PIPE_ARTIFACT_PATTERN.sub(" | ", part)
        cleaned = _LEADING_SPACED_BACKSLASH_PATTERN.sub(r"\1", cleaned)
        cleaned = _TRAILING_SPACED_BACKSLASH_PATTERN.sub(" ", cleaned)
        out.append(cleaned)

    return "".join(out)


def _update_skip_stack(tag_fragment: str, skip_stack: list[str]) -> None:
    raw = tag_fragment.strip()
    if not raw.startswith("<") or raw.startswith("<!--") or raw.startswith("<!"):
        return

    close_match = _CLOSE_TAG_PATTERN.match(raw)
    if close_match is not None:
        tag_name = close_match.group(1).lower()
        for idx in range(len(skip_stack) - 1, -1, -1):
            if skip_stack[idx] == tag_name:
                del skip_stack[idx]
                break
        return

    if raw.endswith("/>"):
        return

    open_match = _OPEN_TAG_PATTERN.match(raw)
    if open_match is None:
        return
    tag_name = open_match.group(1).lower()
    if tag_name in _SKIP_AUTOLINK_TAGS:
        skip_stack.append(tag_name)


def _split_url_and_trailing_punct(url: str) -> tuple[str, str]:
    core = url
    trailing = ""

    while core and core[-1] in ".,;:!?":
        trailing = core[-1] + trailing
        core = core[:-1]

    while core.endswith(")") and core.count("(") < core.count(")"):
        trailing = ")" + trailing
        core = core[:-1]

    return core, trailing


def _autolink_text_urls(text: str) -> str:
    repaired_text = _BROKEN_URL_SPLIT_PATTERN.sub(r"\1\2", text)

    def replace(match: re.Match[str]) -> str:
        raw_url = match.group("url")
        core_url, trailing = _split_url_and_trailing_punct(raw_url)
        if not core_url:
            return raw_url

        href = core_url
        if core_url.lower().startswith("www."):
            href = f"https://{core_url}"

        return (
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">{core_url}</a>'
            f"{trailing}"
        )

    return _URL_PATTERN.sub(replace, repaired_text)


def _autolink_plain_urls(html: str) -> str:
    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    for part in parts:
        if not part:
            continue
        if part.startswith("<"):
            _update_skip_stack(part, skip_stack)
            out.append(part)
            continue
        if skip_stack:
            out.append(part)
            continue
        out.append(_autolink_text_urls(part))

    return "".join(out)


def _link_bracket_citations(html: str, ref_count: int) -> str:
    """Wrap [N] citation markers with anchor links to #ref-N in text nodes.

    Skips text inside tags that should not be modified (scripts, math, existing
    anchors, etc.).  Only links numbers in the range [1, ref_count].
    """
    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    def replace_bracket(match: re.Match[str]) -> str:
        try:
            number = int(match.group(1))
        except ValueError:
            return match.group(0)
        if 1 <= number <= ref_count:
            return f'<a href="#ref-{number}" class="z2m-ref-link">[{number}]</a>'
        return match.group(0)

    for part in parts:
        if not part:
            continue
        if part.startswith("<"):
            _update_skip_stack(part, skip_stack)
            out.append(part)
            continue
        if skip_stack:
            out.append(part)
            continue
        out.append(_BRACKET_CITATION_PATTERN.sub(replace_bracket, part))

    return "".join(out)


def _recover_bare_citations(html: str, ref_count: int) -> str:
    """Wrap bare citation numbers glued to words in ``<sup>`` tags.

    Marker sometimes fails to detect superscript formatting and produces
    ``issues17,68`` instead of ``issues<sup>17,68</sup>``.  We match
    comma-separated number groups directly following a letter and wrap them
    only when *every* number falls within [1, ref_count].

    Skips content inside tags that should not be modified.
    """
    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    def _wrap(m: re.Match[str]) -> str:
        nums_text = m.group(1)
        nums = [n.strip() for n in nums_text.split(",")]
        try:
            if all(1 <= int(n) <= ref_count for n in nums):
                return f"<sup>{nums_text}</sup>"
        except ValueError:
            pass
        return m.group(0)

    for part in parts:
        if not part:
            continue
        if part.startswith("<"):
            _update_skip_stack(part, skip_stack)
            out.append(part)
            continue
        if skip_stack:
            out.append(part)
            continue
        out.append(_BARE_CITATION_PATTERN.sub(_wrap, part))

    return "".join(out)


def _add_reference_ids_and_citation_links(html: str) -> str:
    heading_match = _REFERENCES_HEADING_PATTERN.search(html)
    if heading_match is None:
        return html

    split_at = heading_match.end()
    before_references = html[:split_at]
    references_and_after = html[split_at:]

    ref_index = 0

    def add_li_id(match: re.Match[str]) -> str:
        nonlocal ref_index
        attrs = match.group(1) or ""
        if re.search(r"\bid\s*=", attrs, re.IGNORECASE):
            return match.group(0)
        ref_index += 1
        return f'<li{attrs} id="ref-{ref_index}">'

    references_with_ids = _LI_OPEN_PATTERN.sub(add_li_id, references_and_after)
    if ref_index == 0:
        return html

    if not re.search(r"<ol\b", references_with_ids, re.IGNORECASE):
        def ensure_visible_ref_number(match: re.Match[str]) -> str:
            attrs = match.group(1) or ""
            body = match.group(2) or ""
            id_match = _LI_ID_PATTERN.search(attrs)
            if id_match is None:
                return match.group(0)
            # Strip leading "[N]" bracket number (IEEE/Vancouver style) to avoid
            # "1. [1] Author..." double-numbering.
            body = _BRACKET_REF_NUM_STRIP_PATTERN.sub("", body)
            # If Marker already wrote "N. Author..." wrap that N. in the span for
            # consistent bold styling instead of leaving it unstyled.
            if _LEADING_REF_NUMBER_PATTERN.search(body):
                body = re.sub(
                    r'^(\s*(?:<[^>]+>\s*)*?)(\d+\.)\s+',
                    lambda m: f'{m.group(1)}<span class="z2m-ref-num">{m.group(2)}</span> ',
                    body,
                )
                return f"<li{attrs}>{body}</li>"
            number = id_match.group(1)
            numbered_body = f'<span class="z2m-ref-num">{number}.</span> {body.lstrip()}'
            return f"<li{attrs}>{numbered_body}</li>"

        references_with_ids = _LI_BLOCK_PATTERN.sub(ensure_visible_ref_number, references_with_ids)

    def link_sup(match: re.Match[str]) -> str:
        inner = match.group(1)
        if "<a " in inner.lower():
            return match.group(0)

        def replace_number(num_match: re.Match[str]) -> str:
            number_text = num_match.group(0)
            try:
                number = int(number_text)
            except ValueError:
                return number_text
            if 1 <= number <= ref_index:
                return f'<a href="#ref-{number}" class="z2m-ref-link">{number_text}</a>'
            return number_text

        linked_inner = _SUP_NUMBER_PATTERN.sub(replace_number, inner)
        return f"<sup>{linked_inner}</sup>"

    # Recover bare citations: "issues17,68" → "issues<sup>17,68</sup>"
    before_references = _recover_bare_citations(before_references, ref_index)

    # Link <sup>N</sup> citations first, then [N] bracket-style citations.
    before_with_citation_links = _SUP_PATTERN.sub(link_sup, before_references)
    before_with_citation_links = _link_bracket_citations(before_with_citation_links, ref_index)
    return before_with_citation_links + references_with_ids


def _cleanup_empty_html_blocks(html: str) -> str:
    cleaned = _EMPTY_PARAGRAPH_PATTERN.sub("", html)
    cleaned = _EXCESSIVE_BREAKS_PATTERN.sub("<br><br>", cleaned)
    return cleaned


def _fix_equation_display(html: str) -> str:
    """Fix two Marker equation-paragraph rendering issues in ``<p block-type="Equation">``.

    1. Equation numbers like ``(1)`` that are plain text nodes after ``\\[...\\]``
       inside the paragraph are wrapped in ``<span class="z2m-eq-num">`` so CSS
       can position them flush-right while the formula stays centred.

    2. ``\\[...\\]`` that appears mid-sentence (paragraph has surrounding text
       beyond the equation number) is demoted to inline ``\\(...\\)`` so MathJax
       does not force a block-level line break in the middle of prose.
    """
    def fix_para(m: re.Match[str]) -> str:
        open_tag, body, close_tag = m.group(1), m.group(2), m.group(3)

        # Check if there is real prose text around the display math.
        # Strip \[...\], equation numbers (N), and \tag{N}; if text remains
        # this is "text-with-math" and the display math should become inline.
        stripped = _DISPLAY_MATH_IN_PARA_PATTERN.sub("", body)
        stripped = re.sub(r"\(\d+\)", "", stripped)
        stripped = stripped.strip()
        if stripped:
            # Demote block math to inline so it flows with the prose.
            body = _DISPLAY_MATH_IN_PARA_PATTERN.sub(
                lambda bm: f"\\({bm.group(1)}\\)", body
            )
            return f"{open_tag}{body}{close_tag}"

        # Pure equation paragraph: extract the equation number and wrap in a flex
        # row so the number sits flush-right regardless of how MathJax renders.
        # Two sources of equation numbers:
        #   a) \tag{N} inside the LaTeX itself (Marker embeds the tag in the math)
        #   b) Plain text "(N)" following the \[...\] block
        body_rstripped = body.rstrip()

        # Check for \tag{N} inside any \[...\] block and strip it out so MathJax
        # does not render it (we show the number via our own z2m-eq-num span).
        def _strip_tag(math_match: re.Match[str]) -> tuple[str, str | None]:
            content = math_match.group(0)
            tag_m = _LATEX_TAG_PATTERN.search(content)
            if tag_m:
                num = tag_m.group(1)
                content_no_tag = _LATEX_TAG_PATTERN.sub("", content).rstrip()
                return content_no_tag, f"({num})"
            return content, None

        tag_num: str | None = None
        new_body_parts: list[str] = []
        last = 0
        for dm in _DISPLAY_MATH_IN_PARA_PATTERN.finditer(body_rstripped):
            new_body_parts.append(body_rstripped[last : dm.start()])
            cleaned, found_num = _strip_tag(dm)
            new_body_parts.append(cleaned)
            if found_num and tag_num is None:
                tag_num = found_num
            last = dm.end()
        new_body_parts.append(body_rstripped[last:])
        body_no_tag = "".join(new_body_parts).rstrip()

        if tag_num:
            return (
                f'<div class="z2m-equation-row">'
                f'<span class="z2m-eq-lhs"></span>'
                f"{open_tag}{body_no_tag}{close_tag}"
                f'<span class="z2m-eq-num">{tag_num}</span>'
                f"</div>"
            )

        # Fall back to trailing "(N)" text
        eq_num_match = _TRAILING_EQ_NUM_PATTERN.search(body_rstripped)
        if eq_num_match:
            num_text = eq_num_match.group(0).strip()
            body_no_num = body_rstripped[: eq_num_match.start()].rstrip()
            return (
                f'<div class="z2m-equation-row">'
                f'<span class="z2m-eq-lhs"></span>'
                f"{open_tag}{body_no_num}{close_tag}"
                f'<span class="z2m-eq-num">{num_text}</span>'
                f"</div>"
            )

        return m.group(0)

    return _EQUATION_PARA_PATTERN.sub(fix_para, html)


def _convert_math_tags_to_tex(html: str) -> str:
    """Convert <math> HTML elements that contain raw LaTeX into MathJax-renderable
    delimiters: ``\\[...\\]`` for block and ``\\(...\\)`` for inline math.

    Real MathML (content with child XML elements) is left untouched.
    """

    def replace_math(match: re.Match[str]) -> str:
        attrs = match.group(1)
        content = match.group(2).strip()
        if not content:
            return ""  # empty math element — drop it
        # Content that contains XML child tags is real MathML — leave as-is.
        if re.search(r"<[a-zA-Z]", content):
            return match.group(0)
        is_block = bool(
            re.search(r'\bdisplay\s*=\s*["\']block["\']', attrs, re.IGNORECASE)
        )
        if is_block:
            return f"\\[{content}\\]"
        return f"\\({content}\\)"

    return _MATH_TAG_PATTERN.sub(replace_math, html)


def polish_html_document(html: str) -> str:
    polished = drop_repeated_phrases(html)
    polished = _fix_latex_text_commands(polished)
    polished = _unescape_inline_sup_sub(polished)
    polished = _normalize_spaced_inline_sup_sub_tags(polished)
    polished = _fix_common_mojibake(polished)
    polished = _BYTE_TOKEN_CITATION_PATTERN.sub(r'<sup>\1</sup>', polished)
    polished = _BYTE_TOKEN_ARTIFACT_PATTERN.sub("", polished)
    polished = _cleanup_marker_escape_artifacts(polished)
    polished = _fix_equation_display(polished)
    polished = _convert_math_tags_to_tex(polished)
    polished = _add_reference_ids_and_citation_links(polished)
    polished = _autolink_plain_urls(polished)
    polished = _inject_utf8_charset(polished)
    polished = _inject_default_styles(polished)
    polished = _inject_mathjax(polished)
    polished = _wrap_body_in_container(polished)
    polished = _cleanup_empty_html_blocks(polished)
    return polished


def inline_images_from_html_file(html_path: Path) -> InlineHtmlResult:
    text = html_path.read_text(encoding="utf-8", errors="replace")
    base_dir = html_path.parent
    inlined_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal inlined_count
        prefix = match.group(1)
        quote = match.group(2)
        src_value = match.group(3).strip()
        suffix = match.group(4)

        if not src_value or _is_inline_or_remote(src_value):
            return match.group(0)

        clean_path = src_value.split("?", 1)[0].split("#", 1)[0]
        decoded = urllib.parse.unquote(clean_path)
        candidate = (base_dir / decoded).resolve(strict=False)
        if not candidate.is_file():
            return match.group(0)

        data_url = _to_data_url(candidate)
        if data_url is None:
            return match.group(0)

        inlined_count += 1
        return f"{prefix}{quote}{data_url}{suffix}"

    inlined_html = _IMG_SRC_PATTERN.sub(replace, text)
    inlined_html = polish_html_document(inlined_html)
    return InlineHtmlResult(html=inlined_html, inlined_images=inlined_count)
