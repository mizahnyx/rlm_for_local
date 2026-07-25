"""Deterministic synthetic page generator for load testing (D-5 Tier 1).

Produces a corpus of N markdown pages with realistic distributions of:
- Page kinds (contract 1%, template 2%, definition 15%, helper 10%,
  fewshot 2%, note 50%, topic 10%, cache 10%)
- Body sizes (200–8,192 chars, log-normal-ish)
- Tags (0–5 tags per page from a vocabulary of 200)
- Wikilinks (0–10 per page, drawn from a pool of page names)
- Frontmatter populated per spec §3.2

The output directory is NOT inside the repository (specified by --output).
Deterministic: seeded RNG ensures reproducibility.

Usage:
    python -m tests.load.gen_corpus --output /tmp/load_vault --pages 100000 --seed 42
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path


# ── Configuration ──────────────────────────────────────────────────────────

KIND_DISTRIBUTION: list[tuple[str, float]] = [
    ("contract", 0.01),
    ("template", 0.02),
    ("definition", 0.15),
    ("helper", 0.10),
    ("fewshot", 0.02),
    ("note", 0.50),
    ("topic", 0.10),
    ("cache", 0.10),
]

TAG_VOCABULARY: list[str] = [
    "ai", "llm", "python", "rust", "testing", "deployment", "security",
    "performance", "database", "networking", "frontend", "backend", "devops",
    "ml", "data", "api", "cli", "config", "logging", "monitoring",
    "architecture", "design", "review", "tutorial", "reference", "guide",
    "spec", "draft", "final", "deprecated", "experimental", "stable",
    "v1", "v2", "v3", "alpha", "beta", "production", "staging", "development",
    "linux", "windows", "macos", "bsd", "docker", "kubernetes", "cloud",
    "aws", "gcp", "azure", "local", "remote", "distributed", "edge",
    "fast", "slow", "memory", "cpu", "gpu", "disk", "network", "io",
    "sync", "async", "parallel", "concurrent", "sequential", "batch",
    "small", "medium", "large", "huge", "tiny", "micro", "nano",
    "red", "blue", "green", "yellow", "purple", "orange", "black", "white",
    "alpha-widget", "beta-gadget", "gamma-tool", "delta-service",
    "epsilon-module", "zeta-package", "eta-library", "theta-framework",
    "iota-plugin", "kappa-engine", "lambda-runtime", "mu-compiler",
    "abstract", "concrete", "generic", "specific", "dynamic", "static",
    "lazy", "eager", "mutable", "immutable", "pure", "impure",
    # ... up to 200
]

# Pad vocabulary to 200
while len(TAG_VOCABULARY) < 200:
    TAG_VOCABULARY.append(f"tag-{len(TAG_VOCABULARY)}")


def _sample_kind(rng: random.Random) -> str:
    kinds, weights = zip(*KIND_DISTRIBUTION)
    return rng.choices(kinds, weights=weights, k=1)[0]


def _sample_body_size(rng: random.Random) -> int:
    # Log-normal-ish: median ~1200, range 200–8192
    mu, sigma = 7.0, 0.8
    size = int(rng.lognormvariate(mu, sigma))
    return max(200, min(8192, size))


def _generate_body(rng: random.Random, size: int, kind: str) -> str:
    """Generate a body of approximately `size` characters."""
    words = [
        "the", "of", "and", "to", "in", "is", "that", "for", "it", "with",
        "as", "on", "be", "at", "by", "this", "have", "from", "or", "an",
        "system", "data", "model", "process", "function", "result", "value",
        "method", "object", "class", "module", "component", "service", "api",
        "query", "response", "request", "state", "context", "configuration",
        "implementation", "interface", "protocol", "framework", "pipeline",
        "index", "search", "vault", "page", "memory", "note", "topic",
        "blue", "red", "green", "widget", "gadget", "tool", "engine",
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
        "performance", "latency", "throughput", "overhead", "optimization",
    ]

    if kind == "helper":
        return _generate_helper_body(rng, size)
    elif kind == "contract":
        return _generate_contract_body(rng, size)
    else:
        return _generate_generic_body(rng, size, words)


def _generate_generic_body(rng: random.Random, size: int, words: list[str]) -> str:
    lines: list[str] = []
    chars = 0
    while chars < size:
        n_words = rng.randint(3, 15)
        sentence = " ".join(rng.choice(words) for _ in range(n_words))
        sentence = sentence[0].upper() + sentence[1:] + "."
        lines.append(sentence)
        chars += len(sentence) + 1  # +1 for newline
        if rng.random() < 0.3:
            lines.append("")  # paragraph break
            chars += 1
        if rng.random() < 0.1:
            lines.append(f"## {rng.choice(words).title()} {rng.choice(words).title()}")
            chars += len(lines[-1]) + 1
    return "\n".join(lines)


def _generate_helper_body(rng: random.Random, size: int) -> str:
    fn_name = f"process_{rng.choice(['data','text','items','records','entries'])}"
    lines = [
        "## Signature",
        "```python",
        f"def {fn_name}(items: list, max_results: int = 10) -> list[str]: ...",
        "```",
        "",
        "## Implementation",
        "```python",
        f"def {fn_name}(items, max_results=10):",
        "    results = []",
        "    for item in items:",
        "        if item:",
        "            results.append(str(item)[:100])",
        "        if len(results) >= max_results:",
        "            break",
        "    return results",
        "```",
        "",
        "## Usage example",
        "```repl",
        f"output = {fn_name}(['a','b','c'], max_results=2)",
        "print(output)",
        "```",
        "",
    ]
    remaining = size - sum(len(l) + 1 for l in lines)
    if remaining > 100:
        words = ["the", "system", "data", "process", "function", "result", "value"]
        lines.append(_generate_generic_body(rng, remaining, words))
    return "\n".join(lines)


def _generate_contract_body(rng: random.Random, size: int) -> str:
    lines = [
        "# System Contract",
        "",
        "This document defines the contract for the system.",
        "",
        "## Rules",
        "",
        "1. The system must respond to all queries.",
        "2. The answer format is `{answer_format}`.",
        "3. Maximum turns: `{max_turns}`.",
        "4. REPL output cap: `{repl_cap}` characters.",
        "",
    ]
    remaining = size - sum(len(l) + 1 for l in lines)
    if remaining > 100:
        words = ["system", "contract", "rule", "must", "shall", "comply", "enforce"]
        lines.append(_generate_generic_body(rng, remaining, words))
    return "\n".join(lines)


def generate_corpus(output_dir: Path, num_pages: int, seed: int = 42) -> None:
    """Generate a corpus of synthetic pages.

    Args:
        output_dir: Root directory for the vault. Must exist or be creatable.
        num_pages: Number of pages to generate.
        seed: Random seed for deterministic output.
    """
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Pre-generate page names for wikilink targets
    name_pool: list[str] = [f"page-{i:06d}" for i in range(num_pages)]
    kind_names: dict[str, list[str]] = {kind: [] for kind, _ in KIND_DISTRIBUTION}

    kind_dirs = {
        "contract": "contract",
        "template": "contract/templates",
        "definition": "definitions",
        "helper": "helpers",
        "fewshot": "fewshots",
        "note": "memory/notes",
        "topic": "memory/topics",
        "cache": "memory/caches",
    }

    for i in range(num_pages):
        kind = _sample_kind(rng)
        name = f"synth-{kind}-{i:06d}"
        title = f"Synthetic {kind.title()} {i:06d}"
        summary = f"A synthetic {kind} page #{i} for load testing."

        body_size = _sample_body_size(rng)
        body = _generate_body(rng, body_size, kind)

        # Tags
        n_tags = rng.randint(0, 5)
        tags = sorted(set(rng.choice(TAG_VOCABULARY) for _ in range(n_tags)))

        # Wikilinks in body
        n_links = rng.randint(0, min(10, num_pages // 100 + 1))
        link_targets = rng.sample(name_pool, min(n_links, len(name_pool)))
        for target in link_targets:
            if rng.random() < 0.5:
                body += f"\nSee also [[{target}]].\n"

        # Assemble frontmatter
        fm_lines = [
            "---",
            "schema: 1",
            f"kind: {kind}",
            f"name: {name}",
            f"title: \"{title}\"",
            f"summary: \"{summary}\"",
        ]
        if tags:
            tag_str = ", ".join(tags)
            fm_lines.append(f"tags: [{tag_str}]")
        fm_lines.append("version: 1")
        fm_lines.append("status: active")
        fm_lines.append("---")

        content = "\n".join(fm_lines) + "\n" + body

        dir_path = output_dir / kind_dirs[kind]
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / f"{name}.md"
        file_path.write_text(content, encoding="utf-8")

        kind_names[kind].append(name)

        if (i + 1) % 10000 == 0:
            print(f"  ... {i + 1}/{num_pages} pages generated")

    # Write a small manifest
    manifest = output_dir / ".corpus_manifest.json"
    import json
    manifest.write_text(json.dumps({
        "num_pages": num_pages,
        "seed": seed,
        "kinds": {k: len(v) for k, v in kind_names.items()},
    }, indent=2), encoding="utf-8")

    print(f"Generated {num_pages} pages in {output_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic vault corpus for load testing"
    )
    parser.add_argument("--output", type=Path, required=True,
                        help="Output directory for the vault")
    parser.add_argument("--pages", type=int, default=100_000,
                        help="Number of pages to generate")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility")
    args = parser.parse_args(argv)

    print(f"Generating {args.pages} pages (seed={args.seed}) into {args.output}...")
    generate_corpus(args.output, args.pages, args.seed)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
