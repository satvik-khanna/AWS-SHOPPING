"""Wiring. Swap an implementation here when a module ships a better one."""
from __future__ import annotations

from .checkout import DryRunCheckout
from .cost import RulesCostEngine
from .listings import FixtureSource
from .memory import JsonMemory
from .pipeline import Deps
from .rank import PriceRanker


def build_deps() -> Deps:
    return Deps(memory=JsonMemory(), source=FixtureSource(), cost=RulesCostEngine(),
                ranker=PriceRanker(), checkout=DryRunCheckout())
