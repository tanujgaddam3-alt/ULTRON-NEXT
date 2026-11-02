"""ULTRON voice engines package."""

from __future__ import annotations

from .base import BaseEngine, EngineResult
from .edge_engine import EdgeEngine
from .xtts_engine import XtTsEngine

__all__ = ["BaseEngine", "EngineResult", "EdgeEngine", "XtTsEngine"]


def create_engine(engine_name: str, config: dict, profile: dict) -> BaseEngine:
    """Factory: returns a configured engine instance."""
    name = (engine_name or "").lower().strip()
    if name == "xtts":
        return XtTsEngine(config, profile)
    if name == "edge":
        return EdgeEngine(config, profile)
    raise ValueError(f"Unknown voice engine: {engine_name!r}")