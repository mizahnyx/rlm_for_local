"""Experiment 2: name the quadratic statement + prove the external-content fix."""
import shutil, sqlite3, sys, tempfile, time
from pathlib import Path

REPO = Path.home() / "Documents" / "Misc" / "rlm_for_local"
sys.path.insert(0, str(REPO / "src")); sys.path.insert(0, str(REPO))
from rlm_kernel.vault import LocalVault  # noqa: E402

CORPUS = Path.home() / "load-corpus-synth-100k"
OUT = Path.home() / "rlm_fts_exp2.txt"
N = 6000
log = OUT.open("w", encoding="utf-8")

def p(*a):
    print(*a); print(*a, file=log); log.flush()

# Load N pages (parse cost excluded from the comparison)
tmp = Path(tempfile.mkdtemp(prefix="rlm_exp2_"))
files = sorted(CORPUS.rglob("*.md"))[:N]
for f in files:
    rel = f.relative_to(CORPUS)
    (tmp / rel.parent).mkdir(parents=True, exist_ok=True)
    shutil.copy2(f, tmp / rel)
pages = LocalVault(tmp, init_git=False).list()
p(f"loaded {len(pages)} pages")

# ── Variant 1: current design — per-statement cumulative timing ─────────────
db1 = tmp / "v1.sqlite"
conn = sqlite3.connect(str(db1))
conn.executescript("""
CREATE TABLE pages (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, kind TEXT,
                    name TEXT, title TEXT, summary TEXT, hash TEXT,
                    version INTEGER, status TEXT, updated TEXT);
CREATE TABLE tags (page_id TEXT, tag TEXT, PRIMARY KEY (page_id, tag));
CREATE VIRTUAL TABLE fts_pages USING fts5(path, kind, name, title, summary, body);
""")
timing = {}
def timed(label, sql, args):
    t = time.perf_counter()
    conn.execute(sql, args)
    timing[label] = timing.get(label, 0) + (time.perf_counter() - t)

t0 = time.perf_counter()
for page in pages:
    fm = page.frontmatter
    timed("pages_insert",
          "INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?,?,?,?)",
          (fm.id, page.path, fm.kind.value, fm.name, fm.title, fm.summary,
           fm.hash, fm.version, fm.status.value, fm.updated.isoformat()))
    timed("tags_delete", "DELETE FROM tags WHERE page_id = ?", (fm.id,))
    for tag in fm.tags:
        timed("tags_insert", "INSERT OR IGNORE INTO tags VALUES (?,?)", (fm.id, tag))
    timed("fts_insert",
          "INSERT INTO fts_pages VALUES (?,?,?,?,?,?)",
          (page.path, fm.kind.value, fm.name, fm.title, fm.summary, page.body))
    if len(timing) and len(pages) and (pages.index(page) + 1) % 2000 == 0:
        conn.commit()
        p(f"  v1 progress {pages.index(page)+1}: cumulative "
          + " ".join(f"{k}={v:.1f}s" for k, v in sorted(timing.items())))
conn.commit()
p(f"[v1] total index time: {time.perf_counter()-t0:.1f}s")
for k, v in sorted(timing.items()):
    p(f"[v1] {k}: {v:.1f}s ({v/N*1000:.2f} ms/page)")
conn.close()

# ── Variant 2: external-content table + single 'rebuild' pass ───────────────
db2 = tmp / "v2.sqlite"
conn = sqlite3.connect(str(db2))
conn.executescript("""
CREATE TABLE content (rowid INTEGER PRIMARY KEY, path TEXT, kind TEXT,
                      name TEXT, title TEXT, summary TEXT, body TEXT);
CREATE VIRTUAL TABLE fts_pages USING fts5(title, summary, body,
                                          content='content', content_rowid='rowid');
""")
t0 = time.perf_counter()
rows = [(page.path, page.frontmatter.kind.value, page.frontmatter.name,
         page.frontmatter.title, page.frontmatter.summary, page.body)
        for page in pages]
conn.executemany("INSERT INTO content VALUES (NULL,?,?,?,?,?,?)", rows)
conn.commit()
p(f"[v2] bulk content insert: {time.perf_counter()-t0:.1f}s")
t1 = time.perf_counter()
conn.execute("INSERT INTO fts_pages(fts_pages) VALUES('rebuild')")
conn.commit()
p(f"[v2] FTS 'rebuild' single pass: {time.perf_counter()-t1:.1f}s")
p(f"[v2] TOTAL: {time.perf_counter()-t0:.1f}s  (vs v1 statement sum)")

# Sanity: search works on the external-content index
t = time.perf_counter()
hits = conn.execute(
    "SELECT rowid FROM fts_pages WHERE fts_pages MATCH '\"helper\"' LIMIT 5").fetchall()
p(f"[v2] search sanity: {len(hits)} hits in {(time.perf_counter()-t)*1000:.1f}ms")
conn.close()
log.close()
print("[done]", OUT)
shutil.rmtree(tmp, ignore_errors=True)
