from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_health_does_not_require_auth(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["embedding_provider"] == "fake"
    assert body["vector_backend"] == "memory"


def test_protected_endpoints_require_token(client: TestClient) -> None:
    r = client.get("/notes/by-path", params={"path": "x.md"})
    assert r.status_code == 401
    r = client.post("/notes", json={"path": "a.md", "content": ""})
    assert r.status_code == 401


def test_invalid_token_is_rejected(client: TestClient) -> None:
    r = client.post(
        "/notes",
        headers={"Authorization": "Bearer wrong"},
        json={"path": "a.md", "content": ""},
    )
    assert r.status_code == 401


def test_create_get_patch_delete_note(
    client: TestClient, auth_headers: dict[str, str], vault_dir: Path
) -> None:
    # CREATE
    r = client.post(
        "/notes",
        headers=auth_headers,
        json={
            "path": "Projects/Test.md",
            "title": "Test",
            "frontmatter": {"tags": ["project", "test"]},
            "content": "# Test\n\nThis is a test note.",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["path"] == "Projects/Test.md"
    assert "project" in body["tags"]
    assert (vault_dir / "Projects" / "Test.md").is_file()

    # CREATE conflict
    r2 = client.post(
        "/notes", headers=auth_headers, json={"path": "Projects/Test.md", "content": ""}
    )
    assert r2.status_code == 409

    # GET
    r = client.get("/notes/by-path", headers=auth_headers, params={"path": "Projects/Test.md"})
    assert r.status_code == 200
    assert "# Test" in r.json()["content"]

    # PATCH (content only)
    r = client.patch(
        "/notes/by-path",
        headers=auth_headers,
        json={"path": "Projects/Test.md", "content": "Only updating content"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "Only updating content" in body["content"]
    # Frontmatter preserved
    assert "project" in body["frontmatter"].get("tags", [])

    # PUT (full replace)
    r = client.put(
        "/notes/by-path",
        headers=auth_headers,
        json={
            "path": "Projects/Test.md",
            "title": "Updated",
            "frontmatter": {"tags": ["updated"]},
            "content": "# Updated\n\nNew content",
        },
    )
    assert r.status_code == 200
    assert r.json()["frontmatter"]["tags"] == ["updated"]

    # DELETE
    r = client.delete("/notes/by-path", headers=auth_headers, params={"path": "Projects/Test.md"})
    assert r.status_code == 204
    assert not (vault_dir / "Projects" / "Test.md").exists()

    # 404 after delete
    r = client.get("/notes/by-path", headers=auth_headers, params={"path": "Projects/Test.md"})
    assert r.status_code == 404


def test_path_traversal_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/notes",
        headers=auth_headers,
        json={"path": "../../escape.md", "content": ""},
    )
    assert r.status_code == 400
    r = client.get(
        "/notes/by-path", headers=auth_headers, params={"path": "../etc/passwd.md"}
    )
    assert r.status_code == 400


def test_non_md_extension_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.post(
        "/notes", headers=auth_headers, json={"path": "evil.txt", "content": ""}
    )
    assert r.status_code == 400


def test_move_note(
    client: TestClient, auth_headers: dict[str, str], vault_dir: Path
) -> None:
    client.post(
        "/notes",
        headers=auth_headers,
        json={"path": "Projects/Old.md", "content": "# Old\nbody"},
    )
    r = client.post(
        "/notes/move",
        headers=auth_headers,
        json={"from_path": "Projects/Old.md", "to_path": "Projects/New.md"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "Projects/New.md"
    assert not (vault_dir / "Projects" / "Old.md").exists()
    assert (vault_dir / "Projects" / "New.md").is_file()

    # Cannot move onto an existing destination
    client.post(
        "/notes",
        headers=auth_headers,
        json={"path": "Projects/Other.md", "content": "x"},
    )
    r = client.post(
        "/notes/move",
        headers=auth_headers,
        json={"from_path": "Projects/Other.md", "to_path": "Projects/New.md"},
    )
    assert r.status_code == 409


def test_index_sync_and_rebuild_pick_up_disk_changes(
    client: TestClient, auth_headers: dict[str, str], vault_dir: Path
) -> None:
    # Drop a file directly on disk (simulating Obsidian Headless sync).
    (vault_dir / "Daily").mkdir()
    (vault_dir / "Daily" / "2026-04-30.md").write_text(
        "---\ntags: [daily]\n---\n# Daily\nfoo bar\n"
    )

    r = client.post("/index/sync", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["upserted"] == 1

    # Idempotent: a second sync should be a no-op.
    r = client.post("/index/sync", headers=auth_headers)
    assert r.json()["upserted"] == 0
    assert r.json()["skipped"] == 1

    # Delete on disk → sync removes from index.
    (vault_dir / "Daily" / "2026-04-30.md").unlink()
    r = client.post("/index/sync", headers=auth_headers)
    assert r.json()["deleted"] == 1

    # Rebuild is also idempotent.
    r = client.post("/index/rebuild", headers=auth_headers)
    assert r.status_code == 200
