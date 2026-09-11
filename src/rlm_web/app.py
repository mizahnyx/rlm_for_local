"""FastAPI web frontend for rlm_web (D2).

HTTPS-ready, Tailscale-reachable, server-rendered with Jinja2 (plus vanilla
EventSource on the job/chat pages).

Trust model (S5/R21)
--------------------
Authentication is a **route dependency** (`require_auth`), not a call each
handler has to remember: `tests/test_web.py::TestAuthCoverageByConstruction`
walks the route table and fails if any API route lacks it. The gate is:

* with `RLM_WEB_TOKEN` set — an authenticated session cookie is required;
* without it — loopback callers only, and a missing peer address is rejected
  unless `RLM_WEB_ALLOW_TESTCLIENT=1` is set explicitly (dev only).

A session cookie authenticates a *request*, not the page that caused it, so
every state-changing route also carries `require_same_origin`, the CSRF control
described below. `TestOriginCoverageByConstruction` walks the route table for
that one too.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from rlm_local.config import load_config
from rlm_local.model_backend import HTTPModelBackend

# ── App setup ──────────────────────────────────────────────────────────────

app = FastAPI(title="RLM Web", version="0.1.0")


def _session_https_only() -> bool:
    """Cookie flags follow the TLS configuration (S5/R21).

    `main()` sets these env vars from `--ssl-keyfile`/`--ssl-certfile`, so the
    session cookie is marked `Secure` whenever the server actually speaks TLS.
    """
    return bool(os.environ.get("RLM_WEB_SSL_KEY")) and bool(
        os.environ.get("RLM_WEB_SSL_CERT")
    )


# Generated once per process when no secret is configured. Never a constant:
# a published signing secret lets anyone forge `{"authenticated": true}` and
# bypass RLM_WEB_TOKEN entirely (S5 follow-up).
_EPHEMERAL_SESSION_SECRET: str | None = None


def _session_secret() -> str:
    """Return the session-signing secret, never a value published in the repo.

    Resolution order:

    1. `RLM_WEB_SECRET` when the operator set it — the only value that survives
       a restart, and the only one an operator has actually chosen.
    2. Otherwise a random per-process secret, generated on first use.

    There is deliberately **no hardcoded fallback**. The previous constant
    (`rlm-web-dev-secret-change-in-production`) was in the repository, so with
    `RLM_WEB_TOKEN` set and `RLM_WEB_SECRET` unset anyone who could reach the
    port could mint a valid session cookie. `main()` additionally refuses to
    start in that configuration; a random fallback keeps programmatic and
    loopback-only use safe either way.
    """
    global _EPHEMERAL_SESSION_SECRET

    configured = os.environ.get("RLM_WEB_SECRET", "").strip()
    if configured:
        return configured

    if _EPHEMERAL_SESSION_SECRET is None:
        _EPHEMERAL_SESSION_SECRET = secrets.token_urlsafe(48)
    return _EPHEMERAL_SESSION_SECRET


def _install_session_middleware(https_only: bool) -> None:
    """(Re)install SessionMiddleware with the given cookie policy.

    Safe to call before the app starts serving: the middleware stack is rebuilt
    on the next request.
    """
    app.user_middleware = [
        m for m in app.user_middleware if getattr(m, "cls", None) is not SessionMiddleware
    ]
    app.middleware_stack = None
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        https_only=https_only,
        same_site="lax",
    )


_install_session_middleware(_session_https_only())

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Upload limits (P2)
MAX_UPLOAD_FILES = 20
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB

# In-memory stores are bounded so a long-lived server cannot grow without
# limit (R22).
MAX_STORED_ENTRIES = 100

# How long an SSE client waits for its stream to appear before being told the
# id is unknown. Without this the chat stream endpoint spun forever on a stale
# or bogus id, holding a request worker open indefinitely.
CHAT_STREAM_WAIT_SECONDS = 30.0

# Templates
from fastapi.templating import Jinja2Templates  # noqa: E402

templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


# ── Bounded in-memory stores (R22) ─────────────────────────────────────────

class _BoundedLRU(OrderedDict):
    """OrderedDict that evicts the least-recently-used entry past `maxlen`.

    Used for jobs and chat sessions: both are process-lifetime caches of
    user-supplied content, and neither had any upper bound.
    """

    def __init__(self, maxlen: int = MAX_STORED_ENTRIES) -> None:
        super().__init__()
        self._maxlen = maxlen

    def __setitem__(self, key, value) -> None:
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._maxlen:
            self.popitem(last=False)

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
        return super().get(key, default)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value


# ── Auth ───────────────────────────────────────────────────────────────────

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _authorize(
    *,
    client_host: str | None,
    session_authenticated: bool,
    token: str,
    allow_testclient: bool,
) -> bool:
    """Decide whether a request may proceed. Pure, so it is directly testable."""
    if token:
        # A token is configured: only an authenticated session passes. The dev
        # opt-in must not override this.
        return session_authenticated
    if client_host is None:
        # No peer address (e.g. behind an ASGI server that omits it): only an
        # explicit dev opt-in, never a fail-open.
        return allow_testclient
    if client_host in _LOOPBACK_HOSTS:
        return True
    return allow_testclient and client_host == "testclient"


def require_auth(request: Request) -> None:
    """FastAPI dependency: bearer-session auth, fail-closed (P3, S5/R21)."""
    token = os.environ.get("RLM_WEB_TOKEN", "")
    allow_testclient = os.environ.get("RLM_WEB_ALLOW_TESTCLIENT") == "1"

    try:
        authenticated = bool(request.session.get("authenticated"))
    except Exception:
        authenticated = False

    client_host = request.client.host if request.client is not None else None

    if not _authorize(
        client_host=client_host,
        session_authenticated=authenticated,
        token=token,
        allow_testclient=allow_testclient,
    ):
        raise HTTPException(status_code=401, detail="Authentication required")


# ── Cross-origin (CSRF) defence ────────────────────────────────────────────
#
# The console authenticates with a session cookie, and a cookie rides along on a
# request another site causes the browser to make. Authentication therefore does
# not authorise a POST by itself: without this check, any page the operator
# visits could submit a job, ingest a file, or clear a chat session in the
# console, and the only thing standing in the way was `SameSite=Lax` (recorded
# as the open item in the 2026-09-10 validation §8.1).
#
# The check is on the request's *claimed* origin rather than on a hidden
# anti-forgery token, because every state-changing route here is reachable both
# from the server-rendered forms and from `fetch()`, and a token would have to be
# threaded through every template and every call site.

ORIGIN_CHECK_ENV = "RLM_WEB_ORIGIN_CHECK"
ALLOWED_ORIGINS_ENV = "RLM_WEB_ALLOWED_ORIGINS"

ORIGIN_CHECK_MODES = ("off", "same-origin", "strict")
DEFAULT_ORIGIN_CHECK = "same-origin"


def _origin_check_mode() -> str:
    """The configured policy, defaulting to `same-origin`.

    An unrecognised value falls back to the default rather than to `off`, so a
    typo cannot silently disable the check at request time; `main()` rejects it
    outright at startup.
    """
    raw = os.environ.get(ORIGIN_CHECK_ENV, "").strip().lower()
    if not raw or raw not in ORIGIN_CHECK_MODES:
        return DEFAULT_ORIGIN_CHECK
    return raw


def _normalize_origin(value: str) -> str:
    """Canonical ``scheme://host:port`` for comparison, or "" when unusable.

    Loopback aliases collapse to a single host: the console is regularly reached
    as both `http://127.0.0.1:8778` and `http://localhost:8778`, and those are
    the same server. A same-origin check that called them different origins
    would lock the operator out of their own console. The scheme is kept, so an
    http page is never treated as the https console.
    """
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    if host in _LOOPBACK_HOSTS:
        host = "loopback"
    try:
        port = parts.port
    except ValueError:
        return ""
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return f"{parts.scheme}://{host}:{port}"


def _allowed_origins() -> frozenset[str]:
    """Operator allowlist, normalized. Empty when unset."""
    raw = os.environ.get(ALLOWED_ORIGINS_ENV, "")
    return frozenset(
        normalized
        for normalized in (_normalize_origin(part) for part in raw.split(","))
        if normalized
    )


def _request_origin(request: Request) -> str | None:
    """The origin this request claims, or ``None`` when it claims none.

    ``Origin`` is authoritative when present; a browser that withholds it still
    sends ``Referer`` on the same request, so the referrer's origin is the
    fallback. A *present but unusable* value — ``Origin: null`` from a sandboxed
    iframe or a `file://` page, or anything unparseable — returns ``""``, which
    is deliberately distinct from ``None`` so it fails closed instead of being
    mistaken for "not a browser".
    """
    raw = request.headers.get("origin")
    if raw is None:
        raw = request.headers.get("referer")
        if not raw:
            return None
    return _normalize_origin(raw)


def _origin_allowed(
    *,
    supplied: str | None,
    mode: str,
    allowlist: frozenset[str],
    own_origin: str,
) -> tuple[bool, str]:
    """Decide whether a request's claimed origin may act. Pure, so table-testable.

    Returns ``(allowed, reason)``; ``reason`` is empty when allowed.

    * ``off`` — the check is disabled entirely.
    * ``same-origin`` (default) — a request that claims an origin must claim
      *this* server's; a request that claims none is allowed, because that is
      not a cross-site browser request (curl, a script, a typed URL).
    * ``strict`` — an origin must be claimed *and* match. This is what you want
      behind a proxy that strips ``Origin`` or when only browsers should reach
      the console.

    A non-empty `allowlist` replaces the same-origin comparison, and does **not**
    implicitly include the console's own origin: that is the mode that also
    resists DNS rebinding, where the attacker's page resolves to this server and
    its ``Origin`` and ``Host`` agree. Add the console's own origin to the
    allowlist explicitly.
    """
    if mode == "off":
        return True, ""
    if supplied is None:
        if mode == "strict":
            return False, "an Origin or Referer header is required in strict mode"
        return True, ""
    if not supplied:
        return False, "the Origin/Referer header cannot be parsed as an origin"
    if allowlist:
        if supplied in allowlist:
            return True, ""
        return False, (
            f"origin {supplied} is not in {ALLOWED_ORIGINS_ENV} "
            "(include the console's own origin there too)"
        )
    if supplied == own_origin:
        return True, ""
    return False, f"origin {supplied} does not match this server ({own_origin})"


def require_same_origin(request: Request) -> None:
    """FastAPI dependency: block cross-origin state-changing requests (CSRF).

    Ordered *before* `require_auth` on every route that carries both, so a
    cross-site request is refused without the auth gate having to say whether a
    session existed.
    """
    mode = _origin_check_mode()
    if mode == "off":
        return

    allowed, reason = _origin_allowed(
        supplied=_request_origin(request),
        mode=mode,
        allowlist=_allowed_origins(),
        own_origin=_normalize_origin(str(request.base_url)),
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Cross-origin request rejected: {reason}.",
        )


@app.post("/login", dependencies=[Depends(require_same_origin)])
async def login(request: Request, token: str = Form(...)):
    expected = os.environ.get("RLM_WEB_TOKEN", "")
    if not expected:
        # Loopback-only mode needs no login, and there is nothing to compare
        # against — accepting any token here was a fail-open (S5/R21).
        raise HTTPException(
            status_code=400,
            detail="No RLM_WEB_TOKEN is configured; login is disabled.",
        )
    if secrets.compare_digest(token, expected):
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse("Invalid token", status_code=401)


@app.get("/logout", dependencies=[Depends(require_same_origin)])
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ── Job storage ────────────────────────────────────────────────────────────

_jobs: dict[str, dict[str, Any]] = _BoundedLRU(MAX_STORED_ENTRIES)
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

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def console(request: Request):
    return templates.TemplateResponse(request, "console.html")


@app.post("/jobs", response_class=HTMLResponse,
          dependencies=[Depends(require_same_origin), Depends(require_auth)])
async def create_job(
    request: Request,
    query: str = Form(...),
    context: str = Form(""),
    profile: str = Form("laptop"),
    files: list[UploadFile] | None = None,
):
    """Create a job. Uploaded .md files are appended to the pasted context.

    The console form has always offered a file picker; the handler used to
    ignore it, so uploads vanished silently (R16).
    """
    if files:
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(
                status_code=413,
                detail=f"Too many files: {len(files)} (max {MAX_UPLOAD_FILES})",
            )
        total = sum(f.size or 0 for f in files)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Upload too large: {total} bytes (max {MAX_UPLOAD_BYTES})",
            )
        parts = [context] if context.strip() else []
        for f in files:
            if not f.filename or not f.filename.endswith(".md"):
                continue
            text = (await f.read()).decode("utf-8", errors="replace")
            if text.strip():
                parts.append(f"# {f.filename}\n\n{text}")
        context = "\n\n".join(parts)

    job_id = _create_job(query, context, profile)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse,
         dependencies=[Depends(require_auth)])
async def job_view(request: Request, job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse(request, "job.html", {
        "job": job,
    })


@app.get("/jobs/{job_id}/events", dependencies=[Depends(require_auth)])
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


@app.get("/vault", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def vault_search(request: Request, q: str = ""):
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


@app.get("/vault/page/{path:path}", response_class=HTMLResponse,
         dependencies=[Depends(require_auth)])
async def vault_page(request: Request, path: str):
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


@app.post("/vault/ingest",
          dependencies=[Depends(require_same_origin), Depends(require_auth)])
async def vault_ingest(request: Request, files: list[UploadFile] | None = None):
    """Ingest uploaded Markdown files into the vault as permanent pages."""
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

@app.get("/docs/{name:path}", response_class=HTMLResponse,
         dependencies=[Depends(require_auth)])
async def docs_page(request: Request, name: str):
    docs_dir = Path(__file__).parent.parent.parent / "docs"
    resolved = (docs_dir / name).resolve()
    if not resolved.is_relative_to(docs_dir.resolve()) or not resolved.is_file():
        raise HTTPException(status_code=404)
    content = resolved.read_text(encoding="utf-8", errors="replace")
    return templates.TemplateResponse(request, "docs.html", {
        "name": name, "content": content,
    })


# ── Chat ───────────────────────────────────────────────────────────────────

_chat_sessions: dict[str, dict[str, Any]] = _BoundedLRU(MAX_STORED_ENTRIES)
_chat_lock = threading.Lock()


@app.get("/chat", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def chat_page(request: Request):
    return templates.TemplateResponse(request, "chat.html", {"profile": "laptop"})


@app.post("/chat/send",
          dependencies=[Depends(require_same_origin), Depends(require_auth)])
async def chat_send(request: Request):
    """Queue a chat message and return a stream ID for SSE."""
    body = await request.json()
    session_id = body.get("session_id", "default")
    message = body.get("message", "").strip()
    profile = body.get("profile", "laptop")
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)
    stream_id = uuid.uuid4().hex[:8]
    with _chat_lock:
        _chat_sessions.setdefault(session_id, {"context": ""})
    threading.Thread(
        target=_process_chat_message,
        args=(session_id, message, stream_id, profile),
        daemon=True,
    ).start()
    return JSONResponse({"stream_id": stream_id, "session_id": session_id})


@app.get("/chat/events/{stream_id}", dependencies=[Depends(require_auth)])
async def chat_events(request: Request, stream_id: str):
    """SSE endpoint for chat response streaming."""
    import asyncio as _asyncio
    from fastapi.responses import StreamingResponse

    async def event_stream():
        waited = 0.0
        while True:
            found = None
            with _chat_lock:
                for sess in list(_chat_sessions.values()):
                    queues = sess.get("_queues", {})
                    if stream_id in queues:
                        found = queues[stream_id]
                        break
            if found is None:
                if waited >= CHAT_STREAM_WAIT_SECONDS:
                    yield (
                        "data: "
                        + json.dumps({"type": "error",
                                      "text": f"unknown stream id: {stream_id}"})
                        + "\n\n"
                    )
                    return
                await _asyncio.sleep(0.2)
                waited += 0.2
                continue
            while found:
                evt = found.pop(0)
                yield f"data: {json.dumps(evt)}\n\n"
                if evt.get("type") in ("done", "error"):
                    return
            await _asyncio.sleep(0.3)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/chat/clear",
          dependencies=[Depends(require_same_origin), Depends(require_auth)])
async def chat_clear(request: Request):
    """Clear a chat session's context."""
    body = await request.json()
    session_id = body.get("session_id", "default")
    with _chat_lock:
        if session_id in _chat_sessions:
            _chat_sessions[session_id]["context"] = ""
    return JSONResponse({"status": "ok"})


@app.post("/chat/context",
          dependencies=[Depends(require_same_origin), Depends(require_auth)])
async def chat_context(request: Request):
    """Return current chat session context."""
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
            vr = _get_vault_root()
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
            vault = LocalVault(_get_vault_root(), init_git=False)
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
            vault_root = _get_vault_root()
            vault = LocalVault(vault_root, init_git=False)
            idx_path = vault_root / ".index" / "meta.sqlite"
            if not idx_path.exists():
                rebuild_index(vault, idx_path)
            globals()["_cached_bridge"] = KernelBridge(vault, idx_path)
        except Exception:
            globals()["_cached_bridge"] = None
    return globals().get("_cached_bridge")


# ── Startup ────────────────────────────────────────────────────────────────

def _require_production_secret() -> None:
    """Refuse to serve with auth on and no operator-chosen session secret.

    Fail closed (S5 follow-up). A token without a secret means the session
    cookie is signed with something the process invented or, before this check
    existed, with a constant published in the repository — either way the
    token gate can be bypassed or every session dies on restart.

    Exits non-zero with an actionable message rather than starting a server
    whose authentication does not hold.
    """
    import sys

    token = os.environ.get("RLM_WEB_TOKEN", "").strip()
    secret = os.environ.get("RLM_WEB_SECRET", "").strip()
    if token and not secret:
        print(
            "error: RLM_WEB_TOKEN is set but RLM_WEB_SECRET is not.\n"
            "\n"
            "The session cookie must be signed with a secret you choose. Without\n"
            "one, either the signing key is guessable (which lets anyone forge an\n"
            "authenticated session and bypass the token) or it changes on every\n"
            "restart (which logs you out constantly).\n"
            "\n"
            "Generate one and export it:\n"
            "    export RLM_WEB_SECRET=\"$(python -c 'import secrets;"
            " print(secrets.token_urlsafe(48))')\"\n"
            "\n"
            "Or run without RLM_WEB_TOKEN, which serves loopback-only and needs no\n"
            "login at all.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _require_valid_origin_config() -> None:
    """Refuse to start on an origin policy that cannot mean what was typed.

    Both failures are silent at request time by design (`_origin_check_mode`
    falls back to the default rather than to `off`, and an unusable allowlist
    entry simply never matches), so the mistake has to be caught here or it
    would look like the console rejecting a legitimate request for no reason.
    """
    import sys

    raw_mode = os.environ.get(ORIGIN_CHECK_ENV, "").strip()
    if raw_mode and raw_mode.lower() not in ORIGIN_CHECK_MODES:
        print(
            f"error: {ORIGIN_CHECK_ENV}={raw_mode!r} is not a valid mode.\n"
            f"\nChoose one of: {', '.join(ORIGIN_CHECK_MODES)} "
            f"(default {DEFAULT_ORIGIN_CHECK}).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    raw_origins = os.environ.get(ALLOWED_ORIGINS_ENV, "")
    unusable = [
        part for part in raw_origins.split(",")
        if part.strip() and not _normalize_origin(part)
    ]
    if unusable:
        print(
            f"error: {ALLOWED_ORIGINS_ENV} has entries that are not origins: "
            f"{', '.join(repr(u) for u in unusable)}\n"
            "\nEach entry needs a scheme, host and (if not 80/443) port, e.g.\n"
            "    export "
            f'{ALLOWED_ORIGINS_ENV}="http://127.0.0.1:8778,'
            'https://lunacode.tail-scale.ts.net"',
            file=sys.stderr,
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
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
        # Cookie flags follow the served scheme (S5/R21): a Secure session
        # cookie once the server actually speaks TLS.
        os.environ["RLM_WEB_SSL_KEY"] = args.ssl_keyfile
        os.environ["RLM_WEB_SSL_CERT"] = args.ssl_certfile
        _install_session_middleware(https_only=True)

    _require_production_secret()
    _require_valid_origin_config()

    uvicorn.run("rlm_web.app:app", **kwargs)
    return 0


if __name__ == "__main__":
    main()
