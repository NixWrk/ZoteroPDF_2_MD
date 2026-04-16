"""WebDAV upload operations using the ``requests`` library.

Kept independent of GUI code so that it can be reused by future web frontends,
CLI tooling, or background workers. Each public method returns a
``(success, message)`` tuple so the caller can decide how to surface results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth

from .webdav_config import WebDavServer


# Default HTTP timeout for all WebDAV operations.
_DEFAULT_TIMEOUT = 30


def _auth(server: WebDavServer) -> HTTPBasicAuth | None:
    """Build a Basic auth object, or None if no credentials were supplied."""
    if not server.username and not server.password:
        return None
    return HTTPBasicAuth(server.username, server.password)


def _build_base_url(server: WebDavServer) -> str:
    """Return ``server.url`` with a trailing slash and optional remote_root."""
    base = server.url.rstrip("/")
    root = server.remote_root.strip("/")
    if root:
        base = base + "/" + quote(root, safe="/")
    return base + "/"


def _build_remote_url(server: WebDavServer, remote_relative: str) -> str:
    """Compose the final URL for ``remote_relative`` beneath the server root.

    Path components are percent-encoded while forward slashes are preserved.
    """
    # Normalise separators and strip any stray leading slash.
    rel = remote_relative.replace("\\", "/").lstrip("/")
    return _build_base_url(server) + quote(rel, safe="/")


def _split_parent_parts(remote_relative: str) -> list[str]:
    """Return the directory parts of ``remote_relative`` (excluding filename)."""
    rel = remote_relative.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p]
    return parts[:-1]  # drop the filename


class WebDavUploader:
    """Small stateless helper wrapping PROPFIND / MKCOL / PUT."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    # --- Connectivity ------------------------------------------------------

    def test_connection(self, server: WebDavServer) -> tuple[bool, str]:
        """PROPFIND the server root with ``Depth: 0``.

        Returns ``(True, message)`` on any 2xx/207 response.
        """
        url = _build_base_url(server)
        try:
            response = requests.request(
                "PROPFIND",
                url,
                auth=_auth(server),
                headers={"Depth": "0"},
                timeout=self.timeout,
            )
        except requests.Timeout:
            return False, f"Timeout after {self.timeout}s"
        except requests.ConnectionError as exc:
            return False, f"Connection error: {exc}"
        except requests.RequestException as exc:
            return False, f"Request error: {exc}"

        if 200 <= response.status_code < 300 or response.status_code == 207:
            return True, f"OK ({response.status_code}) at {url}"
        if response.status_code == 401:
            return False, "Unauthorized (401) — check username/password"
        if response.status_code == 404:
            return False, f"Not found (404) at {url}"
        return False, f"HTTP {response.status_code}: {response.reason}"

    # --- Directories -------------------------------------------------------

    def ensure_remote_dirs(
        self,
        server: WebDavServer,
        remote_path_parts: Iterable[str],
    ) -> tuple[bool, str]:
        """Create each parent directory below the server root via MKCOL.

        Collections that already exist return HTTP 405 (Method Not Allowed) on
        most WebDAV servers; that is treated as success. Any other non-2xx
        status aborts and returns ``(False, message)``.
        """
        parts = [p for p in remote_path_parts if p]
        if not parts:
            return True, "No directories to create"

        base = _build_base_url(server)
        auth = _auth(server)
        cumulative = ""
        for part in parts:
            cumulative = f"{cumulative}/{quote(part, safe='')}" if cumulative else quote(part, safe="")
            url = base + cumulative + "/"
            try:
                response = requests.request(
                    "MKCOL",
                    url,
                    auth=auth,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                return False, f"MKCOL error at {url}: {exc}"

            # 201 Created, 200 OK — fine.
            # 405 Method Not Allowed — typically "directory already exists".
            # 301/302 — some servers redirect; accept.
            if response.status_code in (200, 201, 204, 301, 302, 405):
                continue
            return False, f"MKCOL {url} -> HTTP {response.status_code}: {response.reason}"
        return True, "Remote directories ensured"

    # --- File upload -------------------------------------------------------

    def upload_file(
        self,
        server: WebDavServer,
        local_path: Path,
        remote_relative: str,
    ) -> tuple[bool, str]:
        """Upload ``local_path`` to ``<server>/<remote_root>/<remote_relative>``.

        Creates the parent directory hierarchy on the server first.
        """
        local_path = Path(local_path)
        if not local_path.is_file():
            return False, f"Local file not found: {local_path}"

        # Make sure all parent directories exist on the server.
        dir_ok, dir_msg = self.ensure_remote_dirs(server, _split_parent_parts(remote_relative))
        if not dir_ok:
            return False, dir_msg

        url = _build_remote_url(server, remote_relative)
        try:
            with local_path.open("rb") as handle:
                response = requests.put(
                    url,
                    data=handle,
                    auth=_auth(server),
                    timeout=self.timeout,
                )
        except requests.Timeout:
            return False, f"Timeout uploading to {url}"
        except requests.ConnectionError as exc:
            return False, f"Connection error to {url}: {exc}"
        except requests.RequestException as exc:
            return False, f"Request error to {url}: {exc}"
        except OSError as exc:
            return False, f"Local I/O error: {exc}"

        # 200/201/204 all represent successful PUT on various servers.
        if response.status_code in (200, 201, 204):
            return True, f"Uploaded ({response.status_code}) -> {url}"
        if response.status_code == 401:
            return False, "Unauthorized (401) — check username/password"
        return False, f"HTTP {response.status_code}: {response.reason}"

    # --- Batch helper ------------------------------------------------------

    def upload_html_output(
        self,
        server: WebDavServer,
        output_dir: Path,
        log: Callable[[str], None] | None = None,
    ) -> tuple[int, int]:
        """Walk ``output_dir`` and PUT every ``*.html`` file to the server.

        Returns ``(uploaded, failed)`` counts. Never raises.
        """
        output_dir = Path(output_dir)
        uploaded = 0
        failed = 0
        if not output_dir.is_dir():
            if log:
                log(f"WebDAV upload skipped: output dir missing: {output_dir}")
            return uploaded, failed

        for html_path in sorted(output_dir.rglob("*.html")):
            try:
                relative = html_path.relative_to(output_dir).as_posix()
            except ValueError:
                # Defensive — rglob should always produce a child.
                continue
            ok, msg = self.upload_file(server, html_path, relative)
            if ok:
                uploaded += 1
                if log:
                    log(f"WebDAV upload: {server.name} <- {html_path.name}")
            else:
                failed += 1
                if log:
                    log(f"WebDAV upload failed: {server.name}: {msg}")
        return uploaded, failed
