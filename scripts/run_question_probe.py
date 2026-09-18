"""Run a set of real questions against the corpus, and print aggregates (2026-09-18).

Why this exists
---------------
The cell-budget probe measured a mechanism (a cell that sleeps). This one measures the
*task*: real questions, a real model, the real read-only corpus — because the traces
that say whether this harness can navigate 4.28M files are those traces, not a
sleep's.

Usage (where the corpus and the models are — lunacode):

    python scripts/run_question_probe.py --out-dir ~/rlm-derived/questions \
        --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite \
        --profile laptop --max-turns 6 --cell-timeout 60 --cell-timeout-hard 600

    # or with the owner's own set, which lives beside the corpus:
    python scripts/run_question_probe.py --questions ~/rlm-derived/questions/mine.txt …

Then render the pages a human can read, and hand *the paths* to the owner:

    rlm trace render ~/rlm-derived/questions --out-dir ~/rlm-derived/traces \
        --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite

What it prints, and what it deliberately does not
-------------------------------------------------
One aggregate line per question — turns, timeouts, extensions, helper calls, citations,
refusals, wall clock — plus where the trajectories are. It never prints an answer, a
passage or an address: those are corpus text and they stay in the trajectories beside
the corpus (`AGENTS.md` §1.9). The question set that was run is copied into the output
directory, so the page and its questions stay together.

No limit of its own
-------------------
The probe adds no wall-clock ceiling: the owner's call is that the harness has none, and
the run is bounded by the limits passed to it (`--max-turns`, `--cell-timeout`,
`--cell-timeout-hard`). A question that hits them shows up in its aggregate line as
`cell_timeout`/`forced`, which is the diagnosis rather than a mystery.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rlm_local.config import load_config  # noqa: E402
from rlm_local.model_backend import HTTPModelBackend  # noqa: E402
from rlm_local.question_probe import (  # noqa: E402
    DEFAULT_QUESTIONS,
    Question,
    parse_questions,
    render_line,
    run_question,
    write_question_set,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=None,
                    help="A question set (one per line, `id<TAB>question`, # comments). "
                         "Defaults to the built-in aggregate questions; a set devised "
                         "from passages belongs beside the corpus, not in the repo.")
    ap.add_argument("--out-dir", type=Path,
                    default=Path.home() / "rlm-derived" / "questions")
    ap.add_argument("--corpus-root", default="/srv/corpus")
    ap.add_argument("--corpus-index",
                    default=str(Path.home() / "rlm-derived" / "corpus.sqlite"))
    ap.add_argument("--profile", default="laptop")
    ap.add_argument("--model", default=None, help="Override the profile's root model")
    ap.add_argument("--endpoint", default=None, help="Override the profile's endpoint")
    ap.add_argument("--max-turns", type=int, default=6)
    ap.add_argument("--cell-timeout", type=float, default=60.0)
    ap.add_argument("--cell-timeout-hard", type=float, default=600.0)
    ap.add_argument("--only", default=None,
                    help="Run only the question whose id contains this substring")
    args = ap.parse_args()

    if args.questions is not None:
        questions = parse_questions(args.questions.read_text(encoding="utf-8"))
        source = str(args.questions)
    else:
        questions = [Question(id=name, question=text)
                     for name, text in DEFAULT_QUESTIONS]
        source = "built-in (aggregate questions only)"
    if args.only:
        questions = [q for q in questions if args.only.lower() in q.id.lower()]
    if not questions:
        print("No questions to run.", file=sys.stderr)
        return 2

    cfg = load_config(args.profile, max_turns=args.max_turns,
                      cell_timeout=args.cell_timeout,
                      cell_timeout_hard=args.cell_timeout_hard)
    if args.model:
        cfg.overrides["root_model"] = args.model
        cfg.overrides["sub_model"] = args.model
    if args.endpoint:
        cfg.overrides["root_endpoint"] = args.endpoint

    from rlm_kernel.corpus import CorpusBridge

    bridge = CorpusBridge.open_for(args.corpus_root, args.corpus_index,
                                   cache_root=Path(args.corpus_index).parent / "cache")
    backend = HTTPModelBackend(
        root_endpoint=cfg.root_endpoint, sub_endpoint=cfg.sub_endpoint,
        root_model=cfg.root_model, sub_model=cfg.sub_model,
        verify=False, timeout=300.0,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_question_set(questions, args.out_dir / "questions.txt")

    print(f"# model={cfg.root_model} profile={args.profile} "
          f"max_turns={args.max_turns} soft={args.cell_timeout:g}s "
          f"hard={args.cell_timeout_hard:g}s questions={len(questions)} "
          f"source={source}", flush=True)
    print("# Aggregates only. The answers and the passages they cite stay in the "
          "trajectories", flush=True)
    print(f"# beside the corpus: {args.out_dir}", flush=True)
    try:
        for index, question in enumerate(questions, 1):
            print(f"# [{index}/{len(questions)}] {question.id} — running", flush=True)
            run = run_question(question, config=cfg, backend=backend,
                               corpus_bridge=bridge, out_dir=args.out_dir)
            print(render_line(run), flush=True)
    finally:
        bridge.close()
        backend.close()

    print(f"# trajectories: {args.out_dir}")
    print("# render them where the corpus is: rlm trace render "
          f"{args.out_dir} --out-dir ~/rlm-derived/traces "
          "--corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
