# Mnemonic addresses and structured hits — design, with the literature

**Created:** 2026-09-17 12:15
**Status:** **design, not implemented.** Two of the owner's six findings (2026-09-17)
change what a *hit* is, so they land together and with an owner call on the
model-facing contract. This document records the evidence, the proposed design, and
the gates. Nothing here has been built.
**Supersedes nothing.**

## The two findings

> *"Small models struggle a lot to keep consistency on path-like exact strings, so
> the addresses returned by the `corpus_search` or similar functions must be
> biunivocally translated back and forth into some mnemonic form that is more
> suitable to be consistently handled by smaller local models. This kind of mnemonics
> should be highly repairable."*

> *"Corpus search hits are being interpreted sometimes character by character,
> example, if the hit is 'Somewhere in Mexico City' the model sometimes sees 'S',
> 'o', 'm', etc. … as hits."*

They are one problem with two faces: **the identity of a hit is a long,
semantically neutral string, and the model treats it as text rather than as a
handle.** An address (`some/dir/Somewhere in Mexico City.txt#L1204-1360`) is 40–120
characters of arbitrary path bytes plus numbers; indexing into a hit element yields
its characters, and copying an address into a later cell is where small models slip.

## What the literature says

- **Copy glitches concentrate in semantically neutral strings.** CopyBench measures
  literal reproduction and finds the failures are format- and content-dependent, with
  the sharpest problems for strings that carry no meaning for the model — SHA keys,
  DNA sequences, IDs
  ([CopyBench, EMNLP 2024](https://aclanthology.org/2024.emnlp-main.844.pdf);
  [arXiv 2505.21785](http://www.arxiv.org/pdf/2505.21785) names exactly "glitches in
  copying large semantically neutral text (SHA keys, DNA sequences)").
- **Compressibility helps copying.** Character-level and compressed
  representations are copied more reliably than long natural-language strings
  ([arXiv 2402.01032](https://huggingface.co/buckets/huggingchat/papers-content/tree/2402/2402.01032.md)).
  This is the mechanism behind "make the identifier short and structured".
- **Practical systems already alias identifiers.** `langchain-id-aliaser` exists to
  map long/exotic IDs to aliases the model can handle
  ([socket.dev](https://socket.dev/pypi/package/langchain-id-aliaser/overview/0.1.0));
  LangChain added Anthropic tool-ID sanitization middleware for the same reason
  ([langchain PR #37096](https://github.com/langchain-ai/langchain/pull/37096));
  `id-agent` markets "token-efficient IDs for AI agents" as a UUID alternative
  ([github.com/vostride/id-agent](https://github.com/vostride/id-agent)).
- **Repair against a known set works and is common.** Hermes' agent runtime repairs
  tool-call IDs with `difflib` fuzzy matching at a 0.7 cutoff, plus lowercasing and
  helpful errors ([hermes-agent](https://github.com/NousResearch/hermes-agent/blob/e400d25b0508b8988f14c47e68273b535b2a3b86/agent/agent_runtime_helpers.py)).
  Because the candidate set is *the run's own served addresses*, repair is cheap and
  precise — the search space is tens of items, not billions.
- **Generative-retrieval work reaches the same conclusion** from the other side:
  identifiers for documents should be designed for the model that must emit them
  ([summarization-based document IDs, WikiNLP 2024](https://aclanthology.org/2024.wikinlp-1.18/)).

Nothing in that literature is about this corpus, so the design below is *informed* by
it, not derived from it. The measurements that decide it are ours.

## Proposed design: a per-run alias table with a repairable code

**Alias.** Each address the harness serves gets a short alias from a fixed alphabet:
two letters, a digit, and a check symbol — e.g. `KQ7-3`. The alphabet excludes
confusable glyphs (`0/O`, `1/I/L`, `5/S`, `8/B`), because visual confusion is the
common corruption, and the check symbol is a mod-N digest of the rest so a corrupted
alias is *detectably* corrupt rather than silently pointing elsewhere.

**Bijection.** Alias ↔ address is one-to-one **within a run**; the table is
append-only, and aliases are never reused across runs. A citation therefore cannot
silently resolve to a different passage in a later run — an old trace's alias either
resolves to nothing or is refused, and the trace carries its own mapping.

**Repair, in order** (`resolve(alias)`):

1. exact match;
2. case and whitespace normalisation;
3. confusable folding (`O→0`, `I/L→1`, `S→5`, `B→8`, `rn→m`);
4. edit distance ≤ 1 against the run's live alias set, **with a unique winner
   required** — two candidates means "ambiguous, here they are", never a guess;
5. otherwise: "no such alias in this run; the closest are X and Y".

**Every repair is reported** as a `citation_repaired` guardrail event, so the model's
real error rate on addresses becomes a number instead of an anecdote — and so the
cost of the mnemonic form (extra indirection, a table to explain) can be weighed
against its benefit on the next live run.

**Where it integrates.** `corpus_search` returns each hit with its alias; `corpus_read`
accepts an alias or a full address; citations may be written as aliases and are
mapped back to addresses **before** the served-set check, so the citation audit stays
exact; the trace viewer shows alias and address side by side.

## Second half: a hit should be a record, not a string

The character-by-character finding is the same defect seen from the indexing side.
Today each hit element is a formatted string, so `hits[0][0]` is `'S'` — and a small
model that expects a structure gets a letter and carries on, which is worse than an
error.

Proposed: each element becomes a record with named fields (`address`, `alias`,
`band`, `covers`, `snippet`, `text`) whose `__str__` is exactly today's printable
line (so `print(hits)` is unchanged and prompts that show the element still work), and
whose integer indexing **raises a naming error**: *"a corpus hit is a record; use
`hit['address']` (the address) or `hit['snippet']` (the passage opening)"*. A silent
wrong answer becomes an instructive failure, which is the whole difference for a 4B
model. `corpus_read(hit)` keeps working (the worker accepts a record and reads its
address), and `len(hits)` stays the contract.

## Gates — the owner's call, because this changes the model-facing contract

1. **Per-run aliases (recommended) or stable global codes?** Per-run is short and
   repairable against a small set; a citation means nothing outside its run, which is
   fine because the trace carries the mapping. A stable code (`hash → base32`) is
   portable across runs but must be long enough to be collision-free over 4.97M
   entries, which defeats the purpose.
2. **Do aliases appear in the *answer* the owner reads?** Option A: the model cites
   aliases and the trace/console renders them as addresses (readable, and the alias
   never escapes). Option B: the answer carries both. A is cleaner; it hides the
   model's error rate from the answer text but not from the events.
3. **Do both changes land together?** They touch the same three places (the worker's
   helper contract, the prompt, the citation guard) and are cheaper to verify as one
   change than two.

## What would decide them, measured rather than argued

- The next live run's `corpus_served` + citation events give a baseline error rate on
  addresses: count citations that had to be repaired, and citations refused because
  the address did not match what was served. That number is the *reason* for the
  change, and it is not yet measured on the current form.
- Then the same question with aliases, on the same model: `citation_repaired` rate,
  refusals, turns, elapsed. Two runs per form, host state recorded — the discipline
  every other model-facing change here has followed.
