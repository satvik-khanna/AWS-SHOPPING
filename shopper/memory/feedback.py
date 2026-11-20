"""Approve / reject feedback -> memory. Called by BuyeeCheckout after the human decides."""
from __future__ import annotations

from ..models import Listing
from . import CONDITIONS


def _listing(d: dict) -> Listing:
    return Listing(**{k: d[k] for k in Listing.__dataclass_fields__ if k in d})


def apply_feedback(memory, user_id: str, kind: str, listing: dict, reason: str = "",
                   reject_seller: bool = False) -> list[str]:
    """Returns what changed (for the step log)."""
    changed: list[str] = []
    l = _listing(listing)
    if kind == "approve":
        memory.remember_purchase(user_id, l)
        changed.append(f"remembered purchase {l.id}")
        return changed
    if kind != "reject":
        raise ValueError(kind)
    r = (reason or "").lower()
    if reject_seller or any(w in r for w in ("seller", "scam", "fake", "untrusted")):
        memory.reject_seller(user_id, l.seller)
        changed.append(f"rejected seller {l.seller}")
    if any(w in r for w in ("condition", "scratch", "worn", "damaged", "dirty")) and l.condition in CONDITIONS:
        # raise the bar one step above what was just rejected
        i = CONDITIONS.index(l.condition)
        new_min = CONDITIONS[min(i + 1, len(CONDITIONS) - 1)]
        cur = memory.recall(user_id).condition_min
        if hasattr(memory, "set_condition_min") and (cur is None or CONDITIONS.index(cur) < CONDITIONS.index(new_min)):
            memory.set_condition_min(user_id, new_min)
            changed.append(f"condition_min -> {new_min}")
    if hasattr(memory, "add_note"):
        memory.add_note(user_id, f"rejected {l.id} ({l.seller}): {reason or '-'}")
    return changed
