"""Shared test fixtures."""

from __future__ import annotations

import pytest

from rlm_local.config import Config, load_config


@pytest.fixture
def tiny_config() -> Config:
    return load_config("tiny")


@pytest.fixture
def laptop_config() -> Config:
    return load_config("laptop")
