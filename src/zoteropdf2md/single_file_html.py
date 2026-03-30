from __future__ import annotations

import base64
import mimetypes
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


_IMG_SRC_PATTERN = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)(\2)', re.IGNORECASE)
_HEAD_CLOSE_PATTERN = re.compile(r"</head>", re.IGNORECASE)
_HTML_OPEN_PATTERN = re.compile(r"<html\b[^>]*>", re.IGNORECASE)
_BODY_PATTERN = re.compile(r"(<body\b[^>]*>)(.*?)(</body>)", re.IGNORECASE | re.DOTALL)
_ESCAPED_INLINE_TAG_PATTERN = re.compile(r"&lt;(/?)(sup|sub)&gt;", re.IGNORECASE)

_DEFAULT_READABILITY_STYLE = """
<style data-z2m-style="readable">
  :root { color-scheme: light; }
  body {
    margin: 0;
    padding: 24px;
    font-family: "Segoe UI", Arial, sans-serif;
    line-height: 1.55;
    color: #1f2937;
    background: #f4f6f8;
  }
  #marker-doc {
    max-width: 920px;
    margin: 0 auto;
    background: #ffffff;
    border: 1px solid #dfe5eb;
    border-radius: 10px;
    box-shadow: 0 2px 10px rgba(16, 24, 40, 0.06);
    padding: 28px 34px;
  }
  h1, h2, h3, h4 {
    color: #0f172a;
    line-height: 1.3;
    margin-top: 1.15em;
    margin-bottom: 0.5em;
  }
  p {
    margin: 0.6em 0;
    word-break: break-word;
  }
  img {
    max-width: 100%;
    height: auto;
    display: block;
    margin: 0.9em auto;
    border: 1px solid #d9e0e7;
    border-radius: 6px;
  }
  math {
    overflow-x: auto;
    display: block;
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


def _wrap_body_in_container(html: str) -> str:
    if 'id="marker-doc"' in html:
        return html

    def replace(match: re.Match[str]) -> str:
        body_open, body_inner, body_close = match.groups()
        return f'{body_open}\n  <main id="marker-doc">\n{body_inner}\n  </main>\n{body_close}'

    return _BODY_PATTERN.sub(replace, html, count=1)


def _unescape_inline_sup_sub(html: str) -> str:
    return _ESCAPED_INLINE_TAG_PATTERN.sub(r"<\1\2>", html)


def _fix_common_mojibake(html: str) -> str:
    fixed = html
    for bad, good in _MOJIBAKE_REPLACEMENTS:
        fixed = fixed.replace(bad, good)
    return fixed


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
    inlined_html = _unescape_inline_sup_sub(inlined_html)
    inlined_html = _fix_common_mojibake(inlined_html)
    inlined_html = _inject_default_styles(inlined_html)
    inlined_html = _wrap_body_in_container(inlined_html)
    return InlineHtmlResult(html=inlined_html, inlined_images=inlined_count)
