# services/entity_graph.py
# Entity Graph - Relationship graph connecting photos to structured entities
#
# Models the connections between photos and their metadata entities:
# - People (face clusters via branch_key)
# - Locations (GPS coordinates + reverse-geocoded names)
# - Dates (year/month temporal buckets)
# - Tags (user-applied tags)
# - Devices (import source devices)
# - Events (temporal clusters of photos)
#
# The graph enables relationship-based queries:
# - "Who was at the beach trip?" → people co-occurring with beach photos
# - "Where was John photographed?" → locations linked to a person
# - "What events include both John and Sarah?" → temporal intersection

"""
EntityGraph - Photo entity relationship graph.

Architecture:
- Nodes: photos, people, locations, dates, tags, devices
- Edges: weighted relationships between nodes
- Lazy-loaded from database on first query, cached with TTL
- Read-only view of existing data (no separate storage needed)

Usage:
    from services.entity_graph import EntityGraph

    graph = EntityGraph(project_id=1)

    # Get all entities linked to a photo
    entities = graph.get_photo_entities(photo_path)

    # Get all photos linked to an entity
    paths = graph.get_entity_photos("person", "face_001")

    # Find related entities (e.g. locations where a person appears)
    related = graph.get_related_entities("person", "face_001", "location")

    # Get co-occurrence strength between two entities
    strength = graph.co_occurrence("person", "face_001", "person", "face_002")
"""

from __future__ import annotations
import time
from collections import defaultdict
from typing import List, Dict, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class EntityNode:
    """A node in the entity graph."""
    entity_type: str    # "person", "location", "date", "tag", "device"
    entity_id: str      # branch_key, location_name, "2024-06", tag_name, device_id
    display_name: str   # Human-readable name
    photo_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EntityEdge:
    """An edge connecting two entities through co-occurrence in photos."""
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    weight: int = 0     # Number of photos where both entities co-occur
    photo_paths: List[str] = field(default_factory=list)


class EntityGraph:
    """
    Read-only entity relationship graph built from photo metadata.

    The graph is lazily constructed from the database and cached.
    No separate storage is needed - it's a computed view over:
    - photo_metadata (dates, locations, tags)
    - face_crops / face_branch_reps (people)
    - mobile_devices / device_files (devices)
    """

    _CACHE_TTL = 120.0  # Rebuild graph every 2 minutes

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._nodes: Dict[Tuple[str, str], EntityNode] = {}
        self._photo_entities: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        self._entity_photos: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        self._built_at: float = 0.0

    def _get_db(self):
        from repository.base_repository import DatabaseConnection
        return DatabaseConnection()

    def _ensure_built(self):
        """Ensure the graph is built and fresh."""
        if time.time() - self._built_at < self._CACHE_TTL:
            return
        self._build()

    def _build(self):
        """Build the entity graph from database tables."""
        start = time.time()
        self._nodes.clear()
        self._photo_entities.clear()
        self._entity_photos.clear()

        try:
            db = self._get_db()
            with db.get_connection() as conn:
                self._build_date_entities(conn)
                self._build_location_entities(conn)
                self._build_person_entities(conn)
                self._build_tag_entities(conn)
                self._build_device_entities(conn)
        except Exception as e:
            logger.warning(f"[EntityGraph] Build failed: {e}")

        self._built_at = time.time()
        elapsed = (time.time() - start) * 1000
        logger.info(
            f"[EntityGraph] Built graph: {len(self._nodes)} entities, "
            f"{sum(len(v) for v in self._photo_entities.values())} photo-entity links "
            f"in {elapsed:.0f}ms"
        )

    def _add_link(self, photo_path: str, entity_type: str, entity_id: str,
                  display_name: str, metadata: Optional[Dict] = None):
        """Add a photo-entity link to the graph."""
        key = (entity_type, entity_id)
        if key not in self._nodes:
            self._nodes[key] = EntityNode(
                entity_type=entity_type,
                entity_id=entity_id,
                display_name=display_name,
                metadata=metadata or {},
            )
        self._nodes[key].photo_count += 1
        self._photo_entities[photo_path].add(key)
        self._entity_photos[key].add(photo_path)

    def _build_date_entities(self, conn):
        """Build date entities (year-month buckets)."""
        try:
            cursor = conn.execute(
                "SELECT path, created_date, date_taken, created_year "
                "FROM photo_metadata WHERE project_id = ?",
                (self.project_id,)
            )
            for row in cursor.fetchall():
                path = row["path"]
                date_str = row["created_date"] or row["date_taken"] or ""
                if date_str and len(str(date_str)) >= 7:
                    # Year-month bucket
                    ym = str(date_str)[:7]  # "2024-06"
                    self._add_link(path, "date", ym, ym)
                    # Year bucket
                    year = str(date_str)[:4]
                    if year.isdigit():
                        self._add_link(path, "year", year, year)
        except Exception as e:
            logger.debug(f"[EntityGraph] Date entity build failed: {e}")

    def _build_location_entities(self, conn):
        """Build location entities from GPS data and location names."""
        try:
            cursor = conn.execute(
                "SELECT path, location_name, gps_latitude, gps_longitude "
                "FROM photo_metadata "
                "WHERE project_id = ? AND location_name IS NOT NULL "
                "AND location_name != ''",
                (self.project_id,)
            )
            for row in cursor.fetchall():
                path = row["path"]
                loc_name = row["location_name"]
                if loc_name:
                    self._add_link(path, "location", loc_name.lower(), loc_name, {
                        "latitude": row["gps_latitude"],
                        "longitude": row["gps_longitude"],
                    })
        except Exception as e:
            logger.debug(f"[EntityGraph] Location entity build failed: {e}")

    def _build_person_entities(self, conn):
        """Build person entities from face clusters."""
        try:
            # Get person names
            reps = conn.execute(
                "SELECT branch_key, label FROM face_branch_reps "
                "WHERE project_id = ?",
                (self.project_id,)
            ).fetchall()
            name_map = {r["branch_key"]: r["label"] or r["branch_key"] for r in reps}

            # Get person-photo links
            cursor = conn.execute(
                "SELECT DISTINCT branch_key, image_path FROM face_crops "
                "WHERE project_id = ? AND branch_key IS NOT NULL",
                (self.project_id,)
            )
            for row in cursor.fetchall():
                bk = row["branch_key"]
                path = row["image_path"]
                name = name_map.get(bk, bk)
                self._add_link(path, "person", bk, name)
        except Exception as e:
            logger.debug(f"[EntityGraph] Person entity build failed: {e}")

    def _build_tag_entities(self, conn):
        """Build tag entities from photo_tags."""
        try:
            cursor = conn.execute(
                "SELECT pm.path, t.name FROM photo_tags pt "
                "JOIN photo_metadata pm ON pt.photo_id = pm.id "
                "JOIN tags t ON pt.tag_id = t.id "
                "WHERE pm.project_id = ?",
                (self.project_id,)
            )
            for row in cursor.fetchall():
                self._add_link(row["path"], "tag", row["name"].lower(), row["name"])
        except Exception as e:
            logger.debug(f"[EntityGraph] Tag entity build failed: {e}")

    def _build_device_entities(self, conn):
        """Build device entities from import provenance."""
        try:
            cursor = conn.execute(
                "SELECT df.local_photo_id, df.device_id, md.device_name "
                "FROM device_files df "
                "JOIN mobile_devices md ON df.device_id = md.device_id "
                "WHERE df.local_photo_id IS NOT NULL"
            )
            # Build photo_id → path lookup
            photo_rows = conn.execute(
                "SELECT id, path FROM photo_metadata WHERE project_id = ?",
                (self.project_id,)
            ).fetchall()
            id_to_path = {r["id"]: r["path"] for r in photo_rows}

            for row in cursor.fetchall():
                path = id_to_path.get(row["local_photo_id"])
                if path:
                    self._add_link(
                        path, "device", row["device_id"],
                        row["device_name"]
                    )
        except Exception as e:
            logger.debug(f"[EntityGraph] Device entity build failed: {e}")

    # ═══════════════════════════════════════════════════════════════
    # Public Query API
    # ═══════════════════════════════════════════════════════════════

    def get_photo_entities(self, photo_path: str) -> List[EntityNode]:
        """
        Get all entities linked to a photo.

        Returns:
            List of EntityNode objects for people, locations, dates, tags, etc.
        """
        self._ensure_built()
        keys = self._photo_entities.get(photo_path, set())
        return [self._nodes[k] for k in keys if k in self._nodes]

    def get_entity_photos(self, entity_type: str, entity_id: str) -> List[str]:
        """
        Get all photo paths linked to an entity.

        Args:
            entity_type: "person", "location", "date", "year", "tag", "device"
            entity_id: The entity identifier

        Returns:
            List of photo paths
        """
        self._ensure_built()
        key = (entity_type, entity_id)
        return list(self._entity_photos.get(key, set()))

    def get_entity(self, entity_type: str, entity_id: str) -> Optional[EntityNode]:
        """Get a specific entity node."""
        self._ensure_built()
        return self._nodes.get((entity_type, entity_id))

    def get_entities_by_type(self, entity_type: str,
                             min_photos: int = 1) -> List[EntityNode]:
        """
        Get all entities of a given type, sorted by photo count.

        Args:
            entity_type: "person", "location", "date", "year", "tag", "device"
            min_photos: Minimum photo count to include

        Returns:
            List of EntityNode objects sorted by photo_count descending
        """
        self._ensure_built()
        nodes = [
            n for (etype, _), n in self._nodes.items()
            if etype == entity_type and n.photo_count >= min_photos
        ]
        nodes.sort(key=lambda n: n.photo_count, reverse=True)
        return nodes

    def get_related_entities(
        self,
        source_type: str,
        source_id: str,
        target_type: str,
        min_co_occurrence: int = 1,
    ) -> List[EntityEdge]:
        """
        Find entities of target_type that co-occur with the source entity.

        Example: get_related_entities("person", "face_001", "location")
        → All locations where face_001 appears, with co-occurrence counts.

        Args:
            source_type: Source entity type
            source_id: Source entity ID
            target_type: Target entity type to find
            min_co_occurrence: Minimum shared photos

        Returns:
            List of EntityEdge objects sorted by weight descending
        """
        self._ensure_built()
        source_key = (source_type, source_id)
        source_photos = self._entity_photos.get(source_key, set())
        if not source_photos:
            return []

        # Find all target entities that share photos with source
        co_occurrences: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        for photo in source_photos:
            for entity_key in self._photo_entities.get(photo, set()):
                if entity_key[0] == target_type and entity_key != source_key:
                    co_occurrences[entity_key].add(photo)

        edges = []
        for target_key, shared_photos in co_occurrences.items():
            if len(shared_photos) >= min_co_occurrence:
                edges.append(EntityEdge(
                    source_type=source_type,
                    source_id=source_id,
                    target_type=target_key[0],
                    target_id=target_key[1],
                    weight=len(shared_photos),
                    photo_paths=list(shared_photos)[:50],  # Cap for memory
                ))

        edges.sort(key=lambda e: e.weight, reverse=True)
        return edges

    def co_occurrence(
        self,
        type_a: str, id_a: str,
        type_b: str, id_b: str,
    ) -> int:
        """
        Get co-occurrence count between two entities.

        Returns:
            Number of photos where both entities appear
        """
        self._ensure_built()
        photos_a = self._entity_photos.get((type_a, id_a), set())
        photos_b = self._entity_photos.get((type_b, id_b), set())
        return len(photos_a & photos_b)

    def get_entity_context(
        self, entity_type: str, entity_id: str, max_per_type: int = 5
    ) -> Dict[str, List[EntityNode]]:
        """
        Get the full context of an entity: all related entities grouped by type.

        Example: get_entity_context("person", "face_001")
        → {"location": [...], "date": [...], "tag": [...], "person": [...]}

        Useful for building entity detail panels / knowledge cards.
        """
        self._ensure_built()
        context: Dict[str, List[EntityNode]] = {}

        for target_type in ("person", "location", "date", "year", "tag", "device"):
            if target_type == entity_type:
                # For same-type: show co-occurring entities (e.g. other people)
                edges = self.get_related_entities(
                    entity_type, entity_id, target_type
                )
                nodes = []
                for edge in edges[:max_per_type]:
                    node = self._nodes.get((edge.target_type, edge.target_id))
                    if node:
                        nodes.append(node)
                if nodes:
                    context[target_type] = nodes
            else:
                edges = self.get_related_entities(
                    entity_type, entity_id, target_type
                )
                nodes = []
                for edge in edges[:max_per_type]:
                    node = self._nodes.get((edge.target_type, edge.target_id))
                    if node:
                        nodes.append(node)
                if nodes:
                    context[target_type] = nodes

        return context

    def search_entities(self, query: str, entity_types: Optional[List[str]] = None,
                        limit: int = 20) -> List[EntityNode]:
        """
        Search entities by name/display_name prefix matching.

        Args:
            query: Search string (prefix match)
            entity_types: Optional filter by entity type(s)
            limit: Maximum results

        Returns:
            List of matching EntityNode objects
        """
        self._ensure_built()
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        matches = []
        for (etype, _), node in self._nodes.items():
            if entity_types and etype not in entity_types:
                continue
            if node.display_name.lower().startswith(query_lower):
                matches.append(node)
            elif query_lower in node.display_name.lower():
                matches.append(node)

        matches.sort(key=lambda n: n.photo_count, reverse=True)
        return matches[:limit]

    def invalidate(self):
        """Force rebuild on next query."""
        self._built_at = 0.0


# ── Module-level singleton cache ──
_graph_cache: Dict[int, EntityGraph] = {}


def get_entity_graph(project_id: int) -> EntityGraph:
    """Get or create an EntityGraph for a project."""
    if project_id not in _graph_cache:
        _graph_cache[project_id] = EntityGraph(project_id)
    return _graph_cache[project_id]
