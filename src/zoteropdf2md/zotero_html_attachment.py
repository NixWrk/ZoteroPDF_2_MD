from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .naming import make_unique_filename


_KEY_ALPHABET = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


@dataclass(frozen=True)
class AttachedHtmlResult:
    item_id: int
    item_key: str
    html_path: Path
    parent_item_id: int


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _next_id(conn: sqlite3.Connection, table: str, column: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}").fetchone()
    return int(row[0])


def _insert_row(conn: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    columns = _table_columns(conn, table)
    payload = {k: v for k, v in values.items() if k in columns}
    if not payload:
        raise RuntimeError(f"No compatible columns found for table {table}.")

    names = list(payload.keys())
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
        tuple(payload[name] for name in names),
    )


def _is_lock_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "database is locked" in msg
        or "database table is locked" in msg
        or "database is busy" in msg
    )


def _lookup_optional_int(
    conn: sqlite3.Connection,
    query: str,
    params: tuple[object, ...],
) -> int | None:
    try:
        row = conn.execute(query, params).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _attachment_item_type_id(conn: sqlite3.Connection) -> int:
    for query in (
        "SELECT itemTypeID FROM itemTypesCombined WHERE typeName = ?",
        "SELECT itemTypeID FROM itemTypes WHERE typeName = ?",
    ):
        value = _lookup_optional_int(conn, query, ("attachment",))
        if value is not None:
            return value
    return 14


def _imported_file_link_mode(conn: sqlite3.Connection) -> int:
    for query in (
        "SELECT linkModeID FROM itemAttachmentLinkModes WHERE linkMode = ?",
        "SELECT linkMode FROM itemAttachmentLinkModes WHERE linkModeName = ?",
    ):
        value = _lookup_optional_int(conn, query, ("imported_file",))
        if value is not None:
            return value
    return 1


def _library_id_for_parent(conn: sqlite3.Connection, parent_item_id: int) -> int | None:
    return _lookup_optional_int(conn, "SELECT libraryID FROM items WHERE itemID = ?", (parent_item_id,))


def _generate_unique_item_key(conn: sqlite3.Connection) -> str:
    while True:
        key = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(8))
        exists = conn.execute("SELECT 1 FROM items WHERE key = ? LIMIT 1", (key,)).fetchone()
        if exists is None:
            return key


def _get_title_field_id(conn: sqlite3.Connection) -> int | None:
    for query in (
        "SELECT fieldID FROM fieldsCombined WHERE fieldName = ?",
        "SELECT fieldID FROM fields WHERE fieldName = ?",
    ):
        field_id = _lookup_optional_int(conn, query, ("title",))
        if field_id is not None:
            return field_id
    return None


def _upsert_item_data_value(conn: sqlite3.Connection, value: str) -> int:
    existing = _lookup_optional_int(conn, "SELECT valueID FROM itemDataValues WHERE value = ? LIMIT 1", (value,))
    if existing is not None:
        return existing
    value_id = _next_id(conn, "itemDataValues", "valueID")
    _insert_row(conn, "itemDataValues", {"valueID": value_id, "value": value})
    return value_id


def _try_set_attachment_title(conn: sqlite3.Connection, item_id: int, title: str) -> None:
    field_id = _get_title_field_id(conn)
    if field_id is None:
        return
    value_id = _upsert_item_data_value(conn, title)
    _insert_row(conn, "itemData", {"itemID": item_id, "fieldID": field_id, "valueID": value_id})


def attach_single_file_html(
    zotero_data_dir: Path,
    parent_item_id: int,
    source_pdf_path: Path,
    html_content: str,
) -> AttachedHtmlResult:
    db_path = zotero_data_dir / "zotero.sqlite"
    if not db_path.is_file():
        raise FileNotFoundError(f"zotero.sqlite not found: {db_path}")

    conn = sqlite3.connect(db_path, timeout=2.0)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        parent_exists = conn.execute("SELECT 1 FROM items WHERE itemID = ? LIMIT 1", (parent_item_id,)).fetchone()
        if parent_exists is None:
            raise RuntimeError(f"Parent item not found in Zotero DB: itemID={parent_item_id}")

        item_id = _next_id(conn, "items", "itemID")
        item_key = _generate_unique_item_key(conn)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        library_id = _library_id_for_parent(conn, parent_item_id)
        _insert_row(
            conn,
            "items",
            {
                "itemID": item_id,
                "itemTypeID": _attachment_item_type_id(conn),
                "dateAdded": now,
                "dateModified": now,
                "libraryID": library_id,
                "key": item_key,
                "version": 0,
                "synced": 0,
            },
        )

        storage_dir = zotero_data_dir / "storage" / item_key
        storage_dir.mkdir(parents=True, exist_ok=True)

        html_filename = make_unique_filename(f"{source_pdf_path.stem}_marker", ".html", set(), max_stem_len=120)
        html_path = storage_dir / html_filename
        html_path.write_text(html_content, encoding="utf-8")
        mod_time_ms = int(html_path.stat().st_mtime * 1000)

        _insert_row(
            conn,
            "itemAttachments",
            {
                "itemID": item_id,
                "parentItemID": parent_item_id,
                "linkMode": _imported_file_link_mode(conn),
                "contentType": "text/html",
                "path": f"storage:{html_filename}",
                "syncState": 0,
                "storageModTime": mod_time_ms,
                "lastProcessedModificationTime": mod_time_ms,
            },
        )

        try:
            _try_set_attachment_title(conn, item_id, f"{source_pdf_path.stem} (Marker HTML)")
        except sqlite3.Error:
            # Title is optional. Attachment remains valid without explicit itemData row.
            pass

        conn.commit()
        return AttachedHtmlResult(
            item_id=item_id,
            item_key=item_key,
            html_path=html_path,
            parent_item_id=parent_item_id,
        )
    except sqlite3.OperationalError as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        if _is_lock_error(exc):
            raise RuntimeError(
                "Zotero database is locked for writing. Close Zotero and retry Zotero export mode."
            ) from exc
        raise
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
