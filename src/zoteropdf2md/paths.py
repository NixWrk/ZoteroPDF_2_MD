from __future__ import annotations

import configparser
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


_PREF_BOOL_RE = re.compile(
    r'user_pref\("extensions\.zotero\.useDataDir"\s*,\s*(true|false)\s*\)\s*;',
    re.IGNORECASE,
)
_PREF_DATA_DIR_RE = re.compile(
    r'user_pref\("extensions\.zotero\.dataDir"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)\s*;',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ZoteroProfile:
    name: str
    profile_dir: Path
    zotero_data_dir: Path
    is_default: bool
    source: str

    @property
    def display(self) -> str:
        default = " [default]" if self.is_default else ""
        return f"{self.name}{default} -> {self.zotero_data_dir}"


def _is_valid_zotero_data_dir(path: Path) -> bool:
    return (path / "zotero.sqlite").is_file() and (path / "storage").is_dir()


def _unescape_pref_string(value: str) -> str:
    # Parse the captured JS string payload with JSON semantics.
    # This preserves non-ASCII paths and handles escaped backslashes safely.
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace(r"\\", "\\").replace(r"\"", "\"")


def _extract_prefs_data_dir(profile_dir: Path) -> Path | None:
    prefs_path = profile_dir / "prefs.js"
    if not prefs_path.is_file():
        return None

    try:
        text = prefs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    use_data_dir = False
    bool_match = _PREF_BOOL_RE.search(text)
    if bool_match:
        use_data_dir = bool_match.group(1).lower() == "true"

    if not use_data_dir:
        return None

    data_dir_match = _PREF_DATA_DIR_RE.search(text)
    if not data_dir_match:
        return None

    raw = _unescape_pref_string(data_dir_match.group(1)).strip()
    if not raw:
        return None

    candidate = Path(os.path.expandvars(raw)).expanduser()
    if not candidate.is_absolute():
        candidate = (profile_dir / candidate).resolve(strict=False)
    return candidate


def _resolve_zotero_app_root(appdata_root: str | Path | None = None) -> Path | None:
    if appdata_root is None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None
        return Path(appdata) / "Zotero" / "Zotero"

    candidate = Path(appdata_root).expanduser().resolve(strict=False)
    if (candidate / "profiles.ini").is_file() and (candidate / "Profiles").is_dir():
        return candidate
    nested = candidate / "Zotero" / "Zotero"
    if (nested / "profiles.ini").is_file() and (nested / "Profiles").is_dir():
        return nested
    return candidate


def discover_zotero_profiles(appdata_root: str | Path | None = None) -> list[ZoteroProfile]:
    app_root = _resolve_zotero_app_root(appdata_root)
    if app_root is None:
        return []

    profiles_ini = app_root / "profiles.ini"
    profiles_root = app_root / "Profiles"
    if not profiles_root.is_dir():
        return []

    profiles: list[ZoteroProfile] = []
    parsed_profile_dirs: set[Path] = set()

    if profiles_ini.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(profiles_ini, encoding="utf-8")
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            sec = parser[section]

            raw_path = sec.get("Path", "").strip()
            if not raw_path:
                continue

            is_relative = sec.get("IsRelative", "1").strip() == "1"
            profile_dir = (app_root / raw_path) if is_relative else Path(raw_path).expanduser()
            profile_dir = profile_dir.resolve(strict=False)
            if not profile_dir.is_dir():
                continue

            parsed_profile_dirs.add(profile_dir)
            name = sec.get("Name", profile_dir.name).strip() or profile_dir.name
            is_default = sec.get("Default", "0").strip() == "1"

            explicit_data_dir = _extract_prefs_data_dir(profile_dir)
            if explicit_data_dir is not None and _is_valid_zotero_data_dir(explicit_data_dir):
                profiles.append(
                    ZoteroProfile(
                        name=name,
                        profile_dir=profile_dir,
                        zotero_data_dir=explicit_data_dir.resolve(strict=False),
                        is_default=is_default,
                        source="prefs.dataDir",
                    )
                )
                continue

            fallback_dir = profile_dir / "zotero"
            if _is_valid_zotero_data_dir(fallback_dir):
                profiles.append(
                    ZoteroProfile(
                        name=name,
                        profile_dir=profile_dir,
                        zotero_data_dir=fallback_dir.resolve(strict=False),
                        is_default=is_default,
                        source="profile.zotero",
                    )
                )

    # Fallback: include valid profile dirs not listed in profiles.ini
    for profile_dir in sorted(profiles_root.iterdir()):
        if profile_dir in parsed_profile_dirs or not profile_dir.is_dir():
            continue
        fallback_dir = profile_dir / "zotero"
        if _is_valid_zotero_data_dir(fallback_dir):
            profiles.append(
                ZoteroProfile(
                    name=profile_dir.name,
                    profile_dir=profile_dir.resolve(strict=False),
                    zotero_data_dir=fallback_dir.resolve(strict=False),
                    is_default=False,
                    source="profile.scan",
                )
            )

    profiles.sort(key=lambda p: (not p.is_default, p.name.lower(), str(p.zotero_data_dir).lower()))
    return profiles


def detect_default_zotero_data_dir() -> Path | None:
    profiles = discover_zotero_profiles()
    if profiles:
        return profiles[0].zotero_data_dir
    return None


def resolve_zotero_data_dir(user_path: str | Path) -> Path:
    candidate = Path(user_path).expanduser().resolve(strict=False)

    if _is_valid_zotero_data_dir(candidate):
        return candidate

    profiles_root = candidate / "Profiles"
    if profiles_root.is_dir():
        profiles = discover_zotero_profiles(candidate)
        if profiles:
            return profiles[0].zotero_data_dir

    maybe_profiles = discover_zotero_profiles(candidate)
    if maybe_profiles:
        return maybe_profiles[0].zotero_data_dir

    raise ValueError(
        "Cannot find Zotero data dir with zotero.sqlite and storage. "
        "Pick the profile '.../zotero' directory."
    )
