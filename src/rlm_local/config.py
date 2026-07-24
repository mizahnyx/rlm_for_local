"""Configuration system with hardware profiles.

All numbers from §6 of the design document. Profiles are frozen; the active
profile is selected at init and individual values may be overridden.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Profile:
    """One hardware profile (§6). All defaults from the design doc."""

    name: str

    # Model routing
    root_model: str = ""
    sub_model: str = ""
    root_endpoint: str = "https://localhost:9010/v1"
    sub_endpoint: str = ""  # empty = same as root

    # Context windows (tokens)
    root_ctx_size: int = 16384
    sub_ctx_size: int = 16384

    # Budgets (characters)
    sub_prompt_char_budget: int = 16000
    repl_output_char_cap: int = 4000
    max_subcalls: int = 60
    max_subcall_chars: int = 4_000_000

    # Turn limits
    max_turns: int = 15
    max_concurrent_subcalls: int = 2
    max_depth: int = 1
    cell_timeout: float = 60.0

    # Context store
    context_spill_threshold: int = 1_000_000

    # Error budgets
    max_consecutive_errors: int = 3
    max_consecutive_nudges: int = 2

    # Anti-shortcut (fraction of total context; 0 = disabled)
    shortcut_warn_fraction: float = 0.60

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


# §6 — Three shipping profiles
PROFILES: dict[str, Profile] = {
    "tiny": Profile(
        name="tiny",
        root_model="LFM2.5-VL-1.6B",
        sub_model="LFM2.5-VL-1.6B",
        root_ctx_size=8192,
        sub_ctx_size=8192,
        sub_prompt_char_budget=8000,
        repl_output_char_cap=2000,
        max_turns=12,
        max_concurrent_subcalls=1,
        max_subcalls=30,
        max_subcall_chars=1_000_000,
        cell_timeout=60.0,
        context_spill_threshold=500_000,
    ),
    "laptop": Profile(
        name="laptop",
        root_model="LFM2.5-VL-1.6B",
        sub_model="LFM2.5-VL-1.6B",
        root_ctx_size=16384,
        sub_ctx_size=16384,
        sub_prompt_char_budget=16000,
        repl_output_char_cap=4000,
        max_turns=15,
        max_concurrent_subcalls=2,
        cell_timeout=60.0,
    ),
    "workstation": Profile(
        name="workstation",
        root_model="LFM2.5-VL-1.6B",
        sub_model="LFM2.5-VL-1.6B",
        root_ctx_size=32768,
        sub_ctx_size=32768,
        sub_prompt_char_budget=24000,
        repl_output_char_cap=8000,
        max_turns=20,
        max_concurrent_subcalls=4,
        max_subcalls=100,
        max_subcall_chars=12_000_000,
        cell_timeout=120.0,
    ),
}


@dataclass
class Config:
    """Active configuration: a profile plus optional overrides."""

    profile: Profile
    overrides: dict[str, Any] = field(default_factory=dict)

    # Injected prompt placeholders
    example_chunking_idiom: str = (
        'chunks = chunk(context, size=3000)\n'
        'results = map_query(chunks, "Summarize this text in 2 sentences: {text}")\n'
        'combined = "\\n---\\n".join(results)'
    )

    def __getattr__(self, name: str) -> Any:
        if name in self.overrides:
            return self.overrides[name]
        if hasattr(self.profile, name):
            return getattr(self.profile, name)
        raise AttributeError(name)

    def prompt_vars(self) -> dict[str, Any]:
        """Return a dict of values for .format() injection into prompts."""
        return {
            "repl_cap": self.repl_output_char_cap,
            "sub_budget": self.sub_prompt_char_budget,
            "max_turns": self.max_turns,
            "example_chunking_idiom": self.example_chunking_idiom,
            "root_ctx_size": self.root_ctx_size,
            "sub_ctx_size": self.sub_ctx_size,
        }


def load_config(
    profile_name: str = "laptop",
    config_path: str | None = None,
    **overrides: Any,
) -> Config:
    """Load a profile, optionally layered with a TOML file and keyword overrides.

    Resolution: profile defaults < TOML file < keyword overrides.
    """
    if profile_name not in PROFILES:
        raise ValueError(
            f"Unknown profile '{profile_name}'. Choices: {sorted(PROFILES)}"
        )
    profile = PROFILES[profile_name]

    file_overrides: dict[str, Any] = {}
    if config_path:
        raw = tomllib.loads(Path(config_path).read_text())
        file_overrides = {k: v for k, v in raw.items() if k in profile.__dict__}

    merged = {**file_overrides, **overrides}
    return Config(profile=profile, overrides=merged)
