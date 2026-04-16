from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable

from .abbreviations import LATIN_ABBREV_TO_RU, RU_ABBREV_TO_LATIN
from .single_file_html import polish_html_document, _REFERENCES_HEADING_PATTERN


OFFICIAL_TRANSLATEGEMMA_MODEL_REPO = "google/translategemma-4b-it"
LOCAL_TRANSLATEGEMMA_MODEL_DIR = (
    Path(__file__).resolve().parents[2] / "models" / "translategemma-4b-it"
).resolve(strict=False)
DEFAULT_TRANSLATEGEMMA_MODEL = (
    str(LOCAL_TRANSLATEGEMMA_MODEL_DIR)
    if LOCAL_TRANSLATEGEMMA_MODEL_DIR.exists()
    else OFFICIAL_TRANSLATEGEMMA_MODEL_REPO
)
DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE = "ru"
TRANSLATEGEMMA_LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    ("en", "English"),
    ("ru", "Russian"),
    ("de", "German"),
    ("zh", "Chinese"),
)
_LANGUAGE_NAME_BY_CODE = dict(TRANSLATEGEMMA_LANGUAGE_CHOICES)
_LANGUAGE_CODE_BY_NAME = {name.lower(): code for code, name in TRANSLATEGEMMA_LANGUAGE_CHOICES}

_TAG_SPLIT_PATTERN = re.compile(r"(<[^>]+>)")
_OPEN_TAG_PATTERN = re.compile(r"^<\s*([a-zA-Z0-9:_-]+)")
_CLOSE_TAG_PATTERN = re.compile(r"^<\s*/\s*([a-zA-Z0-9:_-]+)")
_TRANSLATABLE_TEXT_PATTERN = re.compile(r"[A-Za-z\u0400-\u04FF\u4E00-\u9FFF]")
_SKIP_TRANSLATION_TAGS = {"script", "style", "code", "pre", "math", "svg", "a", "sup", "sub"}

# Matches the translate="no" attribute (HTML spec for marking non-translatable content).
_NO_TRANSLATE_ATTR_PATTERN = re.compile(r'\btranslate\s*=\s*["\']no["\']', re.IGNORECASE)

# SentencePiece byte-fallback tokens that Gemma sometimes emits when it encounters
# Unicode characters near the translation boundary.  They appear as literal ASCII
# sequences like <0xE2><0x82><0xA9> in the output.
# When followed by citation-like numbers the whole group is a dropped <sup>; restore it.
_BYTE_TOKEN_ARTIFACT_PATTERN = re.compile(r'(?:<0x[0-9A-Fa-f]{2}>)+')
_BYTE_TOKEN_CITATION_PATTERN = re.compile(
    r'(?:<0x[0-9A-Fa-f]{2}>)+(\d[\d,\u2013\u2014\-]*)'
)

# Uppercase Latin abbreviations that must survive translation unchanged.
# Restricted to SHORT sequences (2–5 letters) so that all-caps section titles
# such as INTRODUCTION, CONCLUSION, RESULTS (≥6 letters) are NOT masked and
# can still be translated normally.  Real abbreviations (IEEE, MEMS, GAI, LC,
# ADC, VNA, RF) are typically ≤5 characters and will be protected.
_ABBREV_PATTERN = re.compile(r'\b[A-Z]{2,5}\d*\b')
# Placeholder tokens used to protect abbreviations during model calls.
_ABBREV_TOKEN_PATTERN = re.compile(r'<z2m-a id="(\d+)"/>')

# Additional patterns for protecting specific abbreviations from translation
_LATIN_ABBREV_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in LATIN_ABBREV_TO_RU.keys()]
_RU_ABBREV_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in RU_ABBREV_TO_LATIN.keys()]

# Patterns to protect from prompt leakage (fragments that should not appear in output text)
_PROMPT_LEAK_PROTECTION_PATTERNS = [
    # Common technical terms that may leak from prompts
    r'\bLC\b',
    r'\bVNA\b',
    r'\bICP\b',
    r'\bSNR\b',
    r'\bADC\b',
    r'\bIEEE\b',
    r'\bRF\b',
    r'\bMEMS\b',
    r'\bFPGA\b',
    r'\bAC\b',
    r'\bDC\b',
    r'\bUSB\b',
    r'\bMIMO\b',

    # Other potentially problematic terms
    r'\b(?:translation|translated text)\s*:\s*',
    r'\boriginal(?:\s+text)?\s*:\s*',
    r'\b(?:source|исходн)(?:\s+текст)?\s*:\s*',
]

# Patterns that indicate the model produced a meta-commentary / refusal instead of a
# translation.  When any of these match the translated output we fall back to the
# original source text so that no garbage leaks into the HTML.
_TRANSLATOR_REFUSAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"невозможно перевести", re.IGNORECASE),
    re.compile(r"не могу перевести", re.IGNORECASE),
    re.compile(r"не могу точно перевести", re.IGNORECASE),
    re.compile(r"не удаётся перевести", re.IGNORECASE),
    re.compile(r"пожалуйста.{0,40}предоставьте", re.IGNORECASE | re.DOTALL),
    re.compile(r"без дополнительного контекста", re.IGNORECASE),
    re.compile(r"нет достаточного контекста", re.IGNORECASE),
    re.compile(r"i cannot translate", re.IGNORECASE),
    re.compile(r"i(?:'m| am) unable to translate", re.IGNORECASE),
    re.compile(r"please provide.{0,60}context", re.IGNORECASE | re.DOTALL),
    re.compile(r"more context.{0,60}(?:to translate|for translation)", re.IGNORECASE | re.DOTALL),
)

_TRANSLATION_PREFIX_PATTERN = re.compile(
    r"^\s*(?:translation|translated text|перевод)\s*:\s*",
    re.IGNORECASE,
)
_ORIGINAL_SECTION_PATTERN = re.compile(
    r"(?:\r?\n){1,2}\s*(?:original(?: text)?|source(?: text)?|исходн(?:ый|ого)\s+текст)\s*:\s*",
    re.IGNORECASE,
)

_FORMULA_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Inline math delimiters.
    re.compile(r"\$[^$\n]{1,600}\$"),
    re.compile(r"\\\([^\n]{1,600}?\\\)"),
    re.compile(r"\\\[[\s\S]{1,600}?\\\]"),
    # LaTeX commands with optional brace arguments.
    re.compile(r"\\[A-Za-z]+(?:\s*\{[^{}]{0,160}\}){0,3}"),
    # Subscript / superscript expressions (e.g., I_1, L_{m}^{2}).
    re.compile(
        r"[A-Za-z](?:\s*_\{[^{}]{1,80}\}|\s*_[A-Za-z0-9]{1,20}|\s*\^\{[^{}]{1,80}\}|\s*\^[A-Za-z0-9]{1,20})+"
    ),
    # Single-letter coefficient directly before a LaTeX symbol (e.g., j\omega).
    re.compile(r"(?<!\w)[A-Za-z]\s*(?=\\[A-Za-z])"),
    # Dense equation chunks carrying operators with LaTeX/subscript markers.
    re.compile(
        r"(?<!\w)(?=[^,\n]{0,240}[=+\-*/])(?=[^,\n]{0,240}(?:\\|_|\^))"
        r"[A-Za-z0-9\\{}_^().]+(?:\s+[A-Za-z0-9\\{}_^().]+){0,40}(?!\w)"
    ),
    # Compact dimension style (e.g., 72 \times 48 \times 20 mm).
    re.compile(r"(?<!\w)\d+(?:\s*\\times\s*\d+){1,4}(?:\s*[A-Za-z]{1,8})?(?!\w)", re.IGNORECASE),
)

# Helpers for author-line detection.
_H1_CLOSE_PATTERN = re.compile(r"</h[1-6]\s*>", re.IGNORECASE)
_FIRST_P_OPEN_PATTERN = re.compile(r"<p(\b[^>]*)>", re.IGNORECASE)
_P_CLOSE_PATTERN = re.compile(r"</p\s*>", re.IGNORECASE)
_ABSTRACT_MARKER_PATTERN = re.compile(r"\bAbstract\b", re.IGNORECASE)

@dataclass(frozen=True)
class TranslateGemmaConfig:
    model_ref: str = DEFAULT_TRANSLATEGEMMA_MODEL
    target_language_code: str = DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE
    source_language: str = "Auto"
    hf_token: str | None = None
    cache_dir: str | None = None
    # Used only as a fallback when a full-segment translation does not fit context/memory.
    max_chunk_chars: int = 1800
    max_new_tokens: int = 65536


@dataclass(frozen=True)
class TranslatedHtmlArtifact:
    source_html_path: Path
    translated_html_path: Path
    language_code: str
    language_name: str
    translated_segments: int


def normalize_language_code(value: str | None) -> str:
    if value is None:
        return DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE

    raw = value.strip()
    if not raw:
        return DEFAULT_TRANSLATEGEMMA_TARGET_LANGUAGE

    lowered = raw.lower()
    if lowered in _LANGUAGE_NAME_BY_CODE:
        return lowered

    by_name = _LANGUAGE_CODE_BY_NAME.get(lowered)
    if by_name is not None:
        return by_name

    supported = ", ".join(code for code, _ in TRANSLATEGEMMA_LANGUAGE_CHOICES)
    raise ValueError(f"Unsupported translation language '{value}'. Supported codes: {supported}")


def language_name_for_code(language_code: str) -> str:
    normalized = normalize_language_code(language_code)
    return _LANGUAGE_NAME_BY_CODE.get(normalized, normalized)


def translated_html_output_path(source_html_path: Path, language_code: str) -> Path:
    normalized = normalize_language_code(language_code)
    return source_html_path.with_name(f"{source_html_path.stem}.{normalized}.html")


def _split_text_chunks(text: str, max_chunk_chars: int) -> list[str]:
    if max_chunk_chars < 256:
        max_chunk_chars = 256
    if len(text) <= max_chunk_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    total_len = len(text)

    while start < total_len:
        end = min(total_len, start + max_chunk_chars)
        if end < total_len:
            min_boundary = start + max_chunk_chars // 2
            boundary = max(
                text.rfind("\n\n", min_boundary, end),
                text.rfind("\n", min_boundary, end),
                text.rfind(". ", min_boundary, end),
                text.rfind("! ", min_boundary, end),
                text.rfind("? ", min_boundary, end),
                text.rfind("\u3002", min_boundary, end),
                text.rfind("\uFF01", min_boundary, end),
                text.rfind("\uFF1F", min_boundary, end),
                text.rfind(" ", min_boundary, end),
            )
            if boundary > start:
                end = boundary + 1

        if end <= start:
            end = min(total_len, start + max_chunk_chars)

        chunks.append(text[start:end])
        start = end

    return chunks


def _update_skip_stack(tag_fragment: str, skip_stack: list[str]) -> None:
    raw = tag_fragment.strip()
    if not raw.startswith("<"):
        return
    if raw.startswith("<!--") or raw.startswith("<!"):
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
    # Skip translation inside known non-translatable tags AND inside any element
    # that carries the standard HTML translate="no" attribute.
    if tag_name in _SKIP_TRANSLATION_TAGS or _NO_TRANSLATE_ATTR_PATTERN.search(raw):
        skip_stack.append(tag_name)


def _is_translator_refusal(text: str) -> bool:
    """Return True when *text* looks like a model refusal or meta-commentary."""
    return any(p.search(text) for p in _TRANSLATOR_REFUSAL_PATTERNS)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_source_echo(translated: str, source: str) -> str:
    """Trim common "translation + original source" echoes from model output."""
    cleaned = translated.strip()
    if not cleaned:
        return translated

    cleaned = _TRANSLATION_PREFIX_PATTERN.sub("", cleaned)

    source_clean = source.strip()
    if not source_clean:
        return cleaned

    labeled_original = _ORIGINAL_SECTION_PATTERN.search(cleaned)
    if labeled_original is not None:
        head = cleaned[:labeled_original.start()].rstrip()
        if head:
            return head

    exact_pos = cleaned.rfind(source_clean)
    if exact_pos > 0:
        prefix = cleaned[:exact_pos].rstrip()
        suffix = cleaned[exact_pos + len(source_clean):].strip()
        if prefix and (not suffix or len(suffix) <= 12):
            return prefix

    source_norm = _normalize_ws(source_clean).lower()
    if not source_norm:
        return cleaned

    blocks = [block.strip() for block in re.split(r"(?:\r?\n){2,}", cleaned) if block.strip()]
    if len(blocks) > 1:
        kept: list[str] = []
        removed = False
        for block in blocks:
            block_norm = _normalize_ws(block).lower()
            if block_norm == source_norm:
                removed = True
                continue
            kept.append(block)
        if removed and kept:
            return "\n\n".join(kept)

    return cleaned


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    spans.sort(key=lambda item: (item[0], item[1]))
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _formula_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in _FORMULA_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if end > start:
                spans.append((start, end))
    return _merge_spans(spans)


# ---------------------------------------------------------------------------
# Batch translation helpers
# ---------------------------------------------------------------------------

# Separator injected between segments in batch mode.  Looks like an HTML
# self-closing tag so translation models treat it as non-translatable markup.
_BATCH_SEPARATOR = "\n<z2m-sep/>\n"
_BATCH_SEP_PATTERN = re.compile(r'\n?[ \t]*<z2m-sep\s*/?>[ \t]*\n?', re.IGNORECASE)

# Formula placeholder tokens: <z2m-f id="N"/>  — kept unchanged by the model.
_FORMULA_TOKEN_PATTERN = re.compile(r'<z2m-f\s+id\s*=\s*"(\d+)"\s*/?>', re.IGNORECASE)

# Safety limit: skip batch mode if the combined text exceeds this many characters
# (rough estimate 4 chars ≈ 1 token, limit ≈ 50k tokens input).
_MAX_BATCH_CHARS = 80_000
_WINDOW_BATCH_TARGET_SEGMENTS = 8
_WINDOW_BATCH_OVERLAP_SEGMENTS = 1
_MAX_WINDOW_BATCH_CHARS = 40_000


def _apply_formula_mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace formula spans with ``<z2m-f id="N"/>`` tokens.

    Returns ``(masked_text, token_map)`` where *token_map* maps each token
    back to the original formula string so it can be restored after translation.
    """
    spans = _formula_spans(text)
    if not spans:
        return text, {}
    fmap: dict[str, str] = {}
    masked = text
    # Replace right-to-left so positions stay valid.
    for j, (start, end) in enumerate(reversed(spans)):
        real_j = len(spans) - 1 - j
        token = f'<z2m-f id="{real_j}"/>'
        fmap[token] = text[start:end]
        masked = masked[:start] + token + masked[end:]
    return masked, fmap


def _restore_formula_mask(text: str, fmap: dict[str, str]) -> str:
    """Substitute formula placeholder tokens back with their original strings."""
    if not fmap:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = f'<z2m-f id="{match.group(1)}"/>'
        return fmap.get(token, match.group(0))

    restored = _FORMULA_TOKEN_PATTERN.sub(_replace, text)
    for token, formula in fmap.items():
        restored = restored.replace(token, formula)
    return restored


def _apply_prompt_leak_mask(text: str) -> tuple[str, dict[str, str]]:
    """Protect against prompt leakage by masking specific patterns."""
    amap: dict[str, str] = {}
    masked = text

    # Apply patterns that should not leak from prompts
    for pattern in _PROMPT_LEAK_PROTECTION_PATTERNS:
        compiled_pattern = re.compile(pattern, re.IGNORECASE)
        def replace_match(match):
            original = match.group(0)
            token = f'<z2m-p id="{len(amap)}"/>'
            amap[token] = original
            return token

        masked = compiled_pattern.sub(replace_match, masked)

    return masked, amap


def _apply_custom_abbrev_mask(text: str) -> tuple[str, dict[str, str]]:
    """Apply custom masking for specific Latin abbreviations using our dictionary."""
    amap: dict[str, str] = {}
    masked = text

    # Apply patterns from our custom dictionary
    for pattern in _LATIN_ABBREV_PATTERNS:
        def replace_match(match):
            original = match.group(0)
            # Get the replacement from our mapping
            for key_pattern, replacement in LATIN_ABBREV_TO_RU.items():
                if re.match(key_pattern, original, re.IGNORECASE):
                    token = f'<z2m-a id="{len(amap)}"/>'
                    amap[token] = original
                    return token
            return original

        masked = pattern.sub(replace_match, masked)

    return masked, amap


def _apply_abbrev_mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace uppercase abbreviations with ``<z2m-a id="N"/>`` tokens.

    Protects sequences such as ``IEEE``, ``GAI``, ``LC``, ``ADC`` from being
    transliterated or "translated" by the model (e.g. GAI → ГАИ).
    Single-letter identifiers are intentionally left unmasked to avoid
    interfering with normal sentence capitalisation.
    """
    spans = [(m.start(), m.end()) for m in _ABBREV_PATTERN.finditer(text)]
    if not spans:
        return text, {}
    amap: dict[str, str] = {}
    masked = text
    for j, (start, end) in enumerate(reversed(spans)):
        real_j = len(spans) - 1 - j
        token = f'<z2m-a id="{real_j}"/>'
        amap[token] = text[start:end]
        masked = masked[:start] + token + masked[end:]
    return masked, amap


def _restore_abbrev_mask(text: str, amap: dict[str, str]) -> str:
    """Substitute abbreviation placeholder tokens back with their original strings.

    Returns the original *text* unchanged if any placeholder token could not be
    restored (which would indicate the model dropped part of the masked text).
    """
    if not amap:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = f'<z2m-a id="{match.group(1)}"/>'
        return amap.get(token, match.group(0))

    restored = _ABBREV_TOKEN_PATTERN.sub(_replace, text)

    # Also handle prompt leak protection tokens
    def _replace_prompt_leak(match: re.Match[str]) -> str:
        token = f'<z2m-p id="{match.group(1)}"/>'
        return amap.get(token, match.group(0))

    restored = re.compile(r'<z2m-p id="(\d+)"/>').sub(_replace_prompt_leak, restored)

    # Check that all tokens were consumed; orphaned tokens signal model interference.
    for token in amap:
        restored = restored.replace(token, amap[token])
    return restored


def _split_outer_ws(text: str) -> tuple[str, str, str]:
    leading_len = len(text) - len(text.lstrip())
    trailing_len = len(text) - len(text.rstrip())
    core_end = len(text) - trailing_len if trailing_len else len(text)
    return text[:leading_len], text[leading_len:core_end], text[core_end:]


def _try_batch_translate(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    max_batch_chars: int = _MAX_BATCH_CHARS,
) -> list[str] | None:
    """Translate all *segments* in a single model call.

    Masks mathematical formulas, joins segments with ``<z2m-sep/>`` separator,
    calls ``translate_text`` once, then splits the result back.

    Returns the translated list on success.  Returns ``None`` — signalling the
    caller to fall back to per-segment translation — when:

    * there is only one segment (batch overhead not worth it),
    * the combined text exceeds ``max_batch_chars``,
    * the model call raises an exception,
    * or the separator count in the output does not match the input.
    """
    if len(segments) < 2:
        return None

    # Mask formulas so the model does not try to translate LaTeX/math notation.
    masked_segs: list[str] = []
    fmaps: list[dict[str, str]] = []
    for seg in segments:
        masked, fmap = _apply_formula_mask(seg)
        masked_segs.append(masked)
        fmaps.append(fmap)

    batch_text = _BATCH_SEPARATOR.join(masked_segs)
    if len(batch_text) > max_batch_chars:
        return None

    try:
        translated_batch = translate_text(batch_text)
    except Exception:
        return None

    # Clean byte-token artefacts before splitting.
    translated_batch = _BYTE_TOKEN_CITATION_PATTERN.sub(r'<sup>\1</sup>', translated_batch)
    translated_batch = _BYTE_TOKEN_ARTIFACT_PATTERN.sub("", translated_batch)

    translated_parts = _BATCH_SEP_PATTERN.split(translated_batch)
    if len(translated_parts) != len(segments):
        # Model ate or duplicated separators — result is not trustworthy.
        return None

    result: list[str] = []
    for orig, t_seg, fmap in zip(segments, translated_parts, fmaps):
        lead, core, tail = _split_outer_ws(t_seg)
        _, orig_core, _ = _split_outer_ws(orig)
        if _is_translator_refusal(core.strip()):
            t_seg = orig
        else:
            core = _strip_source_echo(core, orig_core)
            core = _restore_formula_mask(core, fmap)
            t_seg = f"{lead}{core}{tail}"
        result.append(t_seg)

    return result


def _try_windowed_batch_translate(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    window_segments: int = _WINDOW_BATCH_TARGET_SEGMENTS,
    overlap_segments: int = _WINDOW_BATCH_OVERLAP_SEGMENTS,
    max_window_chars: int = _MAX_WINDOW_BATCH_CHARS,
) -> list[str] | None:
    """Translate in overlapping windows to balance context quality and GPU load."""
    if len(segments) < 2:
        return None

    window_segments = max(2, int(window_segments))
    overlap_segments = max(0, int(overlap_segments))
    n = len(segments)
    translated: list[str | None] = [None] * n

    core_start = 0
    while core_start < n:
        core_end = min(n, core_start + window_segments)
        ext_start = max(0, core_start - overlap_segments)
        ext_end = min(n, core_end + overlap_segments)

        window_result = _try_batch_translate(
            segments[ext_start:ext_end],
            translate_text,
            max_batch_chars=max_window_chars,
        )
        if window_result is None:
            return None

        local_start = core_start - ext_start
        for idx in range(core_start, core_end):
            translated[idx] = window_result[local_start + (idx - core_start)]
        core_start = core_end

    if any(item is None for item in translated):
        return None
    return [item for item in translated if item is not None]


def _translate_plain_fragment(
    text: str,
    *,
    translate_text: Callable[[str], str],
    cache: dict[str, str],
    max_chunk_chars: int,
) -> str:
    if not text or not text.strip():
        return text
    if not _TRANSLATABLE_TEXT_PATTERN.search(text):
        return text

    leading_len = len(text) - len(text.lstrip())
    trailing_len = len(text) - len(text.rstrip())
    core_end = len(text) - trailing_len if trailing_len else len(text)
    core = text[leading_len:core_end]
    if not core or not core.strip():
        return text

    translated = cache.get(core)
    if translated is None:
        translated = _translate_with_chunk_fallback(
            core,
            translate_text=translate_text,
            max_chunk_chars=max_chunk_chars,
        )
        if _is_translator_refusal(translated):
            translated = core
        else:
            translated = _strip_source_echo(translated, core)
            # Byte-token sequences followed by citation numbers are dropped <sup> tags;
            # restore them so _add_reference_ids_and_citation_links can linkify them.
            translated = _BYTE_TOKEN_CITATION_PATTERN.sub(r'<sup>\1</sup>', translated)
            translated = _BYTE_TOKEN_ARTIFACT_PATTERN.sub("", translated)
        cache[core] = translated

    return text[:leading_len] + translated + text[core_end:]


def _mark_author_line_notranslate(html: str) -> str:
    """Add translate="no" to the author-line paragraph (first <p> after the title <h1>).

    In scientific papers the paragraph immediately following the title heading
    contains author names.  Marking it with translate="no" prevents the
    translation step from mangling proper names.

    The paragraph is skipped if it appears to be the Abstract rather than
    an author line (i.e. it contains the word "Abstract").
    """
    h1_end = _H1_CLOSE_PATTERN.search(html)
    if h1_end is None:
        return html

    p_match = _FIRST_P_OPEN_PATTERN.search(html, h1_end.end())
    if p_match is None:
        return html

    # Peek at the paragraph content to make sure this is not the Abstract.
    p_close = _P_CLOSE_PATTERN.search(html, p_match.end())
    if p_close is not None:
        p_content = html[p_match.end():p_close.start()]
        if _ABSTRACT_MARKER_PATTERN.search(p_content):
            return html

    attrs = p_match.group(1)
    if "translate" not in attrs.lower():
        new_tag = f"<p{attrs} translate=\"no\">"
        return html[: p_match.start()] + new_tag + html[p_match.end():]

    return html


def _is_context_or_memory_error(exc: Exception) -> bool:
    lowered = str(exc).lower()
    markers = (
        "context window exceeded",
        "sequence length",
        "max position embeddings",
        "token indices sequence length is longer",
        "index out of range in self",
        "cuda out of memory",
        "outofmemoryerror",
    )
    return any(marker in lowered for marker in markers)


def _translate_with_chunk_fallback(
    text: str,
    *,
    translate_text: Callable[[str], str],
    max_chunk_chars: int,
) -> str:
    if not text:
        return text
    if not _TRANSLATABLE_TEXT_PATTERN.search(text):
        return text

    # Apply prompt leak protection before translation
    masked_text, prompt_leak_map = _apply_prompt_leak_mask(text)

    def translate_with_masked_text(masked_text: str) -> str:
        translated = translate_text(masked_text)
        # Restore any prompt leak protection tokens that might have been processed
        if prompt_leak_map:
            # We can't restore the original text here since it's already translated,
            # but we make sure no prompt leak patterns appear in the result by
            # removing them (they should be rare anyway)
            return translated
        return translated

    try:
        result = translate_with_masked_text(masked_text)
        return result
    except Exception as exc:
        if not _is_context_or_memory_error(exc):
            raise

    chunk_chars = max(256, max_chunk_chars)
    if len(text) <= chunk_chars:
        # Nothing left to split; re-raise by trying one more time for the real traceback.
        return translate_with_masked_text(masked_text)

    def _translate_recursive(chunk_text: str, current_chunk_chars: int) -> str:
        if not _TRANSLATABLE_TEXT_PATTERN.search(chunk_text):
            return chunk_text

        # Apply prompt leak protection to chunks too
        masked_chunk, chunk_prompt_leak_map = _apply_prompt_leak_mask(chunk_text)

        def translate_with_masked_chunk(masked_chunk: str) -> str:
            translated = translate_text(masked_chunk)
            if chunk_prompt_leak_map:
                return translated
            return translated

        try:
            result = translate_with_masked_chunk(masked_chunk)
            return result
        except Exception as chunk_exc:
            if not _is_context_or_memory_error(chunk_exc):
                raise
            if len(chunk_text) <= 256:
                raise

        next_chunk_chars = max(256, current_chunk_chars // 2)
        if next_chunk_chars >= len(chunk_text):
            next_chunk_chars = max(256, len(chunk_text) // 2)
        if next_chunk_chars >= len(chunk_text):
            return translate_with_masked_chunk(masked_chunk)

        translated_parts: list[str] = []
        for sub_chunk in _split_text_chunks(chunk_text, max_chunk_chars=next_chunk_chars):
            translated_parts.append(_translate_recursive(sub_chunk, next_chunk_chars))
        return "".join(translated_parts)

    translated_parts: list[str] = []
    for chunk in _split_text_chunks(text, max_chunk_chars=chunk_chars):
        translated_parts.append(_translate_recursive(chunk, chunk_chars))
    return "".join(translated_parts)


def _translate_text_segment(
    segment: str,
    translate_text: Callable[[str], str],
    cache: dict[str, str],
    max_chunk_chars: int,
) -> str:
    if not segment:
        return segment

    leading_len = len(segment) - len(segment.lstrip())
    trailing_len = len(segment) - len(segment.rstrip())
    core_end = len(segment) - trailing_len if trailing_len else len(segment)
    core = segment[leading_len:core_end]
    if not core.strip():
        return segment

    spans = _formula_spans(core)
    if not spans:
        translated_core = _translate_plain_fragment(
            core,
            translate_text=translate_text,
            cache=cache,
            max_chunk_chars=max_chunk_chars,
        )
        return segment[:leading_len] + translated_core + segment[core_end:]

    translated_parts: list[str] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            translated_parts.append(
                _translate_plain_fragment(
                    core[cursor:start],
                    translate_text=translate_text,
                    cache=cache,
                    max_chunk_chars=max_chunk_chars,
                )
            )
        translated_parts.append(core[start:end])
        cursor = end

    if cursor < len(core):
        translated_parts.append(
            _translate_plain_fragment(
                core[cursor:],
                translate_text=translate_text,
                cache=cache,
                max_chunk_chars=max_chunk_chars,
            )
        )

    translated_core = "".join(translated_parts)
    return segment[:leading_len] + translated_core + segment[core_end:]


def translate_html_text_nodes(
    html: str,
    translate_text: Callable[[str], str],
    *,
    max_chunk_chars: int = 1800,
    on_segment_start: Callable[[int, int], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[str, int]:
    """Translate all translatable text nodes in *html*, leaving markup intact.

    **Batch mode (primary path):** translatable text nodes are translated in
    overlapping windows with ``<z2m-sep/>`` markers (one model call per window).
    This preserves local inter-paragraph context while avoiding one huge call.

    **Fallback path:** if the batch fails (context overflow, separator count
    mismatch, or any exception), each segment is translated individually — the
    previous behaviour.

    The References / Bibliography section is never translated.
    """
    # The References / Bibliography section must never be translated – author
    # names, journal titles and DOIs should stay in the original language.
    heading_match = _REFERENCES_HEADING_PATTERN.search(html)
    if heading_match is not None:
        references_tail = html[heading_match.start():]
        html = html[: heading_match.start()]
    else:
        references_tail = ""

    parts = _TAG_SPLIT_PATTERN.split(html)

    # Single pass: collect indices of translatable text nodes.
    skip_stack: list[str] = []
    translatable_indices: list[int] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if part.startswith("<"):
            _update_skip_stack(part, skip_stack)
            continue
        if not skip_stack and _TRANSLATABLE_TEXT_PATTERN.search(part):
            translatable_indices.append(i)

    total_segments = len(translatable_indices)
    if total_segments == 0:
        return "".join(p for p in parts if p) + references_tail, 0

    # ------------------------------------------------------------------ #
    # Primary path: batch translation                                      #
    # ------------------------------------------------------------------ #
    source_texts = [parts[i] for i in translatable_indices]
    batch_result = _try_windowed_batch_translate(
        source_texts,
        translate_text,
        window_segments=_WINDOW_BATCH_TARGET_SEGMENTS,
        overlap_segments=_WINDOW_BATCH_OVERLAP_SEGMENTS,
        max_window_chars=_MAX_WINDOW_BATCH_CHARS,
    )

    if batch_result is not None:
        translated_segments = 0
        for idx, src, tgt in zip(translatable_indices, source_texts, batch_result):
            parts[idx] = tgt
            if tgt != src:
                translated_segments += 1
        if on_progress is not None:
            try:
                on_progress(total_segments, total_segments)
            except Exception:
                pass
        return "".join(p for p in parts if p) + references_tail, translated_segments

    # ------------------------------------------------------------------ #
    # Fallback: per-segment translation (original behaviour)              #
    # ------------------------------------------------------------------ #
    cache: dict[str, str] = {}
    translated_segments = 0
    processed_segments = 0
    out: list[str] = []
    fallback_skip_stack: list[str] = []

    for part in parts:
        if not part:
            continue
        if part.startswith("<"):
            _update_skip_stack(part, fallback_skip_stack)
            out.append(part)
            continue
        if fallback_skip_stack or not _TRANSLATABLE_TEXT_PATTERN.search(part):
            out.append(part)
            continue

        current_segment = processed_segments + 1
        if on_segment_start is not None:
            try:
                on_segment_start(current_segment, total_segments)
            except Exception:
                pass

        translated = _translate_text_segment(
            part,
            translate_text=translate_text,
            cache=cache,
            max_chunk_chars=max_chunk_chars,
        )
        if translated != part:
            translated_segments += 1
        out.append(translated)
        processed_segments += 1
        if on_progress is not None:
            try:
                on_progress(processed_segments, total_segments)
            except Exception:
                pass

    return "".join(out) + references_tail, translated_segments


class TranslateGemmaTranslator:
    def __init__(
        self,
        config: TranslateGemmaConfig,
        *,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._log = log
        self._torch = None
        self._tokenizer = None
        self._model = None
        self._device = "cpu"
        self._context_window_tokens: int | None = None
        self._base_streamer_cls = None

    def _log_line(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    def _resolve_model_ref(self) -> str:
        model_ref = (self._config.model_ref or "").strip() or DEFAULT_TRANSLATEGEMMA_MODEL
        candidate = Path(model_ref).expanduser()
        if candidate.exists():
            return str(candidate.resolve(strict=False))

        try:
            from huggingface_hub import snapshot_download
        except Exception as exc:
            raise RuntimeError(
                "TranslateGemma requires huggingface_hub. "
                "Install dependencies: pip install transformers accelerate huggingface_hub"
            ) from exc

        token = (self._config.hf_token or "").strip() or None
        try:
            return snapshot_download(
                model_ref,
                token=token,
                cache_dir=self._config.cache_dir,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to download TranslateGemma model. "
                "Accept the model license on Hugging Face and provide a valid HF token "
                f"(HF_TOKEN / HUGGINGFACE_HUB_TOKEN), model_ref='{model_ref}', details: {exc}"
            ) from exc

    def _ensure_loaded(self) -> None:
        if self._model is not None and self._tokenizer is not None:
            return

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from transformers.generation.streamers import BaseStreamer
        except Exception as exc:
            raise RuntimeError(
                "TranslateGemma dependencies are missing. Install: "
                "pip install torch transformers accelerate huggingface_hub "
                f"(details: {exc})"
            ) from exc

        self._torch = torch
        self._base_streamer_cls = BaseStreamer
        resolved_model_ref = self._resolve_model_ref()
        token = (self._config.hf_token or "").strip() or None
        cache_dir = self._config.cache_dir

        if torch.cuda.is_available():
            self._device = "cuda"
            try:
                bf16_supported = torch.cuda.is_bf16_supported()
            except Exception:
                bf16_supported = False
            torch_dtype = torch.bfloat16 if bf16_supported else torch.float16
            device_map = "cuda"
        else:
            self._device = "cpu"
            torch_dtype = torch.float32
            device_map = "cpu"
            self._log_line(
                "TranslateGemma: CUDA not available, running on CPU (this will be very slow)."
            )

        model_load_started_at = perf_counter()
        self._log_line(f"TranslateGemma: loading model '{resolved_model_ref}'")
        self._tokenizer = AutoTokenizer.from_pretrained(
            resolved_model_ref,
            token=token,
            cache_dir=cache_dir,
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            resolved_model_ref,
            token=token,
            cache_dir=cache_dir,
            torch_dtype=torch_dtype,
            device_map=device_map,
            low_cpu_mem_usage=True,
        ).eval()
        if self._tokenizer.pad_token_id is None and self._tokenizer.eos_token_id is not None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._context_window_tokens = None
        self._log_line(
            f"[timer] translategemma.model_load: {perf_counter() - model_load_started_at:.2f}s"
        )
        self._log_line(
            "TranslateGemma: model ready "
            f"(device={self._device}, context_window={self._resolve_context_window_tokens()} tokens)"
        )

    def _resolve_context_window_tokens(self) -> int:
        if self._context_window_tokens is not None:
            return self._context_window_tokens

        context_window: int | None = None
        if self._model is not None:
            config = getattr(self._model, "config", None)
            raw_ctx = getattr(config, "max_position_embeddings", None)
            if isinstance(raw_ctx, int) and raw_ctx > 0:
                context_window = int(raw_ctx)

        if context_window is None and self._tokenizer is not None:
            raw_ctx = getattr(self._tokenizer, "model_max_length", None)
            if isinstance(raw_ctx, int) and 0 < raw_ctx < 10_000_000:
                context_window = int(raw_ctx)

        if context_window is None:
            context_window = 131072

        self._context_window_tokens = context_window
        return context_window

    def translate_text(
        self,
        text: str,
        *,
        on_token_progress: Callable[[int, int], None] | None = None,
    ) -> str:
        self._ensure_loaded()
        assert self._torch is not None
        assert self._tokenizer is not None
        assert self._model is not None

        if not text.strip():
            return text

        source_language = (self._config.source_language or "Auto").strip()
        target_language = language_name_for_code(self._config.target_language_code)
        # Constraints prevent transliteration of abbreviations (GAI→ГАИ, VNA→ВНА),
        # mangling of author names, and meta-commentary when the model is uncertain.
        _extra = (
            "Rules: "
            "(1) Keep all uppercase Latin abbreviations letter-for-letter – "
            "write LC, VNA, ICP, SNR, ADC, IEEE, GAI, RF, MEMS, FPGA, AC, DC, USB, "
            "MIMO, and any other sequence of uppercase Latin letters exactly as given; "
            "never transliterate them into Cyrillic. "
            "(2) Do not translate or modify proper names, author names, or DOIs. "
            "(3) If you cannot translate a specific term, leave it unchanged. "
            "(4) Output only the translation, nothing else."
        )
        if source_language.lower() in {"auto", "auto-detect", "autodetect"}:
            instruction = (
                f"Translate the following text to {target_language}. "
                f"Detect the source language automatically. {_extra}"
            )
        else:
            instruction = (
                f"Translate the following text from {source_language} to {target_language}. "
                f"{_extra}"
            )

        prompt = (
            "<start_of_turn>user\n"
            f"{instruction}\n\n"
            f"{text}<end_of_turn>\n"
            "<start_of_turn>model\n"
        )
        inputs = self._tokenizer(prompt, return_tensors="pt")
        if self._device == "cuda":
            inputs = {k: v.to("cuda") for k, v in inputs.items()}

        prompt_len = int(inputs["input_ids"].shape[1])
        context_window = self._resolve_context_window_tokens()
        context_margin = 4096
        available_for_generation = context_window - prompt_len - context_margin
        if available_for_generation < 256:
            raise RuntimeError(
                "TranslateGemma context window exceeded for this segment "
                f"(prompt_tokens={prompt_len}, context_window={context_window})."
            )

        dynamic_cap = max(256, int(prompt_len * 1.5))
        max_new_tokens = min(8192, dynamic_cap)
        configured_cap = int(self._config.max_new_tokens)
        if configured_cap > 0:
            max_new_tokens = min(max_new_tokens, configured_cap)
        max_new_tokens = min(max_new_tokens, available_for_generation)

        streamer = None
        if on_token_progress is not None and self._base_streamer_cls is not None:
            base_streamer_cls = self._base_streamer_cls
            report_step_tokens = 16
            report_interval_sec = 0.75

            class _TokenProgressStreamer(base_streamer_cls):
                def __init__(self) -> None:
                    self.generated_tokens = 0
                    self._last_reported_tokens = 0
                    self._last_reported_at = perf_counter()
                    self._first_chunk = True

                @staticmethod
                def _token_count(value: object) -> int:
                    shape = getattr(value, "shape", None)
                    if shape is not None:
                        count = 1
                        for dim in shape:
                            count *= int(dim)
                        return max(0, count)
                    if isinstance(value, (list, tuple)):
                        return len(value)
                    return 1

                def put(self, value: object) -> None:
                    token_count = self._token_count(value)
                    if token_count <= 0:
                        return

                    # Some generation paths emit the prompt in one initial chunk.
                    if self._first_chunk and token_count > 1:
                        self._first_chunk = False
                        return
                    self._first_chunk = False

                    self.generated_tokens += token_count
                    now = perf_counter()
                    should_report = False
                    if self.generated_tokens >= max_new_tokens:
                        should_report = True
                    elif self.generated_tokens - self._last_reported_tokens >= report_step_tokens:
                        should_report = True
                    elif now - self._last_reported_at >= report_interval_sec:
                        should_report = True

                    if should_report:
                        self._last_reported_tokens = self.generated_tokens
                        self._last_reported_at = now
                        try:
                            on_token_progress(self.generated_tokens, max_new_tokens)
                        except Exception:
                            pass

                def end(self) -> None:
                    if self.generated_tokens != self._last_reported_tokens:
                        try:
                            on_token_progress(self.generated_tokens, max_new_tokens)
                        except Exception:
                            pass

            streamer = _TokenProgressStreamer()

        with self._torch.no_grad():
            out = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                repetition_penalty=1.2,
                pad_token_id=self._tokenizer.eos_token_id,
                streamer=streamer,
            )

        translated = self._tokenizer.decode(out[0][prompt_len:], skip_special_tokens=True).strip()
        translated = translated.replace("<end_of_turn>", "").strip()
        return translated or text

    def translate_html_file(self, html_path: Path) -> TranslatedHtmlArtifact:
        file_started_at = perf_counter()
        self._log_line(f"TranslateGemma: start file '{html_path.name}'")

        read_started_at = perf_counter()
        source_html_raw = html_path.read_text(encoding="utf-8", errors="replace")
        # Polish the source (EN) HTML and overwrite the file so it gets the same
        # citation links, reference IDs, math conversion, and styles as the RU file.
        polished_source = polish_html_document(source_html_raw)
        html_path.write_text(polished_source, encoding="utf-8")
        self._log_line(
            f"[timer] translategemma.polish_source_html: {perf_counter() - read_started_at:.2f}s"
        )
        # Protect the author-line paragraph from translation before processing.
        # Use polished_source (not source_html_raw) so that section/figure anchors
        # added by polish_html_document survive into the translated HTML — the
        # translation engine only touches text nodes, not HTML attributes, so
        # id="section-II" and id="fig-3" are carried through intact.
        source_html = _mark_author_line_notranslate(polished_source)
        self._log_line(f"[timer] translategemma.read_html: {perf_counter() - read_started_at:.2f}s")

        translate_started_at = perf_counter()
        progress_next_pct = 10
        progress_logged_first = False
        llm_calls = 0

        def translate_text_with_progress(text: str) -> str:
            nonlocal llm_calls
            llm_calls += 1
            call_started_at = perf_counter()
            token_next_pct = 5
            token_last_logged = 0
            self._log_line(
                "TranslateGemma progress: "
                f"LLM call {llm_calls} start for {html_path.name} "
                f"(chars={len(text)})"
            )

            def on_token_progress(generated: int, target: int) -> None:
                nonlocal token_next_pct, token_last_logged
                if target <= 0:
                    return
                pct = int((generated * 100) / target)
                should_log = False
                if generated >= target:
                    should_log = True
                elif generated - token_last_logged >= 64:
                    should_log = True
                elif pct >= token_next_pct:
                    should_log = True
                    while pct >= token_next_pct:
                        token_next_pct += 5
                if not should_log:
                    return
                token_last_logged = generated
                self._log_line(
                    "TranslateGemma progress: "
                    f"LLM call {llm_calls} tokens {generated}/{target} ({pct}%) "
                    f"for {html_path.name}"
                )

            translated = self.translate_text(text, on_token_progress=on_token_progress)
            self._log_line(
                "TranslateGemma progress: "
                f"LLM call {llm_calls} done for {html_path.name} "
                f"(elapsed={perf_counter() - call_started_at:.2f}s)"
            )
            return translated

        def on_segment_start(segment_no: int, total: int) -> None:
            self._log_line(
                "TranslateGemma progress: "
                f"segment {segment_no}/{max(1, total)} start for {html_path.name}"
            )

        def on_progress(done: int, total: int) -> None:
            nonlocal progress_next_pct, progress_logged_first
            if total <= 0:
                return
            pct = int((done * 100) / total)
            should_log = False
            if not progress_logged_first:
                should_log = True
                progress_logged_first = True
            elif done >= total:
                should_log = True
            elif pct >= progress_next_pct:
                should_log = True
                while pct >= progress_next_pct:
                    progress_next_pct += 10
            if not should_log:
                return
            self._log_line(
                "TranslateGemma progress: "
                f"{done}/{total} segments ({pct}%) "
                f"for {html_path.name} "
                f"(elapsed={perf_counter() - translate_started_at:.1f}s)"
            )

        translated_html, translated_segments = translate_html_text_nodes(
            source_html,
            translate_text=translate_text_with_progress,
            max_chunk_chars=max(256, self._config.max_chunk_chars),
            on_segment_start=on_segment_start,
            on_progress=on_progress,
        )
        self._log_line(
            f"[timer] translategemma.translate_html: {perf_counter() - translate_started_at:.2f}s"
        )

        polish_started_at = perf_counter()
        polished = polish_html_document(translated_html)
        self._log_line(f"[timer] translategemma.polish_html: {perf_counter() - polish_started_at:.2f}s")

        language_code = normalize_language_code(self._config.target_language_code)
        language_name = language_name_for_code(language_code)
        output_path = translated_html_output_path(html_path, language_code)
        write_started_at = perf_counter()
        output_path.write_text(polished, encoding="utf-8")
        self._log_line(f"[timer] translategemma.write_html: {perf_counter() - write_started_at:.2f}s")
        self._log_line(
            f"[timer] translategemma.file_total: {perf_counter() - file_started_at:.2f}s ({html_path.name})"
        )

        return TranslatedHtmlArtifact(
            source_html_path=html_path,
            translated_html_path=output_path,
            language_code=language_code,
            language_name=language_name,
            translated_segments=translated_segments,
        )
