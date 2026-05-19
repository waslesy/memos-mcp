#!/usr/bin/env python3
"""
memos-mcp v2.0
Raw MCP server for Memos — zero pip dependencies
Transport: SSE (/sse) + Streamable HTTP (/mcp)
Target: Memos v0.22+
"""

import json, os, sys, uuid, queue, logging
from collections import Counter
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.error import HTTPError

# ════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════
MEMOS_HOST  = os.getenv("MEMOS_HOST", "").rstrip("/")
MEMOS_TOKEN = os.getenv("MEMOS_TOKEN", "")
PORT        = int(os.getenv("PORT", "8080"))

# Memos 版本适配 — 按实际版本改这三行即可
# v0.22: rowStatus / NORMAL / ARCHIVED
# v0.23+: rowStatus / ACTIVE / ARCHIVED
ROW_STATUS     = "state"
ACTIVE_VALUE   = "NORMAL"
ARCHIVED_VALUE = "ARCHIVED"
FILTER_FIELD   = "state"

logging.basicConfig(
    stream=sys.stderr, level=logging.INFO,
    format="%(asctime)s [memos-mcp] %(message)s", datefmt="%H:%M:%S",
)
log = logging.getLogger("memos-mcp")


# ════════════════════════════════════════════════════
#  MEMOS API CLIENT  (stdlib only)
# ════════════════════════════════════════════════════
class Memos:
    def __init__(self, host: str, token: str):
        self.host = host
        self.h = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _call(self, method, path, body=None, params=None):
        url = f"{self.host}{path}"
        if params:
            qs = {k: str(v) for k, v in params.items() if v is not None}
            if qs:
                url += "?" + urlencode(qs)
        data = json.dumps(body).encode() if body else None
        req = Request(url, data=data, method=method, headers=self.h)
        try:
            with urlopen(req) as r:
                raw = r.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except HTTPError as e:
            err = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} → {e.code}: {err}")

    # ── CRUD ──
    def create(self, content, visibility="PRIVATE"):
        return self._call("POST", "/api/v1/memos",
                          {"content": content, "visibility": visibility})

    def get(self, uid):
        return self._call("GET", f"/api/v1/memos/{uid}")

    def update(self, uid, **fields):
        return self._call("PATCH", f"/api/v1/memos/{uid}", fields)

    def delete(self, uid):
        return self._call("DELETE", f"/api/v1/memos/{uid}")

    # ── List ──
    def list(self, page_size=20, page_token=None, filter_str=None):
        r = self._call("GET", "/api/v1/memos", params={
            "pageSize": page_size,
            "pageToken": page_token,
            "filter": filter_str,
            "creator": "users/1",
        })
        return r if isinstance(r, dict) else {"memos": r}

    # ── State ──
    def set_archived(self, uid, archived: bool):
        val = ARCHIVED_VALUE if archived else ACTIVE_VALUE
        return self.update(uid, **{ROW_STATUS: val})


api = Memos(MEMOS_HOST, MEMOS_TOKEN)

# ── SQLite 直读（绕过 v0.24 ListMemos bug） ──
MEMOS_DB = os.getenv("MEMOS_DB", "/data/memos.db")

def sqlite_list(include_archived=False):
    import sqlite3
    conn = sqlite3.connect(MEMOS_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if include_archived:
        c.execute('SELECT uid, content, row_status FROM memo ORDER BY created_ts DESC')
    else:
        c.execute('SELECT uid, content, row_status FROM memo WHERE row_status="NORMAL" ORDER BY created_ts DESC')
    rows = c.fetchall()
    conn.close()
    return [{
        "name": f"memos/{r['uid']}",
        "content": r['content'],
        "state": r['row_status'],
    } for r in rows]


# ════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════
def clean_id(mid: str) -> str:
    return str(mid).removeprefix("memos/")

def preview(text: str, n=60) -> str:
    line = text.split("\n", 1)[0]
    return line[:n] + ("…" if len(line) > n else "")

def state_of(m: dict) -> str:
    return m.get("state", m.get("rowStatus", ACTIVE_VALUE))

def fmt(m: dict, show_state=False) -> str:
    uid = m.get("name", m.get("uid", "?"))
    archived = state_of(m) == ARCHIVED_VALUE
    tag = " [归档]" if archived and show_state else ""
    return f"[{uid}]{tag} {preview(m.get('content', ''))}"

def safe_list(filter_str=None, page_size=20, page_token=None):
    """服务端 filter 优先；400 则降级为无 filter"""
    try:
        return api.list(page_size, page_token, filter_str)
    except RuntimeError as e:
        if any(code in str(e) for code in ["400","500","13"]):
            log.warning(f"Filter rejected: {filter_str}")
            return api.list(page_size, page_token)
        raise


# ════════════════════════════════════════════════════
#  TOOL HANDLERS  (12 tools)
# ════════════════════════════════════════════════════

def h_create(content: str, visibility: str = "PRIVATE") -> str:
    r = api.create(content, visibility)
    return f"✅ 已创建 Memo {r.get('name', r.get('uid', '?'))}"

def h_read(memo_id: str) -> str:
    uid = clean_id(memo_id)
    m = api.get(uid)
    tag = " [已归档]" if state_of(m) == ARCHIVED_VALUE else ""
    return f"── Memo {uid}{tag} ──\n{m.get('content', '')}"

def h_update(memo_id: str, content: str) -> str:
    uid = clean_id(memo_id)
    api.update(uid, content=content)
    return f"✅ Memo {uid} 已更新"

def h_archive(memo_id: str) -> str:
    uid = clean_id(memo_id)
    m = api.get(uid)
    if state_of(m) == ARCHIVED_VALUE:
        return f"Memo {uid} 已是归档状态"
    api.set_archived(uid, True)
    return f"✅ Memo {uid} 已归档"

def h_restore(memo_id: str) -> str:
    uid = clean_id(memo_id)
    m = api.get(uid)
    if state_of(m) != ARCHIVED_VALUE:
        return f"Memo {uid} 不在归档状态"
    api.set_archived(uid, False)
    return f"✅ Memo {uid} 已恢复"

def h_delete(memo_id: str, hard: bool = False) -> str:
    uid = clean_id(memo_id)
    if not hard:
        return h_archive(memo_id)
    m = api.get(uid)
    if state_of(m) != ARCHIVED_VALUE:
        return f"⚠️ 安全拦截：Memo {uid} 未归档，请先归档再硬删除（2FA）"
    api.delete(uid)
    return f"✅ Memo {uid} 已彻底删除（不可恢复）"

def h_list(page_size: int = 20, page_token: str = None,
           include_archived: bool = False) -> str:
    memos = sqlite_list(include_archived)
    if not memos:
        return "暂无 Memo"
    lines = [fmt(m, show_state=include_archived) for m in memos[:page_size]]
    return "\n".join(lines)

def h_list_archived(page_size: int = 50) -> str:
    memos = [m for m in sqlite_list(True) if m.get("state", "NORMAL") == "ARCHIVED"]
    if not memos:
        return "无归档 Memo"
    return "\n".join(fmt(m) for m in memos[:page_size])

def h_search(keyword: str, include_archived: bool = False) -> str:
    kw = keyword.lower()
    memos = sqlite_list(include_archived)
    memos = [m for m in memos if kw in m.get("content", "").lower()]
    if not memos:
        return f"未找到 '{keyword}'"
    return (f"🔍 '{keyword}' ({len(memos)} 条):\n"
            + "\n".join(fmt(m, show_state=True) for m in memos))
def h_by_tag(tag: str) -> str:
    tag = tag.lstrip("#")
    memos = sqlite_list(False)
    memos = [m for m in memos if f"#{tag}" in m.get("content", "")]
    if not memos:
        return f"未找到 #{tag}"
    return (f"🏷️ #{tag} ({len(memos)} 条):\n"
            + "\n".join(fmt(m) for m in memos))
def h_batch_archive(memo_ids: list) -> str:
    out = []
    for mid in memo_ids:
        try:
            out.append(h_archive(mid))
        except Exception as e:
            out.append(f"❌ {mid}: {e}")
    return "\n".join(out)

def h_stats() -> str:
    memos = sqlite_list(True)
    active = sum(1 for m in memos if m.get("state", "NORMAL") != "ARCHIVED")
    archived = len(memos) - active
    tags = Counter()
    for m in memos:
        for w in m.get("content", "").split():
            w = w.rstrip(".,;:!?()[]{}\"'")
            if w.startswith("#") and len(w) > 1:
                tags[w] += 1
    top = " ".join(f"{t}({c})" for t, c in tags.most_common(10))
    return f"📊 活跃 {active} · 归档 {archived} · 共 {len(memos)}\n🏷️ {top or '无标签'}"


# ════════════════════════════════════════════════════
#  TOOL REGISTRY
# ════════════════════════════════════════════════════
HANDLERS = {
    "create_memo":   h_create,
    "read_memo":     h_read,
    "update_memo":   h_update,
    "archive_memo":  h_archive,
    "restore_memo":  h_restore,
    "delete_memo":   h_delete,
    "list_memos":    h_list,
    "list_archived": h_list_archived,
    "search_memos":  h_search,
    "list_by_tag":   h_by_tag,
    "batch_archive": h_batch_archive,
    "get_stats":     h_stats,
}

SCHEMAS = [
    {"name": "create_memo",
     "description": "创建 Memo（支持 Markdown + #标签 + __html）",
     "inputSchema": {"type": "object", "properties": {
         "content":    {"type": "string", "description": "内容"},
         "visibility": {"type": "string",
                        "enum": ["PRIVATE", "PROTECTED", "PUBLIC"],
                        "description": "可见性，默认 PRIVATE"},
     }, "required": ["content"]}},

    {"name": "read_memo",
     "description": "读取指定 Memo 完整内容",
     "inputSchema": {"type": "object", "properties": {
         "memo_id": {"type": "string", "description": "Memo ID"},
     }, "required": ["memo_id"]}},

    {"name": "update_memo",
     "description": "更新 Memo 内容",
     "inputSchema": {"type": "object", "properties": {
         "memo_id": {"type": "string", "description": "Memo ID"},
         "content": {"type": "string", "description": "新内容"},
     }, "required": ["memo_id", "content"]}},

    {"name": "archive_memo",
     "description": "归档 Memo（可恢复）",
     "inputSchema": {"type": "object", "properties": {
         "memo_id": {"type": "string", "description": "Memo ID"},
     }, "required": ["memo_id"]}},

    {"name": "restore_memo",
     "description": "恢复已归档的 Memo",
     "inputSchema": {"type": "object", "properties": {
         "memo_id": {"type": "string", "description": "Memo ID"},
     }, "required": ["memo_id"]}},

    {"name": "delete_memo",
     "description": "删除 Memo。默认软删除（归档）；hard=true 彻底删除（须先归档 · 2FA）",
     "inputSchema": {"type": "object", "properties": {
         "memo_id": {"type": "string", "description": "Memo ID"},
         "hard":    {"type": "boolean", "description": "彻底删除，默认 false"},
     }, "required": ["memo_id"]}},

    {"name": "list_memos",
     "description": "列出 Memo（支持分页）",
     "inputSchema": {"type": "object", "properties": {
         "page_size":        {"type": "integer", "description": "每页数量，默认 20"},
         "page_token":       {"type": "string",  "description": "翻页 token"},
         "include_archived": {"type": "boolean", "description": "含归档，默认 false"},
     }}},

    {"name": "list_archived",
     "description": "仅列出归档 Memo",
     "inputSchema": {"type": "object", "properties": {
         "page_size": {"type": "integer", "description": "数量，默认 50"},
     }}},

    {"name": "search_memos",
     "description": "关键词搜索（服务端优先 → 自动降级客户端）",
     "inputSchema": {"type": "object", "properties": {
         "keyword":          {"type": "string",  "description": "关键词"},
         "include_archived": {"type": "boolean", "description": "含归档，默认 false"},
     }, "required": ["keyword"]}},

    {"name": "list_by_tag",
     "description": "按 #标签 筛选 Memo",
     "inputSchema": {"type": "object", "properties": {
         "tag": {"type": "string", "description": "标签（带不带 # 均可）"},
     }, "required": ["tag"]}},

    {"name": "batch_archive",
     "description": "批量归档多个 Memo",
     "inputSchema": {"type": "object", "properties": {
         "memo_ids": {"type": "array", "items": {"type": "string"},
                      "description": "ID 列表"},
     }, "required": ["memo_ids"]}},

    {"name": "get_stats",
     "description": "统计：活跃/归档数量 + 标签 Top10",
     "inputSchema": {"type": "object", "properties": {}}},
]


# ════════════════════════════════════════════════════
#  MCP JSON-RPC
# ════════════════════════════════════════════════════
def rpc(req: dict):
    method = req.get("method", "")
    rid    = req.get("id")
    params = req.get("params", {})

    if rid is None:                       # notification → no response
        return None

    if method == "initialize":
        return _ok(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "memos-mcp", "version": "2.0.0"},
        })
    if method == "tools/list":
        return _ok(rid, {"tools": SCHEMAS})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})
        fn = HANDLERS.get(name)
        if not fn:
            return _err(rid, -32601, f"Unknown tool: {name}")
        try:
            text = fn(**args)
            return _ok(rid, {"content": [{"type": "text", "text": text}]})
        except Exception as e:
            log.error(f"Tool {name}: {e}")
            return _ok(rid, {
                "content": [{"type": "text", "text": f"❌ {e}"}],
                "isError": True,
            })
    if method == "ping":
        return _ok(rid, {})

    return _err(rid, -32601, f"Unknown method: {method}")

def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}

def _err(rid, code, msg):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


# ════════════════════════════════════════════════════
#  HTTP + SSE TRANSPORT
# ════════════════════════════════════════════════════
sessions: dict[str, queue.Queue] = {}

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        log.info(f"{self.client_address[0]} {fmt % args}")

    # ── routing ──
    def do_GET(self):
        if self.path == "/sse":
            self._sse()
        elif self.path == "/health":
            self._json(200, {"status": "ok",
                             "transport": ["sse", "streamable-http"]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/messages"):
            self._sse_msg()
        elif self.path == "/mcp":
            self._streamable()
        else:
            self._json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    # ── SSE: long-lived connection ──
    def _sse(self):
        sid = str(uuid.uuid4())
        q = queue.Queue()
        sessions[sid] = q

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        self._event("endpoint", f"/messages?sessionId={sid}")
        log.info(f"SSE {sid[:8]}… connected")

        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    if msg is None:
                        break
                    self._event("message", json.dumps(msg))
                except queue.Empty:
                    # 30s keepalive — 检测死连接
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            sessions.pop(sid, None)
            log.info(f"SSE {sid[:8]}… closed")

    # ── SSE: receive via POST ──
    def _sse_msg(self):
        parsed = urlparse(self.path)
        sid = parse_qs(parsed.query).get("sessionId", [None])[0]
        if not sid or sid not in sessions:
            self._json(404, {"error": "session not found"})
            return
        body = self._body()
        if body is None:
            return
        resp = rpc(body)
        if resp:
            sessions[sid].put(resp)
        self.send_response(202)
        self._cors()
        self.end_headers()

    # ── Streamable HTTP: simple req→resp ──
    def _streamable(self):
        body = self._body()
        if body is None:
            return
        resp = rpc(body)
        if resp:
            self._json(200, resp)
        else:
            self.send_response(204)
            self.end_headers()

    # ── util ──
    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n))
        except (json.JSONDecodeError, ValueError) as e:
            self._json(400, {"error": f"bad json: {e}"})
            return None

    def _json(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self._cors()
        self.end_headers()
        self.wfile.write(raw)

    def _event(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {data}\n\n".encode())
        self.wfile.flush()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


# ════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════
def main():
    if not MEMOS_HOST or not MEMOS_TOKEN:
        log.error("MEMOS_HOST and MEMOS_TOKEN required")
        sys.exit(1)

    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info(f"memos-mcp v2.0 on :{PORT}")
    log.info(f"  SSE          GET  /sse → POST /messages")
    log.info(f"  Streamable   POST /mcp")
    log.info(f"  Health       GET  /health")
    log.info(f"  Memos        {MEMOS_HOST}")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down…")
        for q in sessions.values():
            q.put(None)
        srv.shutdown()


if __name__ == "__main__":
    main()