from pathlib import Path

from zoteropdf2md.paths import detect_default_zotero_data_dir, discover_zotero_profiles, resolve_zotero_data_dir


def _make_valid_zotero_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "zotero.sqlite").write_bytes(b"")
    (path / "storage").mkdir(exist_ok=True)


def test_discover_profiles_from_profiles_ini_relative_default(monkeypatch, tmp_path: Path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    app_root = appdata / "Zotero" / "Zotero"
    profiles_root = app_root / "Profiles"
    profile_dir = profiles_root / "abcd1234.default"
    data_dir = profile_dir / "zotero"

    _make_valid_zotero_data_dir(data_dir)
    profiles_root.mkdir(parents=True, exist_ok=True)
    (app_root / "profiles.ini").write_text(
        "[Profile0]\n"
        "Name=Main profile\n"
        "IsRelative=1\n"
        "Path=Profiles/abcd1234.default\n"
        "Default=1\n",
        encoding="utf-8",
    )

    profiles = discover_zotero_profiles(appdata)
    assert len(profiles) == 1
    assert profiles[0].name == "Main profile"
    assert profiles[0].is_default is True
    assert profiles[0].zotero_data_dir == data_dir.resolve(strict=False)

    monkeypatch.setenv("APPDATA", str(appdata))
    assert detect_default_zotero_data_dir() == data_dir.resolve(strict=False)


def test_discover_profiles_respects_prefs_custom_data_dir(tmp_path: Path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    app_root = appdata / "Zotero" / "Zotero"
    profiles_root = app_root / "Profiles"
    profile_dir = profiles_root / "efgh5678.default"
    custom_data_dir = tmp_path / "custom_data"

    _make_valid_zotero_data_dir(custom_data_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    profiles_root.mkdir(parents=True, exist_ok=True)
    (app_root / "profiles.ini").write_text(
        "[Profile0]\n"
        "Name=Custom\n"
        "IsRelative=1\n"
        "Path=Profiles/efgh5678.default\n"
        "Default=0\n",
        encoding="utf-8",
    )

    escaped = str(custom_data_dir).replace("\\", "\\\\")
    (profile_dir / "prefs.js").write_text(
        'user_pref("extensions.zotero.useDataDir", true);\n'
        f'user_pref("extensions.zotero.dataDir", "{escaped}");\n',
        encoding="utf-8",
    )

    profiles = discover_zotero_profiles(appdata)
    assert len(profiles) == 1
    assert profiles[0].zotero_data_dir == custom_data_dir.resolve(strict=False)
    assert profiles[0].source == "prefs.dataDir"


def test_resolve_zotero_data_dir_accepts_app_root(tmp_path: Path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    app_root = appdata / "Zotero" / "Zotero"
    profiles_root = app_root / "Profiles"
    profile_dir = profiles_root / "ijkl9012.default"
    data_dir = profile_dir / "zotero"

    _make_valid_zotero_data_dir(data_dir)
    profiles_root.mkdir(parents=True, exist_ok=True)
    (app_root / "profiles.ini").write_text(
        "[Profile0]\n"
        "Name=Main\n"
        "IsRelative=1\n"
        "Path=Profiles/ijkl9012.default\n"
        "Default=1\n",
        encoding="utf-8",
    )

    resolved = resolve_zotero_data_dir(app_root)
    assert resolved == data_dir.resolve(strict=False)

