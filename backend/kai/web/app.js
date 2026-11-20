/* KAI web UI. All remote text is inserted as text nodes, never as HTML. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = { status: null, run: null, categories: [] };

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : (detail && detail[0] && detail[0].msg) || `Request failed (${res.status})`);
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

const isFixtureUrl = (u) => { try { return new URL(u).hostname.endsWith(".invalid"); } catch (_) { return false; } };
function link(url, text) {
  if (!url) return h("span", { class: "unknown" }, "unknown");
  if (isFixtureUrl(url)) return h("span", { class: "url", title: "Sample URL on a reserved .invalid domain; it does not exist" }, (text || url) + " (sample URL)");
  if (!/^https?:\/\//i.test(url)) return h("span", { class: "url" }, text || url);
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer nofollow", class: text ? null : "url" }, text || url);
}
const unknown = () => h("span", { class: "unknown" }, "unknown");
const val = (v, fmt) => (v === null || v === undefined || v === "" ? unknown() : (fmt ? fmt(v) : v));
const yen = (v) => "¥" + Number(v).toLocaleString("en-US");
const usd = (v) => "$" + Number(v).toFixed(2);
const words = (s) => String(s || "").replace(/_/g, " ").toLowerCase();
function when(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return iso.length <= 10 ? d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
const badge = (text, kind) => h("span", { class: "badge " + (kind || "") }, text);

const TYPE_BADGE = { NEW_RELEASE: ["New release", "ok"], RESTOCK: ["Restock", "ok"], PREORDER: ["Preorder", "info"], LOTTERY: ["Lottery", "warn"],
  HANDMADE: ["Handmade", "info"], SOLD_OUT: ["Sold out", "err"], GENERAL_PROMOTION: ["Promotion", ""], UNKNOWN: ["Type unknown", ""] };
const AVAIL_BADGE = { IN_STOCK: ["In stock", "ok"], PREORDER: ["Preorder open", "info"], LOTTERY: ["Lottery entry", "warn"],
  MADE_TO_ORDER: ["Made to order", "info"], SOLD_OUT: ["Sold out", "err"], UNKNOWN: ["Availability unknown", ""] };
const VERIFY_BADGE = { LISTING_RESOLVED: ["Store listing read", "ok"], STORE_FOUND_PRODUCT_UNRESOLVED: ["Store found, product not identified", "warn"],
  STORE_LINK_UNRESOLVED: ["Store link not reachable", "warn"], BLOCKED: ["Store blocked access", "err"],
  UNSUPPORTED_STORE: ["Unsupported store · manual only", "warn"], ANNOUNCEMENT_ONLY: ["Announcement only", ""] };
const PATH_BADGE = { PROXY_REQUEST_DRAFT: ["Draft available", "ok"], MANUAL_ONLY: ["Manual only", "warn"], WATCH_ONLY: ["Watch only", ""] };
const PROVIDER_LABEL = { X_OFFICIAL_API: "X official API", INDEXED_WEB_SEARCH: "Indexed web search", MANUAL_IMPORT: "Manual import", FIXTURE: "Fixtures (sample data)" };
const MODE_LABEL = { live: "live", fallback: "fallback", error: "error · fallback in use", configured_untested: "configured, not yet called" };

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  ({ hunts: loadHunts, social: loadSocial, shortlist: loadShortlist, review: loadDrafts, listings: loadListings, memory: loadMemory, activity: loadActivity }[name] || (() => {}))();
}

/* ---------------- status ---------------- */
async function loadStatus() {
  const s = await api("/api/status");
  state.status = s;
  state.categories = s.categories;
  const box = $("#integrations");
  box.replaceChildren(...s.integrations.map((i) =>
    h("span", { class: "chip " + i.mode, title: i.detail }, h("i"), `${i.label}: ${MODE_LABEL[i.mode] || i.mode}`)));
  const sel = $("#social-category");
  if (!sel.options.length) {
    sel.append(h("option", { value: "" }, "Watched + recent"));
    s.categories.forEach((c) => sel.append(h("option", { value: c.key }, `${c.label} · ${c.ja[0]}`)));
    $("#watched-cats").replaceChildren(...s.categories.map((c) =>
      h("label", {}, h("input", { type: "checkbox", value: c.key }), c.label)));
  }
  return s;
}
async function refreshCounts() {
  try {
    const [sl, dr] = await Promise.all([api("/api/shortlist"), api("/api/drafts")]);
    $("#count-shortlist").textContent = sl.length || "";
    $("#count-review").textContent = dr.filter((d) => d.status === "PENDING_REVIEW").length || "";
  } catch (_) { /* counts are cosmetic */ }
}

/* ---------------- shared renderers ---------------- */
function renderChain(chain) {
  return h("ol", { class: "chain" }, chain.map((s) =>
    h("li", { class: s.status }, h("div", { class: "stage" }, words(s.stage)), h("div", {}, h("strong", {}, s.label)),
      s.detail ? h("div", { class: "muted small" }, s.detail) : null, s.url ? h("div", {}, link(s.url)) : null)));
}

function renderCost(cost) {
  if (!cost) return h("p", { class: "muted" }, "No cost estimate.");
  if (cost.totalUsd === null || cost.totalUsd === undefined) {
    return h("div", {}, h("p", { class: "unknown" }, "Landed cost unknown."), ...cost.warnings.map((w) => h("p", { class: "muted small" }, w)));
  }
  const cites = Object.fromEntries(cost.citations.map((c, i) => [c.id, i + 1]));
  const rows = cost.lines.map((l) => h("tr", {},
    h("td", {}, l.label, h("div", { class: "muted small" }, l.basis)),
    h("td", {}, badge(l.confidence, { sourced: "ok", observed: "info", assumption: "warn" }[l.confidence]),
      " ", l.citationIds.map((id) => h("sup", { title: id }, `[${cites[id] || "?"}]`))),
    h("td", { class: "num" }, l.amountJpy !== null && l.amountJpy !== undefined ? yen(l.amountJpy) : ""),
    h("td", { class: "num" }, usd(l.amountUsd))));
  const budget = cost.budgetUsd !== null && cost.budgetUsd !== undefined
    ? badge(cost.withinBudget === "yes" ? `Within $${cost.budgetUsd} budget` : `Over $${cost.budgetUsd} budget`, cost.withinBudget === "yes" ? "ok" : "err") : null;
  return h("div", {},
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Cost line"), h("th", {}, "Basis"), h("th", { class: "num" }, "JPY"), h("th", { class: "num" }, "USD"))),
      h("tbody", {}, rows, h("tr", { class: "total" }, h("td", {}, "Estimated delivered total ", budget), h("td"), h("td"), h("td", { class: "num" }, usd(cost.totalUsd))))),
    h("p", { class: "muted small" }, `Exchange rate: 1 JPY = ${cost.fxRateUsdPerJpy} USD · ${cost.fxSource}${cost.fxAsOf ? " · as of " + cost.fxAsOf : ""}`),
    h("h4", {}, `Sources (${cost.citationBackend === "senso" ? "retrieved through Senso" : cost.citationBackend === "bundled" ? "bundled copies; Senso not in use" : "Senso with bundled fallback"})`),
    h("ol", { class: "small" }, cost.citations.map((c) => h("li", {}, link(c.url, c.title), " · ", c.publisher || "", c.retrievedAt ? ` · retrieved ${c.retrievedAt}` : "",
      c.excerpt ? h("div", { class: "muted" }, "“" + c.excerpt + "”") : null))),
    cost.warnings.map((w) => h("p", { class: "muted small" }, "• " + w)));
}

function renderDraft(d, onChange) {
  const pending = d.status === "PENDING_REVIEW";
  const statusBadge = pending ? badge("Waiting for your review", "warn")
    : d.status === "APPROVED_FOR_MANUAL_HANDOFF" ? badge("Approved · yours to act on", "ok") : badge("Rejected", "err");
  const note = h("input", { placeholder: "Optional note", maxlength: 300 });
  const act = async (decision) => {
    try {
      await api(`/api/drafts/${d.id}/review`, { method: "POST", body: { decision, note: note.value } });
      toast(decision === "approve" ? "Approved for manual handoff. KAI placed no order." : "Draft rejected.");
      refreshCounts();
      onChange && onChange();
    } catch (e) { toast(e.message); }
  };
  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("div", {}, h("div", { class: "card-title" }, d.title),
      h("div", { class: "badges" }, statusBadge, badge(d.path === "PROXY_REQUEST_DRAFT" ? "Nonbinding proxy request draft" : "Manual purchase notes", "info"),
        d.isFixture ? badge("Sample data", "fx") : null, badge("Not submitted", "")))),
    d.warnings.length ? h("div", { class: "banner warn" }, d.warnings.map((w) => h("div", {}, "• " + w))) : null,
    h("h4", {}, "Evidence chain"), renderChain(d.evidenceChain),
    h("h4", {}, "Estimated landed cost"), renderCost(d.cost),
    h("h4", {}, d.path === "PROXY_REQUEST_DRAFT" ? "Request text (for you to submit, if you choose)" : "Notes"),
    h("pre", { class: "request" }, d.requestText),
    h("h4", {}, "What happens next is up to you"), h("ol", { class: "reasons" }, d.manualSteps.map((s) => h("li", {}, s))),
    pending ? h("div", { class: "actions" }, note, h("button", { class: "approve", onclick: () => act("approve") }, "Approve for manual handoff"),
      h("button", { onclick: () => act("reject") }, "Reject"))
      : h("p", { class: "muted small" }, `Reviewed ${when(d.reviewedAt) || ""}${d.reviewerNote ? " · " + d.reviewerNote : ""}`),
    h("p", { class: "muted small" }, "Approving records your decision only. KAI has no ability to order, bid or pay."));
}

function renderProviderStatuses(statuses) {
  return h("div", {}, statuses.map((p) => {
    const kind = p.state === "ok" ? "ok" : ["unconfigured", "skipped", "empty"].includes(p.state) ? "" : "err";
    return h("div", { class: "provider" },
      h("div", {}, h("strong", {}, PROVIDER_LABEL[p.provider] || p.provider), h("div", { class: "badges" }, badge(words(p.state), kind),
        p.selected ? badge("selected", "info") : null, p.isFixture ? badge("sample data", "fx") : null, p.fromCache ? badge("from cache", "") : null)),
      h("div", {},
        h("div", {}, p.postsReturned ? `${p.postsReturned} post(s)${p.queriesRun ? ` from ${p.queriesRun} query(ies)` : ""}` : "No posts returned",
          p.retrievedAt ? ` · retrieved ${when(p.retrievedAt)}` : "", p.costUsdEstimate ? ` · est. ${usd(p.costUsdEstimate)}` : ""),
        p.error ? h("div", { class: p.state === "unconfigured" || p.state === "skipped" ? "muted small" : "error small" }, p.error) : null,
        p.coverage ? h("div", { class: "muted small" }, "Coverage: " + p.coverage) : null));
  }));
}

function renderQueries(queries) {
  return h("ul", { class: "reasons" }, queries.map((q) => h("li", {}, h("code", { class: "q" }, q.text), " ",
    h("span", { class: "muted small" }, `${q.rationale}`), q.valid ? null : h("span", { class: "error small" }, " invalid: " + q.errors.join("; ")))));
}

function renderTimeline(events) {
  return h("ol", { class: "timeline" }, events.map((e) => h("li", { class: e.status },
    h("time", {}, new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })),
    h("span", { class: "stage" }, h("i", { class: "dot " + e.status }), e.stage), h("span", { class: "msg" }, e.message))));
}

/* ---------------- lead card ---------------- */
function renderLead(lead, opts = {}) {
  const sf = lead.storefront || {};
  const prov = lead.provenance;
  const t = TYPE_BADGE[lead.announcementType] || TYPE_BADGE.UNKNOWN;
  const a = AVAIL_BADGE[lead.availabilityStatus] || AVAIL_BADGE.UNKNOWN;
  const v = VERIFY_BADGE[lead.verificationStatus] || [words(lead.verificationStatus), ""];
  const img = (lead.mediaUrls || []).find((u) => u.startsWith("/static/") || (prov.provider === "X_OFFICIAL_API" && /^https:\/\//.test(u)));
  const translation = lead.englishTranslation
    ? h("div", { class: "en" }, lead.englishTranslation, h("div", { class: "muted small" },
        { llm: "Machine translation by the configured LLM", fixture_provided: "Translation bundled with the sample post" }[prov.translationMethod] || ""))
    : h("div", { class: "en" }, h("span", { class: "unknown" }, "No translation: no LLM is configured."),
        lead.glossary.length ? h("div", { class: "muted small" }, "Keyword glosses: " + lead.glossary.join(" · ")) : null);
  const deadline = lead.lotteryDeadline ? ["Lottery deadline", lead.lotteryDeadline] : lead.preorderDeadline ? ["Preorder deadline", lead.preorderDeadline] : ["Deadline", null];
  const chain = [
    { status: prov.isFixture ? "fixture" : "ok", stage: "X post", label: prov.isFixture ? "Sample post (fixture)" : "Original post", url: lead.sourcePostUrl,
      detail: `${lead.sourceTimestamp ? "posted " + when(lead.sourceTimestamp) : "post time unknown"} · retrieved ${when(lead.retrievalTimestamp)} via ${PROVIDER_LABEL[prov.provider] || prov.provider}` },
    { status: lead.authorHandle ? (prov.isFixture ? "fixture" : "ok") : "missing", stage: "Creator", label: lead.authorHandle ? "@" + lead.authorHandle : "unknown",
      detail: lead.authorDisplayName, url: lead.authorProfileUrl },
    sf.status === "RESOLVED_PRODUCT"
      ? { status: sf.isFixture ? "fixture" : "ok", stage: "Store", label: sf.storeName || sf.storeType || "store", detail: `Type: ${sf.storeType || "unknown"} · reached in ${sf.hops.length} step(s)`, url: sf.storeUrl }
      : { status: sf.status === "STORE_ONLY" ? "warn" : "missing", stage: "Store", label: sf.status === "STORE_ONLY" ? (sf.storeName || "Store found") : "Not resolved",
          detail: sf.error || (sf.status === "NO_LINK" || !sf.status ? "The post has no store link." : sf.blockedReason), url: sf.storeUrl },
    sf.status === "RESOLVED_PRODUCT"
      ? { status: lead.injectionFlags.length ? "warn" : (sf.isFixture ? "fixture" : "ok"), stage: "Product", label: sf.productTitle || "Listing",
          detail: `${sf.priceJpy !== null && sf.priceJpy !== undefined ? yen(sf.priceJpy) : "price unknown"} · ${words(sf.availability)}${sf.availabilitySource ? " (" + sf.availabilitySource + ")" : ""} · read ${when(sf.fetchedAt)}`, url: sf.purchaseUrl }
      : { status: "missing", stage: "Product", label: "Product page not resolved", detail: "Manual check needed. The post itself cannot be purchased from." },
  ];
  const shortBtn = h("button", { class: "small", disabled: lead.shortlisted, onclick: async (ev) => {
    try { await api("/api/shortlist", { method: "POST", body: { leadId: lead.id } }); ev.target.disabled = true; ev.target.textContent = "Shortlisted"; toast("Added to shortlist"); refreshCounts(); }
    catch (e) { toast(e.message); } } }, lead.shortlisted ? "Shortlisted" : "Add to shortlist");
  return h("article", { class: "card" },
    h("div", { class: "card-head" },
      h("div", {},
        h("div", { class: "card-title" }, lead.productName || (sf.status === "RESOLVED_PRODUCT" && sf.productTitle) || h("span", { class: "unknown" }, "Product name unknown")),
        h("div", { class: "muted small" }, lead.authorHandle ? `@${lead.authorHandle}` : "creator unknown", lead.authorDisplayName ? ` · ${lead.authorDisplayName}` : "",
          lead.sourceTimestamp ? ` · announced ${when(lead.sourceTimestamp)}` : " · announcement date unknown"),
        h("div", { class: "badges" }, badge(t[0], t[1]), badge(a[0], a[1]), badge(v[0], v[1]),
          prov.isFixture ? badge("Sample data · not a real post", "fx") : badge(PROVIDER_LABEL[prov.provider] || prov.provider, "info"),
          prov.partialText ? badge("Snippet only", "warn") : null, prov.fromCache ? badge("Cached", "") : null,
          lead.injectionFlags.length ? badge("Instruction-like text ignored", "warn") : null)),
      img ? h("img", { class: "thumb", src: img, alt: prov.isFixture ? "Sample image" : "Post image", loading: "lazy", referrerpolicy: "no-referrer" }) : null),
    h("div", { class: "jp", lang: "ja" }, lead.originalJapaneseText), translation,
    h("dl", { class: "facts" },
      h("div", {}, h("dt", {}, "Announced price"), h("dd", {}, val(lead.announcedPriceJPY, yen))),
      h("div", {}, h("dt", {}, "Store price"), h("dd", {}, val(sf.priceJpy, yen))),
      h("div", {}, h("dt", {}, "Release / start"), h("dd", {}, val(lead.releaseDate, when))),
      h("div", {}, h("dt", {}, deadline[0]), h("dd", {}, val(deadline[1], when))),
      h("div", {}, h("dt", {}, "What the post claims"), h("dd", {}, val(lead.announcedAvailabilityClaim))),
      h("div", {}, h("dt", {}, "Confidence"), h("dd", {}, Math.round(lead.confidence * 100) + "%"))),
    sf.restrictions && sf.restrictions.length ? h("p", { class: "small" }, h("strong", {}, "Restrictions: "), sf.restrictions.join(" · ")) : null,
    sf.status === "RESOLVED_PRODUCT" ? h("p", { class: "small muted" }, `Ships internationally: ${sf.shipsInternationally}`) : null,
    lead.duplicatePostUrls.length ? h("p", { class: "small muted" }, `${lead.duplicatePostUrls.length} other post(s) about the same product were merged.`) : null,
    prov.notes.length ? h("p", { class: "small muted" }, prov.notes.join(" ")) : null,
    h("details", {}, h("summary", {}, "Evidence: post → creator → store → product"), renderChain(chain),
      prov.coverageNote ? h("p", { class: "muted small" }, "Source coverage: " + prov.coverageNote) : null),
    opts.noActions ? null : h("div", { class: "actions" }, shortBtn));
}

/* ---------------- agent tab ---------------- */
document.querySelectorAll("[data-example]").forEach((b) => b.addEventListener("click", () => { $("#run-input").value = b.dataset.example; }));
$("#run-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("#run-input").value.trim();
  if (!msg) return;
  const btn = $("#run-btn");
  btn.disabled = true;
  $("#run-output").replaceChildren(h("div", { class: "panel" }, h("span", { class: "spinner" }),
    "Searching announcements, tracing stores, comparing listings and estimating delivered cost…"));
  try {
    const run = await api("/api/agent/run", { method: "POST", body: { message: msg } });
    state.run = run;
    renderRun(run);
    loadStatus(); refreshCounts();
  } catch (e) {
    $("#run-output").replaceChildren(h("div", { class: "banner err" }, "The run failed: " + e.message));
  } finally { btn.disabled = false; }
});

function renderRanked(rc, run) {
  const c = rc.candidate, s = rc.score, p = PATH_BADGE[rc.purchasePath] || [rc.purchasePath, ""];
  const a = AVAIL_BADGE[c.availability] || AVAIL_BADGE.UNKNOWN;
  const isDraft = run.draft && run.draft.candidateId === c.id;
  const bar = (label, v) => [h("span", { class: "muted" }, label), h("span", { class: "bar" }, h("i", { style: `width:${Math.round(v * 100)}%` })), h("span", { class: "num" }, v.toFixed(2))];
  const actions = h("div", { class: "actions" },
    h("button", { class: "small", onclick: async () => { try { await api("/api/shortlist", { method: "POST", body: { runId: run.id, candidateId: c.id } }); toast("Added to shortlist"); refreshCounts(); } catch (e) { toast(e.message); } } }, "Add to shortlist"),
    rc.purchasePath !== "WATCH_ONLY" && !isDraft ? h("button", { class: "small", onclick: async () => {
      try { await api("/api/drafts", { method: "POST", body: { runId: run.id, candidateId: c.id } }); toast("Draft prepared. Review it in the Review tab."); refreshCounts(); } catch (e) { toast(e.message); } } },
      rc.purchasePath === "PROXY_REQUEST_DRAFT" ? "Prepare draft" : "Prepare manual notes") : null);
  return h("article", { class: "card" + (isDraft ? " top-pick" : "") },
    h("div", { class: "card-head" },
      h("div", {}, h("div", { class: "card-title" }, h("span", { class: "rank" }, "#" + rc.rank), c.title),
        h("div", { class: "muted small" }, c.sourceLabel, c.storeName ? ` · ${c.storeName}` : ""),
        h("div", { class: "badges" }, badge(c.source === "SOCIAL_DROP" ? "X announcement" : c.source === "URL_IMPORT" ? "Imported listing" : "Marketplace", "info"),
          badge(a[0], a[1]), badge(p[0], p[1]), c.isFixture ? badge("Sample data", "fx") : null, isDraft ? badge("Draft prepared", "ok") : null)),
      h("div", { style: "text-align:right" }, h("div", { class: "card-title" }, rc.cost && rc.cost.totalUsd !== null ? usd(rc.cost.totalUsd) : h("span", { class: "unknown" }, "cost unknown")),
        h("div", { class: "muted small" }, "est. delivered"), h("div", { class: "small" }, val(c.priceJpy, yen)))),
    h("ul", { class: "reasons" }, s.reasons.map((r) => h("li", {}, r)), h("li", { class: "muted" }, rc.purchasePathReason)),
    h("div", { class: "small" }, "Announcement: ", c.announcementUrl ? link(c.announcementUrl) : h("span", { class: "muted" }, "n/a (not from X)")),
    h("div", { class: "small" }, "Purchase page: ", c.purchaseUrl ? link(c.purchaseUrl) : h("span", { class: "unknown" }, "not resolved")),
    h("details", {}, h("summary", {}, `Score ${s.total.toFixed(2)} · how it was ranked`),
      h("div", { class: "bars" }, bar("Relevance", s.semantic), bar("Preferences", s.preference), bar("Budget", s.budget), bar("Availability", s.availability), bar("Trust", s.trust)),
      h("p", { class: "muted small" }, "Relevance backend: " + s.rankingBackend),
      rc.cost ? h("div", {}, h("h4", {}, "Cost breakdown"), renderCost(rc.cost)) : null),
    actions);
}

function renderRun(run, out = $("#run-output")) {
  if (run.error) {
    out.replaceChildren(h("div", { class: "banner warn" }, run.message), h("div", { class: "panel" }, h("h3", {}, "Activity"), renderTimeline(run.timeline || [])));
    return;
  }
  const d = run.discovery;
  const nodes = [];
  const anyFixture = run.ranked.some((r) => r.candidate.isFixture);
  if (d && !d.freshDiscovery) nodes.push(h("div", { class: "banner fx" }, h("strong", {}, "Not fresh X discovery. "),
    anyFixture ? `${run.ranked.filter((r) => r.candidate.isFixture).length} comparison entries use sample data. Any other entries are from imports. Samples are labeled and do not represent real products.`
      : "Results below come from posts or listings you imported."));
  else if (anyFixture) nodes.push(h("div", { class: "banner fx" }, "Some comparison listings are bundled sample data, labeled as such."));
  nodes.push(h("div", { class: "banner ok" }, h("strong", {}, "Stopped before purchase. "), run.summary));
  nodes.push(h("div", { class: "panel row between" },
    h("div", {}, h("strong", {}, "Keep looking for this"), h("p", { class: "muted small" }, "Save the brief and check later for new matches, restocks, and price changes.")),
    h("button", { onclick: async () => {
      try { await api("/api/hunts", { method: "POST", body: { runId: run.id } }); toast("Hunt saved with this comparison as its baseline."); showTab("hunts"); }
      catch (e) { toast(e.message); }
    } }, "Save hunt")));

  const mem = run.memory;
  const left = h("div", {},
    h("div", { class: "panel" }, h("h3", {}, "What I understood"),
      h("dl", { class: "facts" },
        h("div", {}, h("dt", {}, "Looking for"), h("dd", {}, run.request.categoryLabel)),
        h("div", {}, h("dt", {}, "Budget, delivered"), h("dd", {}, val(run.request.budgetUsd, (b) => "$" + b))),
        h("div", {}, h("dt", {}, "Ship to"), h("dd", {}, run.request.destination)),
        h("div", {}, h("dt", {}, "Japanese terms"), h("dd", { lang: "ja" }, run.request.jaTerms.join("、")))),
      h("p", { class: "small" }, h("strong", {}, `Memory (${mem.backend === "cognee" ? "Cognee" : "local fallback"}): `),
        mem.preferred.length ? "prefers " + mem.preferred.join(", ") : "no stored preferences", mem.avoided.length ? "; avoids " + mem.avoided.join(", ") : "", "."),
      (run.request.statedPreferences || []).length || (run.request.statedAvoidances || []).length ? h("p", { class: "small" },
        h("strong", {}, "This brief: "), (run.request.statedPreferences || []).length ? "wants " + run.request.statedPreferences.join(", ") : "",
        (run.request.statedAvoidances || []).length ? "; avoids " + run.request.statedAvoidances.join(", ") : "", ". Current brief overrides stored taste.") : null,
      mem.backend !== "cognee" ? h("p", { class: "muted small" }, mem.backendReason) : null),
    h("div", { class: "panel" }, h("h3", {}, `Ranked comparison (${run.ranked.length})`),
      h("p", { class: "muted small" }, `${d ? d.leads.length : 0} from X announcements, ${run.marketplaceCount} from marketplace sources.`),
      run.marketplaceSources.map((s) => h("div", { class: "small muted" }, `${words(s.provider)}: ${s.state}${s.count ? " · " + s.count + " listing(s)" : ""}${s.error ? " · " + s.error : ""}${s.note ? " · " + s.note : ""}`))),
    run.ranked.length ? run.ranked.map((rc) => renderRanked(rc, run)) : h("div", { class: "empty" }, "No candidates found."));

  const right = h("div", {},
    run.draft ? h("div", {}, h("h3", {}, "Draft for your review"), renderDraft(run.draft, async () => { const r = await api("/api/runs/" + run.id); const dr = (await api("/api/drafts")).find((x) => x.id === run.draft.id); r.draft = dr; state.run = r; renderRun(r, out); }))
      : h("div", { class: "panel" }, h("h3", {}, "No draft prepared"), h("p", { class: "muted" }, "You can prepare a draft from the comparison when you want one.")),
    run.manualNextSteps.length ? h("div", { class: "panel" }, h("h3", {}, "Manual next steps"), h("ul", { class: "reasons" }, run.manualNextSteps.map((s) => h("li", {}, s)))) : null,
    d ? h("div", { class: "panel" }, h("h3", {}, "X discovery"),
      h("p", { class: "small" }, `${d.stats.postsRetrieved} post(s) retrieved · ${d.stats.usefulAnnouncements} useful · ${d.stats.storefrontLinksResolved} store listing(s) resolved · ${d.stats.verifiedPurchasable} purchasable · ${d.stats.duplicatesFiltered} duplicate(s) merged · ${d.stats.blockedOrUnsupported} blocked or unsupported`),
      d.notes.map((n) => h("p", { class: "muted small" }, n)),
      h("details", {}, h("summary", {}, `Japanese search queries (${d.queries.length})`), renderQueries(d.queries)),
      h("details", { open: true }, h("summary", {}, "Providers"), renderProviderStatuses(d.providerStatuses)),
      h("details", {}, h("summary", {}, `Announcement leads (${d.leads.length})`), d.leads.map((l) => renderLead(l)))) : null,
    h("div", { class: "panel" }, h("h3", {}, "Source-to-decision timeline"), renderTimeline(run.timeline)));

  nodes.push(h("div", { class: "grid-2" }, left, right));
  out.replaceChildren(...nodes);
}

/* ---------------- saved hunts ---------------- */
const CHANGE_LABEL = { NEW_MATCH: "New match", RESTOCK: "Back in stock", AVAILABILITY_CHANGED: "Availability changed",
  PRICE_DROP: "Price dropped", PRICE_INCREASE: "Price increased", DELIVERED_COST_CHANGED: "Delivered estimate changed", NOW_MATCHES_BRIEF: "Now matches your brief" };

function renderHuntCheck(hunt, check, run) {
  const comparison = h("div");
  renderRun(run, comparison);
  const observation = (item) => item ? `${words(item.availability)} · ${item.priceJpy === null ? "price unknown" : yen(item.priceJpy)} · ${item.totalUsd === null ? "delivered cost unknown" : usd(item.totalUsd) + " est. delivered"}` : "Not previously seen by this hunt";
  $("#hunt-output").replaceChildren(...[
    h("div", { class: "panel" }, h("h2", {}, hunt.title + " · check results"),
      h("p", { class: "muted" }, `${check.changes.length} observed change(s) · ${check.matchCount} match(es) · ${when(check.at)}`),
      h("div", { class: "badges" }, badge(check.freshXDiscovery ? (check.cachedX ? "X results include cached posts" : "X discovery returned posts") : "No fresh X discovery", check.freshXDiscovery ? "info" : "warn"),
        check.sampleCount ? badge(`${check.sampleCount} sample entries`, "fx") : null, badge("No draft created", "")),
      h("p", { class: "muted small" }, check.note)),
    ...check.changes.map((change) => h("div", { class: "card" }, h("h3", {}, change.after.title),
      h("div", { class: "badges" }, change.kinds.map((kind) => badge(CHANGE_LABEL[kind] || words(kind), "info")),
        change.after.isFixture ? badge("Sample data", "fx") : null,
        badge(change.after.matchesBrief ? "Matches brief" : "Review fit and budget", change.after.matchesBrief ? "ok" : "warn")),
      h("p", { class: "muted small" }, "Previously: " + observation(change.before)),
      h("p", { class: "small" }, "Now: " + observation(change.after)), change.after.url ? link(change.after.url) : null)),
    check.changes.length ? null : h("p", { class: "empty" }, "No changes observed in the results returned by this check."),
    h("details", {}, h("summary", {}, "Open full comparison and prepare a draft"), comparison)].filter(Boolean));
}

async function loadHunts() {
  try {
    const hunts = await api("/api/hunts");
    $("#hunts").replaceChildren(...(hunts.length ? hunts.map((hunt) => {
      const checkButton = h("button", { class: "primary", onclick: async () => {
        checkButton.disabled = true;
        checkButton.textContent = "Checking…";
        try {
          const result = await api(`/api/hunts/${hunt.id}/check`, { method: "POST" });
          renderHuntCheck(result.hunt, result.check, result.run);
          await loadHunts(); loadStatus(); refreshCounts();
        } catch (e) { toast(e.message); }
        finally { checkButton.disabled = false; checkButton.textContent = "Check now"; }
      } }, "Check now");
      return h("article", { class: "card" }, h("h3", {}, hunt.title), h("p", {}, hunt.message),
        h("p", { class: "muted small" }, "Last checked " + when(hunt.lastCheckedAt)),
        h("p", { class: "small" }, hunt.lastCheck ? `${hunt.lastCheck.changes.length} change(s) in the last check · ${hunt.lastCheck.matchCount} match(es)` : "Baseline saved. Your next check will compare against it."),
        h("div", { class: "actions" }, checkButton,
          hunt.lastCheck ? h("button", { onclick: async () => {
            try { const run = await api("/api/runs/" + hunt.lastRunId); renderHuntCheck(hunt, hunt.lastCheck, run); }
            catch (e) { toast(e.message); }
          } }, "Last results") : null,
          h("button", { onclick: async () => {
            try { await api("/api/hunts/" + hunt.id, { method: "DELETE" }); $("#hunt-output").replaceChildren(); loadHunts(); }
            catch (e) { toast(e.message); }
          } }, "Remove")));
    }) : [h("p", { class: "empty" }, "Run a search in Agent, then select Save hunt to keep its brief.")]));
  } catch (e) { $("#hunts").replaceChildren(h("p", { class: "error" }, e.message)); }
}

/* ---------------- social tab ---------------- */
async function loadSocial() {
  const [data, watched] = await Promise.all([api("/api/social/leads"), api("/api/watched")]);
  const last = data.lastRefresh || {};
  $("#social-providers").replaceChildren(h("h4", {}, "Sources"),
    last.providerStatuses ? renderProviderStatuses(last.providerStatuses)
      : h("div", {}, data.providers.map((p) => h("div", { class: "provider" },
          h("div", {}, h("strong", {}, PROVIDER_LABEL[p.provider] || p.provider), h("div", { class: "badges" }, badge(p.configured ? "available" : "not configured", p.configured ? "info" : ""))),
          h("div", {}, h("div", {}, p.detail), h("div", { class: "muted small" }, "Coverage: " + p.coverage))))));
  const info = $("#social-refresh-info");
  if (last.at) {
    info.replaceChildren(
      h("div", { class: "banner " + (last.fresh ? "ok" : "fx") }, last.fresh ? `Fresh results retrieved ${when(last.at)}.`
        : `Last refresh ${when(last.at)}: not fresh X discovery.`,
        (last.notes || []).length ? h("div", { class: "small" }, last.notes.join(" ")) : null),
      h("details", {}, h("summary", {}, `Queries used (${(last.queries || []).length})`), renderQueries(last.queries || [])));
  } else {
    info.replaceChildren(h("p", { class: "muted small" }, "Not refreshed yet. Press Refresh to search now."));
  }
  const feed = $("#social-feed");
  feed.replaceChildren(...(data.leads.length ? data.leads.map((l) => renderLead(l)) : [h("div", { class: "empty" }, "No announcements yet. Press Refresh, or import a post.")]));
  $("#watched-list").replaceChildren(...(watched.length ? watched.map((w) => h("div", { class: "row between", style: "padding:6px 0;border-bottom:1px solid var(--line)" },
    h("div", {}, h("strong", {}, w.handle ? "@" + w.handle : "Search terms"), w.searchTerms.length ? h("div", { class: "small", lang: "ja" }, w.searchTerms.join("、")) : null,
      h("div", { class: "muted small" }, w.categories.length ? w.categories.join(", ") : "all categories")),
    h("button", { class: "small", onclick: async () => { await api("/api/watched/" + w.id, { method: "DELETE" }); loadSocial(); } }, "Remove")))
    : [h("p", { class: "muted small" }, "Nothing watched yet.")]));
}
$("#social-refresh").addEventListener("click", async (ev) => {
  const btn = ev.target;
  btn.disabled = true; btn.textContent = "Refreshing…";
  try {
    const r = await api("/api/social/refresh", { method: "POST", body: { category: $("#social-category").value || null } });
    const n = r.results.reduce((a, x) => a + x.leads.length, 0);
    toast(r.lastRefresh.fresh ? `Found ${n} announcement(s).` : `No live X posts. Showing ${n} sample or imported announcement(s).`);
    await loadSocial(); loadStatus();
  } catch (e) { toast(e.message); } finally { btn.disabled = false; btn.textContent = "Refresh"; }
});
$("#watched-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  $("#watched-error").textContent = "";
  try {
    await api("/api/watched", { method: "POST", body: { handle: f.handle.value, searchTerms: f.terms.value.split(/[,、]/).map((s) => s.trim()).filter(Boolean),
      categories: [...f.querySelectorAll("#watched-cats input:checked")].map((i) => i.value) } });
    f.reset(); loadSocial();
  } catch (e) { $("#watched-error").textContent = e.message; }
});
$("#import-post-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  $("#import-post-error").textContent = "";
  try {
    await api("/api/social/import", { method: "POST", body: { url: f.url.value.trim(), text: f.text.value, links: f.links.value.split(",").map((s) => s.trim()).filter(Boolean) } });
    f.reset(); toast("Post imported."); loadSocial();
  } catch (e) { $("#import-post-error").textContent = e.message; }
});

/* ---------------- shortlist / review / listings ---------------- */
function renderCandidateRow(c, actions) {
  const a = AVAIL_BADGE[c.availability] || AVAIL_BADGE.UNKNOWN;
  return h("div", { class: "card" }, h("div", { class: "card-head" },
    h("div", {}, h("div", { class: "card-title" }, c.title), h("div", { class: "muted small" }, c.sourceLabel, c.storeName ? ` · ${c.storeName}` : ""),
      h("div", { class: "badges" }, badge(a[0], a[1]), c.isFixture ? badge("Sample data", "fx") : null, c.injectionFlags.length ? badge("Instruction-like text ignored", "warn") : null)),
    h("div", { class: "card-title" }, val(c.priceJpy, yen))),
    c.announcementUrl ? h("div", { class: "small" }, "Announcement: ", link(c.announcementUrl)) : null,
    h("div", { class: "small" }, "Purchase page: ", c.purchaseUrl ? link(c.purchaseUrl) : h("span", { class: "unknown" }, "not resolved")),
    c.restrictions.length ? h("p", { class: "small" }, h("strong", {}, "Restrictions: "), c.restrictions.join(" · ")) : null,
    h("div", { class: "actions" }, actions));
}
async function loadShortlist() {
  const items = await api("/api/shortlist");
  $("#shortlist").replaceChildren(...(items.length ? items.map((it) => renderCandidateRow(it.candidate, [
    h("button", { class: "small primary", onclick: async () => { try { await api("/api/drafts", { method: "POST", body: { shortlistId: it.id } }); toast("Draft prepared."); refreshCounts(); showTab("review"); } catch (e) { toast(e.message); } } }, "Prepare draft for review"),
    h("button", { class: "small", onclick: async () => { await api("/api/shortlist/" + it.id, { method: "DELETE" }); loadShortlist(); refreshCounts(); } }, "Remove")]))
    : [h("div", { class: "empty" }, "Your shortlist is empty. Add items from Social Drops or a ranked comparison.")]));
}
async function loadDrafts() {
  const drafts = await api("/api/drafts");
  $("#drafts").replaceChildren(...(drafts.length ? drafts.map((d) => renderDraft(d, loadDrafts)) : [h("div", { class: "empty" }, "Nothing to review yet.")]));
  refreshCounts();
}
async function loadListings() {
  const items = await api("/api/listings");
  $("#listings").replaceChildren(...(items.length ? items.map((c) => renderCandidateRow(c, [
    h("span", { class: "muted small" }, `Read ${when(c.retrievedAt) || "time unknown"}`),
    h("button", { class: "small", onclick: async () => { await api("/api/listings/" + c.id, { method: "DELETE" }); loadListings(); } }, "Remove")]))
    : [h("div", { class: "empty" }, "No imported listings yet.")]));
}
$("#import-listing-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target, btn = f.querySelector("button");
  $("#import-listing-error").textContent = "";
  btn.disabled = true;
  try { await api("/api/listings/import", { method: "POST", body: { url: f.url.value.trim() } }); f.reset(); toast("Listing imported."); loadListings(); }
  catch (e) { $("#import-listing-error").textContent = e.message; } finally { btn.disabled = false; }
});

/* ---------------- memory ---------------- */
async function loadMemory() {
  const m = await api("/api/memory");
  $("#memory-backend").replaceChildren(h("p", { class: "small", style: "margin:6px 0 10px" },
    badge(m.backend.mode === "live" ? "Cognee live" : m.backend.mode === "fallback" ? "Local fallback" : MODE_LABEL[m.backend.mode] || m.backend.mode, m.backend.mode === "live" ? "ok" : m.backend.mode === "error" ? "err" : "warn"),
    " ", m.backend.detail));
  $("#pref-list").replaceChildren(...(m.preferences.length ? m.preferences.map((p) => h("div", { class: "row between", style: "padding:6px 0;border-bottom:1px solid var(--line)" },
    h("div", {}, p.attribute ? badge((p.polarity === "like" ? "Likes " : "Avoids ") + p.label, p.polarity === "like" ? "ok" : "err") : badge("Note", ""),
      h("div", { class: "small" }, "“" + p.text + "”"), h("div", { class: "muted small" }, `${words(p.source)} · ${p.inCognee ? "stored in Cognee and locally" : "stored locally"}`)),
    h("button", { class: "small", onclick: async () => { await api("/api/memory/preferences/" + p.id, { method: "DELETE" }); loadMemory(); } }, "Forget")))
    : [h("p", { class: "muted small" }, "No preferences stored. Tell KAI what you like and it will use it when ranking.")]));
  $("#search-list").replaceChildren(...(m.searches.length ? m.searches.map((s) => h("div", { class: "small", style: "padding:5px 0;border-bottom:1px solid var(--line)" }, s.raw, h("div", { class: "muted" }, `${s.category} · ${when(s.ts)}`)))
    : [h("p", { class: "muted small" }, "No searches yet.")]));
}
$("#pref-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try { const r = await api("/api/memory/preferences", { method: "POST", body: { text: f.text.value } }); f.reset(); toast(`Remembered ${r.saved.length} preference(s).`); loadMemory(); loadStatus(); }
  catch (e) { toast(e.message); }
});

/* ---------------- activity ---------------- */
async function loadActivity() {
  const [s, events] = await Promise.all([loadStatus(), api("/api/activity?limit=250")]);
  $("#integration-detail").replaceChildren(h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Integration"), h("th", {}, "State"), h("th", {}, "Detail"), h("th", { class: "num" }, "Live calls"), h("th", { class: "num" }, "Fallback calls"))),
    h("tbody", {}, s.integrations.map((i) => h("tr", {}, h("td", {}, i.label), h("td", {}, badge(MODE_LABEL[i.mode] || i.mode, i.mode === "live" ? "ok" : i.mode === "error" ? "err" : i.mode === "fallback" ? "warn" : "info")),
      h("td", {}, i.detail), h("td", { class: "num" }, i.liveCalls ?? i.calls ?? 0), h("td", { class: "num" }, i.fallbackCalls ?? i.failures ?? 0))))),
    h("p", { class: "muted small", style: "margin-top:8px" }, `X API estimated spend: ${usd(s.xSpend.spentUsd)} of ${usd(s.xSpend.maxUsd)} ceiling. ${s.xSpend.note}`));
  $("#usage").replaceChildren(s.usage.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Service"), h("th", {}, "Operation"), h("th", { class: "num" }, "Calls"), h("th", { class: "num" }, "Units"), h("th", { class: "num" }, "Cost"))),
    h("tbody", {}, s.usage.map((u) => h("tr", {}, h("td", {}, u.service), h("td", {}, u.operation), h("td", { class: "num" }, u.calls), h("td", { class: "num" }, `${u.units} ${u.unitName}`),
      h("td", { class: "num" }, u.costKnown ? usd(u.costUsd) : h("span", { class: "unknown" }, "not priced")))))) : h("p", { class: "muted small" }, "No external usage recorded yet."));
  $("#activity").replaceChildren(events.length ? renderTimeline(events) : h("p", { class: "muted small" }, "No activity yet."));
}
$("#check-integrations").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  try { await api("/api/integrations/check", { method: "POST" }); toast("Connectivity check finished."); loadActivity(); } catch (e) { toast(e.message); } finally { ev.target.disabled = false; }
});
$("#seed-senso").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  try { const r = await api("/api/integrations/senso/seed", { method: "POST" }); toast(r.ok ? "Cost sources uploaded to Senso." : (r.error || "Some uploads failed; see the activity log.")); loadActivity(); }
  catch (e) { toast(e.message); } finally { ev.target.disabled = false; }
});

loadStatus().catch((e) => toast(e.message));
refreshCounts();
