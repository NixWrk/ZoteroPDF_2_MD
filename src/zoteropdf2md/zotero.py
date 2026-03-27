from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .models import AttachmentRecord, Collection


class ZoteroRepository:
    def __init__(self, zotero_data_dir: Path) -> None:
        self.zotero_data_dir = zotero_data_dir
        self.db_path = zotero_data_dir / "zotero.sqlite"
        if not self.db_path.is_file():
            raise FileNotFoundError(f"zotero.sqlite not found: {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def get_collections(self) -> list[Collection]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT collectionID, key, collectionName, parentCollectionID
                FROM collections
                """
            ).fetchall()

        raw = {
            int(r["collectionID"]): {
                "collection_id": int(r["collectionID"]),
                "key": str(r["key"]),
                "name": str(r["collectionName"]),
                "parent_collection_id": int(r["parentCollectionID"]) if r["parentCollectionID"] is not None else None,
            }
            for r in rows
        }

        memo: dict[int, str] = {}

        def full_name(collection_id: int, trail: set[int] | None = None) -> str:
            if collection_id in memo:
                return memo[collection_id]

            data = raw[collection_id]
            parent_id = data["parent_collection_id"]
            if parent_id is None:
                name = data["name"]
                memo[collection_id] = name
                return name

            trail = set() if trail is None else set(trail)
            if collection_id in trail:
                name = data["name"]
                memo[collection_id] = name
                return name

            trail.add(collection_id)
            if parent_id in raw:
                name = f"{full_name(parent_id, trail)} / {data['name']}"
            else:
                name = data["name"]
            memo[collection_id] = name
            return name

        collections = [
            Collection(
                collection_id=data["collection_id"],
                key=data["key"],
                name=data["name"],
                parent_collection_id=data["parent_collection_id"],
                full_name=full_name(collection_id),
            )
            for collection_id, data in raw.items()
        ]
        return sorted(collections, key=lambda c: c.full_name.lower())

    def get_collection_by_key(self, key: str) -> Collection:
        for collection in self.get_collections():
            if collection.key == key:
                return collection
        raise KeyError(f"Collection key not found: {key}")

    def get_descendant_collection_ids(self, root_collection_id: int, include_subcollections: bool) -> list[int]:
        if not include_subcollections:
            return [root_collection_id]

        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH RECURSIVE sub(collectionID) AS (
                    SELECT collectionID
                    FROM collections
                    WHERE collectionID = ?
                    UNION ALL
                    SELECT c.collectionID
                    FROM collections c
                    JOIN sub s ON c.parentCollectionID = s.collectionID
                )
                SELECT collectionID
                FROM sub
                """,
                (root_collection_id,),
            ).fetchall()
        return [int(r["collectionID"]) for r in rows]

    def get_attachment_records(self, collection_ids: Iterable[int]) -> list[AttachmentRecord]:
        collection_ids = list(collection_ids)
        if not collection_ids:
            return []

        placeholders = ",".join("?" for _ in collection_ids)

        query = f"""
            WITH selected_items AS (
                SELECT DISTINCT ci.itemID
                FROM collectionItems ci
                LEFT JOIN deletedItems di ON di.itemID = ci.itemID
                WHERE ci.collectionID IN ({placeholders})
                  AND di.itemID IS NULL
            ),
            attachment_candidates AS (
                SELECT DISTINCT ia.itemID
                FROM itemAttachments ia
                WHERE ia.parentItemID IN (SELECT itemID FROM selected_items)
                UNION
                SELECT DISTINCT ia.itemID
                FROM itemAttachments ia
                WHERE ia.itemID IN (SELECT itemID FROM selected_items)
            )
            SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.path, ia.contentType, i.key AS attachmentKey
            FROM itemAttachments ia
            JOIN items i ON i.itemID = ia.itemID
            LEFT JOIN deletedItems di ON di.itemID = ia.itemID
            WHERE ia.itemID IN (SELECT itemID FROM attachment_candidates)
              AND di.itemID IS NULL
        """

        with self._connect() as conn:
            rows = conn.execute(query, tuple(collection_ids)).fetchall()

        records = [
            AttachmentRecord(
                item_id=int(r["itemID"]),
                attachment_key=str(r["attachmentKey"]),
                parent_item_id=int(r["parentItemID"]) if r["parentItemID"] is not None else None,
                link_mode=int(r["linkMode"]) if r["linkMode"] is not None else None,
                path=str(r["path"]) if r["path"] is not None else None,
                content_type=str(r["contentType"]) if r["contentType"] is not None else None,
            )
            for r in rows
        ]
        return records
