# A citation on the dict is a citation

**2026-09-22.** The owner read the two A/B trajectories and reported the shape of the failure
before any of us looked at the code: *"the harness wants a 'Citations: ' literal last line in
the text, while the model is providing the citation mnemonics as a key 'citations' of the
`answer` object in the REPL. Precious turns are wasted in this wrestling."* That is exactly
what was happening, and the correlation in the trajectories is exact.

## The mechanism

Two halves of the harness disagreed about the shape of a citation:

- the **contract was text**: `prompts.py` tells the model to end with `Citations: <alias>`;
  `templates.py` repeats it in both nudges; `root_loop._map_citations(answer: str)` and
  `_record_citations(turn, answer)` audit the answer **string**;
- the **scaffold was a dict**: the worker hands the model `answer` with `content` and `ready`,
  and only `answer['content']` was ever forwarded. `repl.py` built `final_answer` from that
  key alone, so anything else the model put on the dict — including `answer['citations']` —
  was discarded *inside the worker*, before the parent could see it. The parent then refused
  the answer for citing nothing and asked again.

The model was doing the reasonable thing with the object it had been given, and the harness
was reading the one field it had always read.

## The measurement

Counted over the six-question prose set and its two-question A/B:

| question | wrote `answer['citations']` | uncited refusals |
|---|---|---|
| prompt-ab prose-002 | 2× | **3** |
| prompt-ab prose-003 | 2× | **3** |
| prose-005 | 1× | **3** |
| prose-001, -003, -004, -006 | 0× | 0 |

The correlation is exact: **the questions that wrote that key were the questions refused for
having no address, three times each, and the questions that did not write it were refused
none.** Three refusals is three turns — the owner's "precious turns wasted" is countable, and
it is most of the difference between a run that finishes in ten minutes and one that grinds.

Recorded as an open item, not fixed here: the refusal detail reads `nudges=3/2`, past its own
nominal cap of two. Whether that is a second channel counting separately or a cap that is not
enforced is a question for its own measurement.

## The fix

`_with_citations(content, answer_obj)` in the worker: when the model cited on the dict and the
text carries no `Citations:` line, the line is appended once, where the answer leaves the
worker. Accepted shapes are a list, tuple or set, or a string separated by `;` or `,` — the
shapes a model actually produces. **The text wins when it already has a line**, so a model
that complied in prose is never argued with, and a model that did neither is unchanged: the
reconciliation is invisible in the cases it does not apply to.

The text remains what travels — the alias mapping, the audit and the delivered answer all read
it — so nothing downstream needed to change, and the narrow refusal still works on the shape it
was built for.

## Verified, and not

- **Verified**: the reconciliation at the worker boundary, with three tests that run real cells
  through the real sandbox (a list, a `;`-separated string, a `,`-separated string, a tuple),
  a test that a text line is not doubled, and a test that an answer with no citations key is
  returned byte-for-byte. One mutation entry, red as required.
- **Not yet verified**: the effect on a live run. The prediction is that `refusals_uncited`
  drops to zero or near it on the two A/B questions, and that is a re-run rather than an
  argument — the same two questions, once more, and the counts compared.

## Also worth noting

This is the second time today that the harness and the model were each behaving sensibly and
the *interface* between them was the defect (the first was the few-shot exemplar,
`docs/20260922-2330-the-exemplar-was-the-quest.md`). In both cases the fix was to accept what
the model actually does rather than to instruct it harder: the exemplar stopped being a quest,
and the citation stopped being only a line of text.
