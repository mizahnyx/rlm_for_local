"""Shared fixtures for rlm_kernel tests."""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_vault() -> Path:
    """Create a temporary vault root for testing.
    
    Uses ignore_cleanup_errors to handle Windows file locking on SQLite files.
    """
    td = tempfile.TemporaryDirectory(
        prefix="rlm_kernel_vault_", ignore_cleanup_errors=True,
    )
    vault = Path(td.name)
    # Create subdirs
    for sub in ["contract", "definitions", "helpers", "fewshots",
                 "memory/notes", "memory/topics", "memory/caches",
                 "quarantine", ".index"]:
        (vault / sub).mkdir(parents=True, exist_ok=True)
    yield vault
    # Force garbage collection to release any SQLite connections
    gc.collect()
    td.cleanup()
