"""Pluggable embedding providers.

Two providers ship in the box:

* ``openai`` – calls the OpenAI embeddings API and is the production default.
* ``fake`` – deterministic, offline, hash-derived vectors used by the test
  suite and for environments without internet access.

Both providers honor the per-chunk content-hash cache held in
:class:`app.db.MetadataStore` so that *only changed chunks are re-embedded*.
"""

from __future__ import annotations

import hashlib
import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Settings, get_settings
from .db import MetadataStore

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when the embedding provider fails permanently."""


class EmbeddingProvider(ABC):
    name: str
    model: str
    dim: int

    @abstractmethod
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text."""


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic, hash-derived embeddings (no network)."""

    name = "fake"

    def __init__(self, model: str = "fake-deterministic", dim: int = 64) -> None:
        self.model = model
        self.dim = dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # Expand the 32-byte digest into ``dim`` floats in [-1, 1].
            raw: list[float] = []
            i = 0
            while len(raw) < self.dim:
                b = digest[i % len(digest)]
                raw.append((b / 127.5) - 1.0)
                i += 1
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            out.append([x / norm for x in raw])
        return out


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI embeddings provider with retry + exponential backoff."""

    name = "openai"

    def __init__(self, api_key: str, model: str, dim: int) -> None:
        if not api_key:
            raise EmbeddingError("OPENAI_API_KEY is required for the openai provider")
        # Imported lazily so the test suite can run without the dependency wired.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self.model = model
        self.dim = dim

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, max=8.0),
        retry=retry_if_exception_type(Exception),
    )
    def _call(self, texts: Sequence[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=list(texts))
        return [d.embedding for d in resp.data]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return self._call(texts)
        except Exception as exc:  # pragma: no cover - exercised in integration only
            raise EmbeddingError(f"OpenAI embedding call failed: {exc}") from exc


def build_provider(settings: Settings | None = None) -> EmbeddingProvider:
    """Construct an :class:`EmbeddingProvider` from settings."""
    cfg = settings or get_settings()
    if cfg.embedding_provider == "fake":
        return FakeEmbeddingProvider(dim=cfg.embedding_dim or 64)
    return OpenAIEmbeddingProvider(
        api_key=cfg.openai_api_key,
        model=cfg.openai_embedding_model,
        dim=cfg.embedding_dim,
    )


class CachingEmbedder:
    """Wraps an :class:`EmbeddingProvider` with a content-hash cache."""

    def __init__(self, provider: EmbeddingProvider, store: MetadataStore) -> None:
        self._provider = provider
        self._store = store

    @property
    def dim(self) -> int:
        return self._provider.dim

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def model(self) -> str:
        return self._provider.model

    def embed_with_hashes(
        self, items: Sequence[tuple[str, str]]
    ) -> list[list[float]]:
        """Embed a list of ``(content_hash, text)`` pairs, using the cache.

        Order of returned vectors matches the input order.
        """
        results: list[list[float] | None] = [None] * len(items)
        to_compute_idx: list[int] = []
        to_compute_text: list[str] = []
        to_compute_hash: list[str] = []

        for i, (h, text) in enumerate(items):
            cached = self._store.get_cached_embedding(self._provider.name, self._provider.model, h)
            if cached is not None:
                results[i] = cached
            else:
                to_compute_idx.append(i)
                to_compute_text.append(text)
                to_compute_hash.append(h)

        if to_compute_text:
            log.info(
                "embedding_batch",
                extra={"provider": self._provider.name, "count": len(to_compute_text)},
            )
            vectors = self._provider.embed(to_compute_text)
            if len(vectors) != len(to_compute_text):
                raise EmbeddingError("provider returned wrong vector count")
            for idx, h, vec in zip(to_compute_idx, to_compute_hash, vectors, strict=True):
                results[idx] = vec
                self._store.put_cached_embedding(
                    self._provider.name, self._provider.model, h, vec
                )

        # mypy/runtime guard
        return [v if v is not None else [] for v in results]
