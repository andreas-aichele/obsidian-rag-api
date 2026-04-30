<div align="center">

# 🧠 obsidian-rag-api

**A Dockerized Retrieval-Augmented Generation API on top of your [Obsidian](https://obsidian.md) vault — built for AI agents.**

Path-addressable notes. Heading-aware chunking. Pluggable embeddings & vector stores.
Your vault on disk is the single source of truth; everything else is a derived index you can rebuild at any time.

[![CI](https://github.com/andreas-aichele/obsidian-rag-api/actions/workflows/ci.yml/badge.svg)](https://github.com/andreas-aichele/obsidian-rag-api/actions/workflows/ci.yml)
[![Release](https://github.com/andreas-aichele/obsidian-rag-api/actions/workflows/release.yml/badge.svg)](https://github.com/andreas-aichele/obsidian-rag-api/actions/workflows/release.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-obsidian--rag--api-2496ED?logo=docker&logoColor=white)](https://github.com/andreas-aichele/obsidian-rag-api/pkgs/container/obsidian-rag-api)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Qdrant](https://img.shields.io/badge/Qdrant-DC382D?logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![Obsidian](https://img.shields.io/badge/Obsidian-7C3AED?logo=obsidian&logoColor=white)](https://obsidian.md/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

</div>

---

## ✨ Highlights

- 🗂️ **Path is the API.** Notes are addressed by their **vault-relative path** (`Projects/Test.md`) — exactly how Obsidian thinks about them.
- 📁 **Filesystem is truth.** SQLite metadata and the vector store are derived indexes that can be rebuilt byte-for-byte from the vault alone.
- 🔁 **Obsidian Headless supervisor** keeps the on-disk vault in sync with Obsidian's cloud (when configured).
- ⚡ **Incremental, content-hash-cached embeddings** — only changed chunks are re-embedded.
- 🧩 **Heading-aware chunking** with paragraph/sentence fallback; chunks carry their full heading breadcrumb to agents.
- 🔌 **Pluggable** embeddings (OpenAI / fake) and vector stores (Qdrant / in-memory) — swap by env var.
- 🛡️ **Hardened** path validation and constant-time bearer-token auth; the token never appears in logs or errors.
- 🤖 **Agent-friendly aliases** (`/agent/*`) for tools that prefer `POST` everywhere.
- 🧪 **Fully offline test suite** — no Docker or network required.

---

## 📑 Table of contents

1. [Architecture overview](#1-architecture-overview)
2. [Project structure](#2-project-structure)
3. [Quick start (Docker)](#3-quick-start-docker)
4. [Environment variables](#4-environment-variables)
5. [API](#5-api)
6. [Design decisions](#6-design-decisions)
7. [Local development & tests](#7-local-development--tests)
8. [Releases](#8-releases)
9. [Extension roadmap](#9-extension-roadmap)
10. [License](#10-license)

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

**Key invariants**

1. Every write goes to the filesystem first, then triggers an incremental index update.
2. Embeddings are **content-hash cached**; only changed chunks are re-embedded.
3. `/index/rebuild` and `/index/sync` can recreate every derived index from the vault alone.
4. Bearer-token check uses constant-time comparison and the token is never written to logs or error bodies.
5. All paths are validated for traversal, absolute prefixes and the `.md` suffix before any filesystem operation.

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

- `qdrant` — vector storage, persisted to a named volume
- `app` — this service, listening on `:8000`

**Volumes**

| Path     | Purpose                                  |
|----------|------------------------------------------|
| `/vault` | Obsidian vault (Markdown files)          |
| `/data`  | SQLite metadata + embedding cache        |

### 🐳 Pre-built images

Tagged releases are published to GitHub Container Registry:

```bash
docker pull ghcr.io/andreas-aichele/obsidian-rag-api:latest
# or pin to a version
docker pull ghcr.io/andreas-aichele/obsidian-rag-api:0.1.0
```

### 🥖 API testing with Bruno

A [Bruno](https://www.usebruno.com/) collection lives in [`bruno/`](./bruno/) and covers every endpoint (Health, Notes CRUD/move, Search, Context, Index sync/rebuild, Agent aliases). Open the folder in Bruno or run the whole collection from the CLI:

```bash
npm install -g @usebruno/cli
cd bruno && bru run --env Local
```

See [`bruno/README.md`](./bruno/README.md) for details.

---

## 4. Environment variables

```env
# Obsidian Headless
OBSIDIAN_EMAIL=
OBSIDIAN_PASSWORD=
OBSIDIAN_VAULT_NAME=
OBSIDIAN_VAULT_ENCRYPTION_PASSWORD=  # optional: Obsidian Sync E2E password
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

The Docker image installs the official Obsidian Headless CLI (`ob`, from the `obsidian-headless` npm package) by default. On startup the supervisor runs `ob login` and (if needed) `ob sync-setup` against the configured vault, then keeps `ob sync --continuous` running. Set `OBSIDIAN_HEADLESS_ENABLED=false` to skip the supervisor entirely, or build the image with `--build-arg INSTALL_OBSIDIAN_HEADLESS=false` to omit the CLI (and the Node 22 runtime that backs it) when the vault is populated by other means (direct file writes, sync mounts, etc.). If the binary is not on `PATH`, the supervisor logs a warning and the rest of the service runs normally.

> ℹ️ `OBSIDIAN_VAULT_NAME` must match the remote Obsidian Sync vault name exactly (including case), or use the remote vault ID shown by `ob sync-list-remote`. If the remote vault uses end-to-end encryption and the vault password differs from `OBSIDIAN_PASSWORD`, set `OBSIDIAN_VAULT_ENCRYPTION_PASSWORD` to the vault encryption password.

---

## 5. API

All non-health endpoints require:

```http
Authorization: Bearer <API_TOKEN>
```

### 🩺 Health

```http
GET /health
```

### 📝 Notes (path-based; primary interface)

```http
GET    /notes/by-path?path=Projects/Test.md
POST   /notes
PUT    /notes/by-path
PATCH  /notes/by-path
DELETE /notes/by-path?path=Projects/Test.md
POST   /notes/move
```

<details>
<summary><strong>Create</strong></summary>

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
</details>

<details>
<summary><strong>Update</strong> (PUT replaces, PATCH merges)</summary>

```bash
curl -X PATCH http://localhost:8000/notes/by-path \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"path": "Projects/Test.md", "content": "Only updating content"}'
```
</details>

<details>
<summary><strong>Move / rename</strong></summary>

```bash
curl -X POST http://localhost:8000/notes/move \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"from_path": "Projects/Old.md", "to_path": "Projects/New.md"}'
```
</details>

### 🔍 Search & RAG

```http
POST /search
POST /context
POST /index/sync
POST /index/rebuild
```

**Search request:**

```json
{
  "query": "Cloudflare Zero Trust setup",
  "top_k": 5,
  "filters": {"tags": ["infrastructure"]}
}
```

**Search hit** (deterministic, agent-friendly):

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

### 🤖 Agent aliases

Equivalent endpoints with stable verb-style URLs for tools that prefer `POST` everywhere:

```http
POST /agent/search-notes
POST /agent/get-context
POST /agent/create-note
POST /agent/update-note
```

---

## 6. Design decisions

- **Path is the public ID.** Internal `note_id`s are returned for diagnostics but never required by any endpoint. This matches how Obsidian itself thinks about notes.
- **Case sensitivity.** Comparison is byte-exact (case-sensitive). On case-insensitive filesystems Obsidian may resolve `Foo.md` and `foo.md` to the same file; this service treats them as distinct logical notes. Preserve the casing chosen at create time.
- **Atomic writes.** Notes are written to a `.tmp` sibling and renamed into place so partial writes can never be observed by either the watcher or a concurrent reader.
- **Heading-aware chunking with size fallback.** Chunks preserve the ancestor heading path (`heading_context` in search results) so agents receive a meaningful breadcrumb without re-parsing files. Oversized sections fall back to paragraph/sentence splits, then hard slicing.
- **Token estimation is heuristic** (`len(text) // 4`). Adequate for chunk sizing; we deliberately avoid pulling a tokenizer into the runtime image.
- **Pluggable embeddings.** `EmbeddingProvider` is an abstract base class; `openai` is the production default and `fake` is a deterministic, offline provider used by the test suite. Adding a new provider is one class.
- **Pluggable vector store.** `VectorStore` has a Qdrant backend for production and an in-memory backend for tests / offline dev.
- **Embedding cache.** Vectors are cached in SQLite keyed by `(provider, model, content_hash)` so re-indexes and content rewrites only re-embed *changed* chunks.
- **Filesystem is truth.** `/index/rebuild` truncates SQLite and the vector collection, then walks the vault from scratch. The result is byte-for-byte identical to incremental indexing.
- **Auth.** `hmac.compare_digest` for constant-time token comparison; the token never appears in error responses or logs.
- **Path safety.** A single chokepoint (`app.paths.normalize_note_path` + `resolve_in_vault`) rejects absolute paths, traversal, NUL bytes, hidden files and non-`.md` extensions; the resolved absolute path is asserted to be inside `OBSIDIAN_VAULT_PATH`.

---

## 7. Local development & tests

The test suite is fully offline (in-memory vector store + deterministic fake embeddings). No Docker or network access required.

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

Then open the auto-generated docs at <http://localhost:8000/docs>.

---

## 8. Releases

Tagged pushes (`v*`) trigger [`.github/workflows/release.yml`](./.github/workflows/release.yml), which:

1. Builds the `Dockerfile` for `linux/amd64` + `linux/arm64`.
2. Pushes to `ghcr.io/<owner>/<repo>` with semver + `latest` tags.
3. Creates a GitHub Release with auto-generated notes and the pull command for the new image.

To cut a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Every push to `main` (and every PR) also runs lint + tests via [`.github/workflows/ci.yml`](./.github/workflows/ci.yml).

---

## 9. Extension roadmap

- [ ] **Backlinks at scale** — currently computed via SQL at request time; for very large vaults, materialize them on write.
- [ ] **Hybrid search** — add BM25 (e.g. via SQLite FTS5) and combine with vector scores via reciprocal rank fusion.
- [ ] **Attachments** — index image / PDF attachments with appropriate content extractors.
- [ ] **Multi-vault** — generalize `vault_root` into a per-request scope to serve multiple vaults from one deployment.
- [ ] **Authn upgrade** — replace the static bearer token with mTLS or signed JWTs once we need per-agent identities.
- [ ] **Streaming index** — push embedding requests onto a background queue for large bulk imports.

Contributions and ideas are welcome — please open an [issue](https://github.com/andreas-aichele/obsidian-rag-api/issues) or pull request.

---

## 10. License

Released under the [MIT License](./LICENSE) © Andreas Aichele.

<div align="center">

<sub>Built with ❤️ for agents that read, write, and reason over your notes.</sub>

</div>
