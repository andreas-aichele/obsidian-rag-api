# Bruno API collection

This directory is a [Bruno](https://www.usebruno.com/) collection for
exercising the `obsidian-rag-api` HTTP surface end-to-end.

## Open in Bruno

1. Install Bruno: <https://www.usebruno.com/downloads>
2. **File → Open Collection** and select this `bruno/` folder.
3. In the top-right environment selector, choose **Local**.
4. Edit the `Local` environment and set:
   - `base_url` (default `http://localhost:8000`)
   - `api_token` to match the service's `API_TOKEN`.

## CLI runner

You can also run the whole collection from the terminal:

```bash
npm install -g @usebruno/cli
bru run --env Local
```

## Layout

```
bruno/
├── bruno.json                  Collection manifest
├── collection.bru              Collection-level auth (bearer)
├── environments/Local.bru      base_url + api_token
├── Health.bru                  GET  /health
├── Notes/                      Path-based CRUD + move
├── Search/                     /search and /context
├── Index/                      /index/sync, /index/rebuild
└── Agent/                      /agent/* aliases
```

## Suggested smoke flow

`Health` → `Notes/Create note` → `Notes/Get note by path`
→ `Notes/Patch note (PATCH)` → `Search/Search` → `Notes/Delete note`.
