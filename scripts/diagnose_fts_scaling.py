"""Diagnose rebuild bottleneck at scale: FTS5 insert scaling vs parse cost vs tombstones."""
import shutil, sqlite3, sys, tempfile, time
from pathlib import Path

REPO = Path.home() / "Documents" / "Misc" / "rlm_for_local"
sys.path.insert(0, str(REPO / "src")); sys.path.insert(0, str(REPO))
from rlm_kernel.index import Index  # noqa: E402
from rlm_kernel.vault import LocalVault  # noqa: E402

CORPUS = Path.home() / "load-corpus-synth-100k"
OUT = Path.home() / "rlm_fts_diagnosis.txt"
log = OUT.open("w", encoding="utf-8")

def p(*a):
    print(*a); print(*a, file=log); log.flush()

# ── Phase A: 20K slice — parse phase vs index phase, with insert scaling ────
tmp = Path(tempfile.mkdtemp(prefix="rlm_diag_"))
files = sorted(CORPUS.rglob("*.md"))[:20_000]
for f in files:
    rel = f.relative_to(CORPUS)
    (tmp / rel.parent).mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, tmp / rel)

vault = LocalVault(tmp, init_git=False)
t = time.perf_counter()
pages = vault.list()
parse_s = time.perf_counter() - t
p(f"[A] vault.list() parse: {len(pages)} pages in {parse_s:.0f}s ({parse_s/len(pages)*1000:.1f} ms/page)")

idx = Index(tmp / ".index" / "meta.sqlite")
idx._ensure_schema()
B = 4000
for start in range(0, len(pages), B):
    batch = pages[start:start + B]
    t = time.perf_counter()
    for page in batch:
        idx._index_page(page)
    idx.conn.commit()
    dt = time.perf_counter() - t
    p(f"[A] index pages {start}–{start+len(batch)}: {dt:.1f}s ({dt/len(batch)*1000:.1f} ms/page)")
idx.close()

# ── Phase B: the gate-run index copy — fragmentation + tombstone cost ───────
src_db = CORPUS / ".index" / "meta.sqlite"
if src_db.exists():
    db_copy = tmp / "gate_copy.sqlite"
    shutil.copy2(src_db, db_copy)
    for ext in ("-wal", "-shm"):
        src_extra = Path(str(src_db) + ext)
        if src_extra.exists():
            shutil.copy2(src_extra, Path(str(db_copy) + ext))

    def p95_search(db_path):
        conn = sqlite3.connect(str(db_path))
        qs = ["error", "function", "system", "data", "process",
              "implementation", "configuration", "test", "result", "value"]
        lat = []
        for q in qs:
            t = time.perf_counter()
            conn.execute("SELECT path FROM fts_pages WHERE fts_pages MATCH ? ORDER BY rank LIMIT 5",
                         (f'"{q}"',)).fetchall()
            lat.append((time.perf_counter() - t) * 1000)
        conn.close()
        lat.sort()
        return lat[int(len(lat) * 0.95) - 1] if len(lat) > 1 else lat[0]

    p(f"[B] search p95 on gate-run index (as-is): {p95_search(db_copy):.1f} ms")

    t = time.perf_counter()
    conn = sqlite3.connect(str(db_copy))
    conn.execute("INSERT INTO fts_pages(fts_pages) VALUES('optimize')")
    conn.commit(); conn.close()
    p(f"[B] FTS 'optimize': {time.perf_counter()-t:.0f}s")
    p(f"[B] search p95 after optimize: {p95_search(db_copy):.1f} ms")

    conn = sqlite3.connect(str(db_copy))
    t = time.perf_counter()
    conn.execute("DELETE FROM fts_pages WHERE rowid IN (SELECT rowid FROM fts_pages LIMIT 1000)")
    conn.commit()
    p(f"[B] DELETE of 1000 FTS rows: {time.perf_counter()-t:.1f}s (tombstone cost probe)")
    conn.close()

    # Fresh-file rebuild comparison: search on the 20K fresh index
    p(f"[B] search p95 on fresh 20K index: {p95_search(tmp / '.index' / 'meta.sqlite'):.1f} ms")
else:
    p("[B] gate index file not found:", src_db)

log.close()
p("[done] results at", OUT)
shutil.rmtree(tmp, ignore_errors=True)
