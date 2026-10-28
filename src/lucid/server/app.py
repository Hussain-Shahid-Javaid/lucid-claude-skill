"""Optional Lucid service: dashboard API + background timers. Requires `pip install lucid-skill[service]`.

Security model
* Binds to 127.0.0.1 by default. Non-loopback binding requires --allow-remote AND TLS.
* Every /api request needs a bearer token (X-Lucid-Token or Authorization: Bearer).
  Tokens map to a user; the user scope always comes from the token, never the request.
* Host header allow-list (DNS-rebinding protection) and Origin check (cross-site
  browser requests are rejected). No CORS headers are emitted.
* Strict CSP on the dashboard; imported content is rendered as text, never HTML.
* Responses pass through secret redaction.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from importlib import resources
from typing import Any

from ..core import Lucid
from ..decisions import DecisionError, DecisionScheduler
from ..handoff import SharingDenied
from ..memory import MemoryError, Scope
from ..redact import install_log_redaction, redact
from ..util import lucid_home

try:
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
except ImportError as e:  # pragma: no cover
    raise SystemExit("the service needs: pip install 'lucid-skill[service]'") from e

TOKEN_FILE = "service_tokens.json"


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def ensure_token(home: str | None = None, user: str = "default") -> tuple[str, str]:
    """Create (or reuse) the local token file; returns (path, plaintext token if newly created else '')."""
    home = home or lucid_home()
    os.makedirs(home, mode=0o700, exist_ok=True)
    path = os.path.join(home, TOKEN_FILE)
    tokens = {}
    if os.path.exists(path):
        with open(path) as fh:
            tokens = json.load(fh)
    for h, u in tokens.items():
        if u == user:
            return path, ""
    tok = secrets.token_urlsafe(32)
    tokens[_hash(tok)] = user
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(tokens, fh)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path, tok


def rotate_token(home: str | None = None, user: str = "default") -> str:
    home = home or lucid_home()
    path = os.path.join(home, TOKEN_FILE)
    tokens = {}
    if os.path.exists(path):
        with open(path) as fh:
            tokens = {h: u for h, u in json.load(fh).items() if u != user}
    tok = secrets.token_urlsafe(32)
    tokens[_hash(tok)] = user
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(tokens, fh)
    return tok


def _web_dir() -> str | None:
    try:
        d = resources.files("lucid") / "web"
        p = str(d)
        return p if os.path.exists(os.path.join(p, "index.html")) else None
    except Exception:
        return None


def create_app(lucid: Lucid, *, token_map: dict[str, str], allowed_hosts: list[str], demo: bool = False,
               start_timers: bool = True, admin_users: set[str] | None = None) -> FastAPI:
    """admin_users may see/act on global operational state (activity, sync queues, tasks, budgets,
    questions, experiments, capture-site settings, backups). Everyone else only sees their own scope."""
    admins = {"default"} if admin_users is None else set(admin_users)
    import contextlib

    L = lucid
    install_log_redaction()
    state: dict[str, Any] = {"scheduler": None}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        L.outbox.recover_after_restart()  # in-flight writes from a crash have unknown outcomes
        if start_timers:
            state["scheduler"] = DecisionScheduler(L.decisions)  # also reconciles missed deadlines
            state["scheduler"].start()
        yield
        if state["scheduler"]:
            state["scheduler"].stop()

    app = FastAPI(title="Lucid", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if host not in allowed_hosts:
            return PlainTextResponse("host not allowed", status_code=421)
        origin = request.headers.get("origin")
        if origin:
            o_host = origin.split("://", 1)[-1].split(":")[0].lower()
            if o_host not in allowed_hosts:
                return PlainTextResponse("cross-origin request rejected", status_code=403)
        sfs = request.headers.get("sec-fetch-site")
        if sfs and sfs not in ("same-origin", "none"):
            return PlainTextResponse("cross-site request rejected", status_code=403)
        if request.url.path.startswith("/api/"):
            tok = request.headers.get("x-lucid-token") or ""
            auth = request.headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                tok = auth[7:]
            user = None
            th = _hash(tok) if tok else ""
            for h, u in token_map.items():
                if th and hmac.compare_digest(h, th):
                    user = u
            if not user:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.state.user = user
        resp: Response = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' "
                                                   "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                                                   "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def scope(request: Request, project: str | None = None) -> Scope:
        return Scope(request.state.user, project or "")

    def admin(request: Request) -> None:
        if request.state.user not in admins:
            raise HTTPException(403, "admin only: this view covers global operational state")

    def ok(data: Any) -> JSONResponse:
        return JSONResponse(json.loads(redact(json.dumps(data, default=str))))

    def err(e: Exception, code: int = 400):
        raise HTTPException(code, str(e))

    # ------------------------------------------------------------------ status
    @app.get("/api/health")
    def health(request: Request):
        if request.state.user not in admins:
            return ok({"user": request.state.user, "demo": demo, "admin": False,
                       "schema_version": L.store.schema_version()})
        h = L.health()
        h["admin"] = True
        h["demo"] = demo
        h["user"] = request.state.user
        h["scheduler_running"] = bool(state["scheduler"] and state["scheduler"].is_alive())
        return ok(h)

    @app.get("/api/capabilities")
    def capabilities():
        from ..capabilities import load_matrix
        return ok(load_matrix())

    @app.get("/api/connectors")
    def connectors(request: Request):
        admin(request)
        from ..importers import manifests
        sites = [dict(r) for r in L.store.q("SELECT * FROM capture_sites ORDER BY site")]
        storage = [
            {"id": "local", "status": "configured", "authoritative": "SQLite", "path": L.store.path},
            {"id": "s3", "status": "configured" if L.store.get_setting("s3_bucket") else "unconfigured",
             "note": "set with `lucid` Python API / docs/storage.md"},
            {"id": "google_docs", "status": "configured" if L.store.get_setting("gdocs_ids") else "unconfigured",
             "note": "requires OAuth (drive.file, documents)"},
        ]
        return ok({"importers": manifests(), "storage": storage, "capture_sites": sites})

    @app.get("/api/activity")
    def activity(request: Request, limit: int = 100):
        admin(request)
        return ok([dict(r) for r in L.store.q("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (min(limit, 1000),))])

    # ------------------------------------------------------------------ memory
    @app.get("/api/memories")
    def memories(request: Request, q: str = "", project: str = "", kind: str = "", status: str = "active",
                 budget: int = 4000):
        sc = scope(request, project)
        if q:
            r = L.memory.search(q, sc, kinds=[kind] if kind else None, budget_tokens=budget, limit=100)
            return ok({"items": r.items, "used_tokens": r.used_tokens, "omitted": r.omitted,
                       "explanation": r.explanation})
        return ok({"items": L.memory.list(sc, kind=kind or None, status=status, limit=500)})

    @app.get("/api/memories/{mid}")
    def memory(request: Request, mid: str, project: str = ""):
        try:
            m = L.memory.get(mid, scope=scope(request, project))
            m["history"] = L.memory.history(mid)
            return ok(m)
        except MemoryError as e:
            err(e, 404)

    @app.post("/api/memories")
    def memory_add(request: Request, body: dict = Body(...)):
        try:
            return ok(L.memory.add(body["content"], kind=body.get("kind", "note"), origin="user",
                                   scope=scope(request, body.get("project")), key=body.get("key"),
                                   tags=body.get("tags", []), ttl=body.get("ttl"), actor="dashboard"))
        except (MemoryError, KeyError) as e:
            err(e)

    @app.post("/api/memories/{mid}/correct")
    def memory_correct(request: Request, mid: str, body: dict = Body(...)):
        try:
            L.memory.get(mid, scope=scope(request, body.get("project")))
            return ok(L.memory.correct(mid, body["content"], reason=body.get("reason", "corrected in dashboard"),
                                       actor="dashboard"))
        except (MemoryError, KeyError) as e:
            err(e)

    @app.delete("/api/memories/{mid}")
    def memory_delete(request: Request, mid: str, project: str = ""):
        try:
            L.memory.get(mid, scope=scope(request, project))
            return ok(L.memory.delete(mid, reason="deleted in dashboard", actor="dashboard"))
        except MemoryError as e:
            err(e, 404)

    @app.get("/api/conflicts")
    def conflicts(request: Request, project: str = ""):
        out = []
        for c in L.memory.conflicts(scope(request, project)):
            c["a"] = L.memory.get(c["memory_a"])
            c["b"] = L.memory.get(c["memory_b"])
            out.append(c)
        return ok(out)

    @app.post("/api/conflicts/{cid}/resolve")
    def resolve(request: Request, cid: str, body: dict = Body(...)):
        conf = L.store.one("SELECT memory_a FROM memory_conflicts WHERE id=?", (cid,))
        owner = L.store.one("SELECT user_scope FROM memories WHERE id=?", (conf[0],)) if conf else None
        if not owner or owner[0] != request.state.user:
            raise HTTPException(404, "not found")
        try:
            return ok(L.memory.resolve_conflict(cid, keep=body.get("keep"), resolution=body.get("resolution", "dashboard"),
                                                actor="dashboard"))
        except MemoryError as e:
            err(e)

    # ------------------------------------------------------------------ conversations
    @app.get("/api/conversations")
    def conversations(request: Request, provider: str = ""):
        return ok(L.archive.list(provider=provider or None, user=request.state.user))

    @app.get("/api/conversations/{cid}")
    def conversation(request: Request, cid: str):
        try:
            c = L.archive.get(cid)
        except KeyError:
            raise HTTPException(404, "not found")
        if c["user_scope"] != request.state.user:
            raise HTTPException(404, "not found")
        return ok(c)

    @app.delete("/api/conversations/{cid}")
    def conversation_delete(request: Request, cid: str):
        c = L.store.one("SELECT user_scope FROM conversations WHERE id=?", (cid,))
        if not c or c[0] != request.state.user:
            raise HTTPException(404, "not found")
        return ok(L.archive.delete(cid, actor="dashboard"))

    # ------------------------------------------------------------------ sync / sharing
    @app.get("/api/sync")
    def sync(request: Request):
        admin(request)
        return ok({"destinations": L.outbox.status(), "items": L.outbox.items(limit=200),
                   "imports": [dict(r) for r in L.store.q("SELECT * FROM imports ORDER BY started_at DESC LIMIT 50")]})

    @app.get("/api/sharing")
    def sharing(request: Request, project: str = ""):
        return ok({"rules": L.handoffs.rules(scope(request, project)),
                   "handoffs": L.handoffs.list(user=request.state.user)})

    @app.post("/api/sharing")
    def sharing_add(request: Request, body: dict = Body(...)):
        try:
            return ok(L.handoffs.add_rule(body["destination"], body["categories"], scope(request, body.get("project"))))
        except (ValueError, KeyError) as e:
            err(e)

    @app.post("/api/sharing/{rid}/disable")
    def sharing_disable(request: Request, rid: str):
        r = L.store.one("SELECT user_scope FROM sharing_rules WHERE id=?", (rid,))
        if not r or r[0] != request.state.user:
            raise HTTPException(404, "not found")
        L.handoffs.disable_rule(rid)
        return ok({"disabled": rid})

    @app.post("/api/handoff/preview")
    def handoff_preview(request: Request, body: dict = Body(...)):
        try:
            return ok(L.handoffs.prepare(body["destination"], scope=scope(request, body.get("project")),
                                         goal=body.get("goal", ""), query=body.get("query", ""),
                                         categories=body.get("categories"), preview=True, actor="dashboard"))
        except (SharingDenied, KeyError) as e:
            err(e)

    # ------------------------------------------------------------------ tasks / budgets / decisions
    @app.get("/api/tasks")
    def tasks(request: Request):
        admin(request)
        return ok(L.tasks.list())

    @app.get("/api/budgets")
    def budgets(request: Request):
        admin(request)
        out = []
        for led in L.budget.ledgers():
            led["entries"] = L.budget.entries(led["ledger_id"])
            out.append(led)
        return ok(out)

    @app.get("/api/questions")
    def questions(request: Request, status: str = ""):
        admin(request)
        return ok({"questions": L.decisions.list(status or None), "authorizations": L.decisions.authorizations()})

    @app.post("/api/questions/{qid}/{action}")
    def question_action(request: Request, qid: str, action: str, body: dict = Body(default={})):
        admin(request)
        d = L.decisions
        try:
            if action == "answer":
                return ok(d.answer(qid, body["option"], by="dashboard", remember=bool(body.get("remember")),
                                   mem_scope=scope(request, body.get("project"))))
            if action == "extend":
                return ok(d.extend(qid, float(body.get("seconds", 120))))
            if action == "accept-default":
                return ok(d.accept_default(qid, by="dashboard"))
            if action in ("pause", "resume", "cancel"):
                return ok({"ok": getattr(d, action)(qid)})
        except (DecisionError, KeyError) as e:
            err(e)
        raise HTTPException(404, "unknown action")

    @app.get("/api/verifications")
    def verifications(request: Request):
        admin(request)
        rows = [dict(r) for r in L.store.q("SELECT * FROM verifications ORDER BY created_at DESC LIMIT 200")]
        preds = L.monitor.calibration()
        return ok({"verifications": rows, "calibration": preds, "monitor": L.monitor.items()[-200:]})

    @app.get("/api/experiments")
    def experiments(request: Request, module: str = ""):
        admin(request)
        return ok(L.experiments.list(module or None))

    # ------------------------------------------------------------------ backup / export
    @app.post("/api/backup")
    def backup(request: Request):
        admin(request)
        path = os.path.join(os.path.dirname(L.store.path) or ".", "backups",
                            f"lucid-{L.store.clock.iso().replace(':', '')}.db")
        return ok({"backup": L.store.backup(path)})

    @app.get("/api/export")
    def export(request: Request, project: str = ""):
        return ok(L.memory.export(scope(request, project)))

    # ------------------------------------------------------------------ browser capture
    @app.get("/api/capture/sites")
    def capture_sites(request: Request):
        admin(request)
        return ok([dict(r) for r in L.store.q("SELECT * FROM capture_sites ORDER BY site")])

    @app.post("/api/capture/sites")
    def capture_site_set(request: Request, body: dict = Body(...)):
        admin(request)
        site = str(body["site"]).lower().strip()
        L.store.ex("INSERT INTO capture_sites (site, enabled, auto_capture, paused, updated_at) VALUES (?,?,?,?,?)"
                   " ON CONFLICT(site) DO UPDATE SET enabled=excluded.enabled, auto_capture=excluded.auto_capture,"
                   " paused=excluded.paused, updated_at=excluded.updated_at",
                   (site, int(bool(body.get("enabled"))), int(bool(body.get("auto_capture"))),
                    int(bool(body.get("paused"))), L.store.clock.iso()))
        return ok({"site": site})

    @app.post("/api/capture")
    def capture(request: Request, body: dict = Body(...)):
        from ..capture import CaptureRejected, ingest_capture
        try:
            return ok(ingest_capture(L, body, user=request.state.user))
        except CaptureRejected as e:
            raise HTTPException(403, str(e))

    @app.get("/api/captures")
    def captures(request: Request):
        rows = L.store.q("SELECT id, uri, title, captured_at, metadata FROM sources WHERE user_scope=? AND kind='web'"
                         " AND content IS NOT NULL ORDER BY captured_at DESC LIMIT 200", (request.state.user,))
        return ok([dict(r) for r in rows])

    @app.delete("/api/captures/{sid}")
    def capture_delete(request: Request, sid: str):
        r = L.store.one("SELECT user_scope FROM sources WHERE id=?", (sid,))
        if not r or r[0] != request.state.user:
            raise HTTPException(404, "not found")
        return ok(L.memory.delete_source(sid, actor="dashboard"))

    # ------------------------------------------------------------------ dashboard
    web = _web_dir()

    @app.get("/")
    def index():
        if not web:
            return PlainTextResponse("Dashboard not built. Run: cd dashboard && npm ci && npm run build", 503)
        return FileResponse(os.path.join(web, "index.html"))

    @app.get("/assets/{name}")
    def asset(name: str):
        if not web or "/" in name or ".." in name:
            raise HTTPException(404)
        p = os.path.join(web, "assets", name)
        if not os.path.isfile(p):
            raise HTTPException(404)
        return FileResponse(p)

    return app


def serve(db: str | None = None, *, host: str = "127.0.0.1", port: int = 8765, allow_remote: bool = False,
          tls_cert: str | None = None, tls_key: str | None = None, demo: bool = False) -> None:
    import uvicorn
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not (allow_remote and tls_cert and tls_key):
        raise SystemExit("the dashboard only listens on this computer unless you pass --allow-remote with a TLS certificate and key")
    path, tok = ensure_token()
    with open(path) as fh:
        token_map = json.load(fh)
    if not tok:
        print("Existing token in", path, "(rotate with: python -m lucid.server.app --rotate-token)")
    L = Lucid(db)
    if demo:
        from .demo import seed_demo
        seed_demo(L)
    hosts = ["127.0.0.1", "localhost", "::1"] + ([host] if not loopback else [])
    app = create_app(L, token_map=token_map, allowed_hosts=hosts, demo=demo)
    if tok:
        print(f"Dashboard: http://127.0.0.1:{port}/#token={tok}\n(token shown once; stored hashed in {path})")
    uvicorn.run(app, host=host, port=port, ssl_certfile=tls_cert, ssl_keyfile=tls_key, log_level="warning")


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="lucid-serve")
    p.add_argument("--db")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--allow-remote", action="store_true")
    p.add_argument("--tls-cert")
    p.add_argument("--tls-key")
    p.add_argument("--demo", action="store_true", help="seed clearly labelled demo data (use a separate --db)")
    p.add_argument("--rotate-token", action="store_true")
    a = p.parse_args(argv)
    if a.rotate_token:
        print(f"new token: {rotate_token()}")
        return 0
    serve(a.db, host=a.host, port=a.port, allow_remote=a.allow_remote, tls_cert=a.tls_cert, tls_key=a.tls_key,
          demo=a.demo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
