from __future__ import annotations

import re


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


def sanitize_filename_component(value: str, fallback: str = "item") -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", value).strip().rstrip(".")
    return cleaned or fallback


def shorten_filename_component(value: str, max_len: int) -> str:
    if max_len < 8:
        raise ValueError("max_len must be >= 8")
    if len(value) <= max_len:
        return value
    return value[:max_len].rstrip(" ._") or value[:max_len]


def make_unique_filename(stem: str, extension: str, used_lower: set[str], max_stem_len: int = 180) -> str:
    safe_stem = shorten_filename_component(sanitize_filename_component(stem), max_stem_len)
    normalized_ext = extension if extension.startswith(".") else f".{extension}"
    candidate = f"{safe_stem}{normalized_ext}"
    lower = candidate.lower()
    if lower not in used_lower:
        used_lower.add(lower)
        return candidate

    idx = 2
    while True:
        suffix = f"_{idx}"
        trimmed = shorten_filename_component(safe_stem, max_stem_len - len(suffix))
        candidate = f"{trimmed}{suffix}{normalized_ext}"
        lower = candidate.lower()
        if lower not in used_lower:
            used_lower.add(lower)
            return candidate
        idx += 1

