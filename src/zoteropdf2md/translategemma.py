from __future__ import annotations

import re
import os
import sys
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
# ASCII sentinel is more robust than XML-style tags in free-form generation.
_ABBREV_TOKEN_PATTERN = re.compile(r"@@Z2M_A(\d+)@@", re.IGNORECASE)
_TAG_TOKEN_PATTERN = re.compile(r"@@Z2M_T(\d+)@@", re.IGNORECASE)

# Additional patterns for protecting specific abbreviations from translation
_LATIN_ABBREV_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in LATIN_ABBREV_TO_RU.keys()]
_RU_ABBREV_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in RU_ABBREV_TO_LATIN.keys()]

# Patterns that mask meta-commentary prefixes the model sometimes emits before
# the actual translation.  Abbreviations are no longer listed here: since commit 3b
# the prompt already contains an abstract rule ("keep every 2+ uppercase Latin
# letters"), masking them with <z2m-p> tokens causes them to be LOST when the
# model drops the unfamiliar XML token.  Rely on the prompt rule instead.
_PROMPT_LEAK_PROTECTION_PATTERNS = [
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

# Guard against prompt-leak: if the model echoes back a comma-separated list of
# uppercase Latin acronyms followed by wording that references translation/language
# (the tail of our abbreviation instruction), strip the fragment so it never
# reaches the rendered HTML.
_PROMPT_LEAK_SIGNATURE = re.compile(
    r'[A-Z]{2,},\s*[A-Z]{2,},\s*[A-Z]{2,}.*?(?:перевод|translation|язык|language).*?[\.\n]',
    re.IGNORECASE | re.DOTALL,
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


def _update_paragraph_stack(
    tag_fragment: str,
    paragraph_stack: list[int],
    paragraph_counter: list[int],
) -> None:
    raw = tag_fragment.strip()
    if not raw.startswith("<"):
        return
    if raw.startswith("<!--") or raw.startswith("<!"):
        return

    close_match = _CLOSE_TAG_PATTERN.match(raw)
    if close_match is not None and close_match.group(1).lower() == "p":
        if paragraph_stack:
            paragraph_stack.pop()
        return

    if raw.endswith("/>"):
        return

    open_match = _OPEN_TAG_PATTERN.match(raw)
    if open_match is None or open_match.group(1).lower() != "p":
        return

    paragraph_counter[0] += 1
    paragraph_stack.append(paragraph_counter[0])


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

# ID-addressed markers injected before each segment in batch mode.
# Example payload:
#   <z2m-i1/>First segment text...
#   <z2m-i2/>Second segment text...
_BATCH_ITEM_PATTERN = re.compile(
    r"<z2m-i(\d+)\s*/>([\s\S]*?)(?=<z2m-i\d+\s*/>|\Z)",
    re.IGNORECASE,
)

# Formula placeholder tokens (ASCII sentinel) kept unchanged by the model.
_FORMULA_TOKEN_PATTERN = re.compile(r"@@Z2MF(\d+)@@", re.IGNORECASE)
# Any internal protocol marker leaking into the final translated text means the
# reconstructed batch output is not trustworthy and the segment should be
# recovered locally through single-segment translation.
_INTERNAL_MARKER_LEAK_PATTERN = re.compile(
    r"<\s*z2m-[^>]*>|@@Z2M_[A-Z0-9_]+@@",
    re.IGNORECASE,
)

# Safety limit: skip batch mode if the combined text exceeds this many characters
# (rough estimate 4 chars ≈ 1 token, limit ≈ 50k tokens input).
_MAX_BATCH_CHARS = 80_000
_WINDOW_BATCH_TARGET_SEGMENTS = 8
_WINDOW_BATCH_OVERLAP_SEGMENTS = 1
_MAX_WINDOW_BATCH_CHARS = 40_000
_HEADING_MERGE_SEPARATOR = "@@Z2M_HSEP@@"
_TABLE_CAPTION_ALLCAPS_PATTERN = re.compile(
    r"^\s*TABLE\s+([IVX]+)\s+([A-Z0-9][A-Z0-9\s,()/:+\-]{3,})\s*$"
)
_RECOVERY_CONTEXT_DEPTH = 0


def _cascade_debug(message: str) -> None:
    flag = os.getenv("Z2M_DEBUG_CASCADE", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return
    print(f"[cascade] {message}", flush=True)


def _is_recovery_context_active() -> bool:
    return _RECOVERY_CONTEXT_DEPTH > 0


def _sanitize_generation_config_for_greedy(generation_config: object | None) -> None:
    """Align generation config with deterministic decoding.

    Some model bundles ship sampling defaults (top_p/top_k/temperature) in
    generation_config.json. We run with do_sample=False, so keep config in a
    greedy-compatible state to avoid warnings and ambiguous behavior.
    """
    if generation_config is None:
        return

    def _set(attr: str, value: object) -> None:
        if not hasattr(generation_config, attr):
            return
        try:
            setattr(generation_config, attr, value)
        except Exception:
            return

    _set("do_sample", False)
    _set("top_p", 1.0)
    _set("top_k", 50)
    _set("temperature", 1.0)
    _set("typical_p", 1.0)


def _format_int_list(values: list[int], *, max_items: int = 8) -> str:
    if not values:
        return "[]"
    if len(values) <= max_items:
        return "[" + ",".join(str(v) for v in values) + "]"
    head = ",".join(str(v) for v in values[:max_items])
    return f"[{head},...+{len(values) - max_items}]"


def _apply_formula_mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace formula spans with ``@@Z2MF{N}@@`` tokens.

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
        token = f"@@Z2MF{real_j}@@"
        fmap[token] = text[start:end]
        masked = masked[:start] + token + masked[end:]
    return masked, fmap


def _restore_formula_mask(text: str, fmap: dict[str, str]) -> str:
    """Substitute formula placeholder tokens back with their original strings."""
    if not fmap:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = f"@@Z2MF{int(match.group(1))}@@"
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
                    token = f"@@Z2M_A{len(amap)}@@"
                    amap[token] = original
                    return token
            return original

        masked = pattern.sub(replace_match, masked)

    return masked, amap


def _apply_abbrev_mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace uppercase abbreviations with ``@@Z2M_A{N}@@`` tokens.

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
        token = f"@@Z2M_A{real_j}@@"
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
        token = f"@@Z2M_A{int(match.group(1))}@@"
        return amap.get(token, match.group(0))

    restored = _ABBREV_TOKEN_PATTERN.sub(_replace, text)

    # Check that all tokens were consumed; orphaned tokens signal model interference.
    for token in amap:
        restored = restored.replace(token, amap[token])
    return restored


def _apply_tag_mask(text: str) -> tuple[str, dict[str, str]]:
    """Mask inline HTML tags inside a text segment to keep recovery stable.

    This is used only for single-segment recovery paths where the model may
    hallucinate punctuation around missing inline anchors/tags.
    """
    tags = list(re.finditer(r"<[^>]+>", text))
    if not tags:
        return text, {}

    masked = text
    tmap: dict[str, str] = {}
    for j, match in enumerate(reversed(tags)):
        real_j = len(tags) - 1 - j
        token = f"@@Z2M_T{real_j}@@"
        tmap[token] = match.group(0)
        start, end = match.span()
        masked = masked[:start] + token + masked[end:]
    return masked, tmap


def _restore_tag_mask(text: str, tmap: dict[str, str]) -> str:
    """Restore inline tag tokens after recovery translation."""
    if not tmap:
        return text

    def _replace(match: re.Match[str]) -> str:
        token = f"@@Z2M_T{int(match.group(1))}@@"
        return tmap.get(token, match.group(0))

    restored = _TAG_TOKEN_PATTERN.sub(_replace, text)
    for token, original in tmap.items():
        restored = restored.replace(token, original)
    return restored


def _split_outer_ws(text: str) -> tuple[str, str, str]:
    leading_len = len(text) - len(text.lstrip())
    trailing_len = len(text) - len(text.rstrip())
    core_end = len(text) - trailing_len if trailing_len else len(text)
    return text[:leading_len], text[leading_len:core_end], text[core_end:]


def _segment_core_text(text: str) -> str:
    _, core, _ = _split_outer_ws(text)
    return core


def _has_following_translatable(segments: list[str], current_index: int) -> bool:
    for next_idx in range(current_index + 1, len(segments)):
        nxt = segments[next_idx]
        if not nxt or not nxt.strip():
            continue
        return bool(_TRANSLATABLE_TEXT_PATTERN.search(nxt))
    return False


def _has_trailing_ellipsis_artifact(
    source_seg: str,
    translated_seg: str,
    source_segments: list[str],
    source_index: int,
) -> bool:
    source_core = _segment_core_text(source_seg).rstrip()
    translated_core = _segment_core_text(translated_seg).rstrip()
    if not translated_core:
        return False

    src_has_ellipsis = source_core.endswith(("...", "…"))
    out_has_ellipsis = translated_core.endswith(("...", "…"))
    if not out_has_ellipsis or src_has_ellipsis:
        return False

    return _has_following_translatable(source_segments, source_index)


def _recover_single_segment_with_tag_mask(
    source_seg: str,
    *,
    translate_text: Callable[[str], str],
    cache: dict[str, str],
    max_chunk_chars: int,
    context_label: str,
    seg_index: int | None = None,
) -> str:
    global _RECOVERY_CONTEXT_DEPTH
    masked_source, tmap = _apply_tag_mask(source_seg)
    _RECOVERY_CONTEXT_DEPTH += 1
    try:
        recovered_masked = _translate_text_segment(
            masked_source,
            translate_text=translate_text,
            cache=cache,
            max_chunk_chars=max_chunk_chars,
        )
    finally:
        _RECOVERY_CONTEXT_DEPTH = max(0, _RECOVERY_CONTEXT_DEPTH - 1)
    restored = _restore_tag_mask(recovered_masked, tmap)
    if tmap:
        expected_ids = list(range(len(tmap)))
        found_ids = sorted(int(m.group(1)) for m in _TAG_TOKEN_PATTERN.finditer(recovered_masked))
        if found_ids != expected_ids:
            seg_info = f" seg={seg_index}" if seg_index is not None else ""
            _cascade_debug(
                f"{context_label}_lenient reason=tag_mask_dropped{seg_info} "
                f"expected={_format_int_list(expected_ids)} got={_format_int_list(found_ids)}"
            )
    return restored


def _is_duplicate_neighbor_segment(candidate: str, neighbor: str | None) -> bool:
    if neighbor is None:
        return False
    cand_norm = _normalize_ws(_segment_core_text(candidate)).lower()
    neigh_norm = _normalize_ws(_segment_core_text(neighbor)).lower()
    if len(cand_norm) < 40 or len(neigh_norm) < 40:
        return False

    span = min(30, len(cand_norm), len(neigh_norm))
    if span < 12:
        return False

    same_prefix = cand_norm[:span] == neigh_norm[:span]
    same_suffix = cand_norm[-span:] == neigh_norm[-span:]
    return same_prefix and same_suffix


def _has_min_latin_words(text: str, min_count: int = 2) -> bool:
    return len(re.findall(r"[A-Za-z]{2,}", text)) >= min_count


def _is_identity_residual(source_seg: str, translated_seg: str) -> bool:
    source_core = _normalize_ws(_segment_core_text(source_seg))
    translated_core = _normalize_ws(_segment_core_text(translated_seg))
    if not source_core or not translated_core:
        return False
    if len(translated_core) < 10:
        return False
    if not _has_min_latin_words(source_core, min_count=2):
        return False

    if translated_core == source_core:
        return True

    # Count only letters to avoid punctuation/digit skew for technical strings.
    letters = [char for char in translated_core if char.isalpha()]
    if len(letters) < 5:
        return False
    latin_chars = sum(1 for char in letters if ("A" <= char <= "Z") or ("a" <= char <= "z"))
    latin_ratio = latin_chars / len(letters)
    return latin_ratio >= 0.8


def _is_identity_residual_segment(source_seg: str, translated_seg: str) -> bool:
    # Backward-compatible alias used by older tests/call-sites.
    return _is_identity_residual(source_seg, translated_seg)


def _post_reassembly_guard_reason(
    *,
    source_segments: list[str],
    source_index: int,
    source_seg: str,
    translated_seg: str,
    prev_translated: str | None,
    next_translated: str | None,
) -> str | None:
    translated_core = _segment_core_text(translated_seg)
    if _INTERNAL_MARKER_LEAK_PATTERN.search(translated_core):
        return "marker_leak"
    if _is_identity_residual(source_seg, translated_seg):
        return "identity_residual"
    if _is_duplicate_neighbor_segment(translated_seg, prev_translated):
        return "duplicate_leak"
    if _is_duplicate_neighbor_segment(translated_seg, next_translated):
        return "duplicate_leak"
    if _has_trailing_ellipsis_artifact(source_seg, translated_seg, source_segments, source_index):
        return "trailing_ellipsis_artifact"
    return None


def _apply_post_reassembly_guards(
    *,
    source_segments: list[str],
    translated_segments: list[str],
    translate_text: Callable[[str], str],
    cache: dict[str, str],
    max_chunk_chars: int,
    context_label: str,
    segment_groups: list[int | None] | None = None,
    enable_paragraph_identity_guard: bool = True,
) -> tuple[list[str], dict[str, int]]:
    if len(source_segments) != len(translated_segments):
        return translated_segments, {}

    snapshot = list(translated_segments)
    result = list(translated_segments)
    recovery_counts: dict[str, int] = {}
    paragraph_identity_groups: dict[int, list[int]] = {}
    paragraph_identity_indices: set[int] = set()

    if (
        enable_paragraph_identity_guard
        and segment_groups is not None
        and len(segment_groups) == len(source_segments)
    ):
        grouped_indices: dict[int, list[int]] = {}
        for seg_idx, group_id in enumerate(segment_groups):
            if group_id is None:
                continue
            grouped_indices.setdefault(group_id, []).append(seg_idx)
        for group_id, indices in grouped_indices.items():
            if len(indices) < 2:
                continue
            if all(_is_identity_residual(source_segments[i], snapshot[i]) for i in indices):
                paragraph_identity_groups[group_id] = indices
                paragraph_identity_indices.update(indices)

    for idx, (source_seg, translated_seg) in enumerate(
        zip(source_segments, snapshot),
        start=1,
    ):
        prev_seg = snapshot[idx - 2] if idx > 1 else None
        next_seg = snapshot[idx] if idx < len(snapshot) else None
        reason = _post_reassembly_guard_reason(
            source_segments=source_segments,
            source_index=idx - 1,
            source_seg=source_seg,
            translated_seg=translated_seg,
            prev_translated=prev_seg,
            next_translated=next_seg,
        )
        if reason is None:
            continue
        if reason == "identity_residual" and (idx - 1) in paragraph_identity_indices:
            # Handle full-paragraph identity in one call below.
            continue

        result[idx - 1] = _recover_single_segment_with_tag_mask(
            source_seg,
            translate_text=translate_text,
            cache=cache,
            max_chunk_chars=max_chunk_chars,
            context_label=context_label,
            seg_index=idx,
        )
        recovery_counts[reason] = recovery_counts.get(reason, 0) + 1
        _cascade_debug(
            f"{context_label}_lenient "
            f"reason={reason} seg={idx} action=local_segment_recovery"
        )
        if _is_identity_residual(source_seg, result[idx - 1]):
            recovery_counts["identity_terminal"] = recovery_counts.get("identity_terminal", 0) + 1
            _cascade_debug(
                f"{context_label}_lenient reason=identity_terminal seg={idx} action=keep_recovered"
            )

    if paragraph_identity_groups:
        for group_id, indices in sorted(paragraph_identity_groups.items()):
            group_sources = [source_segments[i] for i in indices]
            group_result, group_reason = _try_batch_translate_with_reason(
                group_sources,
                translate_text,
                max_batch_chars=max(_MAX_BATCH_CHARS, max_chunk_chars * 8),
                segment_groups=[1] * len(group_sources),
                enable_paragraph_identity_guard=False,
            )
            if group_result is None:
                group_result = [
                    _recover_single_segment_with_tag_mask(
                        source_segments[i],
                        translate_text=translate_text,
                        cache=cache,
                        max_chunk_chars=max_chunk_chars,
                        context_label=context_label,
                        seg_index=i + 1,
                    )
                    for i in indices
                ]
                _cascade_debug(
                    f"{context_label}_lenient reason=identity_residual_paragraph "
                    f"group={group_id} segs={_format_int_list([i + 1 for i in indices])} "
                    f"action=local_segment_recovery reason_detail={group_reason}"
                )
            else:
                _cascade_debug(
                    f"{context_label}_lenient reason=identity_residual_paragraph "
                    f"group={group_id} segs={_format_int_list([i + 1 for i in indices])} "
                    "action=paragraph_recovery"
                )

            for local_idx, seg_idx in enumerate(indices):
                result[seg_idx] = group_result[local_idx]
                if _is_identity_residual(source_segments[seg_idx], result[seg_idx]):
                    recovery_counts["identity_terminal"] = recovery_counts.get("identity_terminal", 0) + 1
                    _cascade_debug(
                        f"{context_label}_lenient reason=identity_terminal seg={seg_idx + 1} "
                        "action=keep_recovered"
                    )
            recovery_counts["identity_residual_paragraph"] = (
                recovery_counts.get("identity_residual_paragraph", 0) + 1
            )

    return result, recovery_counts


def _try_batch_translate(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    max_batch_chars: int = _MAX_BATCH_CHARS,
) -> list[str] | None:
    result, _ = _try_batch_translate_with_reason(
        segments,
        translate_text,
        max_batch_chars=max_batch_chars,
    )
    return result


def _try_batch_translate_with_reason_legacy(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    max_batch_chars: int = _MAX_BATCH_CHARS,
) -> tuple[list[str] | None, str]:
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
        return None, f"single_segment count={len(segments)}"

    # Mask formulas so the model does not try to translate LaTeX/math notation.
    masked_segs: list[str] = []
    fmaps: list[dict[str, str]] = []
    for seg in segments:
        masked, fmap = _apply_formula_mask(seg)
        masked_segs.append(masked)
        fmaps.append(fmap)

    batch_text = _BATCH_SEPARATOR.join(masked_segs)
    if len(batch_text) > max_batch_chars:
        return None, f"batch_too_long chars={len(batch_text)} max={max_batch_chars}"

    try:
        translated_batch = translate_text(batch_text)
    except Exception as exc:
        message = str(exc).replace("\n", " ").strip()
        if len(message) > 200:
            message = message[:200] + "..."
        return None, f"llm_exception type={type(exc).__name__} msg={message!r}"

    # Clean byte-token artefacts before splitting.
    translated_batch = _BYTE_TOKEN_CITATION_PATTERN.sub(r'<sup>\1</sup>', translated_batch)
    translated_batch = _BYTE_TOKEN_ARTIFACT_PATTERN.sub("", translated_batch)

    translated_parts = _BATCH_SEP_PATTERN.split(translated_batch)
    if len(translated_parts) != len(segments):
        # Model ate or duplicated separators — result is not trustworthy.
        return None, (
            "separator_mismatch "
            f"expected={len(segments)} got={len(translated_parts)}"
        )

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

    return result, "ok"


def _try_windowed_batch_translate(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    window_segments: int = _WINDOW_BATCH_TARGET_SEGMENTS,
    overlap_segments: int = _WINDOW_BATCH_OVERLAP_SEGMENTS,
    max_window_chars: int = _MAX_WINDOW_BATCH_CHARS,
    segment_groups: list[int | None] | None = None,
) -> list[str] | None:
    result, _ = _try_windowed_batch_translate_with_reason(
        segments,
        translate_text,
        window_segments=window_segments,
        overlap_segments=overlap_segments,
        max_window_chars=max_window_chars,
        segment_groups=segment_groups,
    )
    return result


def _try_windowed_batch_translate_with_reason_legacy(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    window_segments: int = _WINDOW_BATCH_TARGET_SEGMENTS,
    overlap_segments: int = _WINDOW_BATCH_OVERLAP_SEGMENTS,
    max_window_chars: int = _MAX_WINDOW_BATCH_CHARS,
) -> tuple[list[str] | None, str]:
    """Translate in overlapping windows to balance context quality and GPU load."""
    if len(segments) < 2:
        return None, f"single_segment count={len(segments)}"

    window_segments = max(2, int(window_segments))
    overlap_segments = max(0, int(overlap_segments))
    n = len(segments)
    translated: list[str | None] = [None] * n

    core_start = 0
    while core_start < n:
        core_end = min(n, core_start + window_segments)
        ext_start = max(0, core_start - overlap_segments)
        ext_end = min(n, core_end + overlap_segments)

        window_result, reason = _try_batch_translate_with_reason(
            segments[ext_start:ext_end],
            translate_text,
            max_batch_chars=max_window_chars,
        )
        if window_result is None:
            return None, (
                "window_failed "
                f"core=[{core_start}:{core_end}) "
                f"extended=[{ext_start}:{ext_end}) "
                f"reason={reason}"
            )

        local_start = core_start - ext_start
        for idx in range(core_start, core_end):
            translated[idx] = window_result[local_start + (idx - core_start)]
        core_start = core_end

    if any(item is None for item in translated):
        return None, "window_postcheck_none_entries"
    return [item for item in translated if item is not None], "ok"


# --------------------------------------------------------------------------- #
# Batch translation protocol v2 (id-addressed + retry/bisect recovery)
# --------------------------------------------------------------------------- #

def _try_batch_translate_with_reason(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    max_batch_chars: int = _MAX_BATCH_CHARS,
    segment_groups: list[int | None] | None = None,
    enable_paragraph_identity_guard: bool = True,
) -> tuple[list[str] | None, str]:
    if len(segments) < 2:
        return None, f"single_segment count={len(segments)}"

    masked_segs: list[str] = []
    fmaps: list[dict[str, str]] = []
    amaps: list[dict[str, str]] = []
    for seg in segments:
        masked, fmap = _apply_formula_mask(seg)
        masked, amap = _apply_abbrev_mask(masked)
        masked_segs.append(masked)
        fmaps.append(fmap)
        amaps.append(amap)

    batch_text = "".join(
        f"<z2m-i{idx}/>{seg}"
        for idx, seg in enumerate(masked_segs, start=1)
    )
    if len(batch_text) > max_batch_chars:
        return None, f"batch_too_long chars={len(batch_text)} max={max_batch_chars}"

    try:
        translated_batch = translate_text(batch_text)
    except Exception as exc:
        message = str(exc).replace("\n", " ").strip()
        if len(message) > 200:
            message = message[:200] + "..."
        return None, f"llm_exception type={type(exc).__name__} msg={message!r}"

    translated_batch = _BYTE_TOKEN_CITATION_PATTERN.sub(r'<sup>\1</sup>', translated_batch)
    translated_batch = _BYTE_TOKEN_ARTIFACT_PATTERN.sub("", translated_batch)

    matches = list(_BATCH_ITEM_PATTERN.finditer(translated_batch))
    if not matches:
        return None, "structured_parse_failed blocks=0"

    parsed_by_id: dict[int, str] = {}
    duplicate_ids: set[int] = set()
    for match in matches:
        item_id = int(match.group(1))
        item_text = match.group(2)
        if item_id in parsed_by_id:
            duplicate_ids.add(item_id)
            continue
        parsed_by_id[item_id] = item_text

    if duplicate_ids:
        _cascade_debug(f"batch_fail reason=duplicate_ids ids={_format_int_list(sorted(duplicate_ids))}")
        return None, (
            "duplicate_ids "
            f"ids={_format_int_list(sorted(duplicate_ids))}"
        )

    expected_ids = list(range(1, len(segments) + 1))
    expected_ids_set = set(expected_ids)
    found_ids_set = set(parsed_by_id.keys())
    missing_ids = sorted(expected_ids_set - found_ids_set)
    extra_ids = sorted(found_ids_set - expected_ids_set)

    lenient_missing_id: int | None = None
    lenient_trailing_eos_k = 0
    lenient_missing_limit = len(segments) // 10
    if missing_ids or extra_ids:
        if (
            lenient_missing_limit >= 1
            and not extra_ids
            and len(missing_ids) == 1
            and len(parsed_by_id) == len(segments) - 1
        ):
            lenient_missing_id = missing_ids[0]
            parsed_by_id[lenient_missing_id] = segments[lenient_missing_id - 1]
        elif not extra_ids:
            trailing_limit = max(1, len(segments) // 3)
            trailing_suffix = list(
                range(len(segments) - len(missing_ids) + 1, len(segments) + 1)
            )
            if (
                missing_ids == trailing_suffix
                and len(missing_ids) <= trailing_limit
                and len(parsed_by_id) == len(segments) - len(missing_ids)
            ):
                lenient_trailing_eos_k = len(missing_ids)
                for missing_id in missing_ids:
                    parsed_by_id[missing_id] = segments[missing_id - 1]
            else:
                _cascade_debug(
                    "batch_fail reason=id_mismatch "
                    f"missing={_format_int_list(missing_ids)} "
                    f"extra={_format_int_list(extra_ids)}"
                )
                return None, (
                    "id_mismatch "
                    f"missing={_format_int_list(missing_ids)} "
                    f"extra={_format_int_list(extra_ids)}"
                )
        else:
            _cascade_debug(
                "batch_fail reason=id_mismatch "
                f"missing={_format_int_list(missing_ids)} "
                f"extra={_format_int_list(extra_ids)}"
            )
            return None, (
                "id_mismatch "
                f"missing={_format_int_list(missing_ids)} "
                f"extra={_format_int_list(extra_ids)}"
            )

    translated_parts = [parsed_by_id[item_id] for item_id in expected_ids]
    result: list[str] = []
    lenient_abbrev_recovered = 0
    lenient_formula_recovered = 0
    seg_recovery_cache: dict[str, str] = {}
    for seg_idx, (orig, t_seg, fmap, amap) in enumerate(
        zip(segments, translated_parts, fmaps, amaps),
        start=1,
    ):
        lead, core, tail = _split_outer_ws(t_seg)
        _, orig_core, _ = _split_outer_ws(orig)

        if amap:
            expected_abbrev_ids = list(range(len(amap)))
            found_abbrev_ids = sorted(
                int(m.group(1)) for m in _ABBREV_TOKEN_PATTERN.finditer(core)
            )
            if found_abbrev_ids != expected_abbrev_ids:
                missing_abbrev_ids = sorted(
                    set(expected_abbrev_ids) - set(found_abbrev_ids)
                )
                extra_abbrev_ids = sorted(
                    set(found_abbrev_ids) - set(expected_abbrev_ids)
                )
                # Model altered abbrev sentinels in this segment. Recover this
                # segment locally instead of failing the whole window.
                recovered_seg = _recover_single_segment_with_tag_mask(
                    orig,
                    translate_text=translate_text,
                    cache=seg_recovery_cache,
                    max_chunk_chars=1800,
                    context_label="batch",
                    seg_index=seg_idx,
                )
                result.append(recovered_seg)
                lenient_abbrev_recovered += 1
                _cascade_debug(
                    "batch_lenient reason=abbrev_tokens_altered "
                    f"seg={seg_idx} "
                    f"expected={_format_int_list(expected_abbrev_ids)} "
                    f"got={_format_int_list(found_abbrev_ids)} "
                    f"missing={_format_int_list(missing_abbrev_ids)} "
                    f"extra={_format_int_list(extra_abbrev_ids)} "
                    "action=local_segment_recovery"
                )
                continue

        if fmap:
            expected_token_ids = list(range(len(fmap)))
            found_token_ids = sorted(
                int(m.group(1)) for m in _FORMULA_TOKEN_PATTERN.finditer(core)
            )
            if found_token_ids != expected_token_ids:
                missing_formula_ids = sorted(
                    set(expected_token_ids) - set(found_token_ids)
                )
                extra_formula_ids = sorted(
                    set(found_token_ids) - set(expected_token_ids)
                )
                recovered_seg = _recover_single_segment_with_tag_mask(
                    orig,
                    translate_text=translate_text,
                    cache=seg_recovery_cache,
                    max_chunk_chars=1800,
                    context_label="batch",
                    seg_index=seg_idx,
                )
                result.append(recovered_seg)
                lenient_formula_recovered += 1
                _cascade_debug(
                    "batch_lenient reason=formula_tokens_altered "
                    f"seg={seg_idx} "
                    f"expected={_format_int_list(expected_token_ids)} "
                    f"got={_format_int_list(found_token_ids)} "
                    f"missing={_format_int_list(missing_formula_ids)} "
                    f"extra={_format_int_list(extra_formula_ids)} "
                    "action=local_segment_recovery"
                )
                continue

        if _is_translator_refusal(core.strip()):
            t_seg = orig
        else:
            core = _strip_source_echo(core, orig_core)
            core = _restore_abbrev_mask(core, amap)
            core = _restore_formula_mask(core, fmap)
            t_seg = f"{lead}{core}{tail}"
        result.append(t_seg)

    if segment_groups is not None and len(segment_groups) != len(segments):
        segment_groups = None

    result, guard_recovery_counts = _apply_post_reassembly_guards(
        source_segments=segments,
        translated_segments=result,
        translate_text=translate_text,
        cache=seg_recovery_cache,
        max_chunk_chars=1800,
        context_label="batch",
        segment_groups=segment_groups,
        enable_paragraph_identity_guard=enable_paragraph_identity_guard,
    )
    guard_recovered = sum(guard_recovery_counts.values())
    if guard_recovered > 0:
        details = ",".join(
            f"{name}={count}"
            for name, count in sorted(guard_recovery_counts.items())
            if count > 0
        )
        return result, (
            "ok_leak_recovery "
            f"count={guard_recovered} details={details}"
        )

    if lenient_missing_id is not None:
        return result, f"ok_lenient_missing_id={lenient_missing_id}"
    if lenient_trailing_eos_k > 0:
        return result, f"ok_lenient_trailing_eos k={lenient_trailing_eos_k}"
    if lenient_formula_recovered > 0:
        return result, f"ok_lenient_formula_recovered count={lenient_formula_recovered}"
    if lenient_abbrev_recovered > 0:
        return result, f"ok_lenient_abbrev_recovered count={lenient_abbrev_recovered}"
    return result, "ok"


def _try_windowed_batch_translate_with_reason(
    segments: list[str],
    translate_text: Callable[[str], str],
    *,
    window_segments: int = _WINDOW_BATCH_TARGET_SEGMENTS,
    overlap_segments: int = _WINDOW_BATCH_OVERLAP_SEGMENTS,
    max_window_chars: int = _MAX_WINDOW_BATCH_CHARS,
    segment_groups: list[int | None] | None = None,
) -> tuple[list[str] | None, str]:
    if len(segments) < 2:
        return None, f"single_segment count={len(segments)}"

    window_segments = max(2, int(window_segments))
    overlap_segments = max(0, int(overlap_segments))
    n = len(segments)
    translated: list[str | None] = [None] * n
    leaf_cache: dict[str, str] = {}
    leaf_max_chunk_chars = max(256, min(max_window_chars, 1800))

    def _store_core_from_window(
        *,
        core_start: int,
        core_end: int,
        ext_start: int,
        window_result: list[str],
    ) -> None:
        local_start = core_start - ext_start
        for idx in range(core_start, core_end):
            translated[idx] = window_result[local_start + (idx - core_start)]

    def _translate_core_range(core_start: int, core_end: int) -> tuple[bool, str]:
        ext_start = max(0, core_start - overlap_segments)
        ext_end = min(n, core_end + overlap_segments)
        window_result, reason = _try_batch_translate_with_reason(
            segments[ext_start:ext_end],
            translate_text,
            max_batch_chars=max_window_chars,
            segment_groups=(
                segment_groups[ext_start:ext_end]
                if segment_groups is not None
                else None
            ),
        )
        if window_result is not None:
            _store_core_from_window(
                core_start=core_start,
                core_end=core_end,
                ext_start=ext_start,
                window_result=window_result,
            )
            return True, "ok"
        _cascade_debug(
            "window_fail "
            f"core=[{core_start}:{core_end}) "
            f"extended=[{ext_start}:{ext_end}) "
            f"reason={reason}"
        )

        core_len = core_end - core_start
        if core_len <= 2:
            _cascade_debug(
                "leaf_per_segment "
                f"core=[{core_start}:{core_end}) "
                f"extended=[{ext_start}:{ext_end}) "
                f"reason={reason}"
            )
            for idx in range(core_start, core_end):
                translated[idx] = _recover_single_segment_with_tag_mask(
                    segments[idx],
                    translate_text=translate_text,
                    cache=leaf_cache,
                    max_chunk_chars=leaf_max_chunk_chars,
                    context_label="window",
                    seg_index=idx + 1,
                )
            return True, (
                "ok_leaf_per_segment "
                f"core=[{core_start}:{core_end}) "
                f"extended=[{ext_start}:{ext_end}) "
                f"reason={reason}"
            )

        mid = core_start + core_len // 2
        left_ok, left_reason = _translate_core_range(core_start, mid)
        if not left_ok:
            return False, left_reason
        right_ok, right_reason = _translate_core_range(mid, core_end)
        if not right_ok:
            return False, right_reason
        return True, "ok"

    core_start = 0
    while core_start < n:
        core_end = min(n, core_start + window_segments)
        ok, reason = _translate_core_range(core_start, core_end)
        if not ok:
            return None, reason
        core_start = core_end

    if any(item is None for item in translated):
        return None, "window_postcheck_none_entries"
    translated_full = [item for item in translated if item is not None]
    translated_full, guard_recovery_counts = _apply_post_reassembly_guards(
        source_segments=segments,
        translated_segments=translated_full,
        translate_text=translate_text,
        cache=leaf_cache,
        max_chunk_chars=leaf_max_chunk_chars,
        context_label="window",
        segment_groups=segment_groups,
    )
    guard_recovered = sum(guard_recovery_counts.values())
    if guard_recovered > 0:
        details = ",".join(
            f"{name}={count}"
            for name, count in sorted(guard_recovery_counts.items())
            if count > 0
        )
        return translated_full, (
            "ok_window_leak_recovery "
            f"count={guard_recovered} details={details}"
        )
    return translated_full, "ok"


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


def _translate_plain_fragment_preserving_abbrev(
    text: str,
    *,
    translate_text: Callable[[str], str],
    cache: dict[str, str],
    max_chunk_chars: int,
) -> str:
    if not text or not text.strip():
        return text

    masked_text, amap = _apply_abbrev_mask(text)
    translated_masked = _translate_plain_fragment(
        masked_text,
        translate_text=translate_text,
        cache=cache,
        max_chunk_chars=max_chunk_chars,
    )

    if not amap:
        return translated_masked

    expected_abbrev_ids = list(range(len(amap)))
    found_abbrev_ids = sorted(
        int(m.group(1)) for m in _ABBREV_TOKEN_PATTERN.finditer(translated_masked)
    )
    if found_abbrev_ids != expected_abbrev_ids:
        # The model altered abbreviation placeholders; safest fallback is source text.
        return text
    return _restore_abbrev_mask(translated_masked, amap)


def _retry_table_caption_translation(
    *,
    source_core: str,
    translated_core: str,
    translate_text: Callable[[str], str],
    cache: dict[str, str],
    max_chunk_chars: int,
) -> str:
    """Retry all-caps TABLE captions with normalized casing when translation is identity."""
    match = _TABLE_CAPTION_ALLCAPS_PATTERN.match(source_core)
    if match is None:
        return translated_core

    source_norm = _normalize_ws(source_core).lower()
    translated_norm = _normalize_ws(translated_core).lower()
    if translated_norm != source_norm and re.search(r"[А-Яа-яЁё]", translated_core):
        return translated_core

    roman = match.group(1)
    tail = match.group(2)
    retry_source = f"Table {roman} {tail.lower()}"
    retry_translated = _translate_plain_fragment(
        retry_source,
        translate_text=translate_text,
        cache=cache,
        max_chunk_chars=max_chunk_chars,
    ).strip()

    if not retry_translated:
        return translated_core
    if re.search(r"[А-Яа-яЁё]", retry_translated):
        return retry_translated
    return translated_core


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
        translated_core = _translate_plain_fragment_preserving_abbrev(
            core,
            translate_text=translate_text,
            cache=cache,
            max_chunk_chars=max_chunk_chars,
        )
        translated_core = _retry_table_caption_translation(
            source_core=core,
            translated_core=translated_core,
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
                _translate_plain_fragment_preserving_abbrev(
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
            _translate_plain_fragment_preserving_abbrev(
                core[cursor:],
                translate_text=translate_text,
                cache=cache,
                max_chunk_chars=max_chunk_chars,
            )
        )

    translated_core = "".join(translated_parts)
    translated_core = _retry_table_caption_translation(
        source_core=core,
        translated_core=translated_core,
        translate_text=translate_text,
        cache=cache,
        max_chunk_chars=max_chunk_chars,
    )
    return segment[:leading_len] + translated_core + segment[core_end:]


def _merge_heading_text_nodes(
    parts: list[str],
) -> tuple[list[str], dict[int, list[int]]]:
    """Merge text nodes inside heading tags (h1-h6) using ASCII sentinel.

    Returns:
        tuple of (modified_parts, merges) where merges is {merge_src_idx: [secondary_indices]}
        to track which text nodes were merged.
    """
    heading_stack: list[str] = []  # Stack of open heading tag names
    heading_text_indices: dict[int, list[int]] = {}  # depth -> list of text part indices
    current_heading_depth = -1
    merges: dict[int, list[int]] = {}  # merge_src_idx -> [other_indices_that_were_merged]

    # First pass: identify text nodes inside headings
    for i, part in enumerate(parts):
        if not part:
            continue

        if part.startswith("<"):
            # Parse opening/closing tags
            tag_name = ""
            match = re.match(r"</?([a-z0-9]+)", part, re.IGNORECASE)
            if match:
                tag_name = match.group(1).lower()

            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                if not part.startswith("</"):
                    # Opening tag
                    heading_stack.append(tag_name)
                    current_heading_depth = len(heading_stack) - 1
                    heading_text_indices[current_heading_depth] = []
                else:
                    # Closing tag
                    if heading_stack and heading_stack[-1] == tag_name:
                        heading_stack.pop()
                        current_heading_depth = len(heading_stack) - 1
        else:
            # Text node
            if heading_stack:
                # We're inside a heading
                heading_text_indices[current_heading_depth].append(i)

    # Second pass: merge text nodes within headings if >= 2 nodes
    modified_parts = list(parts)

    for depth in sorted(heading_text_indices.keys(), reverse=True):
        text_indices = heading_text_indices[depth]
        if len(text_indices) < 2:
            # Single text node or no text in heading — skip merge
            continue

        # Collect texts from all indices
        texts_to_merge = [modified_parts[idx] for idx in text_indices]

        # Merge via ASCII sentinel that survives tokenization more reliably than
        # private-use unicode separators.
        merged_text = _HEADING_MERGE_SEPARATOR.join(texts_to_merge)

        # Replace first index with merged, empty out the rest
        first_idx = text_indices[0]
        modified_parts[first_idx] = merged_text
        secondary_indices = text_indices[1:]
        for idx in secondary_indices:
            modified_parts[idx] = ""

        # Record which indices were merged
        merges[first_idx] = secondary_indices

    return modified_parts, merges


def _split_heading_text_nodes(
    parts: list[str],
    merges: dict[int, list[int]],
) -> list[str]:
    """Restore heading text nodes after translation.

    If heading separator was preserved, split and restore.
    If lost (model dropped it), keep merged in first index.

    Safely handles index bounds — returns parts unchanged if indices are invalid.
    """
    if not merges:
        return list(parts)

    result = list(parts)

    for merge_src_idx, secondary_indices in merges.items():
        # Bounds check
        if merge_src_idx >= len(result):
            continue

        # Check all secondary indices too
        if any(idx >= len(result) for idx in secondary_indices):
            continue

        merged_text = result[merge_src_idx]

        # Check if separator was preserved
        if not merged_text or _HEADING_MERGE_SEPARATOR not in merged_text:
            # Model dropped the separator — keep merged in first index
            # Secondary indices stay empty
            continue

        # Split by separator
        split_texts = merged_text.split(_HEADING_MERGE_SEPARATOR)
        expected_count = 1 + len(secondary_indices)
        if len(split_texts) != expected_count:
            # Mismatch — keep merged
            continue

        # Distribute split texts
        result[merge_src_idx] = split_texts[0]
        for i, idx in enumerate(secondary_indices, start=1):
            result[idx] = split_texts[i]

    return result


def translate_html_text_nodes(
    html: str,
    translate_text: Callable[[str], str],
    *,
    max_chunk_chars: int = 1800,
    on_segment_start: Callable[[int, int], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_batch_fallback: Callable[[str], None] | None = None,
) -> tuple[str, int]:
    """Translate all translatable text nodes in *html*, leaving markup intact.

    **Batch mode (primary path):** translatable text nodes are translated in
    overlapping windows with id markers (``<z2m-iN/>``), which allows strict
    parse/coverage validation while preserving local inter-paragraph context.

    **Recovery path:** failed windows are bisected recursively; leaf windows
    (1-2 core segments) are translated per-segment locally, so one broken
    batch does not force global document-wide fallback.

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

    # Pre-merge: combine text nodes within heading tags (h1-h6) to preserve context.
    # This fixes the issue where "Sensor" inside <h1>...<i>LC</i>Sensor</h1> was
    # translated separately and incorrectly as "Датчик" (nominative) instead of
    # "датчика" (genitive). The merge uses \x00 as separator, which the model never outputs.
    parts, heading_merges = _merge_heading_text_nodes(parts)

    # Single pass: collect indices of translatable text nodes.
    skip_stack: list[str] = []
    translatable_indices: list[int] = []
    translatable_paragraph_groups: list[int | None] = []
    paragraph_stack: list[int] = []
    paragraph_counter = [0]
    for i, part in enumerate(parts):
        if not part:
            continue
        if part.startswith("<"):
            _update_paragraph_stack(part, paragraph_stack, paragraph_counter)
            _update_skip_stack(part, skip_stack)
            continue
        if not skip_stack and _TRANSLATABLE_TEXT_PATTERN.search(part):
            translatable_indices.append(i)
            translatable_paragraph_groups.append(paragraph_stack[-1] if paragraph_stack else None)

    total_segments = len(translatable_indices)
    if total_segments == 0:
        return "".join(p for p in parts if p) + references_tail, 0

    # ------------------------------------------------------------------ #
    # Primary path: batch translation                                      #
    # ------------------------------------------------------------------ #
    source_texts = [parts[i] for i in translatable_indices]
    batch_result, batch_reason = _try_windowed_batch_translate_with_reason(
        source_texts,
        translate_text,
        window_segments=_WINDOW_BATCH_TARGET_SEGMENTS,
        overlap_segments=_WINDOW_BATCH_OVERLAP_SEGMENTS,
        max_window_chars=_MAX_WINDOW_BATCH_CHARS,
        segment_groups=translatable_paragraph_groups,
    )

    if batch_result is not None:
        for idx, src, tgt in zip(translatable_indices, source_texts, batch_result):
            parts[idx] = tgt
        if on_progress is not None:
            try:
                on_progress(total_segments, total_segments)
            except Exception:
                pass
        # Post-split: restore original heading text nodes if separator preserved.
        parts = _split_heading_text_nodes(parts, heading_merges)
        # Clean up any residual heading separators that weren't split (safety net).
        parts = [p.replace(_HEADING_MERGE_SEPARATOR, " ") for p in parts]
        # Final identity pass (after heading split) catches single-inline headings
        # and any residual EN segments that escaped window-level guards.
        final_cache: dict[str, str] = {}
        for seg_no, (part_idx, source_seg) in enumerate(
            zip(translatable_indices, source_texts),
            start=1,
        ):
            if _HEADING_MERGE_SEPARATOR in source_seg:
                continue
            translated_seg = parts[part_idx]
            if not _is_identity_residual(source_seg, translated_seg):
                continue
            recovered_seg = _recover_single_segment_with_tag_mask(
                source_seg,
                translate_text=translate_text,
                cache=final_cache,
                max_chunk_chars=max_chunk_chars,
                context_label="final",
                seg_index=seg_no,
            )
            parts[part_idx] = recovered_seg
            if _is_identity_residual(source_seg, recovered_seg):
                _cascade_debug(
                    f"final_lenient reason=identity_terminal seg={seg_no} action=keep_recovered"
                )
            else:
                _cascade_debug(
                    f"final_lenient reason=identity_residual seg={seg_no} action=local_segment_recovery"
                )

        translated_segments = sum(
            1
            for part_idx, source_seg in zip(translatable_indices, source_texts)
            if parts[part_idx] != source_seg
        )
        return "".join(p for p in parts if p) + references_tail, translated_segments

    if on_batch_fallback is not None:
        try:
            on_batch_fallback(batch_reason)
        except Exception:
            pass

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

    # In the fallback path, out[] has different indices than parts[] (empty strings
    # are skipped), so index-based split is not applicable. Instead, clean up any
    # residual heading separators by replacing them with a space.
    out = [p.replace(_HEADING_MERGE_SEPARATOR, " ") for p in out]
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
        self.recovery_calls_total = 0
        self.recovery_calls_time_s = 0.0

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
        tokenizer_kwargs = {
            "token": token,
            "cache_dir": cache_dir,
        }
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                resolved_model_ref,
                fix_mistral_regex=True,
                **tokenizer_kwargs,
            )
        except TypeError:
            # Older transformers builds may not support this flag.
            self._tokenizer = AutoTokenizer.from_pretrained(
                resolved_model_ref,
                **tokenizer_kwargs,
            )

        model_kwargs = {
            "token": token,
            "cache_dir": cache_dir,
            "dtype": torch_dtype,
            "device_map": device_map,
            "low_cpu_mem_usage": True,
        }
        try:
            self._model = AutoModelForCausalLM.from_pretrained(
                resolved_model_ref,
                **model_kwargs,
            ).eval()
        except TypeError:
            # Backward compatibility for transformers versions that still use torch_dtype.
            model_kwargs.pop("dtype", None)
            model_kwargs["torch_dtype"] = torch_dtype
            self._model = AutoModelForCausalLM.from_pretrained(
                resolved_model_ref,
                **model_kwargs,
            ).eval()
        if self._tokenizer.pad_token_id is None and self._tokenizer.eos_token_id is not None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        _sanitize_generation_config_for_greedy(getattr(self._model, "generation_config", None))

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
            "(1) Keep every sequence of 2 or more uppercase Latin letters (acronyms) "
            "letter-for-letter – never transliterate them into Cyrillic. "
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
        # Guard against prompt-leak: if the model echoed the abbreviation list back,
        # strip anything before and including the leaked fragment.
        translated = _PROMPT_LEAK_SIGNATURE.sub("", translated).strip()
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
        recovery_calls_total = 0
        recovery_calls_time_s = 0.0

        def translate_text_with_progress(text: str) -> str:
            nonlocal llm_calls, recovery_calls_total, recovery_calls_time_s
            llm_calls += 1
            call_started_at = perf_counter()
            is_recovery_call = _is_recovery_context_active()
            if is_recovery_call:
                recovery_calls_total += 1
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
            call_elapsed = perf_counter() - call_started_at
            if is_recovery_call:
                recovery_calls_time_s += call_elapsed
            self._log_line(
                "TranslateGemma progress: "
                f"LLM call {llm_calls} done for {html_path.name} "
                f"(elapsed={call_elapsed:.2f}s)"
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

        def on_batch_fallback(reason: str) -> None:
            self._log_line(
                "TranslateGemma batch fallback: "
                f"{reason} for {html_path.name}"
            )

        translated_html, translated_segments = translate_html_text_nodes(
            source_html,
            translate_text=translate_text_with_progress,
            max_chunk_chars=max(256, self._config.max_chunk_chars),
            on_segment_start=on_segment_start,
            on_progress=on_progress,
            on_batch_fallback=on_batch_fallback,
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
        self.recovery_calls_total += recovery_calls_total
        self.recovery_calls_time_s += recovery_calls_time_s
        avg_recovery_s = (
            recovery_calls_time_s / recovery_calls_total
            if recovery_calls_total > 0
            else 0.0
        )
        self._log_line(
            "[timer] translategemma.recovery_calls: "
            f"total={recovery_calls_total} "
            f"time={recovery_calls_time_s:.2f}s "
            f"avg={avg_recovery_s:.2f}s"
        )

        return TranslatedHtmlArtifact(
            source_html_path=html_path,
            translated_html_path=output_path,
            language_code=language_code,
            language_name=language_name,
            translated_segments=translated_segments,
        )
