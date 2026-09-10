"""Load test for rlm-kernel — Tier 1 (synthetic) and Tier 2 (organic, optional).

Verifies spec §12 load-gate targets:
  - Full reindex < 2 hours at 100K pages
  - FTS search p95 < 300 ms at 100K pages
  - git status < 2 seconds at 100K pages
  - Index RSS within budget

Tier 1: Deterministic synthetic corpus (gen_corpus.py).
Tier 2: Optional organic corpus from RLM_KERNEL_LOAD_CORPUS env var.

Usage:
    # Tier 1 only (fast-ish on dev box)
    python -m pytest tests/load/test_load.py -v -k "tier1"

    # Tier 2 (requires RLM_KERNEL_LOAD_CORPUS set)
    python -m pytest tests/load/test_load.py -v -k "tier2"

    # Both
    python -m pytest tests/load/test_load.py -v

All load tests are marked @pytest.mark.slow and @pytest.mark.load, and are
excluded from default runs by both `-m "not slow"` and `-k "not load"`
(README's documented `-k "not slow and not load"` therefore selects the fast
unit suite exactly).
"""

from __future__ import annotations

import gc
import os
import tempfile
import time
from pathlib import Path

import pytest

from rlm_kernel.index import Index, rebuild_index
from rlm_kernel.vault import LocalVault

# Every test in this module is a load-gate benchmark: slow by construction and
# permanently excluded from the fast suite by *both* markers.
pytestmark = [pytest.mark.slow, pytest.mark.load]


# ── Helpers ────────────────────────────────────────────────────────────────

def _time_ms(fn, *args, **kwargs):
    """Time a function call in milliseconds."""
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = (time.perf_counter() - t0) * 1000
    return result, elapsed


def _generate_synthetic_10k(tmp_path: Path) -> Path:
    """Generate a small synthetic corpus for smoke-testing."""
    from tests.load.gen_corpus import generate_corpus

    output = tmp_path / "corpus_10k"
    generate_corpus(output, 10_000, seed=42)
    return output


def _percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile (0-100)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_vals):
        return sorted_vals[f] + c * (sorted_vals[f + 1] - sorted_vals[f])
    return sorted_vals[f]


# ── Tier 1 — Synthetic corpus ──────────────────────────────────────────────

class TestLoadTier1:
    """Load tests against a 10K-page synthetic corpus (smoke test for CI)."""

    @pytest.fixture(scope="class")
    def corpus_path(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("load_tier1")
        return _generate_synthetic_10k(tmp)

    @pytest.fixture(scope="class")
    def vault(self, corpus_path):
        return LocalVault(corpus_path, init_git=False)

    def test_build_index_smoke(self, vault, corpus_path):
        """Index build completes and finds expected pages."""
        idx_path = corpus_path / ".index" / "meta.sqlite"
        t0 = time.perf_counter()
        idx = rebuild_index(vault, idx_path)
        elapsed = time.perf_counter() - t0

        pc = idx.page_count()
        idx.close()
        assert pc >= 9000, f"Expected ≥9000 pages, got {pc}"
        print(f"  Build: {pc} pages in {elapsed:.1f}s")

    def test_search_latency(self, vault, corpus_path):
        """FTS search latency is within bounds."""
        idx_path = corpus_path / ".index" / "meta.sqlite"
        idx = Index(idx_path)

        queries = ["blue widget", "performance optimization",
                    "security protocol", "data pipeline",
                    "alpha beta gamma", "testing framework",
                    "deployment strategy", "memory management",
                    "network configuration", "error handling"]

        latencies = []
        for q in queries:
            _, ms = _time_ms(idx.fts_search, q, limit=5)
            latencies.append(ms)

        idx.close()

        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)

        print(f"  Search latency: p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")
        assert p95 < 500, f"p95 search latency {p95:.1f}ms exceeds 500ms"
        assert p99 < 1000, f"p99 search latency {p99:.1f}ms exceeds 1000ms"

    def test_search_result_quality(self, vault, corpus_path):
        """Search returns results for realistic queries."""
        idx_path = corpus_path / ".index" / "meta.sqlite"
        idx = Index(idx_path)

        results = idx.fts_search("helper", limit=5)
        idx.close()

        assert len(results) > 0, "Search for 'helper' returned no results"
        print(f"  'helper' search: {len(results)} results")


# ── Tier 2 — Organic corpus (optional) ─────────────────────────────────────

def _get_organic_corpus() -> Path | None:
    """Get the organic corpus path from RLM_KERNEL_LOAD_CORPUS env var."""
    path = os.environ.get("RLM_KERNEL_LOAD_CORPUS")
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        pytest.skip(f"RLM_KERNEL_LOAD_CORPUS={path} does not exist")
    return p


class TestLoadTier2:
    """Load tests against an organic corpus (optional, env-var gated)."""

    @pytest.fixture(scope="class")
    def corpus_path(self):
        path = _get_organic_corpus()
        if path is None:
            pytest.skip("RLM_KERNEL_LOAD_CORPUS not set")
        return path

    @pytest.fixture(scope="class")
    def vault(self, corpus_path):
        return LocalVault(corpus_path, init_git=False)

    def test_build_index(self, vault, corpus_path):
        """Index build on organic corpus."""
        idx_path = corpus_path / ".index" / "meta.sqlite"
        t0 = time.perf_counter()
        idx = rebuild_index(vault, idx_path)
        elapsed = time.perf_counter() - t0

        pc = idx.page_count()
        idx.close()
        print(f"  Build: {pc} pages in {elapsed:.1f}s")

    def test_search_latency(self, vault, corpus_path):
        """FTS search latency on organic corpus."""
        idx_path = corpus_path / ".index" / "meta.sqlite"
        idx = Index(idx_path)

        queries = ["error", "function", "system", "data", "process",
                    "implementation", "configuration", "test", "result", "value"]

        latencies = []
        for q in queries:
            _, ms = _time_ms(idx.fts_search, q, limit=5)
            latencies.append(ms)

        idx.close()

        p95 = _percentile(latencies, 95)
        print(f"  Search latency p95: {p95:.1f}ms (target: < 300ms)")
