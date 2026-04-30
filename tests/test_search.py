from __future__ import annotations

from fastapi.testclient import TestClient


def _seed(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post(
        "/notes",
        headers=auth_headers,
        json={
            "path": "Infrastructure/Cloudflare Zero Trust.md",
            "title": "Cloudflare Zero Trust",
            "frontmatter": {"tags": ["infrastructure", "networking"]},
            "content": "# Cloudflare Zero Trust\n\nSetup notes for tunnels and access policies.",
        },
    )
    client.post(
        "/notes",
        headers=auth_headers,
        json={
            "path": "Recipes/Bread.md",
            "title": "Sourdough Bread",
            "frontmatter": {"tags": ["recipe", "food"]},
            "content": "# Bread\n\nFlour, water, salt, levain.",
        },
    )


def test_search_returns_paths_not_ids(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed(client, auth_headers)
    r = client.post(
        "/search",
        headers=auth_headers,
        json={"query": "Cloudflare Zero Trust setup", "top_k": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "Cloudflare Zero Trust setup"
    assert len(body["hits"]) >= 1
    top = body["hits"][0]
    # Required response fields
    for key in ("score", "path", "title", "chunk_text", "heading_context", "tags", "last_modified"):
        assert key in top
    # No internal note_id leak
    assert "note_id" not in top


def test_search_tag_filter(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed(client, auth_headers)
    r = client.post(
        "/search",
        headers=auth_headers,
        json={"query": "anything", "top_k": 10, "filters": {"tags": ["recipe"]}},
    )
    body = r.json()
    assert all("recipe" in hit["tags"] for hit in body["hits"])
    assert any(hit["path"] == "Recipes/Bread.md" for hit in body["hits"])


def test_context_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    _seed(client, auth_headers)
    r = client.post(
        "/context",
        headers=auth_headers,
        json={"query": "cloudflare", "top_k": 5, "max_chars": 2000},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["context"]
    assert len(body["sources"]) >= 1


def test_agent_aliases(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/agent/create-note",
        headers=auth_headers,
        json={"path": "Agent/Note.md", "content": "# Agent created", "frontmatter": {}},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/agent/search-notes",
        headers=auth_headers,
        json={"query": "agent created", "top_k": 3},
    )
    assert r.status_code == 200
    assert any(h["path"] == "Agent/Note.md" for h in r.json()["hits"])
