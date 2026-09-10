"""GEPA/litellm TLS posture (R18).

`optimize.py` used to set `litellm.ssl_verify = False` at module import — a
**process-global** switch that silently disables certificate verification for
every litellm call made anywhere in the process, including a later remote one.
litellm accepts `ssl_verify` per call, so the setting belongs on the reflection
model's kwargs.
"""

from __future__ import annotations

import re
from pathlib import Path

import rlm_kernel.optimize as optimize_mod

# An actual assignment statement, not a mention in a comment.
GLOBAL_ASSIGN_RE = re.compile(r"^\s*litellm\.ssl_verify\s*=", re.MULTILINE)


class TestLitellmTlsScope:
    def test_no_process_global_ssl_verify_assignment(self):
        src = Path(optimize_mod.__file__).read_text(encoding="utf-8")
        hits = GLOBAL_ASSIGN_RE.findall(src)
        assert hits == [], (
            "a process-global ssl_verify assignment disables verification for "
            "every litellm call in the process"
        )

    def test_the_checker_would_notice_a_real_assignment(self):
        """Non-vacuity: the regex matches an assignment and ignores prose."""
        assert GLOBAL_ASSIGN_RE.findall("    litellm.ssl_verify = False\n")
        assert GLOBAL_ASSIGN_RE.findall("litellm.ssl_verify=False")
        assert GLOBAL_ASSIGN_RE.findall(
            "    # this used to be `litellm.ssl_verify = False`\n"
        ) == []

    def test_ssl_verify_is_passed_per_call(self):
        src = Path(optimize_mod.__file__).read_text(encoding="utf-8")
        assert '"ssl_verify": False' in src, (
            "the self-signed local server needs ssl_verify=False, scoped to the "
            "reflection-model call"
        )

    def test_tradeoff_is_documented_at_the_call_site(self):
        src = Path(optimize_mod.__file__).read_text(encoding="utf-8")
        assert "R18" in src
        assert "process-global" in src
