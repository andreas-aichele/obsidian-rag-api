"""Vector store abstraction with Qdrant and in-memory backends.

The in-memory backend is used by the test suite (and for offline local
development) so the service can boot without a running Qdrant container.
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class VectorPoint:
    """A single vector to upsert."""

    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass
class VectorMatch:
    """A single search hit."""

    id: str
    score: float
    payload: dict[str, Any]


class VectorStore(ABC):
    @abstractmethod
    def ensure_collection(self, dim: int) -> None: ...

    @abstractmethod
    def upsert(self, points: Iterable[VectorPoint]) -> None: ...

    @abstractmethod
    def delete_by_note(self, note_id: str) -> None: ...

    @abstractmethod
    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorMatch]: ...

    @abstractmethod
    def reset(self) -> None: ...


# ----------------------------------------------------------------------
# In-memory backend
# ----------------------------------------------------------------------
class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._points: dict[str, VectorPoint] = {}
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        with self._lock:
            self._dim = dim

    def upsert(self, points: Iterable[VectorPoint]) -> None:
        with self._lock:
            for p in points:
                self._points[p.id] = p

    def delete_by_note(self, note_id: str) -> None:
        with self._lock:
            stale = [pid for pid, p in self._points.items() if p.payload.get("note_id") == note_id]
            for pid in stale:
                self._points.pop(pid, None)

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        with self._lock:
            candidates = list(self._points.values())

        wanted_tags = _coerce_tag_filter(filters)
        if wanted_tags:
            wanted = set(wanted_tags)
            candidates = [
                p for p in candidates
                if wanted.issubset(set(p.payload.get("tags") or []))
            ]

        scored = [
            VectorMatch(id=p.id, score=_cosine(vector, p.vector), payload=p.payload)
            for p in candidates
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:top_k]

    def reset(self) -> None:
        with self._lock:
            self._points.clear()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def _coerce_tag_filter(filters: dict[str, Any] | None) -> list[str]:
    if not filters:
        return []
    raw = filters.get("tags")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, str)]
    return []


# ----------------------------------------------------------------------
# Qdrant backend
# ----------------------------------------------------------------------
class QdrantVectorStore(VectorStore):
    def __init__(self, url: str, collection: str) -> None:
        from qdrant_client import QdrantClient  # imported lazily

        self._client = QdrantClient(url=url)
        self._collection = collection
        self._dim: int | None = None

    def ensure_collection(self, dim: int) -> None:
        from qdrant_client.http import models as qmodels

        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
            )
        self._dim = dim

    def upsert(self, points: Iterable[VectorPoint]) -> None:
        from qdrant_client.http import models as qmodels

        batch = [
            qmodels.PointStruct(id=_to_qdrant_id(p.id), vector=p.vector, payload=p.payload)
            for p in points
        ]
        if not batch:
            return
        self._client.upsert(collection_name=self._collection, points=batch)

    def delete_by_note(self, note_id: str) -> None:
        from qdrant_client.http import models as qmodels

        self._client.delete(
            collection_name=self._collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="note_id",
                            match=qmodels.MatchValue(value=note_id),
                        )
                    ]
                )
            ),
        )

    def search(
        self,
        vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        from qdrant_client.http import models as qmodels

        qfilter: qmodels.Filter | None = None
        wanted_tags = _coerce_tag_filter(filters)
        if wanted_tags:
            qfilter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(key="tags", match=qmodels.MatchValue(value=t))
                    for t in wanted_tags
                ]
            )

        hits = self._client.search(
            collection_name=self._collection,
            query_vector=vector,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        return [
            VectorMatch(id=str(h.id), score=float(h.score), payload=dict(h.payload or {}))
            for h in hits
        ]

    def reset(self) -> None:
        try:
            self._client.delete_collection(collection_name=self._collection)
        except Exception:
            log.exception("qdrant_delete_collection_failed")
        if self._dim:
            self.ensure_collection(self._dim)


def _to_qdrant_id(s: str) -> str:
    """Qdrant accepts either uint64 or UUID strings as point IDs.

    Our chunk IDs are already UUIDs, but if a caller passes anything else
    we fold it through ``uuid.uuid5`` so the value is deterministic.
    """
    try:
        uuid.UUID(s)
        return s
    except (ValueError, TypeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, s))


def build_vector_store(settings: Settings | None = None) -> VectorStore:
    cfg = settings or get_settings()
    if cfg.vector_backend == "memory":
        return InMemoryVectorStore()
    return QdrantVectorStore(url=cfg.qdrant_url, collection=cfg.qdrant_collection)
