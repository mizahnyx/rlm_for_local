"""rlm-kernel — An Evolvable, Smalltalk-style Layer for rlm_for_local.

The kernel makes an rlm_for_local instance evolvable like a Smalltalk image:
its ontology, capabilities, prompts, few-shots, and memory are content —
human-readable pages in a versioned wiki.

Three primitives:
1. REPL evaluation — extended so its namespace is assembled from definition pages.
2. Advanced search — hybrid search over the vault; doubles as introspection.
3. Human-readable indexed wiki — the persistent "image": git-versioned markdown pages.
"""

from __future__ import annotations

__version__ = "0.1.0"
