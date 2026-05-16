# memos-mcp

Raw MCP server for [Memos](https://github.com/usememos/memos) — zero pip dependencies.

## Features

- **Zero dependencies** — stdlib only, no `requirements.txt`
- **Dual transport** — SSE (`/sse`) + Streamable HTTP (`/mcp`)
- **12 tools** — CRUD, archive/restore, search, tag filter, batch ops, stats
- **Server-side search** — with automatic client-side fallback
- **Real pagination** — `pageToken` support
- **2FA delete** — hard delete requires prior archival
- **30s keepalive** — dead connection detection
- **5-line Dockerfile** — Alpine-based, ~50MB image

## Tools

| Tool | Description |
|---|---|
| `create_memo` | Create memo (Markdown + `#tag` + `__html`) |
| `read_memo` | Read full content by ID |
| `update_memo` | Update memo content |
| `archive_memo` | Soft archive (recoverable) |
| `restore_memo` | Restore archived memo |
| `delete_memo` | Soft delete (archive) or hard delete (2FA: must archive first) |
| `list_memos` | List memos with pagination |
| `list_archived` | List archived memos only |
| `search_memos` | Keyword search (server-side priority → client fallback) |
| `list_by_tag` | Filter by `#tag` |
| `batch_archive` | Archive multiple memos at once |
| `get_stats` | Stats: active/archived count + top 10 tags |

## Quick Start

### Docker Compose (recommended)

```bash
git clone https://github.com/waslesy/memos-mcp.git
cd memos-mcp

# Set your token
echo "MEMOS_TOKEN=your_token_here" > .env

docker compose up -d
```

This starts both Memos (`:5230`) and memos-mcp (`:8080`).

### Standalone (Memos already running)

```bash
export MEMOS_HOST=http://your-memos-host:5230
export MEMOS_TOKEN=your_token_here
export PORT=8080

python main.py
```

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/sse` | SSE transport — long-lived connection |
| `POST` | `/messages?sessionId=xxx` | SSE message endpoint |
| `POST` | `/mcp` | Streamable HTTP transport |
| `GET` | `/health` | Health check |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MEMOS_HOST` | ✅ | — | Memos server URL |
| `MEMOS_TOKEN` | ✅ | — | Memos API access token |
| `PORT` | ❌ | `8080` | Server listen port |

## Memos Version

Targets Memos v0.22+. For different versions, adjust the constants at the top of `main.py`:

```python
ROW_STATUS     = "rowStatus"
ACTIVE_VALUE   = "NORMAL"      # or "ACTIVE" for v0.23+
ARCHIVED_VALUE = "ARCHIVED"
FILTER_FIELD   = "row_status"
```

## MCP Client Configuration

### SSE

```json
{
  "mcpServers": {
    "memos": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

### Streamable HTTP

```json
{
  "mcpServers": {
    "memos": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

## License

MIT