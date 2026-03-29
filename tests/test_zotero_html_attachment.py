import sqlite3
from pathlib import Path

from zoteropdf2md.zotero_html_attachment import attach_single_file_html


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


def test_attach_single_file_html_creates_storage_and_db_rows(tmp_path: Path) -> None:
    zotero_dir = tmp_path / "zotero"
    storage_dir = zotero_dir / "storage"
    storage_dir.mkdir(parents=True)
    db_path = zotero_dir / "zotero.sqlite"
    _prepare_minimal_zotero_db(db_path)

    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")

    result = attach_single_file_html(
        zotero_data_dir=zotero_dir,
        parent_item_id=1,
        source_pdf_path=source_pdf,
        html_content="<html><body>ok</body></html>",
    )

    assert result.parent_item_id == 1
    assert result.item_id > 1
    assert result.html_path.is_file()
    assert result.html_path.read_text(encoding="utf-8") == "<html><body>ok</body></html>"

    conn = sqlite3.connect(db_path)
    try:
        item_row = conn.execute("SELECT itemID, key FROM items WHERE itemID = ?", (result.item_id,)).fetchone()
        assert item_row is not None

        attachment_row = conn.execute(
            "SELECT parentItemID, contentType, path FROM itemAttachments WHERE itemID = ?",
            (result.item_id,),
        ).fetchone()
        assert attachment_row is not None
        assert int(attachment_row[0]) == 1
        assert attachment_row[1] == "text/html"
        assert str(attachment_row[2]).startswith("storage:")
    finally:
        conn.close()

