"""K4 v5 health check probe — read-only diagnostic script.

Usage: uv run python scripts/k4_health_check.py
Follows ~/Documents/Misc/aisearches02/docs/20260729-1109-k4-v5-health-check-protocol.md
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
REPO = HOME / "Documents/Misc/rlm_for_local"
LOG_PATH = REPO / "k4-acceptance-run.txt"
CKPT_DIR = REPO / "logs/k4-gepa-checkpoints"
VAULT = HOME / ".local/share/rlm-kernel/vault"
TARGET_PAGE = VAULT / "contract/how-to-work.md"
TEMP_GLOB = str(HOME / "AppData/Local/Temp/rlm_repl_*")
LOG_TAIL_BYTES = 3000

HEADER = False
PASSED = True


def report(section: str) -> None:
    global HEADER
    if not HEADER:
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"K4 v5 health check — {t} local")
        HEADER = True
    print(f"\n  [{section}]")


def fail(msg: str) -> None:
    global PASSED
    PASSED = False
    print(f"  !! {msg}")


def ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def info(msg: str) -> None:
    print(f"     {msg}")


# ── P1 — Log tail and tripwires ──────────────────────────────────────────
report("P1 — Log tail and tripwires")

if not LOG_PATH.exists():
    fail("Log file not found")
else:
    content = LOG_PATH.read_text(errors="replace")
    size = LOG_PATH.stat().st_size
    tail = content[-LOG_TAIL_BYTES:]
    lines = content.split("\n")

    info(f"Log size: {size:,} bytes, {len(lines)} lines")

    for label, pattern in [
        ("UnicodeEncodeError", "UnicodeEncodeError"),
        ("ModuleNotFoundError", "ModuleNotFoundError"),
        ("no-propose loops", "did not propose a new candidate"),
    ]:
        count = content.count(pattern)
        if count > 0:
            fail(f"{label}: {count}")
        else:
            ok(f"{label}: 0")

    # Last tqdm line
    tqdm_lines = [l for l in lines if "GEPA Optimization" in l]
    if tqdm_lines:
        tqdm_last = tqdm_lines[-1].strip()[:120]
        info(f"Last tqdm: {tqdm_last}")
    else:
        info("No tqdm lines found")

    # Last iteration line
    iter_lines = [l for l in lines if re.match(r"Iteration \d+:", l)]
    if iter_lines:
        info(f"Last iteration: {iter_lines[-1].strip()[:120]}")
    else:
        info("No iteration lines found")

    # Terminal status check
    # `validation_failed` (R11) is a terminal status too: the candidate was
    # proposed but the gate rejected it, leaving the incumbent in place.
    status_found = False
    for pat in ["promoted", "no_improvement", "gate_error", "validation_failed"]:
        if f'"status": "{pat}"' in content or f'"status":"{pat}"' in content:
            status_found = True
            info(f"Terminal status: {pat}")
            break
    if not status_found:
        info("No terminal status line (run still in progress)")

    # Show tail end
    info(f"Log tail ({LOG_TAIL_BYTES} bytes):")
    for line in tail.split("\n")[-12:]:
        print(f"     | {line[:150]}")


# ── P2 — Checkpoint freshness ───────────────────────────────────────────
report("P2 — Checkpoints")
if CKPT_DIR.exists():
    files = sorted(CKPT_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    newest_ts = ""
    for f in files[:6]:
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime))
        if not newest_ts:
            newest_ts = mtime
        info(f"{f.name} — {mtime}")
    if newest_ts:
        age_min = (time.time() - os.path.getmtime(files[0])) / 60
        if age_min > 180:
            fail(f"Newest checkpoint is {age_min:.0f} min old (>180 min — suspect)")
        else:
            ok(f"Newest checkpoint {age_min:.0f} min old")
else:
    info("Checkpoint dir not found")


# ── P3 — Page mtime ─────────────────────────────────────────────────────
report("P3 — Page activity")
if TARGET_PAGE.exists():
    mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(TARGET_PAGE.stat().st_mtime))
    age_min = (time.time() - TARGET_PAGE.stat().st_mtime) / 60
    if age_min > 90:
        fail(f"Page mtime: {mtime} ({age_min:.0f} min ago — no activity >90 min)")
    else:
        ok(f"Page mtime: {mtime} ({age_min:.0f} min ago)")
else:
    info("Target page not found")


# ── P4 — REPL workers ───────────────────────────────────────────────────
report("P4 — REPL workers")
repl_dirs = sorted(glob.glob(TEMP_GLOB), key=lambda p: os.path.getmtime(p), reverse=True)
active_count = 0
for d in repl_dirs[:5]:
    mtime = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(d)))
    age_min = (time.time() - os.path.getmtime(d)) / 60
    if age_min < 60:
        active_count += 1
    info(f"{Path(d).name} — {mtime} ({age_min:.0f} min ago)")
if active_count > 0:
    ok(f"{active_count} REPL workers active in last 60 min")
elif repl_dirs:
    info("No REPL workers in last 60 min — suspect")


# ── P5 — Promotion evidence ─────────────────────────────────────────────
report("P5 — Promotion evidence")
if VAULT.exists():
    found = False
    for f in (VAULT / "contract").glob("*.md"):
        if f.exists():
            c = f.read_text(errors="replace")
            if "optimized_by" in c:
                found = True
                ok(f"Promoted: {f.name}")
                for line in c.split("\n"):
                    if "optimized_by" in line:
                        info(f"  {line.strip()[:120]}")
    if not found:
        info("No promotion yet (expected — run in progress)")

    # Git log
    result = subprocess.run(
        ["git", "-C", str(VAULT), "log", "--oneline", "-3"],
        capture_output=True, text=True,
    )
    if result.stdout.strip():
        info(f"Git: {result.stdout.strip()}")
    else:
        info("Git: (no commits — expected before promotion)")

    # Quarantine
    q = VAULT / "quarantine"
    if q.exists():
        qp = list(q.glob("*.md"))
        info(f"Quarantine pages: {len(qp)}")

    # Valset scores
    scores = re.findall(r"Best valset aggregate score so far: ([\d.]+)", content)
    if scores:
        info(f"Best valset scores: {', '.join(scores)}")


# ── Verdict ──────────────────────────────────────────────────────────────
report("VERDICT")
# Check for crash signatures
crash = any(
    content.count(p) > 0
    for p in ["UnicodeEncodeError", "ModuleNotFoundError"]
)
circular = content.count("did not propose a new candidate") >= 3

# Check for completion
completed = any(
    f'"status": "{s}"' in content or f'"status":"{s}"' in content
    for s in ["promoted", "no_improvement", "error", "gate_error", "validation_failed"]
)

if crash:
    print("  CRASHED — UnicodeEncodeError or ModuleNotFoundError detected")
elif circular:
    print("  CIRCULAR — 3+ consecutive 'did not propose' loops")
elif completed:
    print("  COMPLETED — terminal status found in log")
elif active_count == 0 and TARGET_PAGE.exists():
    age = (time.time() - TARGET_PAGE.stat().st_mtime) / 60
    if age > 90:
        print(f"  SUSPECT — no REPL or page activity for {age:.0f} min")
    else:
        print("  HEALTHY — still running, no crash signatures")
else:
    print("  HEALTHY — still running, no crash signatures")

sys.exit(0 if PASSED else 1)
