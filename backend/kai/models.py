"""Shared data models. JSON is camelCase; Python is snake_case.

Unknown values are represented as None and rendered as "unknown" in the UI.
They are never guessed."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class KaiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, use_enum_values=True,
                              validate_assignment=True)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")


# --------------------------------------------------------------------------
# Social discovery
# --------------------------------------------------------------------------


class AnnouncementType(str, Enum):
    NEW_RELEASE = "NEW_RELEASE"
    RESTOCK = "RESTOCK"
    PREORDER = "PREORDER"
    LOTTERY = "LOTTERY"
    HANDMADE = "HANDMADE"
    SOLD_OUT = "SOLD_OUT"
    GENERAL_PROMOTION = "GENERAL_PROMOTION"
    UNKNOWN = "UNKNOWN"


class Availability(str, Enum):
    IN_STOCK = "IN_STOCK"
    PREORDER = "PREORDER"
    LOTTERY = "LOTTERY"
    MADE_TO_ORDER = "MADE_TO_ORDER"
    SOLD_OUT = "SOLD_OUT"
    UNKNOWN = "UNKNOWN"


class Verification(str, Enum):
    ANNOUNCEMENT_ONLY = "ANNOUNCEMENT_ONLY"  # post seen; no store link traced
    STORE_LINK_UNRESOLVED = "STORE_LINK_UNRESOLVED"  # link present but not fetched/parsed
    STORE_FOUND_PRODUCT_UNRESOLVED = "STORE_FOUND_PRODUCT_UNRESOLVED"
    LISTING_RESOLVED = "LISTING_RESOLVED"  # product page fetched and parsed
    BLOCKED = "BLOCKED"  # robots/401/403/429 - not bypassed
    UNSUPPORTED_STORE = "UNSUPPORTED_STORE"  # manual-only


class ProviderName(str, Enum):
    X_OFFICIAL_API = "X_OFFICIAL_API"
    INDEXED_WEB_SEARCH = "INDEXED_WEB_SEARCH"
    MANUAL_IMPORT = "MANUAL_IMPORT"
    FIXTURE = "FIXTURE"


class ExternalLink(KaiModel):
    url: str
    expanded_url: str | None = None
    display_url: str | None = None
    kind: str = "OTHER"  # STOREFRONT | LINK_HUB | SOCIAL | MEDIA | OTHER
    store_type: str | None = None
    is_product_page: bool = False
    source: str = "text"  # api_entities | text | user_supplied | link_hub | search_snippet

    @property
    def target(self) -> str:
        return self.expanded_url or self.url


class Evidence(KaiModel):
    field: str
    snippet: str
    source: str


class StorefrontResolution(KaiModel):
    status: str = "NOT_ATTEMPTED"  # RESOLVED_PRODUCT | STORE_ONLY | BLOCKED | UNSUPPORTED | ERROR | NO_LINK | NOT_ATTEMPTED
    requested_url: str | None = None
    purchase_url: str | None = None  # the actual product page, distinct from the announcement URL
    store_url: str | None = None
    store_type: str | None = None
    store_name: str | None = None
    product_title: str | None = None
    price_jpy: int | None = None
    price_original: float | None = None
    currency: str | None = None
    availability: Availability = Availability.UNKNOWN
    availability_source: str | None = None
    restrictions: list[str] = Field(default_factory=list)
    ships_internationally: str = "unknown"  # yes | no | unknown
    image_urls: list[str] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    description_excerpt: str | None = None
    fetched_at: str | None = None
    http_status: int | None = None
    error: str | None = None
    blocked_reason: str | None = None
    injection_flags: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    hops: list[str] = Field(default_factory=list)
    is_fixture: bool = False
    manual_only: bool = False


class Provenance(KaiModel):
    provider: ProviderName
    is_fixture: bool = False
    query: str | None = None
    coverage_note: str | None = None
    partial_text: bool = False
    from_cache: bool = False
    extraction_method: str = "heuristic"  # heuristic | llm+heuristic
    translation_method: str = "none"  # llm | fixture_provided | glossary_gloss | none
    notes: list[str] = Field(default_factory=list)


class SocialProductLead(KaiModel):
    id: str
    source_post_url: str
    post_id: str | None = None
    author_handle: str | None = None
    author_display_name: str | None = None
    author_profile_url: str | None = None
    original_japanese_text: str
    english_translation: str | None = None
    product_name: str | None = None
    product_category: str | None = None
    announcement_type: AnnouncementType = AnnouncementType.UNKNOWN
    announced_price_jpy: int | None = Field(default=None, alias="announcedPriceJPY")
    release_date: str | None = None
    preorder_deadline: str | None = None
    lottery_deadline: str | None = None
    external_links: list[ExternalLink] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source_timestamp: str | None = None
    retrieval_timestamp: str
    availability_status: Availability = Availability.UNKNOWN
    announced_availability_claim: str | None = None  # what the post claims; not proof
    provenance: Provenance
    verification_status: Verification = Verification.ANNOUNCEMENT_ONLY
    # extras beyond the spec
    glossary: list[str] = Field(default_factory=list)  # keyword glosses; shown when no translation exists
    attributes: list[str] = Field(default_factory=list)
    storefront: StorefrontResolution | None = None
    duplicate_post_urls: list[str] = Field(default_factory=list)
    useful: bool = True
    usefulness_reason: str | None = None
    injection_flags: list[str] = Field(default_factory=list)
    shortlisted: bool = False


class ProviderStatus(KaiModel):
    provider: ProviderName
    state: str  # ok | empty | error | unconfigured | budget_exhausted | rate_limited | unauthorized | forbidden | skipped
    selected: bool = False
    retrieved_at: str | None = None
    coverage: str | None = None
    error: str | None = None
    http_status: int | None = None
    queries_run: int = 0
    posts_returned: int = 0
    cost_usd_estimate: float = 0.0
    is_fixture: bool = False
    from_cache: bool = False


class XQuery(KaiModel):
    text: str
    category: str
    intent: str
    source: str  # category | preference | history | watched_creator | watched_term
    rationale: str
    valid: bool = True
    errors: list[str] = Field(default_factory=list)


class DiscoveryStats(KaiModel):
    posts_retrieved: int = 0
    useful_announcements: int = 0
    not_useful: int = 0
    duplicates_filtered: int = 0
    storefront_links_resolved: int = 0
    verified_purchasable: int = 0
    blocked_or_unsupported: int = 0
    translated: int = 0
    extraction_failures: int = 0


class DiscoveryResult(KaiModel):
    category: str
    queries: list[XQuery] = Field(default_factory=list)
    provider_statuses: list[ProviderStatus] = Field(default_factory=list)
    selected_provider: ProviderName | None = None
    fresh_discovery: bool = False  # true only when a live provider returned posts
    leads: list[SocialProductLead] = Field(default_factory=list)
    stats: DiscoveryStats = Field(default_factory=DiscoveryStats)
    notes: list[str] = Field(default_factory=list)
    retrieved_at: str = Field(default_factory=now_iso)


class WatchedCreator(KaiModel):
    id: str
    handle: str | None = None
    categories: list[str] = Field(default_factory=list)
    search_terms: list[str] = Field(default_factory=list)
    note: str | None = None
    created_at: str = Field(default_factory=now_iso)


# --------------------------------------------------------------------------
# Commerce
# --------------------------------------------------------------------------


class Candidate(KaiModel):
    id: str
    source: str  # SOCIAL_DROP | MARKETPLACE | URL_IMPORT
    source_label: str
    title: str
    description: str | None = None
    price_jpy: int | None = None
    purchase_url: str | None = None
    announcement_url: str | None = None
    store_type: str | None = None
    store_name: str | None = None
    availability: Availability = Availability.UNKNOWN
    attributes: list[str] = Field(default_factory=list)
    image_url: str | None = None
    is_fixture: bool = False
    lead_id: str | None = None
    verification: str = "UNVERIFIED"
    ships_internationally: str = "unknown"
    restrictions: list[str] = Field(default_factory=list)
    retrieved_at: str | None = None
    injection_flags: list[str] = Field(default_factory=list)


class Citation(KaiModel):
    id: str
    title: str
    url: str | None = None
    publisher: str | None = None
    retrieved_at: str | None = None
    excerpt: str | None = None
    via: str = "bundled"  # senso | bundled
    score: float | None = None
    senso_content_id: str | None = None


class CostLine(KaiModel):
    key: str
    label: str
    amount_usd: float
    amount_jpy: int | None = None
    basis: str
    confidence: str  # sourced | assumption | observed
    citation_ids: list[str] = Field(default_factory=list)


class LandedCost(KaiModel):
    destination: str
    lines: list[CostLine] = Field(default_factory=list)
    total_usd: float | None = None
    budget_usd: float | None = None
    within_budget: str = "unknown"  # yes | no | unknown
    fx_rate_usd_per_jpy: float | None = None
    fx_source: str | None = None
    fx_as_of: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    citation_backend: str = "bundled"  # senso | bundled
    warnings: list[str] = Field(default_factory=list)
    conservative: bool = True


class ScoreBreakdown(KaiModel):
    semantic: float = 0.0
    preference: float = 0.0
    budget: float = 0.0
    availability: float = 0.0
    trust: float = 0.0
    total: float = 0.0
    ranking_backend: str = "fallback"
    reasons: list[str] = Field(default_factory=list)


class RankedCandidate(KaiModel):
    candidate: Candidate
    score: ScoreBreakdown
    cost: LandedCost | None = None
    purchase_path: str = "UNKNOWN"  # PROXY_REQUEST_DRAFT | MANUAL_ONLY | WATCH_ONLY
    purchase_path_reason: str | None = None
    rank: int = 0


class DraftStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED_FOR_MANUAL_HANDOFF = "APPROVED_FOR_MANUAL_HANDOFF"
    REJECTED = "REJECTED"
    # Deliberately no SUBMITTED / ORDERED / PAID state: KAI cannot place orders.


class EvidenceStep(KaiModel):
    stage: str  # X_POST | CREATOR | STORE | PRODUCT | LANDED_COST | HUMAN_REVIEW
    label: str
    detail: str | None = None
    url: str | None = None
    status: str = "ok"  # ok | warn | missing | fixture


class PurchaseDraft(KaiModel):
    id: str
    candidate_id: str
    run_id: str | None = None
    status: DraftStatus = DraftStatus.PENDING_REVIEW
    path: str  # PROXY_REQUEST_DRAFT | MANUAL_ONLY
    binding: bool = False
    title: str
    purchase_url: str | None = None
    announcement_url: str | None = None
    request_text: str  # what the human could paste into a proxy request form
    manual_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_chain: list[EvidenceStep] = Field(default_factory=list)
    cost: LandedCost | None = None
    is_fixture: bool = False
    created_at: str = Field(default_factory=now_iso)
    reviewed_at: str | None = None
    reviewer_note: str | None = None


class TimelineEvent(KaiModel):
    id: str
    ts: str
    run_id: str | None = None
    stage: str
    message: str
    status: str = "info"  # info | ok | warn | error
    data: dict[str, Any] | None = None


class ParsedRequest(KaiModel):
    raw: str
    category: str
    category_label: str
    ja_terms: list[str]
    budget_usd: float | None = None
    destination: str = "California, US"
    wants_social: bool = True
    recency_days: int = 7
    stated_preferences: list[str] = Field(default_factory=list)
    parse_method: str = "heuristic"


class MemoryRecall(KaiModel):
    backend: str  # cognee | fallback
    backend_reason: str | None = None
    preferred: list[str] = Field(default_factory=list)
    avoided: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    cognee_results: list[str] = Field(default_factory=list)
    past_searches: list[str] = Field(default_factory=list)


class AgentRun(KaiModel):
    id: str
    created_at: str = Field(default_factory=now_iso)
    request: ParsedRequest
    memory: MemoryRecall
    discovery: DiscoveryResult | None = None
    marketplace_count: int = 0
    marketplace_sources: list[dict[str, Any]] = Field(default_factory=list)
    ranked: list[RankedCandidate] = Field(default_factory=list)
    draft: PurchaseDraft | None = None
    manual_next_steps: list[str] = Field(default_factory=list)
    summary: str = ""
    stopped_before_binding_action: bool = True
    timeline: list[TimelineEvent] = Field(default_factory=list)
    integrations: list[dict[str, Any]] = Field(default_factory=list)
