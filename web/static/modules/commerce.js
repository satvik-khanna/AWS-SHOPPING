/* Module: sortable commerce list with per-item Buy button, cost breakdown, and Swipe-to-Pay gate */
(function (root) {
  "use strict";

  var CONDITION = { "new": "New", like_new: "Like new", good: "Good", fair: "Fair", junk: "Junk" };
  var COND_RANK = { "new": 4, like_new: 3, good: 2, fair: 1, junk: 0 };
  var SOURCE = { mercari: "Mercari", yahoo_auctions: "Yahoo", other: "Shop" };
  var GATE_ICONS = { awaiting: "shield-lock", placed: "check-circle", stopped: "x-circle", idle: "inbox" };
  var KINDS = [
    [/^recalled /, "recall"], [/^found /, "search"], [/^skip /, "skip"], [/^pick /, "pick"], [/^cart /, "cart"],
    [/^no candidate/, "cart"], [/^rejected seller/, "reject"], [/^approval accepted/, "approved"],
    [/^approval refused/, "refused"], [/^(remembered|could not remember)/, "memory"]
  ];

  function chip(text, cls) { return root.JSA.ui.h("span", { class: "chip" + (cls ? " " + cls : "") }, text); }

  function renderPrefs(result) {
    var ui = root.JSA.ui, p = result.preferences, box = ui.clear(ui.$("prefs"));
    var sizes = Object.keys(p.sizes || {});
    var groups = [
      ["Condition", p.condition_min ? [chip(CONDITION[p.condition_min] || p.condition_min, "c-ink")] : []],
      ["Sizes", sizes.map(function (k) { return chip(k + " " + p.sizes[k], "c-ink"); })],
      ["Rejected", (p.rejected_sellers || []).map(function (s) { return chip(s, "c-red struck"); })],
      ["Purchased", (p.past_purchases || []).map(function (s) { return chip(s, "c-green"); })]
    ];
    if (!groups.some(function (g) { return g[1].length; })) {
      box.appendChild(ui.h("span", { class: "drawer-empty" }, p.user_id + " · none"));
      return;
    }
    groups.forEach(function (g) {
      if (!g[1].length) return;
      box.appendChild(ui.h("dl", { class: "pref" }, ui.h("dt", null, g[0]), ui.h("dd", null, g[1])));
    });
  }

  function renderTrail(result) {
    var ui = root.JSA.ui, box = ui.clear(ui.$("trail"));
    var trail = result.trail || [];
    var badge = ui.$("steps-count");
    if (badge) badge.textContent = String(trail.length);
    trail.forEach(function (t) {
      var kind = "step";
      for (var i = 0; i < KINDS.length; i++) if (KINDS[i][0].test(t)) { kind = KINDS[i][1]; break; }
      box.appendChild(ui.h("li", null, ui.h("span", { class: "k k-" + kind }, kind), ui.h("span", { class: "txt" }, t)));
    });
  }

  function sortEntries(entries, sortKey) {
    var ui = root.JSA.ui;
    var list = entries.slice();
    if (sortKey === "price_asc") {
      list.sort(function (a, b) { return ui.totalOf(a.pick.cost) - ui.totalOf(b.pick.cost); });
    } else if (sortKey === "price_desc") {
      list.sort(function (a, b) { return ui.totalOf(b.pick.cost) - ui.totalOf(a.pick.cost); });
    } else if (sortKey === "cond") {
      list.sort(function (a, b) { return (COND_RANK[b.pick.listing.condition] || 0) - (COND_RANK[a.pick.listing.condition] || 0); });
    }
    return list;
  }

  function renderPicks(state, filterText, sortKey, handlers) {
    var ui = root.JSA.ui, r = state.result, box = ui.clear(ui.$("picks"));
    var cartId = state.cart && state.cart.listing_id;
    var entries = r.picks.map(function (p, i) { return { pick: p, idx: i }; }).filter(function (x) {
      if (!filterText) return true;
      var l = x.pick.listing;
      return (l.title_en + " " + l.title_ja + " " + l.seller + " " + l.source).toLowerCase().indexOf(filterText) !== -1;
    });
    entries = sortEntries(entries, sortKey);

    ui.$("picks-sub").textContent = entries.length
      ? entries.length + " matches · max " + ui.usd0(r.request.max_total_usd)
      : "0 matches";

    if (!entries.length) {
      box.appendChild(ui.h("div", { class: "none-found" }, "No matches"));
    }

    entries.forEach(function (entry, pos) {
      var p = entry.pick, i = entry.idx, l = p.listing, total = ui.totalOf(p.cost);
      var inCart = l.id === cartId;
      var placed = inCart && state.cart && state.cart.status === "placed";
      var url = ui.safeUrl(l.url);

      var actions = ui.h("div", { class: "row-actions" },
        url ? ui.h("a", {
          href: url, target: "_blank", rel: "noopener noreferrer",
          class: "btn btn-ghost btn-icon", title: "Open listing", "aria-label": "Open listing",
          onclick: function (e) { e.stopPropagation(); }
        }, ui.icon("link-external", 14)) : null,
        ui.h("button", {
          type: "button", class: "btn btn-ghost btn-icon", title: "Copy item link", "aria-label": "Copy item link",
          onclick: function (e) { e.stopPropagation(); handlers.onCopyItemLink(l.id); }
        }, ui.icon("link", 14)),
        ui.h("button", {
          type: "button", class: "btn btn-ghost btn-icon btn-danger", title: "Reject " + l.seller, "aria-label": "Reject seller",
          onclick: function (e) { e.stopPropagation(); handlers.onReject(l.seller); }
        }, ui.icon("blocked", 14)),
        ui.h("button", {
          type: "button",
          class: "btn btn-small " + (placed ? "btn-ghost" : "btn-primary"),
          disabled: placed,
          title: "Open tab & prepare checkout via Computer Use",
          onclick: function (e) { e.stopPropagation(); handlers.onBuy(p, i); }
        }, placed ? "Paid" : "Buy")
      );

      box.appendChild(ui.h("article", {
        class: "pick-row" + (i === state.selectedIdx ? " selected" : "") + (inCart ? " in-cart" : ""),
        style: "--i:" + pos,
        "data-listing": l.id,
        tabindex: "0",
        onclick: function () { handlers.onSelect(i); }
      },
        ui.h("span", { class: "rank" }, String(pos + 1).padStart(2, "0")),
        ui.h("div", { class: "row-main" },
          ui.h("div", { class: "row-title" },
            ui.h("span", null, l.title_en),
            inCart ? ui.h("span", { class: "tag-cart" + (placed ? " placed" : ""), title: placed ? "Ordered" : "Ready to swipe" },
              ui.icon(placed ? "check-circle" : "package", 12)) : null),
          ui.h("div", { class: "row-meta" },
            chip(CONDITION[l.condition] || l.condition, "c-ink"),
            ui.h("span", null, SOURCE[l.source] || l.source),
            ui.h("span", null, l.seller))),
        ui.h("span", { class: "row-price" }, ui.usd(total)),
        actions));
    });

    var skipped = r.trail.map(function (t) { return /^skip (\S+): (.*)$/.exec(t); }).filter(Boolean);
    var sk = ui.clear(ui.$("skipped"));
    sk.hidden = !skipped.length;
    if (skipped.length) {
      sk.appendChild(ui.h("span", { class: "skipped-label" }, skipped.length + " filtered"));
    }
  }

  function renderCost(state, handlers) {
    var ui = root.JSA.ui, r = state.result, panel = ui.clear(ui.$("cost-panel"));
    var pick = r.picks[state.selectedIdx] || r.picks[0];
    if (!pick) { panel.hidden = true; return; }
    panel.hidden = false;
    var l = pick.listing, cost = pick.cost, req = r.request;
    var total = ui.totalOf(cost), room = req.max_total_usd - total;

    var lines = ui.h("ul", { class: "lines" });
    cost.lines.forEach(function (line, n) {
      var c = line.citation || {};
      var url = c.url && ui.safeUrl(c.url);
      lines.appendChild(ui.h("li", { class: "line", style: "--i:" + n },
        ui.h("span", { class: "dot s" + (n % 6) }),
        ui.h("span", { class: "label" }, line.label,
          /item/i.test(line.label) && l.price_jpy ? ui.h("small", null, ui.yen(l.price_jpy)) : null),
        url ? ui.h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "asof", title: c.source || "" }, c.as_of || "ref")
            : ui.h("span", { class: "asof", title: c.source || "" }, c.as_of || "—"),
        ui.h("span", { class: "amt" }, ui.usd(line.amount_usd))));
    });

    var totalEl = ui.h("span", { class: "cost-total-num" }, ui.usd(total));
    var url = ui.safeUrl(l.url);
    var buyeeUrl = l.fields && l.fields.buyee_url ? ui.safeUrl(String(l.fields.buyee_url)) : null;

    panel.appendChild(ui.h("div", { class: "item-detail-head" },
      ui.h("div", { class: "item-detail-title" }, l.title_en),
      l.title_ja ? ui.h("div", { class: "item-detail-ja", lang: "ja" }, l.title_ja) : null,
      ui.h("div", { class: "row-meta" },
        chip(CONDITION[l.condition] || l.condition, "c-ink"),
        ui.h("span", null, SOURCE[l.source] || l.source),
        ui.h("span", null, l.seller))));

    panel.appendChild(ui.h("div", { class: "cost-head" },
      ui.h("span", { class: "cost-title" }, "Landed (" + req.ship_to + ")"),
      ui.h("span", { class: "cost-sub" },
        room >= 0 ? ui.h("span", { class: "under" }, "-" + ui.usd(room)) : ui.h("span", { class: "over" }, "+" + ui.usd(-room)),
        " · FX " + cost.fx_jpy_per_usd),
      totalEl));
    panel.appendChild(lines);
    panel.appendChild(ui.h("div", { class: "item-detail-actions" },
      handlers && handlers.onBuy ? ui.h("button", { type: "button", class: "btn btn-primary", onclick: function () { handlers.onBuy(pick, state.selectedIdx); } }, "Buy via Agent") : null,
      url ? ui.h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "btn btn-small" }, ui.icon("link-external", 12), "Listing") : null,
      buyeeUrl ? ui.h("a", { href: buyeeUrl, target: "_blank", rel: "noopener noreferrer", class: "btn btn-small" }, ui.icon("package", 12), "Buyee") : null));
    ui.tweenUsd(totalEl, total);
  }

  function renderGate(state, vaultProfile, handlers) {
    var ui = root.JSA.ui, r = state.result, cart = state.cart, g = ui.$("gate");
    ui.clear(g);
    if (!cart || !state.buyReady) {
      g.className = "gate idle";
      g.hidden = !cart;
      if (cart) {
        g.appendChild(ui.h("div", { class: "gate-body" },
          ui.icon("package", 14),
          ui.h("span", { class: "gate-sub" }, "Click Buy on any item to open Chrome tab & prepare Swipe to Pay.")));
      }
      return;
    }
    g.hidden = false;
    var pick = r.picks.filter(function (p) { return p.listing.id === cart.listing_id; })[0] || null;
    var total = pick ? ui.totalOf(pick.cost) : null;
    var kind = cart.status === "awaiting_approval" ? "awaiting" : (cart.status === "placed" ? "placed" : "stopped");
    g.className = "gate " + kind;

    if (kind === "awaiting") {
      var cardText = vaultProfile && vaultProfile.card ? (vaultProfile.card.brand + " •••• " + vaultProfile.card.last4) : "Saved card";
      var swipeEl = root.JSA.swipe.create({
        label: "Swipe to pay " + (total !== null ? ui.usd(total) : "") + " (" + cardText + ")",
        onConfirm: function (btn) { handlers.onApprove(btn); }
      });
      g.appendChild(ui.h("div", { class: "gate-body gate-swipe" },
        ui.h("div", { class: "gate-swipe-top" },
          ui.h("div", { class: "gate-icon", "aria-hidden": "true" }, ui.icon("shield-lock", 15)),
          ui.h("div", { class: "gate-item" },
            ui.h("span", { class: "gate-kicker" }, "Ready to pay"),
            pick ? ui.h("span", { class: "gate-sub" }, " · " + pick.listing.title_en) : null),
          pick ? ui.h("button", {
            type: "button", class: "btn btn-ghost btn-icon btn-danger", id: "reject", title: "Reject " + pick.listing.seller,
            onclick: function () { handlers.onReject(pick.listing.seller); }
          }, ui.icon("blocked", 14)) : null),
        swipeEl));
      return;
    }

    var actions = [];
    if (kind === "placed" && state.usedToken) {
      actions.push(ui.h("button", {
        type: "button", class: "btn btn-small", id: "replay", title: "Test token replay",
        onclick: function (e) { handlers.onReplay(e.currentTarget); }
      }, ui.icon("sync", 14), "Replay"));
    }
    g.appendChild(ui.h("div", { class: "gate-body" },
      ui.h("div", { class: "gate-icon", "aria-hidden": "true" }, ui.icon(GATE_ICONS[kind], 15)),
      ui.h("div", { class: "gate-item" },
        ui.h("span", { class: "gate-kicker" }, kind === "placed" ? "Order placed" : "Blocked"),
        pick ? ui.h("span", { class: "gate-sub" }, " · " + pick.listing.title_en) : null,
        state.gateEvent ? ui.h("div", { class: "ev" }, state.gateEvent) : null),
      ui.h("div", { class: "gate-actions" }, actions)));
  }

  root.JSA = root.JSA || {};
  root.JSA.commerce = { renderPrefs: renderPrefs, renderTrail: renderTrail, renderPicks: renderPicks, renderCost: renderCost, renderGate: renderGate };
})(window);
