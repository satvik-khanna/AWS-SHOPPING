"""Wiring. Swap an implementation here when a module ships a better one."""
from __future__ import annotations

from .checkout import DryRunCheckout
from .cost import RulesCostEngine
from .listings import default_source
from .memory import build_memory
from .pipeline import Deps
from .rank import default_ranker


def build_deps() -> Deps:
    memory = build_memory()
    return Deps(memory=memory, source=default_source(), cost=RulesCostEngine(),
                ranker=default_ranker(), checkout=DryRunCheckout(memory=memory))
