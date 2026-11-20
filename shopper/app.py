"""Wiring. Swap an implementation here when a module ships a better one."""
from __future__ import annotations

from .checkout import DryRunCheckout
from .cost import RulesCostEngine
from .listings import default_source
from .memory import JsonMemory
from .pipeline import Deps
from .rank import default_ranker


def build_deps() -> Deps:
    return Deps(memory=JsonMemory(), source=default_source(), cost=RulesCostEngine(),
                ranker=default_ranker(), checkout=DryRunCheckout())
