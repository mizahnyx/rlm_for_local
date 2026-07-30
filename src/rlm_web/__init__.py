"""rlm_web — HTTPS web frontend for the RLM harness (D2).

FastAPI server-rendered app with htmx. Provides:
- Console: query form with Markdown ingestion
- Job view: live SSE progress from trajectory log
- Vault search: read-only hybrid search cards
- Model check: run suitability battery
- Docs: serve operator guide and manuals
"""

from __future__ import annotations

__version__ = "0.1.0"
