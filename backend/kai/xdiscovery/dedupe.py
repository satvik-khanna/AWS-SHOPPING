"""Collapse posts that announce the same product."""

from __future__ import annotations

from urllib.parse import urlsplit

from ..models import SocialProductLead
from ..textutil import nfkc, ngram_jaccard, strip_urls


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    p = urlsplit(url)
    if not p.hostname:
        return None
    return f"{p.hostname.lower()}{p.path.rstrip('/')}"


def _norm_name(name: str | None) -> str | None:
    if not name:
        return None
    return "".join(nfkc(name).lower().split())


def _quality(lead: SocialProductLead) -> tuple[int, float]:
    resolved = 1 if lead.storefront and lead.storefront.status == "RESOLVED_PRODUCT" else 0
    return (resolved, lead.confidence)


def _product_links(lead: SocialProductLead) -> set[str]:
    out = set()
    if lead.storefront and lead.storefront.purchase_url:
        c = canonical_url(lead.storefront.purchase_url)
        if c:
            out.add(c)
    for l in lead.external_links:
        if l.kind == "STOREFRONT" and l.is_product_page:
            c = canonical_url(l.target)
            if c:
                out.add(c)
    return out


def same_product(a: SocialProductLead, b: SocialProductLead) -> str | None:
    """Return the reason two leads are duplicates, or None."""
    if a.post_id and a.post_id == b.post_id:
        return "same post"
    if _product_links(a) & _product_links(b):
        return "same product page"
    na, nb = _norm_name(a.product_name), _norm_name(b.product_name)
    if na and na == nb and a.author_handle and a.author_handle == b.author_handle:
        return "same creator and product name"
    if ngram_jaccard(strip_urls(a.original_japanese_text), strip_urls(b.original_japanese_text)) >= 0.8:
        return "near-identical text"
    return None


def dedupe_leads(leads: list[SocialProductLead]) -> tuple[list[SocialProductLead], int]:
    kept: list[SocialProductLead] = []
    removed = 0
    # Best lead first; among equal quality the earliest post is kept as the original.
    ordered = sorted(leads, key=lambda l: (-_quality(l)[0], -_quality(l)[1], l.source_timestamp or ""))
    for lead in ordered:
        match = next((k for k in kept if same_product(k, lead)), None)
        if match is None:
            kept.append(lead)
            continue
        removed += 1
        for url in [lead.source_post_url, *lead.duplicate_post_urls]:
            if url != match.source_post_url and url not in match.duplicate_post_urls:
                match.duplicate_post_urls.append(url)
    return kept, removed
