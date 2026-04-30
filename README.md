# obsidian-rag-api

A Dockerized RAG (Retrieval Augmented Generation) API on top of an
**Obsidian** vault, designed for agent consumption (e.g. OpenClaw).

* Notes are identified by their **vault-relative path** (`Projects/Test.md`).
* The Obsidian vault on disk is the **single source of truth** — SQLite
  and the vector store are derived indexes that can be rebuilt at any
  time.
* The service supervises an **Obsidian Headless** sync process (when
  available) so the vault stays in sync with Obsidian's cloud.
* All non-health endpoints require a static **bearer token**.

---

## 1. Architecture overview

```
              ┌────────────────────────────────────────────┐
              │              obsidian-rag-api              │
              │                                            │
              │   FastAPI app  ──►  Indexer  ──►  Qdrant   │
              │       │                │                   │
              │       │                ├──►  SQLite (meta) │
              │       │                │                   │
              │       │                └──►  Embedder      │
              │       │                                    │
              │   Watcher  ────────────┘ (filesystem)      │
              │       │                                    │
              │   Obsidian Headless supervisor             │
              └────────────┬───────────────────────────────┘
                           │
                  /vault   │   (Markdown files, source of truth)
                           │
                  /data    │   (SQLite database, embedding cache)
```

Key invariants:

1. Every write goes to the filesystem first, then triggers an
   incremental index update.
2. Embeddings are **content-hash cached**; only changed chunks are
   re-embedded.
3. `/index/rebuild` and `/index/sync` can recreate every derived index
   from the vault alone.
4. Bearer-token check uses constant-time comparison and the token is
   never written to logs or error bodies.
5. All paths are validated for traversal, absolute prefixes and the
   `.md` suffix before any filesystem operation.

---

## 2. Project structure

```
obsidian-rag-api/
├── app/
│   ├── main.py            # FastAPI app + lifespan wiring
│   ├── config.py          # pydantic-settings, env-driven
│   ├── logging_config.py  # structured JSON logging
│   ├── auth.py            # static bearer-token dependency
│   ├── deps.py            # DI helpers
│   ├── schemas.py         # Pydantic request/response models
│   ├── paths.py           # vault-path normalization & validation
│   ├── markdown.py        # frontmatter / headings / links / tags
│   ├── chunking.py        # heading-aware + size fallback chunker
│   ├── db.py              # SQLite metadata + embedding cache
│   ├── vector_store.py    # Qdrant + in-memory backends
│   ├── embeddings.py      # OpenAI + fake providers w/ caching
│   ├── indexer.py         # incremental + idempotent indexing
│   ├── watcher.py         # watchdog file watcher
│   ├── obsidian.py        # Obsidian Headless process supervisor
│   └── routers/
│       ├── health.py
│       ├── notes.py       # path-based CRUD + move
│       ├── search.py      # /search, /context
│       ├── index.py       # /index/sync, /index/rebuild
│       └── agent.py       # /agent/* aliases
├── tests/                 # offline test suite
├── docker/entrypoint.sh
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── pyproject.toml
└── .env.example
```

---

## 3. Quick start (Docker)

```bash
cp .env.example .env
# edit .env: set API_TOKEN, OPENAI_API_KEY, OBSIDIAN_* credentials

docker compose up --build
```

The compose stack runs:

* `qdrant` (vector storage, persisted to a named volume)
* `app`    (this service, listening on `:8000`)

Volumes:

| Path     | Purpose                                  |
|----------|------------------------------------------|
| `/vault` | Obsidian vault (Markdown files)          |
| `/data`  | SQLite metadata + embedding cache        |

---

## 4. Environment variables

```env
# Obsidian Headless
OBSIDIAN_EMAIL=
OBSIDIAN_PASSWORD=
OBSIDIAN_VAULT_NAME=
OBSIDIAN_VAULT_PATH=/vault
OBSIDIAN_HEADLESS_ENABLED=true   # set to false to disable supervisor

# API
API_TOKEN=change-me

# Embeddings (provider: openai | fake)
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536

# Storage (vector_backend: qdrant | memory)
VECTOR_BACKEND=qdrant
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=obsidian_notes
SQLITE_PATH=/data/obsidian-rag-api.sqlite

# Indexing
WATCHER_ENABLED=true
LOG_LEVEL=info
```

The Obsidian Headless binary is not vendored. If it is not on `PATH`
inside the container, the supervisor logs a warning and the rest of the
service runs normally — the vault can be populated by any other means
(direct file writes, sync mounts, etc.).

---

## 5. API

All non-health endpoints require:

```
Authorization: Bearer <API_TOKEN>
```

### Health

```http
GET /health
```

### Notes (path-based; primary interface)

```http
GET    /notes/by-path?path=Projects/Test.md
POST   /notes
PUT    /notes/by-path
PATCH  /notes/by-path
DELETE /notes/by-path?path=Projects/Test.md
POST   /notes/move
```

#### Create

```bash
curl -X POST http://localhost:8000/notes \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "Projects/Test.md",
    "title": "Test",
    "frontmatter": {"tags": ["project", "test"]},
    "content": "# Test\n\nThis is a test note."
  }'
```

#### Update (PUT replaces, PATCH merges)

```bash
curl -X PATCH http://localhost:8000/notes/by-path \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path": "Projects/Test.md", "content": "Only updating content"}'
```

#### Move / rename

```bash
curl -X POST http://localhost:8000/notes/move \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"from_path": "Projects/Old.md", "to_path": "Projects/New.md"}'
```

### Search & RAG

```http
POST /search
POST /context
POST /index/sync
POST /index/rebuild
```

Search request:

```json
{
  "query": "Cloudflare Zero Trust setup",
  "top_k": 5,
  "filters": {"tags": ["infrastructure"]}
}
```

Search hit (deterministic, agent-friendly):

```json
{
  "score": 0.84,
  "path": "Infrastructure/Cloudflare Zero Trust.md",
  "title": "Cloudflare Zero Trust",
  "chunk_text": "Setup notes for tunnels and access policies.",
  "heading_context": ["Cloudflare Zero Trust", "Tunnels"],
  "tags": ["infrastructure", "networking"],
  "last_modified": 1777535837.31
}
```

### Agent aliases

Equivalent endpoints with stable verb-style URLs for tools that
prefer `POST` everywhere:

```http
POST /agent/search-notes
POST /agent/get-context
POST /agent/create-note
POST /agent/update-note
```

---

## 6. Design decisions

* **Path is the public ID.** Internal `note_id`s are returned for
  diagnostics but never required by any endpoint. This matches how
  Obsidian itself thinks about notes.
* **Case sensitivity.** Comparison is byte-exact (case-sensitive). On
  case-insensitive filesystems Obsidian may resolve `Foo.md` and
  `foo.md` to the same file; this service treats them as distinct
  logical notes. Preserve the casing chosen at create time.
* **Atomic writes.** Notes are written to a `.tmp` sibling and renamed
  into place so partial writes can never be observed by either the
  watcher or a concurrent reader.
* **Heading-aware chunking with size fallback.** Chunks preserve the
  ancestor heading path (`heading_context` in search results) so agents
  receive a meaningful breadcrumb without re-parsing files. Oversized
  sections fall back to paragraph/sentence splits, then hard slicing.
* **Token estimation is heuristic** (`len(text) // 4`). Adequate for
  chunk sizing; we deliberately avoid pulling a tokenizer into the
  runtime image.
* **Pluggable embeddings.** `EmbeddingProvider` is an abstract base
  class; `openai` is the production default and `fake` is a
  deterministic, offline provider used by the test suite. Adding a new
  provider is one class.
* **Pluggable vector store.** `VectorStore` has a Qdrant backend for
  production and an in-memory backend for tests / offline dev.
* **Embedding cache.** Vectors are cached in SQLite keyed by
  `(provider, model, content_hash)` so re-indexes and content rewrites
  only re-embed *changed* chunks.
* **Filesystem is truth.** `/index/rebuild` truncates SQLite and the
  vector collection, then walks the vault from scratch. The result is
  byte-for-byte identical to incremental indexing.
* **Auth.** `hmac.compare_digest` for constant-time token comparison;
  the token never appears in error responses or logs.
* **Path safety.** A single chokepoint (`app.paths.normalize_note_path`
  + `resolve_in_vault`) rejects absolute paths, traversal, NUL bytes,
  hidden files and non-`.md` extensions; the resolved absolute path is
  asserted to be inside `OBSIDIAN_VAULT_PATH`.

---

## 7. Local development & tests

The test suite is fully offline (in-memory vector store + deterministic
fake embeddings). No Docker or network access required.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest pytest-asyncio ruff

.venv/bin/ruff check app tests
.venv/bin/python -m pytest
```

Run the API locally without Docker:

```bash
export API_TOKEN=local
export EMBEDDING_PROVIDER=fake
export VECTOR_BACKEND=memory
export OBSIDIAN_HEADLESS_ENABLED=false
export OBSIDIAN_VAULT_PATH=$(pwd)/vault
export SQLITE_PATH=$(pwd)/data/dev.sqlite
mkdir -p vault data
.venv/bin/python -m app.main
```

---

## 8. Extension roadmap

* **Backlinks at scale.** Currently computed via SQL at request time;
  for very large vaults, materialize them on write.
* **Hybrid search.** Add BM25 (e.g. via SQLite FTS5) and combine with
  vector scores via reciprocal rank fusion.
* **Attachments.** Index image / PDF attachments with appropriate
  content extractors.
* **Multi-vault.** Generalize `vault_root` into a per-request scope to
  serve multiple vaults from one deployment.
* **Authn upgrade.** Replace the static bearer token with mTLS or
  signed JWTs once we need per-agent identities.
* **Streaming index.** Push embedding requests onto a background queue
  for large bulk imports.
