#!/usr/bin/env python3
"""Draft a question set from prose passages, where the corpus is.

    uv run python scripts/draft_question_set.py --n 8 --seed 20260922 \
        --corpus-root /srv/corpus --corpus-index ~/rlm-derived/corpus.sqlite \
        --out-dir ~/rlm-derived/questions --draft-model Qwen3-4B-2507

Why this exists: the owner's evaluation loop needs questions a person would ask of *this*
corpus, and `AGENTS.md` §1.9 keeps corpus prose off every channel that leaves the machine.
So the passages are drawn with the prose-preferring sampler (`rlm corpus sample`, 2026-09-22)
and a **local** model drafts one question per passage. What this prints is a count; the
questions and the addresses they came from are written beside the corpus, 0600, for the owner
to read, edit or throw away.

The drafting model should *differ* from the model that will answer the set — otherwise a low
citation rate says as much about the drafter as about the harness, and that confound is
recorded in the set's header rather than argued away.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from rlm_kernel.corpus import CorpusIndex  # noqa: E402
from rlm_kernel.mounts import LocalTreeMount, ReadOnlyViolation  # noqa: E402
from rlm_local.config import load_config  # noqa: E402
from rlm_local.model_backend import HTTPModelBackend  # noqa: E402
from rlm_local.question_draft import (  # noqa: E402
    Draft, as_questions, draft_question, loaded_models, resident_conflict, write_sources,
)
from rlm_local.question_probe import write_question_set  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="How many questions to draft")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--chars", type=int, default=3_000,
                    help="How much of each passage the drafter sees")
    ap.add_argument("--floor", type=float, default=None)
    ap.add_argument("--corpus-root", default="/srv/corpus")
    ap.add_argument("--corpus-index",
                    default=str(Path.home() / "rlm-derived" / "corpus.sqlite"))
    ap.add_argument("--out-dir", type=Path,
                    default=Path.home() / "rlm-derived" / "questions")
    ap.add_argument("--name", default="prose-drafted",
                    help="Set name; files are <name>.tsv and <name>.sources.tsv")
    ap.add_argument("--profile", default="laptop")
    ap.add_argument("--draft-model", default=None,
                    help="The model that drafts. Use a different one from the model that "
                         "will answer, or say why not.")
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--allow-second-model", action="store_true",
                    help="Draft with a model that is NOT the one already resident. The "
                         "router keeps every model it has served, so this is how the box "
                         "starts swapping (AGENTS.md §4): measured 2026-09-22, it made "
                         "lunacode unresponsive for half an hour. Restart the router first "
                         "if you want a different drafting model.")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(2 ** 31)
    index = CorpusIndex(args.corpus_index)
    try:
        text_index = index.text()
        text_index.ensure()
        mount = LocalTreeMount(args.corpus_root)
        cache_root = Path(args.corpus_index).parent / "cache"
        picks = dict(floor=args.floor) if args.floor is not None else {}
        started = time.time()
        hits, passages, stats = text_index.random_prose(
            args.n, rng=random.Random(seed), mount=mount, cache_root=cache_root,
            chars=max(4_000, args.chars), **picks)
        print(f"# prose draw: {len(hits)} passage(s) from {stats['drawn']} drawn "
              f"({stats['rejected_content']} rejected on content), floor={stats['floor']}, "
              f"mean score {stats['mean_score']}, seed={seed}, "
              f"{time.time() - started:.1f}s", flush=True)

        cfg = load_config(args.profile)
        model = args.draft_model or cfg.root_model
        endpoint = args.endpoint or cfg.root_endpoint
        # Ask the router what is resident *before* naming a model at it. This is the guard
        # for the incident that took this box down: one model at a time is not tidiness, it
        # is the difference between a run and a machine that has to be waited out.
        resident = loaded_models(endpoint)
        conflict = resident_conflict(resident, model)
        if conflict:
            if not args.allow_second_model:
                print(f"Error: {conflict}", file=sys.stderr)
                return 2
            print(f"# WARNING: --allow-second-model was passed. {conflict}", flush=True)
        print(f"# router resident: {', '.join(resident) if resident else 'none reported'}; "
              f"drafting with {model}", flush=True)
        backend = HTTPModelBackend(
            root_endpoint=endpoint,
            sub_endpoint=endpoint,
            root_model=model, sub_model=model, verify=False, timeout=600.0,
        )
        drafts: list[Draft] = []
        try:
            for position, (hit, passage) in enumerate(zip(hits, passages), 1):
                draft = draft_question(
                    backend, passage, draft_model=model,
                    identifier=f"prose-{position:03d}", address=hit.address,
                    passage_chars=args.chars,
                )
                drafts.append(draft)
                print(f"# [{position}/{len(hits)}] drafted "
                      f"{'ok' if draft.usable else 'UNUSABLE'} "
                      f"({len(passage)} chars in)", flush=True)
        finally:
            backend.close()

        usable = as_questions(drafts)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        set_path = args.out_dir / f"{args.name}.tsv"
        write_question_set(usable, set_path)
        write_sources(drafts, args.out_dir / f"{args.name}.sources.tsv")
        print(f"# drafted {len(usable)} usable of {len(drafts)}; "
              f"draft model {model}; drafters differ from the answering model only if the "
              f"run names another", flush=True)
        print(f"# set: {set_path}", flush=True)
        print(f"# addresses (beside the corpus, 0600): "
              f"{args.out_dir / f'{args.name}.sources.tsv'}", flush=True)
        print(f"# run it: uv run python scripts/run_question_probe.py "
              f"--questions {set_path} --out-dir {args.out_dir} ...", flush=True)
        return 0
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
