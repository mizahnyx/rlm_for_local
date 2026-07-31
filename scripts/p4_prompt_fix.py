"""P4 fix: add answer-dict submission instructions to how-to-work.md through the gate."""
from pathlib import Path
from rlm_kernel.vault import LocalVault
from rlm_kernel.gate import propose, validate, promote
from rlm_kernel.schema import PageStatus

vault = LocalVault(Path.home() / ".local/share/rlm-kernel/vault")

new_body = (
    '<!-- optimized_by: gepa-run-v5-rescued-1785419689 -->\n'
    '<!-- rationale: Rescued from GEPA v5 checkpoint (power cut at 32/40). '
    'Direct eval: train 1.0 (5/5), held_out 1.0 (2/2) vs baseline 0.8. -->\n'
    '\n'
    '## How to Work (follow this order)\n'
    '\n'
    '1. **PROBE** \u2014 Pull the first 200-character window and identify '
    'the target field (year, name, price, email, version).  \n'
    '2. **PLAN** \u2014 Map the target field to a concrete extraction step; '
    'list the exact sub-calls and the order in which to run them.  \n'
    '3. **EXECUTE** \u2014 In each turn extract one fact; immediately verify '
    'the extraction by matching a regex pattern and log a short snippet.  \n'
    '4. **SUBMIT** \u2014 Concatenate all extracted snippets into a single '
    'answer. Then, in a ```repl block, set answer["content"] to your final '
    'answer and answer["ready"] = True. Do this only after you have printed '
    'your candidate answer. Never submit in prose or JSON \u2014 only via '
    'the answer dict in code.  \n'
    '\n'
    '## Rules for a Small Context Window\n'
    '\n'
    '- Keep each turn focused on a single fact.  \n'
    '- Use llm_query_batched whenever you need to read multiple sentences '
    'at once.  \n'
    '- If a keyword or regex matches the target, extract it immediately.  \n'
    '- Chunk large passages only when necessary ({example_chunking_idiom}).  \n'
    '\n'
    'You have {max_turns} turns. Plan your extraction sequence accordingly.\n'
)

path = propose(
    vault, "contract", "how-to-work-v6", new_body,
    "Add explicit answer-dict submission instructions to fix P4=0/15 "
    "(model never sets answer[ready])",
)
page = vault.get(path)
report = validate(page, vault=vault)
print(f"Validation: passed={report.passed}")
for e in report.errors:
    print(f"  ERROR: {e}")
for w in report.warnings:
    print(f"  WARN: {w}")

if report.passed:
    page.body = (
        "<!-- optimized_by: gepa-run-v5-rescued-1785419689 gepa-run-edit-p4-fix -->\n"
        + new_body.split("-->", 2)[-1].lstrip()
    )
    tags = list(page.frontmatter.tags)
    if "gepa-optimized" not in tags:
        tags.append("gepa-optimized")
    if "p4-fix" not in tags:
        tags.append("p4-fix")
    page.frontmatter.tags = tags
    new_path = promote(vault, page, target_path="contract/how-to-work.md")
    vault.git_commit(
        "kernel: GEPA edit \u2014 add answer-dict submission instructions "
        "(P4 fix: model never set answer[ready])"
    )
    print(f"Promoted to: {new_path}")
else:
    print("Validation failed — not promoting")
