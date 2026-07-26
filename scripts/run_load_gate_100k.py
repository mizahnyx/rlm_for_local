"""One-off 100K load-gate runner (Tier 1). Prints a report table to stdout."""
import os, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] if "__file__" in dir() else Path.cwd()
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))                      # for tests.load.gen_corpus
from tests.load.gen_corpus import generate_corpus  # noqa: E402
from rlm_kernel.index import Index, rebuild_index  # noqa: E402
from rlm_kernel.vault import LocalVault            # noqa: E402

CORPUS = Path(os.environ.get("LOAD_CORPUS_OUT",
                             Path.home() / "load-corpus-synth-100k"))
N = 100_000
QUERIES = ["error", "function", "system", "data", "process",
           "implementation", "configuration", "test", "result", "value"]

def pct(values, p):
    s = sorted(values); k = (len(s) - 1) * p / 100
    f = int(k); c = k - f
    return s[f] + (s[f + 1] - s[f]) * c if f + 1 < len(s) else s[f]

print(f"=== Tier-1 load gate: {N} pages at {CORPUS} ===")
if not (CORPUS / ".corpus_manifest.json").exists():
    print("[1/5] generating…"); t = time.perf_counter()
    generate_corpus(CORPUS, N, seed=42)
    print(f"  generated in {time.perf_counter()-t:.0f}s")
else:
    print("[1/5] corpus already present, skipping generation")

idx_path = CORPUS / ".index" / "meta.sqlite"
vault = LocalVault(CORPUS, init_git=False)
print("[2/5] full reindex…"); t = time.perf_counter()
idx = rebuild_index(vault, idx_path)
rebuild_s = time.perf_counter() - t
print(f"  {idx.page_count()} pages in {rebuild_s:.0f}s  (target < 7200s)")

print("[3/5] search latency…")
lat = []
for q in QUERIES:
    t = time.perf_counter(); idx.fts_search(q, limit=5)
    lat.append((time.perf_counter() - t) * 1000)
p95 = pct(lat, 95)
print(f"  p95 = {p95:.1f}ms  (target < 300ms)")

print("[4/5] helper listing (G4b check)…")
t = time.perf_counter(); paths = idx.list_paths(kind="helper", status="active")
idx_ms = (time.perf_counter() - t) * 1000
t = time.perf_counter(); pages = vault.list(kind="helper")
walk_s = time.perf_counter() - t
print(f"  index list_paths: {idx_ms:.1f}ms ({len(paths)} paths)")
print(f"  vault.list walk:  {walk_s:.1f}s ({len(pages)} parsed)  <- per-completion cost")
idx.close()

print("[5/5] git…")
if not (CORPUS / ".git").exists():
    subprocess.run(["git", "init"], cwd=CORPUS, capture_output=True)
    (CORPUS / ".gitignore").write_text(".index/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=CORPUS, capture_output=True)
    subprocess.run(["git", "-c", "user.email=load@test", "-c", "user.name=load",
                    "commit", "-m", "corpus", "--quiet"], cwd=CORPUS, capture_output=True)
subprocess.run(["git", "config", "core.fsmonitor", "true"], cwd=CORPUS, capture_output=True)
subprocess.run(["git", "config", "core.untrackedCache", "true"], cwd=CORPUS, capture_output=True)
subprocess.run(["git", "status", "--porcelain"], cwd=CORPUS, capture_output=True)  # warm
t = time.perf_counter()
subprocess.run(["git", "status", "--porcelain"], cwd=CORPUS, capture_output=True)
git_ms = (time.perf_counter() - t) * 1000
print(f"  git status: {git_ms:.0f}ms  (target < 2000ms)")

print("\n=== RESULTS ===")
print(f"pages={N} rebuild={rebuild_s:.0f}s search_p95={p95:.1f}ms "
      f"git_status={git_ms:.0f}ms helper_idx={idx_ms:.1f}ms helper_walk={walk_s:.1f}s")
print(f"gate: rebuild {'PASS' if rebuild_s < 7200 else 'FAIL'} | "
      f"search {'PASS' if p95 < 300 else 'FAIL'} | git {'PASS' if git_ms < 2000 else 'FAIL'}")

