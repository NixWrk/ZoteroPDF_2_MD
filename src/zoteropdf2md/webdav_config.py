"""WebDAV server configuration: data model and JSON persistence.

This module is intentionally independent of any GUI / upload layer so that the
data model can be reused by a future web frontend.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATH = Path("webdav_config.json")


@dataclass
class WebDavServer:
    """A single WebDAV endpoint as configured by the user."""

    name: str = ""          # Display name, e.g. "Home NAS"
    url: str = ""           # Base URL, e.g. "https://dav.example.com/zotero/"
    username: str = ""
    password: str = ""
    remote_root: str = ""   # Optional subdirectory on server, e.g. "zotero_output"
    enabled: bool = True

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WebDavServer":
        """Create a WebDavServer from a dict, tolerating missing keys."""
        return cls(
            name=str(data.get("name", "")),
            url=str(data.get("url", "")),
            username=str(data.get("username", "")),
            password=str(data.get("password", "")),
            remote_root=str(data.get("remote_root", "")),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class WebDavConfig:
    """Container for a list of WebDAV servers and their JSON persistence."""

    servers: list[WebDavServer] = field(default_factory=list)

    # --- Persistence -------------------------------------------------------

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        """Save the config to ``path`` as JSON.

        Passwords are stored in cleartext; this is a local config file and
        matches the expectations of a desktop tool. The caller is responsible
        for file-system permissions.
        """
        path = Path(path)
        payload = {"servers": [s.to_dict() for s in self.servers]}
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> "WebDavConfig":
        """Load config from ``path``. Returns an empty config if missing.

        Malformed JSON is treated as an empty config — the GUI will surface
        this to the user on save.
        """
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError):
            return cls()
        servers_data = data.get("servers", []) if isinstance(data, dict) else []
        servers = [WebDavServer.from_dict(item) for item in servers_data if isinstance(item, dict)]
        return cls(servers=servers)

    # --- Queries -----------------------------------------------------------

    def get_enabled_servers(self) -> list[WebDavServer]:
        """Return only servers marked ``enabled``."""
        return [s for s in self.servers if s.enabled]
