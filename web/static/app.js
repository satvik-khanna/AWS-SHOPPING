/* Japan Shopping Agent demo screen. Plain JS, no dependencies.
   All listing / memory text is untrusted: it only ever goes through textContent. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var state = { result: null, cart: null, usedToken: null, gateEvent: null, busy: false };

  var CONDITION = { "new": "New", like_new: "Like new", good: "Good", fair: "Fair", junk: "Junk" };
  var SOURCE = { mercari: "Mercari", yahoo_auctions: "Yahoo! Auctions", other: "Independent shop" };

  // h("div", {class: "x", onclick: fn}, child, "text", [more])
  function h(tag, attrs) {
    var el = document.createElement(tag);
    for (var k in attrs || {}) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { append(el, x); }); return; }
    el.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

  function usd(n) {
    return "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function usd0(n) { return "$" + Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 }); }
  function yen(n) { return "\u00a5" + Number(n).toLocaleString("en-US"); }
  function safeUrl(u) {
    try {
      var x = new URL(u);
      return (x.protocol === "https:" || x.protocol === "http:") ? x.href : null;
    } catch (e) { return null; }
  }
  function totalOf(cost) {
    if (typeof cost.total_usd === "number") return cost.total_usd;
    return Math.round(cost.lines.reduce(function (s, l) { return s + l.amount_usd; }, 0) * 100) / 100;
  }

  function api(path, body) {
    return fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || ("The server answered " + res.status));
        return data;
      });
    }, function () {
      throw new Error("Cannot reach the demo server. Is `python3 -m web` still running?");
    });
  }

  function showError(msg) {
    var el = $("error");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  function setBusy(on, label) {
    state.busy = on;
    $("go").disabled = on;
    $("go").textContent = on ? (label || "Searching\u2026") : "Find and fill cart";
    $("results").classList.toggle("stale", on);
    $("empty").classList.toggle("busy", on);
    document.querySelector(".empty-lead").textContent =
      on ? "Searching Japanese marketplaces" : "Ask for something that is only sold in Japan.";
  }

  function requestBody() {
    var r = state.result.request;
    return { query: r.query, budget: r.max_total_usd, ship_to: r.ship_to, user_id: r.user_id, condition_min: r.condition_min };
  }

  /* ---------- actions ---------- */

  function runSearch(ev) {
    if (ev) ev.preventDefault();
    if (state.busy) return;
    showError("");
    var body = {
      query: $("query").value, budget: $("budget").value, ship_to: $("ship_to").value,
      condition_min: $("condition_min").value || null
    };
    setBusy(true);
    api("/api/run", body).then(show, function (e) { showError(e.message); }).then(function () { setBusy(false); });
  }

  function rejectSeller(seller) {
    if (state.busy || !state.result) return;
    showError("");
    var body = requestBody();
    body.seller = seller;
    setBusy(true, "Searching again\u2026");
    api("/api/reject", body).then(show, function (e) { showError(e.message); }).then(function () { setBusy(false); });
  }

  function approve(btn) {
    var token = state.cart && state.cart.approval_token;
    if (!token || state.busy) return;
    showError("");
    btn.disabled = true;
    btn.textContent = "Placing\u2026";
    api("/api/approve", { token: token }).then(function (r) {
      state.usedToken = token;
      r.cart.listing_id = r.cart.listing_id || state.cart.listing_id;
      state.cart = r.cart;
      state.result.trail = state.result.trail.concat(r.trail || []);
      renderAll();
    }, function (e) {
      showError(e.message);
      btn.disabled = false;
      btn.textContent = "Approve and place order";
    });
  }

  function replayToken(btn) {
    if (!state.usedToken) return;
    btn.disabled = true;
    api("/api/approve", { token: state.usedToken }).then(function (r) {
      state.gateEvent = "Replay of the used token: " + r.cart.status + (r.cart.detail ? " (" + r.cart.detail + ")" : "");
      state.result.trail = state.result.trail.concat(r.trail || []);
      if (r.cart.status === "placed") state.cart = r.cart;   // would be a gate failure; show it, do not hide it
      renderAll();
    }, function (e) { showError(e.message); btn.disabled = false; });
  }

  function show(result) {
    state.result = result;
    state.cart = result.cart;
    state.usedToken = null;
    state.gateEvent = null;
    $("empty").hidden = true;
    $("results").hidden = false;
    renderAll();
    renderMeta(result.meta);
  }

  /* ---------- render ---------- */

  function renderAll() {
    renderPrefs();
    renderPicks();
    renderGate();
    renderTrail();
  }

  function chip(text, cls) { return h("span", { class: "chip" + (cls ? " " + cls : "") }, text); }

  function renderPrefs() {
    var p = state.result.preferences, box = clear($("prefs"));
    var sizes = Object.keys(p.sizes || {});
    var groups = [
      ["Condition floor", p.condition_min ? [chip(CONDITION[p.condition_min] || p.condition_min, "c-indigo")] : []],
      ["Sizes", sizes.map(function (k) { return chip(k + " " + p.sizes[k], "c-indigo"); })],
      ["Rejected sellers", (p.rejected_sellers || []).map(function (s) { return chip(s, "c-red struck"); })],
      ["Past purchases", (p.past_purchases || []).map(function (s) { return chip(s, "c-green"); })],
      ["Notes", (p.notes || []).map(function (s) { return chip(s); })]
    ];
    var known = groups.some(function (g) { return g[1].length; });
    if (!known) {
      box.appendChild(h("span", { class: "prefs-empty" },
        "Nothing remembered yet for ", h("strong", null, p.user_id),
        ". Reject a seller or approve an order and it is recalled on the next search."));
      return;
    }
    box.appendChild(h("dl", { class: "pref" }, h("dt", null, "User"), h("dd", null, p.user_id)));
    groups.forEach(function (g) {
      box.appendChild(h("dl", { class: "pref" }, h("dt", null, g[0]),
        h("dd", null, g[1].length ? g[1] : h("span", { class: "none" }, "none"))));
    });
  }

  function citeNode(c) {
    c = c || {};
    var url = c.url && safeUrl(c.url);
    var dated = /^\d{4}-\d{2}-\d{2}$/.test(c.as_of || "");
    var src = c.source || "no source";
    return h("div", { class: "cite" },
      url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "src" }, src) : h("span", { class: "src" }, src),
      h("span", { class: "asof" + (dated ? "" : " warn"), title: dated ? "Date of the rule behind this number" : "Not a researched, dated value yet" },
        "as of " + (c.as_of || "unknown")));
  }

  function pickCard(pick, i, inCart) {
    var l = pick.listing, cost = pick.cost, req = state.result.request;
    var total = totalOf(cost), budget = req.max_total_usd, room = budget - total;
    var scale = Math.max(budget, total) || 1;
    var placed = inCart && state.cart && state.cart.status === "placed";
    var url = safeUrl(l.url);
    var photo = (l.photos || []).map(safeUrl).filter(Boolean)[0];

    var bar = h("div", { class: "bar", role: "img", "aria-label": usd(total) + " of " + usd0(budget) + " budget" });
    var lines = h("ul", { class: "lines" });
    cost.lines.forEach(function (line, n) {
      var seg = h("span", { class: "s" + (n % 6), title: line.label + " " + usd(line.amount_usd) });
      seg.style.width = (Math.max(line.amount_usd, 0) / scale * 100) + "%";
      bar.appendChild(seg);
      var hint = /item/i.test(line.label) && l.price_jpy ? h("small", null, yen(l.price_jpy)) : null;
      lines.appendChild(h("li", { class: "line" },
        h("span", { class: "dot s" + (n % 6) }),
        h("span", { class: "label" }, line.label, hint),
        h("span", { class: "amt" }, usd(line.amount_usd)),
        citeNode(line.citation)));
    });

    var chips = [
      chip(CONDITION[l.condition] || l.condition, "c-indigo"),
      chip(SOURCE[l.source] || l.source),
      chip("Seller " + l.seller),
      chip(l.ships_abroad ? "Ships abroad" : "Japan only, via proxy", l.ships_abroad ? "c-green" : "c-amber")
    ];
    Object.keys(l.fields || {}).forEach(function (k) { chips.push(chip(k.replace(/_/g, " ") + " " + l.fields[k])); });

    var img = null;
    if (photo) {
      img = h("img", { class: "thumb", src: photo, alt: l.title_en, loading: "lazy", referrerpolicy: "no-referrer" });
      img.addEventListener("error", function () { img.remove(); });
    }

    return h("article", { class: "pick" + (inCart ? " in-cart" : ""), "data-listing": l.id },
      img,
      h("div", { class: "pick-head" },
        h("div", { class: "pick-rank" },
          h("span", { class: "rank" }, "#" + (i + 1)),
          inCart ? h("span", { class: "tag-cart" + (placed ? " placed" : "") }, placed ? "Ordered" : "In cart") : null),
        h("h3", null, l.title_en),
        h("p", { class: "ja", lang: "ja" }, l.title_ja),
        h("div", { class: "chips" }, chips)),
      h("div", { class: "total" },
        h("div", { class: "total-label" }, "Total landed cost to " + req.ship_to),
        h("div", { class: "total-num" }, usd(total)),
        h("div", { class: "total-sub" },
          room >= 0 ? h("span", { class: "under" }, usd(room) + " under") : h("span", { class: "over" }, usd(-room) + " over"),
          " your " + usd0(budget) + " budget"),
        bar,
        h("div", { class: "bar-cap" }, h("span", null, "$0"), h("span", null, "budget " + usd0(budget)))),
      lines,
      h("div", { class: "fx" }, "FX used: " + cost.fx_jpy_per_usd + " JPY per USD"),
      h("p", { class: "why" }, h("b", null, "Why"), pick.reason, " (score " + Number(pick.score).toFixed(2) + ")"),
      h("div", { class: "pick-actions" },
        url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, "Open listing") : h("span", null, "No listing link"),
        h("button", { type: "button", class: "btn btn-small btn-danger", onclick: function () { rejectSeller(l.seller); } },
          "Reject seller")));
  }

  function renderPicks() {
    var r = state.result, box = clear($("picks"));
    var cartId = state.cart && state.cart.listing_id;
    $("picks-sub").textContent = r.picks.length
      ? r.picks.length + " within " + usd0(r.request.max_total_usd) + " for \u201c" + r.request.query + "\u201d"
      : "";
    if (!r.picks.length) {
      box.appendChild(h("div", { class: "none-found" },
        h("strong", null, "No listing lands within " + usd0(r.request.max_total_usd)),
        "Raise the budget or loosen the condition, then search again. Nothing was added to the cart."));
    }
    r.picks.forEach(function (p, i) { box.appendChild(pickCard(p, i, p.listing.id === cartId)); });

    var skipped = r.trail.map(function (t) { return /^skip (\S+): (.*)$/.exec(t); }).filter(Boolean);
    var sk = clear($("skipped"));
    sk.hidden = !skipped.length;
    if (skipped.length) {
      sk.appendChild(h("h3", null, "Filtered out before ranking (" + skipped.length + ")"));
      sk.appendChild(h("ul", null, skipped.map(function (m) {
        var budget = /budget/i.test(m[2]);
        return h("li", null, chip(budget ? "Over budget" : "Blocked", budget ? "c-amber" : "c-red"), h("code", null, m[1]), m[2]);
      })));
    }
  }

  function gateShell(kind, kicker, note, item, total, actions) {
    var g = $("gate");
    clear(g).className = "gate " + kind;
    g.appendChild(h("div", { class: "gate-body" },
      h("div", { class: "gate-state" }, h("div", { class: "gate-icon", "aria-hidden": "true" }),
        h("div", null, h("div", { class: "gate-kicker" }, kicker), h("div", { class: "gate-note" }, note))),
      h("div", { class: "gate-item" }, item),
      total === null ? h("div") : h("div", { class: "gate-total" }, h("div", { class: "k" }, "Total landed"), h("div", { class: "v" }, usd(total))),
      h("div", { class: "gate-actions" }, actions)));
  }

  function renderGate() {
    var r = state.result, cart = state.cart;
    if (!cart) {
      gateShell("idle", "Nothing in the cart", "No listing fits the budget, so there is nothing to approve.", null, null, null);
      return;
    }
    var pick = r.picks.filter(function (p) { return p.listing.id === cart.listing_id; })[0] || null;
    var total = pick ? totalOf(pick.cost) : null;
    var item = [
      pick ? h("div", { class: "t" }, pick.listing.title_en) : null,
      cart.detail ? h("div", { class: "d" }, cart.detail) : null
    ];
    var cartUrl = cart.cart_url && safeUrl(cart.cart_url);
    if (cartUrl) item.push(h("div", { class: "d" }, h("a", { href: cartUrl, target: "_blank", rel: "noopener noreferrer" }, "Open the filled cart")));

    if (cart.status === "awaiting_approval") {
      item.push(h("div", { class: "tok" }, "single-use approval token " + cart.approval_token));
      gateShell("awaiting", "Stopped for your approval", "The cart is filled. Nothing has been paid.", item, total, [
        h("button", { type: "button", class: "btn btn-approve", id: "approve", onclick: function (e) { approve(e.currentTarget); } },
          "Approve and place order"),
        pick ? h("button", { type: "button", class: "btn btn-danger", id: "reject", onclick: function () { rejectSeller(pick.listing.seller); } },
          "Reject seller " + pick.listing.seller) : null
      ]);
    } else if (cart.status === "placed") {
      item.push(h("div", { class: "tok" }, "approval token used once and now dead"));
      if (state.gateEvent) item.push(h("div", { class: "ev" }, state.gateEvent));
      gateShell("placed", "Order placed", "You approved it. The token cannot be used again.", item, total, [
        state.usedToken ? h("button", { type: "button", class: "btn btn-small", id: "replay", onclick: function (e) { replayToken(e.currentTarget); } },
          "Try the used token again") : null
      ]);
    } else {
      gateShell("stopped", cart.status === "failed" ? "Checkout failed" : "Blocked", "Nothing has been paid.", item, total, null);
    }
  }

  var KINDS = [
    [/^recalled /, "recall"], [/^found /, "search"], [/^skip /, "skip"], [/^pick /, "pick"], [/^cart /, "cart"],
    [/^no candidate/, "cart"], [/^rejected seller/, "reject"], [/^approval accepted/, "approved"],
    [/^approval refused/, "refused"], [/^(remembered|could not remember)/, "memory"]
  ];
  function renderTrail() {
    var box = clear($("trail"));
    state.result.trail.forEach(function (t) {
      var kind = "step";
      for (var i = 0; i < KINDS.length; i++) if (KINDS[i][0].test(t)) { kind = KINDS[i][1]; break; }
      box.appendChild(h("li", null, h("span", { class: "k k-" + kind }, kind), h("span", { class: "txt" }, t)));
    });
  }

  function renderMeta(meta) {
    if (!meta) return;
    var m = $("mode");
    m.hidden = false;
    m.className = "mode" + (meta.live ? " live" : "");
    m.textContent = meta.live ? "Live adapters on" : "Offline dry run";
    var w = meta.wiring || {};
    $("foot").textContent = "wiring: " + Object.keys(w).map(function (k) { return k + "=" + w[k]; }).join("  ") +
      (meta.live ? "" : "  |  offline: no live or paid calls, no order is really placed");
  }

  /* ---------- boot ---------- */

  $("form").addEventListener("submit", runSearch);

  // Deep link for rehearsals: /?q=Fujifilm+X100V&budget=900&ship_to=US-CA&cond=good&run=1
  // run=1 only starts by itself offline: with live adapters a link must not fill a real cart without a click.
  var qs = new URLSearchParams(location.search);
  if (qs.get("q")) $("query").value = qs.get("q");
  if (qs.get("budget")) $("budget").value = qs.get("budget");
  if (qs.get("ship_to")) $("ship_to").value = qs.get("ship_to");
  if (qs.get("cond")) $("condition_min").value = qs.get("cond");
  fetch("/api/meta").then(function (r) { return r.json(); }).then(function (meta) {
    renderMeta(meta);
    if (qs.get("run") === "1" && !meta.live) runSearch();
  }).catch(function () {});
})();
