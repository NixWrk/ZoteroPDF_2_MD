from __future__ import annotations

import contextlib
import shutil
import sqlite3
import sys
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zoteropdf2md.paths import ZoteroProfile, discover_zotero_profiles
from zoteropdf2md.runtime_temp import cleanup_runtime_temp_root, make_temp_dir, runtime_temp_root
from zoteropdf2md.zotero import ZoteroRepository
from zoteropdf2md.zotero_html_attachment import check_zotero_write_access


TEST_NOTE_TEXT = "тест"
TEST_NOTE_HTML = "<p>тест</p>"


@dataclass(frozen=True)
class ItemRow:
    item_id: int
    item_key: str
    item_type: str
    title: str

    @property
    def display(self) -> str:
        title = self.title.strip() or "(без названия)"
        return f"{self.item_id} | {self.item_type} | {title}"


def _log(widget: scrolledtext.ScrolledText, text: str) -> None:
    widget.insert(tk.END, text + "\n")
    widget.see(tk.END)


def _storage_write_check(zotero_data_dir: Path) -> tuple[bool, str]:
    storage_dir = zotero_data_dir / "storage"
    if not storage_dir.is_dir():
        return False, f"storage missing: {storage_dir}"
    probe = storage_dir / ".z2m_write_probe.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _db_write_check(zotero_data_dir: Path) -> tuple[bool, str]:
    try:
        check_zotero_write_access(zotero_data_dir)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _is_lock_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "database is locked" in msg
        or "database table is locked" in msg
        or "database is busy" in msg
    )


def _fetch_rows_with_lock_fallback(
    zotero_data_dir: Path,
    query: str,
    params: tuple[object, ...],
) -> list[sqlite3.Row]:
    db_path = zotero_data_dir / "zotero.sqlite"
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=1.5)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if not _is_lock_error(exc):
            raise

    runtime_root = runtime_temp_root(ROOT / "md_output")
    snapshot_dir = make_temp_dir(runtime_root, "zotero_sketch_snapshot_")
    try:
        snapshot_db = snapshot_dir / "zotero.sqlite"
        shutil.copy2(db_path, snapshot_db)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(db_path) + suffix)
            if sidecar.exists():
                with contextlib.suppress(Exception):
                    shutil.copy2(sidecar, snapshot_dir / f"zotero.sqlite{suffix}")

        snapshot_uri = f"file:{snapshot_db.as_posix()}?mode=ro"
        conn = sqlite3.connect(snapshot_uri, uri=True, timeout=1.5)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()
    finally:
        with contextlib.suppress(Exception):
            shutil.rmtree(snapshot_dir, ignore_errors=True)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _insert_row(conn: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    cols = _table_columns(conn, table)
    payload = {k: v for k, v in values.items() if k in cols}
    if not payload:
        raise RuntimeError(f"No compatible columns for table {table}")
    names = list(payload.keys())
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})",
        tuple(payload[name] for name in names),
    )


def _next_id(conn: sqlite3.Connection, table: str, id_col: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX({id_col}),0)+1 FROM {table}").fetchone()
    return int(row[0])


def _new_item_key(conn: sqlite3.Connection) -> str:
    import random
    alphabet = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
    while True:
        key = "".join(random.choice(alphabet) for _ in range(8))
        exists = conn.execute("SELECT 1 FROM items WHERE key=? LIMIT 1", (key,)).fetchone()
        if exists is None:
            return key


def _lookup_int(conn: sqlite3.Connection, query: str, params: tuple[object, ...]) -> int | None:
    try:
        row = conn.execute(query, params).fetchone()
    except sqlite3.Error:
        return None
    if row is None or row[0] is None:
        return None
    return int(row[0])


def _note_item_type_id(conn: sqlite3.Connection) -> int:
    for q in (
        "SELECT itemTypeID FROM itemTypesCombined WHERE typeName=?",
        "SELECT itemTypeID FROM itemTypes WHERE typeName=?",
    ):
        value = _lookup_int(conn, q, ("note",))
        if value is not None:
            return value
    return 1


def _parent_library_id(conn: sqlite3.Connection, parent_item_id: int) -> int | None:
    return _lookup_int(conn, "SELECT libraryID FROM items WHERE itemID=?", (parent_item_id,))


def add_test_note(zotero_data_dir: Path, parent_item_id: int) -> int:
    conn = sqlite3.connect(zotero_data_dir / "zotero.sqlite", timeout=2.0)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        exists = conn.execute("SELECT 1 FROM items WHERE itemID=? LIMIT 1", (parent_item_id,)).fetchone()
        if exists is None:
            raise RuntimeError(f"Parent item not found: {parent_item_id}")

        item_id = _next_id(conn, "items", "itemID")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _insert_row(
            conn,
            "items",
            {
                "itemID": item_id,
                "itemTypeID": _note_item_type_id(conn),
                "dateAdded": now,
                "dateModified": now,
                "libraryID": _parent_library_id(conn, parent_item_id),
                "key": _new_item_key(conn),
                "version": 0,
                "synced": 0,
            },
        )
        _insert_row(
            conn,
            "itemNotes",
            {
                "itemID": item_id,
                "parentItemID": parent_item_id,
                "note": TEST_NOTE_HTML,
                "title": TEST_NOTE_TEXT,
            },
        )
        conn.commit()
        return item_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_test_note(zotero_data_dir: Path, parent_item_id: int) -> int:
    conn = sqlite3.connect(zotero_data_dir / "zotero.sqlite", timeout=2.0)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT n.itemID
            FROM itemNotes n
            JOIN items i ON i.itemID = n.itemID
            LEFT JOIN deletedItems d ON d.itemID = n.itemID
            WHERE n.parentItemID = ?
              AND d.itemID IS NULL
              AND (
                    n.note = ?
                    OR n.note = ?
                    OR n.note LIKE '%>тест<%'
                    OR n.title = ?
              )
            ORDER BY n.itemID DESC
            LIMIT 1
            """,
            (parent_item_id, TEST_NOTE_TEXT, TEST_NOTE_HTML, TEST_NOTE_TEXT),
        ).fetchone()
        if row is None:
            conn.rollback()
            return 0

        note_item_id = int(row[0])
        # Clear related rows first (schema can vary by Zotero version).
        for table in ("itemData", "itemNotes", "collectionItems", "deletedItems"):
            try:
                conn.execute(f"DELETE FROM {table} WHERE itemID = ?", (note_item_id,))
            except sqlite3.Error:
                pass
        conn.execute("DELETE FROM items WHERE itemID = ?", (note_item_id,))
        conn.commit()
        return note_item_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class SketchApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Zotero Test Note Sketch")
        self.root.geometry("1200x760")

        self.profile_display = tk.StringVar(value="")
        self.collection_display = tk.StringVar(value="")

        self.profile_lookup: dict[str, ZoteroProfile] = {}
        self.collection_lookup: dict[str, int] = {}
        self.item_lookup: dict[str, ItemRow] = {}

        self._build_ui()
        self.refresh_profiles()

    def _build_ui(self) -> None:
        frame = tk.Frame(self.root, padx=10, pady=10)
        frame.pack(fill="both", expand=True)

        row = 0
        tk.Label(frame, text="Profiles / storages").grid(row=row, column=0, sticky="w")
        row += 1
        self.profile_combo = ttk.Combobox(frame, textvariable=self.profile_display, width=110, state="readonly")
        self.profile_combo.grid(row=row, column=0, sticky="we")
        self.profile_combo.bind("<<ComboboxSelected>>", self.on_profile_selected)
        tk.Button(frame, text="Refresh", command=self.refresh_profiles).grid(row=row, column=1, padx=6)

        row += 1
        tk.Label(frame, text="Collection").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        self.collection_combo = ttk.Combobox(frame, textvariable=self.collection_display, width=110, state="readonly")
        self.collection_combo.grid(row=row, column=0, sticky="we")
        tk.Button(frame, text="Load collections", command=self.load_collections).grid(row=row, column=1, padx=6)
        tk.Button(frame, text="Load records", command=self.load_records).grid(row=row, column=2)

        row += 1
        tk.Label(frame, text="Records in collection").grid(row=row, column=0, sticky="w", pady=(10, 0))
        row += 1
        holder = tk.Frame(frame, bd=1, relief="sunken")
        holder.grid(row=row, column=0, columnspan=3, sticky="nsew")
        self.items_list = tk.Listbox(holder, width=155, height=16)
        self.items_list.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(holder, orient="vertical", command=self.items_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.items_list.configure(yscrollcommand=scrollbar.set)

        row += 1
        actions = tk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 10))
        tk.Button(actions, text='Добавить заметку "тест"', command=self.add_note_click).pack(side="left")
        tk.Button(actions, text='Удалить заметку "тест"', command=self.delete_note_click).pack(side="left", padx=(8, 0))

        row += 1
        self.log = scrolledtext.ScrolledText(frame, width=150, height=18)
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew")

        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(row - 2, weight=1)
        frame.grid_rowconfigure(row, weight=1)

    def _selected_profile(self) -> ZoteroProfile | None:
        return self.profile_lookup.get(self.profile_display.get().strip())

    def refresh_profiles(self) -> None:
        profiles = discover_zotero_profiles()
        self.profile_lookup.clear()
        values: list[str] = []
        self.log.delete("1.0", tk.END)

        for profile in profiles:
            db_ok, db_msg = _db_write_check(profile.zotero_data_dir)
            fs_ok, fs_msg = _storage_write_check(profile.zotero_data_dir)
            status = f"db={'OK' if db_ok else 'LOCKED/NO'}; storage={'OK' if fs_ok else 'NO'}"
            display = f"{profile.display} | {status}"
            self.profile_lookup[display] = profile
            values.append(display)
            _log(self.log, f"[profile] {display}")
            if not db_ok:
                _log(self.log, f"  db detail: {db_msg}")
            if not fs_ok:
                _log(self.log, f"  storage detail: {fs_msg}")

        self.profile_combo["values"] = values
        if values:
            self.profile_display.set(values[0])
            self.on_profile_selected()
        else:
            self.profile_display.set("")
            _log(self.log, "Profiles not found.")

    def on_profile_selected(self, _event: object | None = None) -> None:
        self.collection_lookup.clear()
        self.collection_combo["values"] = []
        self.collection_display.set("")
        self.items_list.delete(0, tk.END)
        self.item_lookup.clear()

    def load_collections(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showerror("Error", "Select profile first.")
            return

        temp_root = runtime_temp_root(ROOT / "md_output")
        try:
            repo = ZoteroRepository(profile.zotero_data_dir, snapshot_temp_root=temp_root)
            collections = repo.get_collections()
        except Exception as exc:
            messagebox.showerror("Error", f"Failed to load collections: {exc}")
            return
        finally:
            cleanup_runtime_temp_root(temp_root)

        self.collection_lookup.clear()
        displays: list[str] = []
        for c in collections:
            display = f"{c.full_name} [{c.key}]"
            displays.append(display)
            self.collection_lookup[display] = c.collection_id
        self.collection_combo["values"] = displays
        if displays:
            self.collection_display.set(displays[0])
        _log(self.log, f"Loaded collections: {len(displays)}")

    def load_records(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showerror("Error", "Select profile first.")
            return
        selected_collection = self.collection_display.get().strip()
        collection_id = self.collection_lookup.get(selected_collection)
        if collection_id is None:
            messagebox.showerror("Error", "Select collection first.")
            return

        try:
            rows = _fetch_rows_with_lock_fallback(
                profile.zotero_data_dir,
                """
                SELECT
                    i.itemID,
                    i.key,
                    COALESCE(
                        (
                            SELECT idv.value
                            FROM itemData id
                            JOIN fieldsCombined f ON f.fieldID = id.fieldID
                            JOIN itemDataValues idv ON idv.valueID = id.valueID
                            WHERE id.itemID = i.itemID AND f.fieldName = 'title'
                            LIMIT 1
                        ),
                        ''
                    ) AS title,
                    COALESCE(
                        (
                            SELECT it.typeName
                            FROM itemTypesCombined it
                            WHERE it.itemTypeID = i.itemTypeID
                            LIMIT 1
                        ),
                        'item'
                    ) AS typeName
                FROM collectionItems ci
                JOIN items i ON i.itemID = ci.itemID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE ci.collectionID = ?
                  AND di.itemID IS NULL
                ORDER BY i.itemID DESC
                """,
                (collection_id,),
            )
        except sqlite3.Error:
            rows = _fetch_rows_with_lock_fallback(
                profile.zotero_data_dir,
                """
                SELECT i.itemID, i.key, '' AS title, 'item' AS typeName
                FROM collectionItems ci
                JOIN items i ON i.itemID = ci.itemID
                LEFT JOIN deletedItems di ON di.itemID = i.itemID
                WHERE ci.collectionID = ?
                  AND di.itemID IS NULL
                ORDER BY i.itemID DESC
                """,
                (collection_id,),
            )

        self.items_list.delete(0, tk.END)
        self.item_lookup.clear()
        for row in rows:
            item = ItemRow(
                item_id=int(row["itemID"]),
                item_key=str(row["key"]),
                item_type=str(row["typeName"] or "item"),
                title=str(row["title"] or ""),
            )
            self.items_list.insert(tk.END, item.display)
            self.item_lookup[item.display] = item
        _log(self.log, f"Loaded records: {len(rows)}")

    def _selected_item(self) -> ItemRow | None:
        idxs = self.items_list.curselection()
        if not idxs:
            return None
        text = self.items_list.get(idxs[0])
        return self.item_lookup.get(text)

    def add_note_click(self) -> None:
        profile = self._selected_profile()
        item = self._selected_item()
        if profile is None or item is None:
            messagebox.showerror("Error", "Select profile and record first.")
            return
        try:
            note_item_id = add_test_note(profile.zotero_data_dir, item.item_id)
            _log(self.log, f'Added test note to item {item.item_id}. noteItemID={note_item_id}')
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            _log(self.log, f"Add note failed: {exc}")

    def delete_note_click(self) -> None:
        profile = self._selected_profile()
        item = self._selected_item()
        if profile is None or item is None:
            messagebox.showerror("Error", "Select profile and record first.")
            return
        try:
            deleted_note_id = delete_test_note(profile.zotero_data_dir, item.item_id)
            if deleted_note_id == 0:
                _log(self.log, f'No test note "тест" found for item {item.item_id}')
            else:
                _log(self.log, f"Deleted test note itemID={deleted_note_id} for parent item {item.item_id}")
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            _log(self.log, f"Delete note failed: {exc}")


def main() -> None:
    root = tk.Tk()
    SketchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
