from __future__ import annotations

import os
from pathlib import Path


def detect_default_zotero_data_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None

    profiles_root = Path(appdata) / "Zotero" / "Zotero" / "Profiles"
    if not profiles_root.exists():
        return None

    for profile_dir in sorted(profiles_root.iterdir()):
        zotero_dir = profile_dir / "zotero"
        if (zotero_dir / "zotero.sqlite").is_file() and (zotero_dir / "storage").is_dir():
            return zotero_dir

    return None


def resolve_zotero_data_dir(user_path: str | Path) -> Path:
    candidate = Path(user_path).expanduser().resolve()

    if (candidate / "zotero.sqlite").is_file() and (candidate / "storage").is_dir():
        return candidate

    profiles_root = candidate / "Profiles"
    if profiles_root.is_dir():
        for profile_dir in sorted(profiles_root.iterdir()):
            zotero_dir = profile_dir / "zotero"
            if (zotero_dir / "zotero.sqlite").is_file() and (zotero_dir / "storage").is_dir():
                return zotero_dir

    raise ValueError(
        "Cannot find Zotero data dir with zotero.sqlite and storage. "
        "Pick the profile '.../zotero' directory."
    )
