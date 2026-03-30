import sqlite3
from pathlib import Path

from zoteropdf2md.zotero_pending import (
    build_pending_entry,
    enqueue_pending_attachments,
    load_pending_attachments,
    retry_pending_attachments,
)


def _prepare_minimal_zotero_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY,
                itemTypeID INTEGER,
                dateAdded TEXT,
                dateModified TEXT,
                libraryID INTEGER,
                key TEXT,
                version INTEGER,
                synced INTEGER
            );

            CREATE TABLE itemAttachments (
                itemID INTEGER PRIMARY KEY,
                parentItemID INTEGER,
                linkMode INTEGER,
                contentType TEXT,
                path TEXT,
                syncState INTEGER,
                storageModTime INTEGER,
                lastProcessedModificationTime INTEGER
            );

            CREATE TABLE itemTypes (
                itemTypeID INTEGER PRIMARY KEY,
                typeName TEXT
            );

            CREATE TABLE itemAttachmentLinkModes (
                linkModeID INTEGER PRIMARY KEY,
                linkMode TEXT
            );

            CREATE TABLE fields (
                fieldID INTEGER PRIMARY KEY,
                fieldName TEXT
            );

            CREATE TABLE itemDataValues (
                valueID INTEGER PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE itemData (
                itemID INTEGER,
                fieldID INTEGER,
                valueID INTEGER
            );
            """
        )
        conn.execute("INSERT INTO itemTypes(itemTypeID, typeName) VALUES (?, ?)", (14, "attachment"))
        conn.execute("INSERT INTO itemAttachmentLinkModes(linkModeID, linkMode) VALUES (?, ?)", (1, "imported_file"))
        conn.execute("INSERT INTO fields(fieldID, fieldName) VALUES (?, ?)", (1, "title"))
        conn.execute(
            "INSERT INTO items(itemID, itemTypeID, dateAdded, dateModified, libraryID, key, version, synced) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 2, "2025-01-01 00:00:00", "2025-01-01 00:00:00", 1, "PARENT01", 0, 0),
        )
        conn.commit()
    finally:
        conn.close()


def test_enqueue_pending_deduplicates(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF")
    html = tmp_path / "a.html"
    html.write_text("<html/>", encoding="utf-8")

    one = build_pending_entry(source_pdf_path=source, html_path=html, parent_item_id=1)
    two = build_pending_entry(source_pdf_path=source, html_path=html, parent_item_id=1)

    added1, total1 = enqueue_pending_attachments(out_dir, [one])
    added2, total2 = enqueue_pending_attachments(out_dir, [two])

    assert added1 == 1 and total1 == 1
    assert added2 == 0 and total2 == 1
    assert len(load_pending_attachments(out_dir)) == 1


def test_retry_pending_attaches_and_clears_queue(monkeypatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    zotero_dir = tmp_path / "zotero"
    (zotero_dir / "storage").mkdir(parents=True)
    _prepare_minimal_zotero_db(zotero_dir / "zotero.sqlite")

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    html = out_dir / "paper.html"
    img = out_dir / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    html.write_text('<html><body><img src="img.png"></body></html>', encoding="utf-8")

    enqueue_pending_attachments(out_dir, [build_pending_entry(source, html, 1)])

    logs: list[str] = []
    summary = retry_pending_attachments(
        zotero_data_dir=zotero_dir,
        output_dir=out_dir,
        log=logs.append,
    )

    assert summary.attached == 1
    assert summary.kept_pending == 0
    assert summary.lock_blocked is False
    assert len(load_pending_attachments(out_dir)) == 0


def test_retry_pending_stays_queued_when_locked(monkeypatch, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

    zotero_dir = tmp_path / "zotero"
    (zotero_dir / "storage").mkdir(parents=True)
    db_path = zotero_dir / "zotero.sqlite"
    _prepare_minimal_zotero_db(db_path)

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF")
    html = out_dir / "paper.html"
    html.write_text("<html/>", encoding="utf-8")
    enqueue_pending_attachments(out_dir, [build_pending_entry(source, html, 1)])

    locker = sqlite3.connect(db_path, timeout=0.1)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        logs: list[str] = []
        summary = retry_pending_attachments(
            zotero_data_dir=zotero_dir,
            output_dir=out_dir,
            log=logs.append,
        )
        assert summary.lock_blocked is True
        assert summary.kept_pending == 1
        assert len(load_pending_attachments(out_dir)) == 1
    finally:
        locker.rollback()
        locker.close()
