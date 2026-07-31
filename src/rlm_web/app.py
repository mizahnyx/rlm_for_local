"""FastAPI web frontend for rlm_web (D2).

HTTPS-ready, Tailscale-reachable, server-rendered with Jinja2 + htmx.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from rlm_local.config import load_config
from rlm_local.model_backend import HTTPModelBackend

# ── App setup ──────────────────────────────────────────────────────────────

app = FastAPI(title="RLM Web", version="0.1.0")

# Session middleware for auth cookie
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("RLM_WEB_SECRET", "rlm-web-dev-secret-change-in-production"))
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Upload limits (P2)
MAX_UPLOAD_FILES = 20
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB

# Templates
from fastapi.templating import Jinja2Templates  # noqa: E402

templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


# ── Auth ───────────────────────────────────────────────────────────────────

def _check_auth(request: Request) -> None:
    """Check bearer token or session cookie. Fail-closed for non-local (P3)."""
    token = os.environ.get("RLM_WEB_TOKEN", "")

    if not token:
        # No token configured: allow loopback and test clients only
        if request.client is None:
            return  # TestClient — allow
        host = request.client.host
        if host in ("127.0.0.1", "::1", "localhost", "testclient"):
            return

    # Token configured: require session authentication
    try:
        if request.session.get("authenticated"):
            return
    except Exception:
        pass
    raise HTTPException(status_code=401, detail="Authentication required")
@app.post("/login")
async def login(request: Request, token: str = Form(...)):
    expected = os.environ.get("RLM_WEB_TOKEN", "")
    if token == expected or not expected:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse("Invalid token", status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ── Job storage ────────────────────────────────────────────────────────────

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _create_job(query: str, context: str, profile: str = "laptop") -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "query": query,
            "context": context,
            "profile": profile,
            "state": "pending",
            "result": None,
            "events": [],
            "created": time.time(),
        }
    # Start background run
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return job_id


def _run_job(job_id: str) -> None:
    import rlm_local
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["state"] = "running"
        job["events"].append({"type": "started", "message": "Completion running..."})

    try:
        log_path = f"data/jobs/{job_id}/trajectory.jsonl"
        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        answer = rlm_local.completion(
            job["query"], job["context"],
            profile=job["profile"],
            log_path=log_path,
        )
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "done"
                _jobs[job_id]["result"] = answer
                _jobs[job_id]["events"].append(
                    {"type": "done", "answer": answer}
                )
    except Exception as e:
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["state"] = "error"
                _jobs[job_id]["result"] = str(e)
                _jobs[job_id]["events"].append(
                    {"type": "error", "message": str(e)}
                )

# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def console(request: Request):
    _check_auth(request)
    return templates.TemplateResponse(request, "console.html")


@app.post("/jobs", response_class=HTMLResponse)
async def create_job(
    request: Request,
    query: str = Form(...),
    context: str = Form(""),
    profile: str = Form("laptop"),
):
    _check_auth(request)
    job_id = _create_job(query, context, profile)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_view(request: Request, job_id: str):
    _check_auth(request)
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "job.html", {
        "job": job,
    })


@app.get("/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str):
    """SSE endpoint for live job progress."""
    from fastapi.responses import StreamingResponse

    async def event_stream():
        last_count = 0
        while True:
            job = _jobs.get(job_id)
            if not job:
                break
            events = job.get("events", [])
            while last_count < len(events):
                ev = events[last_count]
                yield f"data: {json.dumps(ev)}\n\n"
                last_count += 1
            if job["state"] in ("done", "error"):
                break
            await _asleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _asleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


@app.get("/vault", response_class=HTMLResponse)
async def vault_search(request: Request, q: str = ""):
    _check_auth(request)
    results = []
    if q:
        try:
            from rlm_kernel.search import search_vault
            from rlm_kernel.vault import LocalVault
            vault_root = _get_vault_root()
            vault = LocalVault(vault_root, init_git=False)
            idx_path = vault_root / ".index" / "meta.sqlite"
            if idx_path.exists():
                results = search_vault(vault, idx_path, q, k=10)
        except Exception:
            pass
    return templates.TemplateResponse(request, "vault.html", {
        "query": q, "results": results,
    })


@app.get("/vault/page/{path:path}", response_class=HTMLResponse)
async def vault_page(request: Request, path: str):
    _check_auth(request)
    try:
        from rlm_kernel.vault import LocalVault
        vault_root = _get_vault_root()
        vault = LocalVault(vault_root, init_git=False)
        page = vault.get(path)
        if page is None:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(request, "vault_page.html", {
            "page": page,
        })
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500)


def _get_vault_root() -> Path:
    """Return the vault root path (env-configurable, for testing)."""
    return Path(os.environ.get("RLM_VAULT_ROOT",
                str(Path.home() / ".local" / "share" / "rlm-kernel" / "vault")))


@app.post("/vault/ingest")
async def vault_ingest(request: Request, files: list[UploadFile] | None = None):
    """Ingest uploaded Markdown files into the vault as permanent pages."""
    _check_auth(request)
    if not files:
        return JSONResponse({"error": "no files uploaded"}, status_code=400)
    if len(files) > MAX_UPLOAD_FILES:
        return JSONResponse(
            {"error": f"Too many files: {len(files)} (max {MAX_UPLOAD_FILES})"},
            status_code=413,
        )
    total_bytes = 0
    for f in files:
        if f.size is not None:
            total_bytes += f.size
    if total_bytes > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"Upload too large: {total_bytes} bytes (max {MAX_UPLOAD_BYTES})"},
            status_code=413,
        )

    from rlm_kernel.schema import Frontmatter, Page, PageKind
    from rlm_kernel.vault import LocalVault
    from rlm_kernel.index import Index

    vault_root = _get_vault_root()
    vault = LocalVault(vault_root, init_git=False)
    idx_path = vault_root / ".index" / "meta.sqlite"

    ingested = 0
    skipped = 0
    results: list[dict] = []

    for f in files:
        if not f.filename or not f.filename.endswith(".md"):
            results.append({"file": f.filename, "status": "rejected",
                            "reason": "not a .md file"})
            continue

        content = (await f.read()).decode("utf-8", errors="replace")
        if not content.strip():
            results.append({"file": f.filename, "status": "rejected",
                            "reason": "empty file"})
            continue

        # Extract title from first heading
        title = f.filename.replace(".md", "")
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break

        # Slugify name
        import re
        name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64]

        # Content hash for dedup
        import hashlib
        content_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()

        # Check duplicates
        existing = vault.list(kind="note")
        duplicate = any(
            (ex.frontmatter.hash and ex.frontmatter.hash == content_hash)
            or ex.body.strip() == content.strip()
            for ex in existing
        )
        if duplicate:
            skipped += 1
            results.append({"file": f.filename, "status": "skipped",
                            "reason": "duplicate"})
            continue

        fm = Frontmatter(
            schema=1, kind=PageKind.NOTE, name=name,
            title=title, summary=title, hash=content_hash,
        )
        page = Page(fm, content)
        page_path = f"memory/notes/{name}.md"
        vault.put(page, page_path)
        ingested += 1
        results.append({"file": f.filename, "status": "ok",
                        "path": page_path})

    if ingested > 0 and idx_path.exists():
        idx = Index(idx_path)
        idx.reindex_delta(vault)
        idx.close()

    return JSONResponse({
        "ingested": ingested, "skipped": skipped, "results": results,
    })

@app.get("/check", response_class=HTMLResponse)
async def check_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse(request, "check.html")


@app.get("/docs/{name:path}", response_class=HTMLResponse)
async def docs_page(request: Request, name: str):
    _check_auth(request)
    docs_dir = Path(__file__).parent.parent.parent / "docs"
    resolved = (docs_dir / name).resolve()
    if not resolved.is_relative_to(docs_dir.resolve()) or not resolved.is_file():
        raise HTTPException(status_code=404)
    content = resolved.read_text(encoding="utf-8", errors="replace")
    return templates.TemplateResponse(request, "docs.html", {
        "name": name, "content": content,
    })


# ── Chat ───────────────────────────────────────────────────────────────────

_chat_sessions: dict[str, dict[str, Any]] = {}
_chat_lock = threading.Lock()


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse(request, "chat.html", {"profile": "laptop"})


@app.post("/chat/send")
async def chat_send(request: Request):
    """Queue a chat message and return a stream ID for SSE."""
    _check_auth(request)
    body = await request.json()
    session_id = body.get("session_id", "default")
    message = body.get("message", "").strip()
    profile = body.get("profile", "laptop")
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    stream_id = uuid.uuid4().hex[:8]
    with _chat_lock:
        sess = _chat_sessions.setdefault(session_id, {"context": ""})
    threading.Thread(
        target=_process_chat_message,
        args=(session_id, message, stream_id, profile),
        daemon=True,
    ).start()
    return JSONResponse({"stream_id": stream_id, "session_id": session_id})


@app.get("/chat/events/{stream_id}")
async def chat_events(request: Request, stream_id: str):
    """SSE endpoint for chat response streaming."""
    import asyncio as _asyncio
    from fastapi.responses import StreamingResponse

    async def event_stream():
        while True:
            found = None
            with _chat_lock:
                for sess in _chat_sessions.values():
                    queues = sess.get("_queues", {})
                    if stream_id in queues:
                        found = queues[stream_id]
                        break
            if found is None:
                await _asyncio.sleep(0.2)
                continue
            while found:
                evt = found.pop(0)
                yield f"data: {json.dumps(evt)}\n\n"
                if evt.get("type") in ("done", "error"):
                    return
            await _asyncio.sleep(0.3)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/chat/clear")
async def chat_clear(request: Request):
    """Clear a chat session's context."""
    _check_auth(request)
    body = await request.json()
    session_id = body.get("session_id", "default")
    with _chat_lock:
        if session_id in _chat_sessions:
            _chat_sessions[session_id]["context"] = ""
    return JSONResponse({"status": "ok"})


@app.post("/chat/context")
async def chat_context(request: Request):
    """Return current chat session context."""
    _check_auth(request)
    body = await request.json()
    session_id = body.get("session_id", "default")
    with _chat_lock:
        ctx = _chat_sessions.get(session_id, {}).get("context", "")
    return JSONResponse({"context": ctx[:500], "total_chars": len(ctx)})


def _process_chat_message(session_id: str, message: str, stream_id: str, profile: str) -> None:
    """Process a chat message in a background thread."""
    queue: list[dict] = []
    with _chat_lock:
        sess = _chat_sessions.setdefault(session_id, {"context": ""})
        sess.setdefault("_queues", {})[stream_id] = queue
        context = sess["context"]

    def emit(t: str, **kw: Any) -> None:
        queue.append({"type": t, **kw})

    msg = message.strip()
    low = msg.lower()

    if low == "/context":
        display = context[:500] if context else "(empty)"
        emit("chunk", text=f"Context ({len(context)} chars):\n{display}\n")
        emit("done", text="")
        return

    if low == "/clear":
        with _chat_lock:
            if session_id in _chat_sessions:
                _chat_sessions[session_id]["context"] = ""
        emit("chunk", text="Context cleared.\n")
        emit("done", text="")
        return

    if low == "/quit":
        emit("chunk", text="Goodbye.\n")
        emit("done", text="")
        return

    if low.startswith("/search "):
        query = msg[len("/search "):].strip()
        try:
            from rlm_kernel.search import search_vault
            from rlm_kernel.vault import LocalVault
            vr = Path.home() / ".local" / "share" / "rlm-kernel" / "vault"
            vault = LocalVault(vr, init_git=False)
            idx = vr / ".index" / "meta.sqlite"
            if idx.exists():
                for r in search_vault(vault, idx, query, k=5):
                    emit("chunk", text=f"[{r['kind']}] {r['name']}: {r['title']}\n")
            else:
                emit("chunk", text="Index not found.\n")
        except Exception as e:
            emit("error", text=str(e))
        emit("done", text="")
        return

    if low.startswith("/get "):
        path = msg[len("/get "):].strip()
        try:
            from rlm_kernel.vault import LocalVault
            vault = LocalVault(Path.home() / ".local/share/rlm-kernel/vault", init_git=False)
            page = vault.get(path)
            emit("chunk", text=f"{page.frontmatter.title}\n\n{page.body[:1000]}\n" if page else f"Not found: {path}\n")
        except Exception as e:
            emit("error", text=str(e))
        emit("done", text="")
        return

    # /ask or plain text — needs context
    query = msg[5:].strip() if low.startswith("/ask ") else msg
    if not context:
        emit("chunk", text="No context loaded. Use /ingest to load .md files.\n")
        emit("done", text="")
        return

    import rlm_local
    try:
        bridge = _get_kernel_bridge()
        answer = rlm_local.completion(
            query, context, profile=profile, max_turns=10,
            kernel_bridge=bridge,
        )
        emit("chunk", text=answer)
    except Exception as e:
        emit("error", text=str(e))
    emit("done", text="")


def _get_kernel_bridge():
    """Lazily create and cache a KernelBridge for vault-aware completions."""
    global _cached_bridge
    if "_cached_bridge" not in globals():
        try:
            from rlm_kernel.repl_bridge import KernelBridge
            from rlm_kernel.vault import LocalVault
            from rlm_kernel.index import rebuild_index
            vault_root = Path.home() / ".local" / "share" / "rlm-kernel" / "vault"
            vault = LocalVault(vault_root, init_git=False)
            idx_path = vault_root / ".index" / "meta.sqlite"
            if not idx_path.exists():
                rebuild_index(vault, idx_path)
            globals()["_cached_bridge"] = KernelBridge(vault, idx_path)
        except Exception:
            globals()["_cached_bridge"] = None
    return globals().get("_cached_bridge")
# ── Startup ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="RLM Web — HTTPS frontend")
    parser.add_argument("--host", default=os.environ.get("RLM_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RLM_WEB_PORT", "8778")))
    parser.add_argument("--ssl-keyfile", default=os.environ.get("RLM_WEB_SSL_KEY"))
    parser.add_argument("--ssl-certfile", default=os.environ.get("RLM_WEB_SSL_CERT"))
    args = parser.parse_args(argv)

    kwargs: dict = {"host": args.host, "port": args.port, "reload": False}
    if args.ssl_keyfile and args.ssl_certfile:
        kwargs["ssl_keyfile"] = args.ssl_keyfile
        kwargs["ssl_certfile"] = args.ssl_certfile

    uvicorn.run("rlm_web.app:app", **kwargs)


if __name__ == "__main__":
    main()
