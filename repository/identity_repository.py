# repository/identity_repository.py

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class PersonIdentityRecord:
    identity_id: str
    display_name: str | None
    canonical_cluster_id: str | None
    created_at: str
    updated_at: str
    is_protected: bool
    is_hidden: bool
    source: str


@dataclass
class IdentityClusterLinkRecord:
    link_id: str
    identity_id: str
    cluster_id: str
    link_type: str
    created_at: str
    removed_at: str | None
    is_active: bool
    source: str


class IdentityRepository:
    """
    Repository for durable person identities and identity membership.
    """

    def __init__(self, conn):
        self.conn = conn

    # ------------------------------------------------------------------
    # Identity CRUD
    # ------------------------------------------------------------------

    def create_identity(
        self,
        identity_id: str,
        created_at: str,
        updated_at: str,
        display_name: str | None = None,
        canonical_cluster_id: str | None = None,
        is_protected: bool = False,
        is_hidden: bool = False,
        source: str = "system",
    ) -> str:
        self.conn.execute(
            """
            INSERT INTO person_identity (
                identity_id,
                display_name,
                canonical_cluster_id,
                created_at,
                updated_at,
                is_protected,
                is_hidden,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity_id,
                display_name,
                canonical_cluster_id,
                created_at,
                updated_at,
                1 if is_protected else 0,
                1 if is_hidden else 0,
                source,
            ),
        )
        self.conn.commit()
        return identity_id

    def get_identity(self, identity_id: str) -> PersonIdentityRecord | None:
        cur = self.conn.execute(
            """
            SELECT
                identity_id,
                display_name,
                canonical_cluster_id,
                created_at,
                updated_at,
                is_protected,
                is_hidden,
                source
            FROM person_identity
            WHERE identity_id = ?
            """,
            (identity_id,),
        )
        row = cur.fetchone()
        return self._row_to_identity(row) if row else None

    def get_identity_by_cluster_id(self, cluster_id: str) -> PersonIdentityRecord | None:
        cur = self.conn.execute(
            """
            SELECT
                pi.identity_id,
                pi.display_name,
                pi.canonical_cluster_id,
                pi.created_at,
                pi.updated_at,
                pi.is_protected,
                pi.is_hidden,
                pi.source
            FROM person_identity pi
            INNER JOIN identity_cluster_link icl
                ON icl.identity_id = pi.identity_id
               AND icl.is_active = 1
            WHERE icl.cluster_id = ?
            ORDER BY
                CASE WHEN icl.link_type = 'canonical' THEN 0 ELSE 1 END,
                icl.created_at ASC
            LIMIT 1
            """,
            (cluster_id,),
        )
        row = cur.fetchone()
        return self._row_to_identity(row) if row else None

    def list_active_cluster_links(self, identity_id: str) -> list[IdentityClusterLinkRecord]:
        cur = self.conn.execute(
            """
            SELECT
                link_id,
                identity_id,
                cluster_id,
                link_type,
                created_at,
                removed_at,
                is_active,
                source
            FROM identity_cluster_link
            WHERE identity_id = ? AND is_active = 1
            ORDER BY
                CASE WHEN link_type = 'canonical' THEN 0 ELSE 1 END,
                created_at ASC
            """,
            (identity_id,),
        )
        return [self._row_to_link(row) for row in cur.fetchall()]

    def attach_cluster_to_identity(
        self,
        link_id: str,
        identity_id: str,
        cluster_id: str,
        link_type: str,
        created_at: str,
        source: str,
    ) -> str:
        # avoid duplicate active link
        cur = self.conn.execute(
            """
            SELECT link_id
            FROM identity_cluster_link
            WHERE identity_id = ? AND cluster_id = ? AND is_active = 1
            LIMIT 1
            """,
            (identity_id, cluster_id),
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]

        self.conn.execute(
            """
            INSERT INTO identity_cluster_link (
                link_id,
                identity_id,
                cluster_id,
                link_type,
                created_at,
                removed_at,
                is_active,
                source
            )
            VALUES (?, ?, ?, ?, ?, NULL, 1, ?)
            """,
            (
                link_id,
                identity_id,
                cluster_id,
                link_type,
                created_at,
                source,
            ),
        )
        self.conn.commit()
        return link_id

    def deactivate_cluster_link(
        self,
        identity_id: str,
        cluster_id: str,
        removed_at: str,
    ) -> int:
        cur = self.conn.execute(
            """
            UPDATE identity_cluster_link
            SET
                is_active = 0,
                removed_at = ?
            WHERE
                identity_id = ?
                AND cluster_id = ?
                AND is_active = 1
            """,
            (removed_at, identity_id, cluster_id),
        )
        self.conn.commit()
        return cur.rowcount

    def set_identity_protected(
        self,
        identity_id: str,
        is_protected: bool,
        updated_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE person_identity
            SET
                is_protected = ?,
                updated_at = ?
            WHERE identity_id = ?
            """,
            (1 if is_protected else 0, updated_at, identity_id),
        )
        self.conn.commit()

    def set_identity_hidden(
        self,
        identity_id: str,
        is_hidden: bool,
        updated_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE person_identity
            SET
                is_hidden = ?,
                updated_at = ?
            WHERE identity_id = ?
            """,
            (1 if is_hidden else 0, updated_at, identity_id),
        )
        self.conn.commit()

    def update_identity_display_name(
        self,
        identity_id: str,
        display_name: str,
        updated_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE person_identity
            SET
                display_name = ?,
                updated_at = ?
            WHERE identity_id = ?
            """,
            (display_name, updated_at, identity_id),
        )
        self.conn.commit()

    def update_canonical_cluster(
        self,
        identity_id: str,
        canonical_cluster_id: str | None,
        updated_at: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE person_identity
            SET
                canonical_cluster_id = ?,
                updated_at = ?
            WHERE identity_id = ?
            """,
            (canonical_cluster_id, updated_at, identity_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Action log
    # ------------------------------------------------------------------

    def log_identity_action(
        self,
        action_id: str,
        action_type: str,
        created_at: str,
        identity_id: str | None = None,
        cluster_id: str | None = None,
        related_identity_id: str | None = None,
        related_cluster_id: str | None = None,
        candidate_id: str | None = None,
        payload_json: str | None = None,
        created_by: str | None = None,
        is_undoable: bool = True,
    ) -> str:
        self.conn.execute(
            """
            INSERT INTO identity_action_log (
                action_id,
                action_type,
                identity_id,
                cluster_id,
                related_identity_id,
                related_cluster_id,
                candidate_id,
                payload_json,
                created_at,
                created_by,
                is_undoable,
                undone_by_action_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                action_id,
                action_type,
                identity_id,
                cluster_id,
                related_identity_id,
                related_cluster_id,
                candidate_id,
                payload_json,
                created_at,
                created_by,
                1 if is_undoable else 0,
            ),
        )
        self.conn.commit()
        return action_id

    def get_last_undoable_action_for_identity(self, identity_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            """
            SELECT
                action_id,
                action_type,
                identity_id,
                cluster_id,
                related_identity_id,
                related_cluster_id,
                candidate_id,
                payload_json,
                created_at,
                created_by,
                is_undoable,
                undone_by_action_id
            FROM identity_action_log
            WHERE
                identity_id = ?
                AND is_undoable = 1
                AND undone_by_action_id IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (identity_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "action_id": row[0],
            "action_type": row[1],
            "identity_id": row[2],
            "cluster_id": row[3],
            "related_identity_id": row[4],
            "related_cluster_id": row[5],
            "candidate_id": row[6],
            "payload_json": row[7],
            "payload": json.loads(row[7]) if row[7] else None,
            "created_at": row[8],
            "created_by": row[9],
            "is_undoable": bool(row[10]),
            "undone_by_action_id": row[11],
        }

    def mark_action_undone(
        self,
        action_id: str,
        undone_by_action_id: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE identity_action_log
            SET undone_by_action_id = ?
            WHERE action_id = ?
            """,
            (undone_by_action_id, action_id),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_identity(row) -> PersonIdentityRecord:
        return PersonIdentityRecord(
            identity_id=row[0],
            display_name=row[1],
            canonical_cluster_id=row[2],
            created_at=row[3],
            updated_at=row[4],
            is_protected=bool(row[5]),
            is_hidden=bool(row[6]),
            source=row[7],
        )

    @staticmethod
    def _row_to_link(row) -> IdentityClusterLinkRecord:
        return IdentityClusterLinkRecord(
            link_id=row[0],
            identity_id=row[1],
            cluster_id=row[2],
            link_type=row[3],
            created_at=row[4],
            removed_at=row[5],
            is_active=bool(row[6]),
            source=row[7],
        )
