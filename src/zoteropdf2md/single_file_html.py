from __future__ import annotations

import base64
import mimetypes
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .abbreviations import RU_ABBREV_TO_LATIN


_IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)(\2)', re.IGNORECASE)
_HEAD_OPEN_PATTERN = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_META_CHARSET_PATTERN = re.compile(r"<meta\s+charset\s*=\s*['\"]?utf-8['\"]?\s*/?>", re.IGNORECASE)
_HTML_OPEN_PATTERN = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_READABILITY_STYLE_BLOCK_PATTERN = re.compile(
    r"<style\b[^>]*\bdata-z2m-style\s*=\s*['\"]readable['\"][^>]*>[\s\S]*?</style>",
    re.IGNORECASE,
)
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
_SUP_PATTERN = re.compile(r"<sup\b[^>]*>(.*?)</sup>", re.IGNORECASE | re.DOTALL)
_SUP_NUMBER_PATTERN = re.compile(r"\d+")
_BRACKET_CITATION_PATTERN = re.compile(r'(?<!\\)\[(\d+)\]')
# Parenthetical reference: "(ref. 30)" / "(ref 30)" / "(см. 30)" → sup link
_PAREN_REF_CITATION_PATTERN = re.compile(
    r'\((?:ref|см|see)\.?\s*(\d{1,3})\)',
    re.IGNORECASE,
)
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
# Marker sometimes emits citation superscripts as MathJax inline math:
# \(^{157}\) or \(^{153-156}\) instead of <sup>157</sup>.
# Capture the content inside \(^{...}\) so it can be promoted to <sup>.
_LATEX_SUP_CITATION_PATTERN = re.compile(
    r'\\\(\^\{([\d,\s\-\u2013\u2014]+)\}\\\)',
)
# Heading translation artefact: the model inserts a period before an inline-
# formatted abbreviation: "беспроводного. <i>LC</i> Датчик" inside <h1>-<h6>.
_HEADING_TAG_PATTERN = re.compile(
    r'(<h[1-6]\b[^>]*>)(.*?)(</h[1-6]>)',
    re.IGNORECASE | re.DOTALL,
)
_HEADING_PERIOD_BEFORE_ABBREV_PATTERN = re.compile(
    r'\.\s+(<(?:i|em|b|strong)\b[^>]*>\s*[A-Z]{2,})',
    re.IGNORECASE,
)
_HEADING_ACRONYM_SENSOR_PATTERN = re.compile(
    r'(<(i|em|b|strong)\b[^>]*>\s*[A-Z0-9]{2,8}\s*</\2>)\s+датчик\b',
    re.IGNORECASE,
)
_Z2M_LINK_GLUE_PATTERN = re.compile(
    r'(<a\b[^>]*\bclass\s*=\s*["\']z2m-(?:ref|fig|section)-link["\'][^>]*>[\s\S]*?</a>)(?=[A-Za-zА-Яа-яЁё])',
    re.IGNORECASE,
)
_SLASH_PIPE_ARTIFACT_PATTERN = re.compile(r"\s*\\\s*\|\s*\\\s*")
# SentencePiece byte-fallback tokens emitted by Gemma when it encounters Unicode
# near translation boundaries: e.g. <0xE2><0x82><0xA9> instead of a real character.
# When followed by citation numbers they represent a dropped <sup> tag.
_BYTE_TOKEN_ARTIFACT_PATTERN = re.compile(r'(?:<0x[0-9A-Fa-f]{2}>)+')
_BYTE_TOKEN_CITATION_PATTERN = re.compile(r'(?:<0x[0-9A-Fa-f]{2}>)+(\d[\d,\u2013\u2014\-]*)')
# Bare citation numbers that Marker failed to mark as superscript.
# Two variants:
#   Glued  — number immediately follows letter: "issues17,68"
#   Spaced — single space before citation group: "issues 17,68."
#            (only allowed before sentence-ending punctuation to reduce false positives)
# Numbers are only wrapped when ALL of them fall within [1, ref_count].
_BARE_CITATION_GLUED_PATTERN = re.compile(
    r'(?<=[A-Za-zА-Яа-яёЁ])(\d{1,3}(?:,\s?\d{1,3})+)(?=[\s.,;:!?)<\]]|$)'
)
_BARE_CITATION_SPACED_PATTERN = re.compile(
    r'(?<=[A-Za-zА-Яа-яёЁ]) (\d{1,3}(?:,\d{1,3})+)(?=[.,;:!?)<\]]|$)'
)
# Dot-separated citations: OCR artefact where Marker writes "17.68" instead of "17,68"
# Only triggered when ALL numbers are within ref_count and the sequence immediately
# follows a letter (no space), to minimise collisions with decimal numbers.
_BARE_CITATION_DOT_PATTERN = re.compile(
    r'(?<=[A-Za-zА-Яа-яёЁ])(\d{1,3}(?:\.\d{1,3})+)(?=[\s.,;:!?)<\]]|$)'
)
# Section headings that start with a Roman numeral (I. INTRODUCTION, II. METHOD …)
_ROMAN_SECTION_HEADING_PATTERN = re.compile(
    r'<(h[1-6])(\b[^>]*)>\s*([IVX]{1,6})\.\s',
    re.IGNORECASE,
)
# In-text references to section numbers (English or Russian).
# Russian case forms: Раздел (nominative/accusative), Раздела (genitive),
# Разделе (locative), Разделу (dative) — all captured by the suffix group.
_SECTION_REF_PATTERN = re.compile(
    r'\b(Section|Раздел[еауо]?)\s+([IVX]{1,6})\b',
    re.IGNORECASE,
)
# Figure/image caption paragraphs: <p …>Fig. 3. Some caption text…
# Russian equivalents: Рис. (standard) or Фиг. (sometimes emitted by translators)
_FIG_CAPTION_PARA_PATTERN = re.compile(
    r'(<p\b[^>]*)>([ \t\r\n]*(?:Fig|Рис|рис|Фиг|фиг|FIG)\.?\s*(\d+)\.)',
    re.IGNORECASE,
)
# In-text figure references: "Fig. 3" / "рис. 3" / "фиг. 3" NOT followed by ". <text>"
# (that would be a figure caption).  We distinguish "Fig. 3. Caption..." from "...Fig. 3."
# (end of sentence) by requiring whitespace after the dot, i.e. ".\s" → caption lookahead.
_FIG_REF_PATTERN = re.compile(
    r'\b((?:Fig|Рис|рис|Фиг|фиг|FIG)\.?)\s*(\d+)\b(?!\s*\.\s)',
    re.IGNORECASE,
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_FIGURE_GAP_PARA_PATTERN = (
    r"<p\b[^>]*>\s*"
    r"(?:(?:<img\b)|(?:<(?:strong|em|b|i)\b[^>]*>\s*)*(?:Fig(?:ure)?|FIG)\.?\s*\d\b)"
    r"[\s\S]*?</p>"
)
_FIGURE_GAP_BLOCK_PATTERN = rf"(?:<figure\b[\s\S]*?</figure>|{_FIGURE_GAP_PARA_PATTERN})"
_SENTENCE_SPLIT_BY_FIGURE_PATTERN = re.compile(
    r'(?P<left_open><p\b[^>]*>)(?P<left_body>[\s\S]*?)(?P<left_close></p>)'
    rf'(?P<middle>\s*{_FIGURE_GAP_BLOCK_PATTERN}\s*)'
    r'(?P<right_open><p\b[^>]*>)(?P<right_body>[\s\S]*?)(?P<right_close></p>)',
    re.IGNORECASE,
)
_SENTENCE_SPLIT_BY_DOUBLE_FIGURE_GAP_PATTERN = re.compile(
    r'(?P<left_open><p\b[^>]*>)(?P<left_body>[\s\S]*?)(?P<left_close></p>)'
    rf'(?P<middle1>\s*{_FIGURE_GAP_BLOCK_PATTERN}\s*)'
    rf'(?P<middle2>\s*{_FIGURE_GAP_BLOCK_PATTERN}\s*)'
    r'(?P<right_open><p\b[^>]*>)(?P<right_body>[\s\S]*?)(?P<right_close></p>)',
    re.IGNORECASE,
)
_SENTENCE_GAP_BLOCK_PATTERN = re.compile(
    _FIGURE_GAP_BLOCK_PATTERN,
    re.IGNORECASE,
)
_SENTENCE_NODE_PATTERN = re.compile(
    r"<p\b[^>]*>[\s\S]*?</p>|<figure\b[\s\S]*?</figure>|<h[1-6]\b[^>]*>[\s\S]*?</h[1-6]>|<table\b[\s\S]*?</table>",
    re.IGNORECASE,
)
_SENTENCE_P_NODE_PATTERN = re.compile(
    r'^(?P<open><p\b[^>]*>)(?P<body>[\s\S]*)(?P<close></p>)$',
    re.IGNORECASE,
)
_SENTENCE_H_NODE_PATTERN = re.compile(
    r'^(?P<open><h[1-6]\b[^>]*>)(?P<body>[\s\S]*)(?P<close></h[1-6]>)$',
    re.IGNORECASE,
)
_TABLE_CAPTION_PARA_PATTERN = re.compile(
    r'(<p\b[^>]*>\s*)(TABLE|Таблица)\s+([IVXLCM\d]+)\s*[\.\-:]?\s*([^<]*?)(\s*</p>)',
    re.IGNORECASE,
)
_LEADING_SPACED_BACKSLASH_PATTERN = re.compile(r"(^|\s)\\\s+")
_TRAILING_SPACED_BACKSLASH_PATTERN = re.compile(r"\s+\\(?=\s|$)")
# Backslash immediately before a quote mark: word\" → word"  (Marker OCR artefact)
_BACKSLASH_BEFORE_QUOTE_PATTERN = re.compile(r'\\(["\'])')
# Marker OCR artefact: figure captions wrapped in <math display="inline"> instead
# of plain HTML.  A genuine <math> block never contains <strong>/<em>/<b>/<i> tags.
_SPURIOUS_MATH_CAPTION_PATTERN = re.compile(
    r'<math\b[^>]*>((?:(?!</math>).)*?<(?:strong|em|b|i)\b(?:(?!</math>).)*?)</math>',
    re.IGNORECASE | re.DOTALL,
)
# URL ending with a common English connector/preposition that OCR appended: wysa.com/and
_URL_TRAILING_CONNECTOR_RE = re.compile(
    r'^(.*/)(?:and|or|the|to|in|of|for|with|from|at|by|a|an)$',
    re.IGNORECASE,
)
# Bare single citation: "knowledge 67. Prompt" — number preceded by letter+space,
# followed by period + capital letter (new-sentence signal).
_BARE_CITATION_SINGLE_SPACED_PATTERN = re.compile(
    r'(?<=[A-Za-zА-Яа-яёЁ]) (\d{1,3})(?=\. [A-Z])'
)
# Dot-citation preceded by space: "issues 17.68." — spaced variant of the glued
# dot pattern.  Only fires when followed by sentence-end punctuation.
_BARE_CITATION_SPACED_DOT_PATTERN = re.compile(
    r'(?<=[A-Za-zА-Яа-яёЁ]) (\d{1,3}(?:\.\d{1,3})+)(?=[.,;:!?)<\]]|$)'
)
_BROKEN_URL_SPLIT_PATTERN = re.compile(
    r"((?:https?://|www\.)[^\s<>\"]+?/)\s+([A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*)",
    re.IGNORECASE,
)

# Quick-scan trigger: only run the subscript-spill fix when this substring exists.
_SUBSCRIPT_OPEN = re.compile(r'[_^]\{')
# Detect an = followed immediately by a "large" LaTeX command inside a subscript/
# superscript brace — this is the Marker OCR artefact where the equation continuation
# (e.g. =\frac{…}{…}) was accidentally included in the sub/superscript.
_SUBSCRIPT_SPILL_RE = re.compile(
    r'=\s*\\(?:frac|sqrt|sum|int|oint|prod|lim|sup|inf|max|min|sin|cos|tan|'
    r'exp|log|ln|left|right|bigl|bigr|Big|Bigl|Bigr|begin|end)\b'
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
    text-indent: 1.25em;
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
  .z2m-section-link,
  .z2m-fig-link {
    text-decoration: none;
    border-bottom: 1px dotted #0b57d0;
  }
  .z2m-section-link:hover,
  .z2m-fig-link:hover {
    border-bottom-style: solid;
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
    text-indent: 0;
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
    if _READABILITY_STYLE_BLOCK_PATTERN.search(html):
        return _READABILITY_STYLE_BLOCK_PATTERN.sub(_DEFAULT_READABILITY_STYLE, html, count=1)

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


def _fix_subscript_equation_spill(html: str) -> str:
    """Fix Marker OCR artefact where ``=\\frac{…}`` ends up inside a subscript/
    superscript brace, causing the fraction to render at sub/superscript size.

    Example::

        \\gamma_{ij=\\frac{2a_ib_j}{a_i^2+b_j^2}}
        →  \\gamma_{ij}=\\frac{2a_ib_j}{a_i^2+b_j^2}

    The fix is applied to ``_{…}`` and ``^{…}`` blocks whose content contains
    ``=\\frac`` (or another "large" LaTeX command) after the first identifier
    characters.  The closing brace is located by balanced-brace counting so
    deeply nested fractions are handled correctly.
    """
    if not _SUBSCRIPT_OPEN.search(html):
        return html

    out: list[str] = []
    i = 0
    n = len(html)

    while i < n:
        ch = html[i]
        # Look for _{ or ^{ only
        if ch not in ('_', '^') or i + 1 >= n or html[i + 1] != '{':
            out.append(ch)
            i += 1
            continue

        # Locate matching closing brace via brace-depth counting.
        brace_open = i + 1          # position of '{'
        content_start = i + 2       # first char inside braces
        depth = 0
        close_pos = -1
        for k in range(brace_open, n):
            if html[k] == '{':
                depth += 1
            elif html[k] == '}':
                depth -= 1
                if depth == 0:
                    close_pos = k
                    break

        if close_pos == -1:
            # Unmatched brace — copy as-is
            out.append(html[i])
            i += 1
            continue

        content = html[content_start:close_pos]

        spill = _SUBSCRIPT_SPILL_RE.search(content)
        if spill is None:
            # Normal subscript — copy verbatim
            out.append(html[i: close_pos + 1])
            i = close_pos + 1
            continue

        # Split: keep everything before '=' in the brace, move '=...' outside.
        eq_pos = spill.start()
        before_eq = content[:eq_pos]
        after_eq = content[eq_pos + 1:]   # skip the '='
        out.append(f'{ch}{{{before_eq}}}={after_eq}')
        i = close_pos + 1

    return "".join(out)


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
        cleaned = _BACKSLASH_BEFORE_QUOTE_PATTERN.sub(r"\1", cleaned)
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


def _unwrap_spurious_math_captions(html: str) -> str:
    """Unwrap <math> tags that contain HTML formatting — they are figure captions.

    Marker sometimes wraps figure captions in ``<math display="inline">`` by mistake.
    Real math never contains ``<strong>``, ``<em>``, ``<b>``, or ``<i>`` tags, so any
    ``<math>`` block that does is safe to unwrap so cleanup and translation can process it.
    """
    return _SPURIOUS_MATH_CAPTION_PATTERN.sub(r'\1', html)


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

        # Strip common English connectors that OCR incorrectly appended: wysa.com/and
        connector_match = _URL_TRAILING_CONNECTOR_RE.match(core_url)
        if connector_match:
            stripped = connector_match.group(1)
            # Put the connector word back as plain text after the link
            connector_word = core_url[len(stripped):]
            core_url = stripped
            trailing = connector_word + trailing

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


def _link_paren_ref_citations(html: str, ref_count: int) -> str:
    """Convert (ref. N) / (ref N) / (см. N) to superscript anchor links.

    Handles the artefact where Marker or the translator leaves parenthetical
    references like ``(ref. 30)`` as plain text instead of ``<sup>30</sup>``.
    Only links numbers in the range [1, ref_count].
    """
    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    def replace_paren_ref(match: re.Match[str]) -> str:
        try:
            number = int(match.group(1))
        except ValueError:
            return match.group(0)
        if 1 <= number <= ref_count:
            return f'<sup><a href="#ref-{number}" class="z2m-ref-link">{number}</a></sup>'
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
        out.append(_PAREN_REF_CITATION_PATTERN.sub(replace_paren_ref, part))

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
    """Wrap bare citation numbers in ``<sup>`` tags.

    Handles three Marker OCR failure modes:

    1. *Glued* — number immediately follows a letter: ``issues17,68``
    2. *Spaced* — space before the group, followed by punctuation: ``issues 17,68.``
    3. *Dot-separated* — Marker wrote commas as dots: ``issues17.68``

    Numbers are only wrapped when *every* individual number falls within
    ``[1, ref_count]`` to minimise false positives.

    Skips content inside tags that should not be modified (scripts, anchors, etc.).
    """
    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    def _valid_nums(nums_text: str, sep: str = ",") -> bool:
        try:
            return all(1 <= int(n.strip()) <= ref_count for n in nums_text.split(sep))
        except ValueError:
            return False

    def _wrap_glued(m: re.Match[str]) -> str:
        nums_text = m.group(1)
        return f"<sup>{nums_text}</sup>" if _valid_nums(nums_text) else m.group(0)

    def _wrap_spaced(m: re.Match[str]) -> str:
        nums_text = m.group(1)
        # Preserve the space before <sup>
        return f" <sup>{nums_text}</sup>" if _valid_nums(nums_text) else m.group(0)

    def _wrap_dot(m: re.Match[str]) -> str:
        nums_text = m.group(1)
        # Convert dots to commas so downstream link logic treats them uniformly
        nums_comma = nums_text.replace(".", ",")
        return f"<sup>{nums_comma}</sup>" if _valid_nums(nums_comma) else m.group(0)

    def _wrap_single_spaced(m: re.Match[str]) -> str:
        nums_text = m.group(1)
        return f" <sup>{nums_text}</sup>" if _valid_nums(nums_text) else m.group(0)

    def _wrap_spaced_dot(m: re.Match[str]) -> str:
        nums_text = m.group(1)
        nums_comma = nums_text.replace(".", ",")
        return f" <sup>{nums_comma}</sup>" if _valid_nums(nums_comma) else m.group(0)

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
        text = _BARE_CITATION_GLUED_PATTERN.sub(_wrap_glued, part)
        # Spaced-dot before single-spaced so "word 17.68." wins over "word 17."
        text = _BARE_CITATION_SPACED_DOT_PATTERN.sub(_wrap_spaced_dot, text)
        text = _BARE_CITATION_SPACED_PATTERN.sub(_wrap_spaced, text)
        text = _BARE_CITATION_SINGLE_SPACED_PATTERN.sub(_wrap_single_spaced, text)
        text = _BARE_CITATION_DOT_PATTERN.sub(_wrap_dot, text)
        out.append(text)

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
        ref_index += 1  # Always count every <li> so ref_index equals total refs
        if re.search(r"\bid\s*=", attrs, re.IGNORECASE):
            return match.group(0)  # Keep existing id= unchanged
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
            # Idempotency guard: if a z2m-ref-num span already exists this <li>
            # was processed in a previous polish pass — don't add another one.
            if 'class="z2m-ref-num"' in body:
                return f"<li{attrs}>{body}</li>"
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

    # Link <sup>N</sup> citations first, then [N] bracket-style, then (ref. N).
    before_with_citation_links = _SUP_PATTERN.sub(link_sup, before_references)
    before_with_citation_links = _link_bracket_citations(before_with_citation_links, ref_index)
    before_with_citation_links = _link_paren_ref_citations(before_with_citation_links, ref_index)
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


def _fix_orphaned_sup_tags(html: str) -> str:
    """Remove broken ``<sup>`` openers whose direct content starts with a period.

    The translator occasionally emits a spurious ``<sup>`` wrapper around body
    text, producing something like::

        …understudied <sup>. However, researchers apply models
        <sup><a href="#ref-5">5</a></sup> to many tasks.</sup>

    — where the entire following paragraph renders as superscript.

    **Strategy**: delete only the ``<sup>`` *opener* tag.  Any eventual
    ``</sup>`` that was meant to close it becomes an orphan — HTML5 parsers
    silently ignore orphan end tags.  Unclosed inner ``<sup>N`` citation tags
    are implicitly closed at the end of their parent block element (``</p>``)
    by the HTML5 parsing algorithm, so they render correctly.

    This avoids any fragile balanced-matching logic and works regardless of
    how many ``</sup>`` tags the translator dropped.

    **Guard**: if the distance to the next ``</sup>`` is ≤ 25 characters,
    the ``<sup>`` is treated as a legitimate short marker (table footnote,
    ``<sup>a</sup>``, etc.) and is left unchanged.
    """
    def _maybe_delete_opener(m: re.Match[str]) -> str:
        # Peek at how far the next </sup> is to distinguish a real short marker
        # from a broken long wrapper.
        next_close = html.find("</sup>", m.end())
        content_len = (next_close - m.end()) if next_close >= 0 else 9999
        if content_len <= 25:
            return m.group(0)   # short marker — leave untouched
        return ""               # delete only the <sup> opener

    return re.sub(r"<sup>(?=\s*\.)", _maybe_delete_opener, html)


# Latin abbreviations that the translator sometimes transliterates into Cyrillic
# when they appear right after an expanded Cyrillic form, e.g.
# "Генеративный искусственный интеллект (ГАИ)".  We restore the Latin form so
# the document stays consistent with the rest of the body text (which, due to
# the translation prompt, keeps "GAI" untouched).
_LATIN_ABBREV_RESTORE_MAP: dict[str, str] = {
    "ГАИ": "GAI",
    "ВНА": "VNA",
    "МПЧ": "ICP",  # Cyrillic mis-transliteration of ICP (sometimes)
    "ИКД": "ICP",
    "ОСШ": "SNR",
    "АЦП": "ADC",
    "ОУ": "AC",    # only in abbreviation contexts — handled via parens
    "ПЧ": "RF",
    "МЭМС": "MEMS",
    "ПЛИС": "FPGA",
    "МИМО": "MIMO",
}


def _restore_latin_abbrevs(html: str) -> str:
    """Replace Cyrillic transliterations of Latin abbrevs in parentheses.

    The translator, when it sees ``Generative artificial intelligence (GAI)``,
    often writes ``Генеративный искусственный интеллект (ГАИ)`` — it
    transliterates the abbreviation even though the prompt forbids it.  We
    restore the Latin form by replacing ``(ГАИ)`` with ``(GAI)`` (and friends)
    after translation.
    """
    if not any(cyr in html for cyr in _LATIN_ABBREV_RESTORE_MAP):
        return html
    for cyr, lat in _LATIN_ABBREV_RESTORE_MAP.items():
        # In parentheses — highest confidence.
        html = re.sub(rf"\(\s*{re.escape(cyr)}\s*\)", f"({lat})", html)
    return html


def _add_section_anchors(html: str) -> tuple[str, set[str]]:
    """Add ``id="section-{ROMAN}"`` to headings that open with a Roman numeral.

    Returns the modified HTML and the set of upper-case Roman numerals found.
    """
    found: set[str] = set()

    def _add_id(m: re.Match[str]) -> str:
        roman = m.group(3).upper()
        attrs = m.group(2)
        if re.search(r'\bid\s*=', attrs, re.IGNORECASE):
            found.add(roman)
            return m.group(0)
        found.add(roman)
        full = m.group(0)
        tag_close = full.index('>')
        return full[:tag_close] + f' id="section-{roman}"' + full[tag_close:]

    result = _ROMAN_SECTION_HEADING_PATTERN.sub(_add_id, html)
    return result, found


def _add_figure_anchors(html: str) -> tuple[str, set[str]]:
    """Add ``id="fig-{n}"`` to paragraphs that open with a figure caption marker.

    Returns the modified HTML and the set of figure number strings found.
    """
    found: set[str] = set()

    def _add_id(m: re.Match[str]) -> str:
        p_attrs = m.group(1)     # '<p ...' (no closing '>')
        caption_start = m.group(2)  # 'Fig. 3.' or 'Рис. 3.' etc.
        fig_num = m.group(3)        # '3'
        if re.search(r'\bid\s*=', p_attrs, re.IGNORECASE):
            found.add(fig_num)
            return m.group(0)
        found.add(fig_num)
        # Ensure we add the id attribute properly even if there are extra spaces
        # or attributes that need to be preserved
        if p_attrs.endswith('>'):
            # Remove the closing '>' and add id
            attrs_without_closing = p_attrs[:-1]
            return f'{attrs_without_closing} id="fig-{fig_num}">{caption_start}'
        else:
            # If no closing >, add it after the attributes
            return f'{p_attrs} id="fig-{fig_num}">{caption_start}'

    result = _FIG_CAPTION_PARA_PATTERN.sub(_add_id, html)
    return result, found


def _link_section_refs(html: str, found_sections: set[str]) -> str:
    """Wrap ``Section II`` / ``Раздел II`` occurrences with ``<a>`` links."""
    if not found_sections:
        return html

    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        word = m.group(1)
        roman = m.group(2).upper()
        if roman not in found_sections:
            return m.group(0)
        return f'<a href="#section-{roman}" class="z2m-section-link">{word}\xa0{roman}</a>'

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
        out.append(_SECTION_REF_PATTERN.sub(_replace, part))

    return "".join(out)


def _link_figure_refs(html: str, found_figures: set[str]) -> str:
    """Wrap ``Fig. 3`` / ``рис. 3`` occurrences with ``<a>`` links.

    Caption paragraphs themselves are intentionally skipped because
    ``_FIG_REF_PATTERN`` has a negative lookahead for a trailing dot.
    """
    if not found_figures:
        return html

    parts = _TAG_SPLIT_PATTERN.split(html)
    out: list[str] = []
    skip_stack: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        prefix = m.group(1)
        num = m.group(2)
        if num not in found_figures:
            return m.group(0)
        return f'<a href="#fig-{num}" class="z2m-fig-link">{prefix}\xa0{num}</a>'

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
        out.append(_FIG_REF_PATTERN.sub(_replace, part))

    return "".join(out)


def _convert_latex_sup_citations(html: str) -> str:
    r"""Convert Marker's LaTeX superscript citations to ``<sup>`` tags.

    Marker sometimes emits citation numbers as MathJax inline math:
    ``\(^{157}\)`` or ``\(^{153-156}\)`` instead of ``<sup>157</sup>``.
    These are not caught by ``_add_reference_ids_and_citation_links`` because
    they look like math.  Promote them to ``<sup>`` before citation linking.
    """
    return _LATEX_SUP_CITATION_PATTERN.sub(r'<sup>\1</sup>', html)


def _fix_heading_translation_breaks(html: str) -> str:
    """Remove false sentence-break periods inserted before inline abbreviations in headings.

    Also lowercase the first Cyrillic capital letter following closing inline tags
    (</i>, </em>, </b>, </strong>) within headings, as a fallback when heading
    text node merging fails.

    The translator processes heading text nodes in isolation, so it may end the
    first node with a period: ``"беспроводного. <i>LC</i> Датчик"``.
    Inside ``<h1>``–``<h6>`` a period before ``<i>``/``<b>``/``<em>``/``<strong>``
    that starts with 2+ uppercase Latin letters is a translation artefact and is removed.
    """
    def fix_heading(m: re.Match[str]) -> str:
        open_tag, content, close_tag = m.group(1), m.group(2), m.group(3)

        # Fix 1: Remove period before inline markup
        fixed = _HEADING_PERIOD_BEFORE_ABBREV_PATTERN.sub(r' \1', content)

        # Fix 2: Lowercase Cyrillic capital after </i>, </em>, etc.
        # Pattern: </tag> followed by whitespace and Cyrillic capital letter
        # This handles the case where "Sensor" became "Датчик" instead of "датчика"
        fixed = re.sub(
            r'(</(i|em|b|strong)>)\s+([А-ЯЁ])',
            lambda m: m.group(1) + ' ' + m.group(3).lower(),
            fixed,
            flags=re.IGNORECASE
        )
        # Fix 3: common title artefact "... <i>LC</i> датчик" -> "... <i>LC</i>-датчика"
        fixed = _HEADING_ACRONYM_SENSOR_PATTERN.sub(
            lambda m: f"{m.group(1)}-датчика",
            fixed,
        )

        return f"{open_tag}{fixed}{close_tag}"

    return _HEADING_TAG_PATTERN.sub(fix_heading, html)


def _normalize_spacing_after_z2m_links(html: str) -> str:
    """Insert a missing space when a z2m link is glued to the following word."""
    return _Z2M_LINK_GLUE_PATTERN.sub(r"\1 ", html)


def _normalize_table_caption_style(html: str, *, table_caption_language: str = "ru") -> str:
    """Normalize table caption paragraphs to a single style.

    Target form:
    - ``Таблица N. Хвост.`` for ``table_caption_language='ru'``
    - ``TABLE N. Tail.`` for ``table_caption_language='en'``
    """
    def _normalize(m: re.Match[str]) -> str:
        p_open, _source_label, table_no, tail, p_close = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        number = table_no.upper()
        cleaned_tail = re.sub(r"\s+", " ", tail).strip()
        cleaned_tail = cleaned_tail.strip(" .;:,")
        label = "TABLE" if table_caption_language == "en" else "Таблица"

        if cleaned_tail:
            sentence_tail = cleaned_tail.lower()
            sentence_tail = sentence_tail[:1].upper() + sentence_tail[1:]
            return f"{p_open}{label} {number}. {sentence_tail}.{p_close}"
        return f"{p_open}{label} {number}.{p_close}"

    return _TABLE_CAPTION_PARA_PATTERN.sub(_normalize, html)


def _visible_text(fragment: str) -> str:
    text = _HTML_TAG_PATTERN.sub(" ", fragment)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&#160;", " ")
        .replace("\u00a0", " ")
    )
    return re.sub(r"\s+", " ", text).strip()


def _looks_inline_figure_block(block_html: str) -> bool:
    stripped = block_html.strip()
    if stripped.lower().startswith("<figure"):
        return True
    visible = _visible_text(stripped)
    if re.match(r"^(?:fig(?:ure)?|fig)\.?\s*\d+\b", visible, re.IGNORECASE):
        return True
    if "<img" not in stripped.lower():
        return False
    # For image-only gaps, allow merge only when no visible caption text exists.
    return len(visible) <= 2


def _looks_nonprose_gap_block(block_html: str) -> bool:
    stripped = block_html.strip()
    lowered = stripped.lower()
    if lowered.startswith("<figure") or lowered.startswith("<table"):
        return True

    if lowered.startswith("<h"):
        h_match = _SENTENCE_H_NODE_PATTERN.match(stripped)
        visible = _visible_text(h_match.group("body") if h_match else stripped)
        return bool(re.match(r"^(?:fig(?:ure)?|table|таблица)\.?\s*[ivxlcdm\d]+\b", visible, re.IGNORECASE))

    if lowered.startswith("<p"):
        if _looks_affiliation_block(stripped):
            return True
        if _looks_inline_figure_block(stripped):
            return True
        visible = _visible_text(stripped)
        if not visible:
            return True
        if re.match(r"^(?:table|таблица)\.?\s*[ivxlcdm\d]+\b", visible, re.IGNORECASE):
            return True
        # Table-tail math notes often rendered as standalone paragraphs.
        if re.match(r"^\\[\(\[]", visible):
            return True

    return False


def _looks_affiliation_block(raw: str) -> bool:
    """Detect long affiliation/author-footnote paragraphs inserted between prose blocks."""
    if not raw.lstrip().lower().startswith("<p"):
        return False
    visible = _visible_text(raw)
    if len(visible) < 220:
        return False

    lower = visible.lower()
    numbered_chunks = len(re.findall(r"(?:^|\s)\d{1,2}\s*[A-Z]", visible))
    org_hits = sum(
        1
        for kw in (
            "university",
            "department",
            "centre",
            "center",
            "school of medicine",
            "institute",
            "hospital",
            "office",
            "authors contributed equally",
        )
        if kw in lower
    )
    has_contact = ("e-mail" in lower) or ("email" in lower) or ("@" in visible)

    if numbered_chunks >= 5 and org_hits >= 2:
        return True
    if numbered_chunks >= 4 and has_contact:
        return True
    if "authors contributed equally" in lower and numbered_chunks >= 3:
        return True
    return False


def _looks_inline_figure_gap(block_html: str) -> bool:
    blocks = _SENTENCE_GAP_BLOCK_PATTERN.findall(block_html)
    if not blocks:
        return False
    saw_figure_like = False
    for block in blocks:
        if not _looks_nonprose_gap_block(block):
            return False
        saw_figure_like = True
    return saw_figure_like


def _is_sentence_continuation(left_text: str, right_text: str) -> bool:
    if not left_text or not right_text:
        return False
    if not re.search(r"[A-Za-z]", left_text + right_text):
        return False
    if re.search(r"[А-Яа-яЁё]", left_text + right_text):
        return False

    right_start = right_text.lstrip()
    if not right_start:
        return False
    norm_right_start = re.sub(r'^[\s"\'(\[\{,;:]+', "", right_start)
    if not norm_right_start:
        norm_right_start = right_start
    if re.match(r"^(?:fig(?:ure)?|table)\.?\s*\d+", norm_right_start, re.IGNORECASE):
        return False
    if right_start.startswith("("):
        return True
    if left_text.rstrip().endswith((".", "!", "?", ":", ";", "…")):
        return False

    first_token_match = re.match(r'^["\'(\[]*([A-Za-z]+)', norm_right_start)
    first_token = first_token_match.group(1).lower() if first_token_match else ""
    continuation_tokens = {
        "and",
        "or",
        "but",
        "with",
        "which",
        "where",
        "when",
        "while",
        "that",
        "to",
        "for",
        "of",
        "in",
        "on",
        "at",
        "as",
        "by",
        "if",
        "after",
        "before",
        "during",
        "because",
        "meanwhile",
        "then",
        "currently",
        "it",
        "this",
        "these",
        "those",
        "the",
        "a",
        "an",
        "acquisition",
    }
    first_char = norm_right_start[0]
    if first_char.islower():
        return True
    if first_token in continuation_tokens:
        return True
    return False


def _is_sentence_continuation_across_affiliation_gap(left_text: str, right_text: str) -> bool:
    """Relaxed continuation check for cases split by long affiliation footnote blocks."""
    if not left_text or not right_text:
        return False
    if not re.search(r"[A-Za-z]", left_text + right_text):
        return False
    if re.search(r"[А-Яа-яЁё]", left_text + right_text):
        return False

    right_start = right_text.lstrip()
    if not right_start:
        return False
    norm_right_start = re.sub(r'^[\s"\'(\[\{,;:]+', "", right_start)
    if not norm_right_start:
        norm_right_start = right_start
    if re.match(r"^(?:fig(?:ure)?|table|box)\.?\s*\d*", norm_right_start, re.IGNORECASE):
        return False
    if re.match(r"^\d+", norm_right_start):
        return False
    if len(norm_right_start) < 4:
        return False
    if left_text.rstrip().endswith((".", "!", "?", ":", ";", "…")):
        return False
    return True


def _is_short_fragment_left(left_text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", left_text)
    if not words or len(words) > 4:
        return False
    return words[0].lower() in {"this", "these", "it", "that", "which", "also"}


def _merge_sentence_parts(left_body: str, right_body: str) -> str:
    merged_left = left_body.rstrip()
    tail = right_body.lstrip()
    if merged_left and tail:
        if merged_left.endswith("-") and re.match(r"^[a-z]", tail):
            # OCR line-wrap hyphenation (e.g. "regis-" + "ter") around split blocks.
            merged_left = merged_left[:-1]
        elif tail[:1] in ",.;:)]}":
            pass
        elif not merged_left.endswith((" ", "\n", "\t", "-", "(", "[", "/")):
            merged_left += " "
    merged_left += tail
    return merged_left


def _is_figure_caption_node(raw: str) -> bool:
    low = raw.lstrip().lower()
    if not (low.startswith("<p") or re.match(r"<h[1-6]\b", low)):
        return False
    visible = _visible_text(raw)
    return bool(re.match(r"^(?:fig(?:ure)?|fig)\.?\s*\d+\b", visible, re.IGNORECASE))


def _extract_caption_intrusion_tail(caption_body: str) -> tuple[str, str] | None:
    """Split a figure-caption body when OCR injected article prose after backslashes.

    Example:
      "... (required) \\ image-modeling task in which ..." ->
      head="... (required)", tail="image-modeling task in which ..."
    """
    m = re.match(r"^(?P<head>[\s\S]*?)\s*\\+\s*(?P<tail>[a-z][\s\S]*)$", caption_body.strip())
    if m is None:
        return None
    head = m.group("head").strip()
    tail = m.group("tail").strip()
    tail_visible = _visible_text(tail)
    if len(tail_visible) < 60:
        return None
    if len(re.findall(r"[A-Za-z]+", tail_visible)) < 8:
        return None
    return head, tail


def _rehome_enumerated_caption_suffix(left_body: str, caption_body: str) -> tuple[str, str]:
    """Move a misplaced "(4) ..." suffix from prose paragraph back into figure caption.

    Marker occasionally pushes the final enumeration item "(4) ..." to the prose
    paragraph before the figure, while the caption keeps only "(1)-(3)".
    """
    left_visible = _visible_text(left_body)
    caption_visible = _visible_text(caption_body)
    if "(4)" not in left_visible:
        return left_body, caption_body
    if "(1)" not in caption_visible or "(2)" not in caption_visible or "(3)" not in caption_visible:
        return left_body, caption_body
    if "(4)" in caption_visible:
        return left_body, caption_body

    lower_left = left_body.lower()
    split_at = lower_left.rfind("to evaluate")
    if split_at < 0:
        pos4 = lower_left.rfind("(4)")
        if pos4 < 0:
            return left_body, caption_body
        semi = left_body.rfind(";", 0, pos4)
        split_at = semi if semi >= 0 else pos4

    suffix = left_body[split_at:].strip()
    prefix = left_body[:split_at].rstrip()
    if len(_visible_text(suffix)) < 20:
        return left_body, caption_body

    merged_caption = caption_body.rstrip()
    if merged_caption and not merged_caption.endswith((" ", "\n", "\t")):
        merged_caption += " "
    merged_caption += suffix
    return prefix, merged_caption


def _repair_sentence_breaks_at_page_boundaries(html: str) -> tuple[str, int]:
    nodes = list(_SENTENCE_NODE_PATTERN.finditer(html))
    if not nodes:
        return html, 0

    replacements: dict[int, str] = {}
    dropped: set[int] = set()
    repairs = 0

    def _is_p_node(raw: str) -> bool:
        return raw.lstrip().lower().startswith("<p")

    def _between_is_whitespace(a_idx: int, b_idx: int) -> bool:
        return html[nodes[a_idx].end():nodes[b_idx].start()].strip() == ""

    i = 0
    while i + 1 < len(nodes):
        left_raw = nodes[i].group(0)
        right_raw = nodes[i + 1].group(0)
        if not _is_p_node(left_raw) or not _is_p_node(right_raw):
            i += 1
            continue
        if not _between_is_whitespace(i, i + 1):
            i += 1
            continue

        left_match = _SENTENCE_P_NODE_PATTERN.match(left_raw)
        right_match = _SENTENCE_P_NODE_PATTERN.match(right_raw)
        if left_match is None or right_match is None:
            i += 1
            continue

        right_open = right_match.group("open")
        if re.search(r"\bid\s*=", right_open, re.IGNORECASE):
            i += 1
            continue

        left_text = _visible_text(left_match.group("body"))
        right_text = _visible_text(right_match.group("body"))
        if len(right_text) < 6:
            i += 1
            continue
        if len(left_text) < 30 and not _is_short_fragment_left(left_text):
            i += 1
            continue
        if re.match(r'^(?:This|These|It|That|Those|The|A|An)\b', right_text.lstrip()):
            i += 1
            continue
        if re.match(r"^\d+\s*[.)]", right_text):
            i += 1
            continue
        if not _is_sentence_continuation(left_text, right_text):
            i += 1
            continue

        merged = _merge_sentence_parts(left_match.group("body"), right_match.group("body"))
        replacements[i] = f"{left_match.group('open')}{merged}{left_match.group('close')}"
        dropped.add(i + 1)
        repairs += 1
        i += 2

    if repairs == 0:
        return html, 0

    out_parts: list[str] = []
    cursor = 0
    for idx, node in enumerate(nodes):
        out_parts.append(html[cursor:node.start()])
        if idx in dropped:
            pass
        elif idx in replacements:
            out_parts.append(replacements[idx])
        else:
            out_parts.append(node.group(0))
        cursor = node.end()
    out_parts.append(html[cursor:])
    return "".join(out_parts), repairs


def _reorder_table_block_away_from_formula_context(html: str) -> tuple[str, int]:
    nodes = list(_SENTENCE_NODE_PATTERN.finditer(html))
    if not nodes:
        return html, 0

    replacements: dict[int, str] = {}
    dropped: set[int] = set()
    moves = 0

    def _between_is_ignorable(a_idx: int, b_idx: int) -> bool:
        segment = html[nodes[a_idx].end():nodes[b_idx].start()]
        if segment.strip() == "":
            return True
        compact = re.sub(r"\s+", "", segment)
        compact = re.sub(
            r'<divclass="z2m-equation-row"><spanclass="z2m-eq-lhs"></span>',
            "",
            compact,
            flags=re.IGNORECASE,
        )
        compact = re.sub(
            r'<spanclass="z2m-eq-num">\(\d+\)</span></div>',
            "",
            compact,
            flags=re.IGNORECASE,
        )
        return compact == ""

    def _is_p_node(raw: str) -> bool:
        return raw.lstrip().lower().startswith("<p")

    def _p_body(raw: str) -> str | None:
        m = _SENTENCE_P_NODE_PATTERN.match(raw)
        return m.group("body") if m else None

    def _is_table_caption_node(raw: str) -> bool:
        low = raw.lstrip().lower()
        if not (low.startswith("<p") or re.match(r"<h[1-6]\b", low)):
            return False
        visible = _visible_text(raw)
        return bool(re.match(r"^(?:table|таблица)\.?\s*[ivxlcdm\d]+\b", visible, re.IGNORECASE))

    def _is_table_node(raw: str) -> bool:
        return raw.lstrip().lower().startswith("<table")

    def _is_formula_p(raw: str) -> bool:
        body = _p_body(raw)
        if body is None:
            return False
        visible = _visible_text(body).strip()
        if not visible:
            return False
        return bool(
            re.match(r"^(?:\\[\(\[]|\(\d+\))", visible)
            or visible.startswith("y(")
            or visible.startswith("Ly_")
        )

    def _is_formula_follow_p(raw: str) -> bool:
        body = _p_body(raw)
        if body is None:
            return False
        visible = _visible_text(body).strip()
        return bool(re.match(r"^(?:We chose|where\b|and where\b)", visible, re.IGNORECASE))

    i = 0
    while i < len(nodes):
        if i in dropped:
            i += 1
            continue

        if i + 3 >= len(nodes):
            break

        intro_raw = nodes[i].group(0)
        cap_raw = nodes[i + 1].group(0)
        table_raw = nodes[i + 2].group(0)
        formula_raw = nodes[i + 3].group(0)

        intro_body = _p_body(intro_raw)
        intro_text = _visible_text(intro_body) if intro_body is not None else ""
        if not intro_body or not intro_text.endswith(":"):
            i += 1
            continue
        if not _is_table_caption_node(cap_raw) or not _is_table_node(table_raw) or not _is_formula_p(formula_raw):
            i += 1
            continue
        if not (_between_is_ignorable(i, i + 1) and _between_is_ignorable(i + 1, i + 2) and _between_is_ignorable(i + 2, i + 3)):
            i += 1
            continue

        follow_idx = None
        if i + 4 < len(nodes) and _between_is_ignorable(i + 3, i + 4) and _is_formula_follow_p(nodes[i + 4].group(0)):
            follow_idx = i + 4

        parts = [intro_raw, formula_raw]
        if follow_idx is not None:
            parts.append(nodes[follow_idx].group(0))
        parts.extend([cap_raw, table_raw])
        replacements[i] = "\n".join(parts)

        for di in (i + 1, i + 2, i + 3):
            dropped.add(di)
        if follow_idx is not None:
            dropped.add(follow_idx)
            i = follow_idx + 1
        else:
            i += 4
        moves += 1

    if moves == 0:
        return html, 0

    out_parts: list[str] = []
    cursor = 0
    for idx, node in enumerate(nodes):
        out_parts.append(html[cursor:node.start()])
        if idx in dropped:
            pass
        elif idx in replacements:
            out_parts.append(replacements[idx])
        else:
            out_parts.append(node.group(0))
        cursor = node.end()
    out_parts.append(html[cursor:])
    return "".join(out_parts), moves


def _repair_sentence_breaks_around_box_blocks(html: str) -> tuple[str, int]:
    nodes = list(_SENTENCE_NODE_PATTERN.finditer(html))
    if not nodes:
        return html, 0

    replacements: dict[int, str] = {}
    dropped: set[int] = set()
    repairs = 0

    def _between_is_whitespace(a_idx: int, b_idx: int) -> bool:
        return html[nodes[a_idx].end():nodes[b_idx].start()].strip() == ""

    def _p_match(raw: str) -> re.Match[str] | None:
        return _SENTENCE_P_NODE_PATTERN.match(raw)

    def _is_box_heading(raw: str) -> bool:
        low = raw.lstrip().lower()
        if not re.match(r"<h[1-6]\b", low):
            return False
        visible = _visible_text(raw)
        return bool(re.match(r"^box\s+\d+\b", visible, re.IGNORECASE))

    max_scan = 36
    for i in range(len(nodes)):
        if i in dropped or i + 2 >= len(nodes):
            continue

        left_raw = nodes[i].group(0)
        left_match = _p_match(left_raw)
        if left_match is None:
            continue
        if not _between_is_whitespace(i, i + 1):
            continue
        if not _is_box_heading(nodes[i + 1].group(0)):
            continue

        left_text = _visible_text(left_match.group("body"))
        if len(left_text) < 20:
            continue
        if left_text.rstrip().endswith((".", "!", "?", ":", ";", "…")):
            continue

        right_idx = None
        upper = min(len(nodes), i + max_scan + 1)
        for j in range(i + 2, upper):
            if not _between_is_whitespace(j - 1, j):
                break
            raw = nodes[j].group(0)
            pm = _p_match(raw)
            if pm is None:
                continue
            right_text = _visible_text(pm.group("body"))
            if len(right_text) < 6:
                continue
            if _is_sentence_continuation(left_text, right_text):
                right_idx = j
                break
            # Stop at major non-box section heading.
            if re.match(r"<h[1-6]\b", raw.lstrip().lower()):
                break

        if right_idx is None:
            continue

        right_match = _p_match(nodes[right_idx].group(0))
        if right_match is None:
            continue
        merged = _merge_sentence_parts(left_match.group("body"), right_match.group("body"))
        replacements[i] = f"{left_match.group('open')}{merged}{left_match.group('close')}"
        dropped.add(right_idx)
        repairs += 1

    if repairs == 0:
        return html, 0

    out_parts: list[str] = []
    cursor = 0
    for idx, node in enumerate(nodes):
        out_parts.append(html[cursor:node.start()])
        if idx in dropped:
            pass
        elif idx in replacements:
            out_parts.append(replacements[idx])
        else:
            out_parts.append(node.group(0))
        cursor = node.end()
    out_parts.append(html[cursor:])
    return "".join(out_parts), repairs


def _repair_sentence_breaks_around_figure_blocks(html: str) -> tuple[str, int]:
    nodes = list(_SENTENCE_NODE_PATTERN.finditer(html))
    if not nodes:
        return html, 0

    replacements: dict[int, str] = {}
    dropped: set[int] = set()
    repairs = 0

    def _is_p_node(raw: str) -> bool:
        return raw.lstrip().lower().startswith("<p")

    def _between_is_whitespace(a_idx: int, b_idx: int) -> bool:
        return html[nodes[a_idx].end():nodes[b_idx].start()].strip() == ""

    i = 0
    max_gap_blocks = 12
    while i < len(nodes):
        if i in dropped:
            i += 1
            continue

        left_raw = nodes[i].group(0)
        if not _is_p_node(left_raw):
            i += 1
            continue
        left_match = _SENTENCE_P_NODE_PATTERN.match(left_raw)
        if left_match is None:
            i += 1
            continue

        j = i + 1
        gap_indices: list[int] = []
        while j < len(nodes):
            prev_idx = j - 1
            if not _between_is_whitespace(prev_idx, j):
                break
            if j in dropped:
                break
            if len(gap_indices) >= max_gap_blocks:
                break

            raw = nodes[j].group(0)
            if not _looks_nonprose_gap_block(raw):
                break
            gap_indices.append(j)
            j += 1

        right_idx = j
        if not gap_indices or right_idx >= len(nodes):
            i += 1
            continue
        if not _between_is_whitespace(gap_indices[-1], right_idx):
            i += 1
            continue

        right_raw = nodes[right_idx].group(0)
        if not _is_p_node(right_raw):
            i += 1
            continue
        right_match = _SENTENCE_P_NODE_PATTERN.match(right_raw)
        if right_match is None:
            i += 1
            continue

        right_open = right_match.group("open")
        if re.search(r"\bid\s*=", right_open, re.IGNORECASE):
            i += 1
            continue

        left_body = left_match.group("body")
        right_body = right_match.group("body")
        left_text = _visible_text(left_body)
        right_text = _visible_text(right_body)
        if len(right_text) < 6:
            i += 1
            continue
        if len(left_text) < 20 and not _is_short_fragment_left(left_text):
            i += 1
            continue
        all_affiliation_gap = all(_looks_affiliation_block(nodes[g].group(0)) for g in gap_indices)
        continuation_ok = _is_sentence_continuation(left_text, right_text)
        if not continuation_ok and all_affiliation_gap:
            continuation_ok = _is_sentence_continuation_across_affiliation_gap(left_text, right_text)

        if continuation_ok:
            merged_left = _merge_sentence_parts(left_body, right_body)
            replacements[i] = f"{left_match.group('open')}{merged_left}{left_match.group('close')}"
            dropped.add(right_idx)
            repairs += 1
            i = right_idx + 1
            continue

        # Recovery path: caption contains a prose tail after backslash artefacts
        # ("... \\ image-modeling ..."), while "(4) ..." escaped into the left paragraph.
        recovered = False
        for gap_idx in gap_indices:
            if gap_idx in dropped:
                continue
            cap_raw = nodes[gap_idx].group(0)
            if not _is_figure_caption_node(cap_raw):
                continue
            cap_match = _SENTENCE_P_NODE_PATTERN.match(cap_raw)
            if cap_match is None:
                continue

            cap_body = cap_match.group("body")
            split = _extract_caption_intrusion_tail(cap_body)
            if split is None:
                continue
            cap_head, prose_tail = split
            left_body_for_recovery, cap_head_for_recovery = _rehome_enumerated_caption_suffix(
                left_body,
                cap_head,
            )
            if not _is_sentence_continuation(_visible_text(left_body_for_recovery), _visible_text(prose_tail)):
                continue

            repaired_left = _merge_sentence_parts(left_body_for_recovery, prose_tail)
            replacements[i] = f"{left_match.group('open')}{repaired_left}{left_match.group('close')}"
            replacements[gap_idx] = (
                f"{cap_match.group('open')}{cap_head_for_recovery}{cap_match.group('close')}"
            )
            repairs += 1
            recovered = True
            i = right_idx
            break

        if not recovered:
            i += 1

    if repairs == 0:
        return html, 0

    out_parts: list[str] = []
    cursor = 0
    for idx, node in enumerate(nodes):
        out_parts.append(html[cursor:node.start()])
        if idx in dropped:
            pass
        elif idx in replacements:
            out_parts.append(replacements[idx])
        else:
            out_parts.append(node.group(0))
        cursor = node.end()
    out_parts.append(html[cursor:])
    return "".join(out_parts), repairs


def polish_html_document(html: str, *, table_caption_language: str = "ru") -> str:
    polished = _unwrap_spurious_math_captions(html)  # before all else: free captions from <math>
    polished = drop_repeated_phrases(polished)
    polished = _fix_latex_text_commands(polished)
    polished = _fix_subscript_equation_spill(polished)
    polished = _fix_orphaned_sup_tags(polished)
    polished = _unescape_inline_sup_sub(polished)
    polished = _normalize_spaced_inline_sup_sub_tags(polished)
    polished = _fix_common_mojibake(polished)
    polished = _BYTE_TOKEN_CITATION_PATTERN.sub(r'<sup>\1</sup>', polished)
    polished = _BYTE_TOKEN_ARTIFACT_PATTERN.sub("", polished)
    polished = _cleanup_marker_escape_artifacts(polished)
    polished = _convert_latex_sup_citations(polished)   # \(^{N}\) → <sup>N</sup>
    polished = _fix_equation_display(polished)
    polished = _convert_math_tags_to_tex(polished)
    polished, _ = _repair_sentence_breaks_around_box_blocks(polished)
    polished, _ = _repair_sentence_breaks_at_page_boundaries(polished)
    polished, _ = _reorder_table_block_away_from_formula_context(polished)
    polished, _ = _repair_sentence_breaks_around_figure_blocks(polished)
    polished, found_sections = _add_section_anchors(polished)
    polished, found_figures = _add_figure_anchors(polished)
    polished = _add_reference_ids_and_citation_links(polished)
    polished = _link_section_refs(polished, found_sections)
    polished = _link_figure_refs(polished, found_figures)
    polished = _normalize_table_caption_style(polished, table_caption_language=table_caption_language)
    polished = _normalize_spacing_after_z2m_links(polished)
    polished = _autolink_plain_urls(polished)
    polished = _inject_utf8_charset(polished)
    polished = _inject_default_styles(polished)
    polished = _inject_mathjax(polished)
    polished = _wrap_body_in_container(polished)
    polished = _cleanup_empty_html_blocks(polished)
    polished = _fix_heading_translation_breaks(polished)  # ". <i>LC</i>" → " <i>LC</i>"
    polished = _restore_abbreviations(polished)
    return polished


def _restore_abbreviations(html: str) -> str:
    """Restore Latin abbreviations that were masked during translation."""
    restored = html

    # Restore specific abbreviations using our mapping
    for pattern, replacement in RU_ABBREV_TO_LATIN.items():
        # Use word boundaries to match complete words only
        def replace_match(match):
            return replacement

        restored = re.sub(pattern, replace_match, restored, flags=re.IGNORECASE)

    return restored


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
