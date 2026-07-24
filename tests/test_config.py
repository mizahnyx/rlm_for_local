"""Tests for config module."""

from __future__ import annotations

import pytest

from rlm_local.config import PROFILES, Config, load_config


class TestProfiles:
    def test_all_profiles_exist(self):
        assert "tiny" in PROFILES
        assert "laptop" in PROFILES
        assert "workstation" in PROFILES

    def test_profile_is_frozen(self):
        profile = PROFILES["tiny"]
        with pytest.raises(Exception):
            profile.max_turns = 100  # type: ignore[misc]

    def test_tiny_values(self):
        p = PROFILES["tiny"]
        assert p.max_turns == 12
        assert p.sub_prompt_char_budget == 8000
        assert p.repl_output_char_cap == 2000

    def test_laptop_values(self):
        p = PROFILES["laptop"]
        assert p.max_turns == 15
        assert p.sub_prompt_char_budget == 16000
        assert p.repl_output_char_cap == 4000

    def test_workstation_values(self):
        p = PROFILES["workstation"]
        assert p.max_turns == 20
        assert p.sub_prompt_char_budget == 24000


class TestConfig:
    def test_load_config_default(self):
        config = load_config("laptop")
        assert config.max_turns == 15

    def test_load_config_override(self):
        config = load_config("tiny", max_turns=5)
        assert config.max_turns == 5
        assert config.sub_prompt_char_budget == 8000  # unchanged

    def test_load_config_unknown_profile(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            load_config("nonexistent")

    def test_prompt_vars(self):
        config = load_config("tiny", max_turns=5)
        pv = config.prompt_vars()
        assert pv["max_turns"] == 5
        assert pv["repl_cap"] == 2000
        assert "example_chunking_idiom" in pv

    def test_attribute_error(self):
        config = load_config("tiny")
        with pytest.raises(AttributeError):
            _ = config.nonexistent_attr
