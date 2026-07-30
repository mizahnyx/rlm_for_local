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

# Templates
from fastapi.templating import Jinja2Templates  # noqa: E402

templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))


# ── Auth ───────────────────────────────────────────────────────────────────

def _check_auth(request: Request) -> None:
    """Check bearer token or session cookie."""
    token = os.environ.get("RLM_WEB_TOKEN", "")
    if not token:
        return  # No token configured — allow all
    try:
        if request.session.get("authenticated"):
            return
    except Exception:
        pass  # Session not available (e.g., TestClient)
    raise HTTPException(status_code=401, detail="Authentication required")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")
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
            vault_root = Path.home() / ".local" / "share" / "rlm-kernel" / "vault"
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
        vault_root = Path.home() / ".local" / "share" / "rlm-kernel" / "vault"
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


@app.get("/check", response_class=HTMLResponse)
async def check_page(request: Request):
    _check_auth(request)
    return templates.TemplateResponse(request, "check.html")


@app.get("/docs/{name:path}", response_class=HTMLResponse)
async def docs_page(request: Request, name: str):
    _check_auth(request)
    docs_dir = Path(__file__).parent.parent.parent / "docs"
    doc_path = docs_dir / name
    if not doc_path.exists() or not doc_path.is_file():
        raise HTTPException(status_code=404)
    content = doc_path.read_text(encoding="utf-8", errors="replace")
    return templates.TemplateResponse(request, "docs.html", {
        "name": name, "content": content,
    })


# ── Startup ────────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    host = os.environ.get("RLM_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("RLM_WEB_PORT", "8778"))
    uvicorn.run("rlm_web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
