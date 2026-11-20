/* Japan Shopping Agent demo screen. Plain JS, no external dependencies.
   Icons: GitHub Primer Octicons v19.42.0 (MIT License), 16px SVG paths.
   All listing / memory text is untrusted: it only ever goes through textContent. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var state = { result: null, cart: null, usedToken: null, gateEvent: null, busy: false };

  var CONDITION = { "new": "New", like_new: "Like new", good: "Good", fair: "Fair", junk: "Junk" };
  var SOURCE = { mercari: "Mercari", yahoo_auctions: "Yahoo! Auctions", other: "Independent shop" };

  // Official @primer/octicons v19.42.0 16px paths (MIT)
  var OCTICONS = {
    "shield-check": ["m8.533.133 5.25 1.68A1.75 1.75 0 0 1 15 3.48V7c0 1.566-.32 3.182-1.303 4.682-.983 1.498-2.585 2.813-5.032 3.855a1.697 1.697 0 0 1-1.33 0c-2.447-1.042-4.049-2.357-5.032-3.855C1.32 10.182 1 8.566 1 7V3.48a1.75 1.75 0 0 1 1.217-1.667l5.25-1.68a1.748 1.748 0 0 1 1.066 0Zm-.61 1.429.001.001-5.25 1.68a.251.251 0 0 0-.174.237V7c0 1.36.275 2.666 1.057 3.859.784 1.194 2.121 2.342 4.366 3.298a.196.196 0 0 0 .154 0c2.245-.957 3.582-2.103 4.366-3.297C13.225 9.666 13.5 8.358 13.5 7V3.48a.25.25 0 0 0-.174-.238l-5.25-1.68a.25.25 0 0 0-.153 0ZM11.28 6.28l-3.5 3.5a.75.75 0 0 1-1.06 0l-1.5-1.5a.749.749 0 0 1 .326-1.275.749.749 0 0 1 .734.215l.97.97 2.97-2.97a.751.751 0 0 1 1.042.018.751.751 0 0 1 .018 1.042Z"],
    "shield-lock": ["m8.533.133 5.25 1.68A1.75 1.75 0 0 1 15 3.48V7c0 1.566-.32 3.182-1.303 4.682-.983 1.498-2.585 2.813-5.032 3.855a1.697 1.697 0 0 1-1.33 0c-2.447-1.042-4.049-2.357-5.032-3.855C1.32 10.182 1 8.566 1 7V3.48a1.75 1.75 0 0 1 1.217-1.667l5.25-1.68a1.748 1.748 0 0 1 1.066 0Zm-.61 1.429.001.001-5.25 1.68a.251.251 0 0 0-.174.237V7c0 1.36.275 2.666 1.057 3.859.784 1.194 2.121 2.342 4.366 3.298a.196.196 0 0 0 .154 0c2.245-.957 3.582-2.103 4.366-3.297C13.225 9.666 13.5 8.358 13.5 7V3.48a.25.25 0 0 0-.174-.238l-5.25-1.68a.25.25 0 0 0-.153 0ZM9.5 6.5c0 .536-.286 1.032-.75 1.3v2.45a.75.75 0 0 1-1.5 0V7.8A1.5 1.5 0 1 1 9.5 6.5Z"],
    "check-circle": ["M0 8a8 8 0 1 1 16 0A8 8 0 0 1 0 8Zm1.5 0a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Zm10.28-1.72-4.5 4.5a.75.75 0 0 1-1.06 0l-2-2a.751.751 0 0 1 .018-1.042.751.751 0 0 1 1.042-.018l1.47 1.47 3.97-3.97a.751.751 0 0 1 1.042.018.751.751 0 0 1 .018 1.042Z"],
    "x-circle": ["M2.344 2.343h-.001a8 8 0 0 1 11.314 11.314A8.002 8.002 0 0 1 .234 10.089a8 8 0 0 1 2.11-7.746Zm1.06 10.253a6.5 6.5 0 1 0 9.108-9.275 6.5 6.5 0 0 0-9.108 9.275ZM6.03 4.97 8 6.94l1.97-1.97a.749.749 0 0 1 1.275.326.749.749 0 0 1-.215.734L9.06 8l1.97 1.97a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215L8 9.06l-1.97 1.97a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734L6.94 8 4.97 6.03a.751.751 0 0 1 .018-1.042.751.751 0 0 1 1.042-.018Z"],
    "link-external": ["M3.75 2h3.5a.75.75 0 0 1 0 1.5h-3.5a.25.25 0 0 0-.25.25v8.5c0 .138.112.25.25.25h8.5a.25.25 0 0 0 .25-.25v-3.5a.75.75 0 0 1 1.5 0v3.5A1.75 1.75 0 0 1 12.25 14h-8.5A1.75 1.75 0 0 1 2 12.25v-8.5C2 2.784 2.784 2 3.75 2Zm6.854-1h4.146a.25.25 0 0 1 .25.25v4.146a.25.25 0 0 1-.427.177L13.03 4.03 9.28 7.78a.751.751 0 0 1-1.042-.018.751.751 0 0 1-.018-1.042l3.75-3.75-1.543-1.543A.25.25 0 0 1 10.604 1Z"],
    "blocked": ["M4.467.22a.749.749 0 0 1 .53-.22h6.006c.199 0 .389.079.53.22l4.247 4.247c.141.14.22.331.22.53v6.006a.749.749 0 0 1-.22.53l-4.247 4.247a.749.749 0 0 1-.53.22H4.997a.749.749 0 0 1-.53-.22L.22 11.533a.749.749 0 0 1-.22-.53V4.997c0-.199.079-.389.22-.53Zm.84 1.28L1.5 5.308v5.384L5.308 14.5h5.384l3.808-3.808V5.308L10.692 1.5ZM4 7.75A.75.75 0 0 1 4.75 7h6.5a.75.75 0 0 1 0 1.5h-6.5A.75.75 0 0 1 4 7.75Z"],
    "check": ["M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.751.751 0 0 1 .018-1.042.751.751 0 0 1 1.042-.018L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z"],
    "globe": ["M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0ZM5.78 8.75a9.64 9.64 0 0 0 1.363 4.177c.255.426.542.832.857 1.215.245-.296.551-.705.857-1.215A9.64 9.64 0 0 0 10.22 8.75Zm4.44-1.5a9.64 9.64 0 0 0-1.363-4.177c-.307-.51-.612-.919-.857-1.215a9.927 9.927 0 0 0-.857 1.215A9.64 9.64 0 0 0 5.78 7.25Zm-5.944 1.5H1.543a6.507 6.507 0 0 0 4.666 5.5c-.123-.181-.24-.365-.352-.552-.715-1.192-1.437-2.874-1.581-4.948Zm-2.733-1.5h2.733c.144-2.074.866-3.756 1.58-4.948.12-.197.237-.381.353-.552a6.507 6.507 0 0 0-4.666 5.5Zm10.181 1.5c-.144 2.074-.866 3.756-1.58 4.948-.12.197-.237.381-.353.552a6.507 6.507 0 0 0 4.666-5.5Zm2.733-1.5a6.507 6.507 0 0 0-4.666-5.5c.123.181.24.365.353.552.714 1.192 1.436 2.874 1.58 4.948Z"],
    "inbox": ["M2.8 2.06A1.75 1.75 0 0 1 4.41 1h7.18c.7 0 1.333.417 1.61 1.06l2.74 6.395c.04.093.06.194.06.295v4.5A1.75 1.75 0 0 1 14.25 15H1.75A1.75 1.75 0 0 1 0 13.25v-4.5c0-.101.02-.202.06-.295Zm1.61.44a.25.25 0 0 0-.23.152L1.887 8H4.75a.75.75 0 0 1 .6.3L6.625 10h2.75l1.275-1.7a.75.75 0 0 1 .6-.3h2.863L11.82 2.652a.25.25 0 0 0-.23-.152Zm10.09 7h-2.875l-1.275 1.7a.75.75 0 0 1-.6.3h-3.5a.75.75 0 0 1-.6-.3L4.375 9.5H1.5v3.75c0 .138.112.25.25.25h12.5a.25.25 0 0 0 .25-.25Z"],
    "sync": ["M1.705 8.005a.75.75 0 0 1 .834.656 5.5 5.5 0 0 0 9.592 2.97l-1.204-1.204a.25.25 0 0 1 .177-.427h3.646a.25.25 0 0 1 .25.25v3.646a.25.25 0 0 1-.427.177l-1.38-1.38A7.002 7.002 0 0 1 1.05 8.84a.75.75 0 0 1 .656-.834ZM8 2.5a5.487 5.487 0 0 0-4.131 1.869l1.204 1.204A.25.25 0 0 1 4.896 6H1.25A.25.25 0 0 1 1 5.75V2.104a.25.25 0 0 1 .427-.177l1.38 1.38A7.002 7.002 0 0 1 14.95 7.16a.75.75 0 0 1-1.49.178A5.5 5.5 0 0 0 8 2.5Z"],
    "package": ["m8.878.392 5.25 3.045c.54.314.872.89.872 1.514v6.098a1.75 1.75 0 0 1-.872 1.514l-5.25 3.045a1.75 1.75 0 0 1-1.756 0l-5.25-3.045A1.75 1.75 0 0 1 1 11.049V4.951c0-.624.332-1.201.872-1.514L7.122.392a1.75 1.75 0 0 1 1.756 0ZM7.875 1.69l-4.63 2.685L8 7.133l4.755-2.758-4.63-2.685a.248.248 0 0 0-.25 0ZM2.5 5.677v5.372c0 .09.047.171.125.216l4.625 2.683V8.432Zm6.25 8.271 4.625-2.683a.25.25 0 0 0 .125-.216V5.677L8.75 8.432Z"],
    "sun": ["M8 12a4 4 0 1 1 0-8 4 4 0 0 1 0 8Zm0-1.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Zm5.657-8.157a.75.75 0 0 1 0 1.061l-1.061 1.06a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734l1.06-1.06a.75.75 0 0 1 1.06 0Zm-9.193 9.193a.75.75 0 0 1 0 1.06l-1.06 1.061a.75.75 0 1 1-1.061-1.06l1.06-1.061a.75.75 0 0 1 1.061 0ZM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0ZM3 8a.75.75 0 0 1-.75.75H.75a.75.75 0 0 1 0-1.5h1.5A.75.75 0 0 1 3 8Zm13 0a.75.75 0 0 1-.75.75h-1.5a.75.75 0 0 1 0-1.5h1.5A.75.75 0 0 1 16 8Zm-8 5a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 13Zm3.536-1.464a.75.75 0 0 1 1.06 0l1.061 1.06a.75.75 0 0 1-1.06 1.061l-1.061-1.06a.75.75 0 0 1 0-1.061ZM2.343 2.343a.75.75 0 0 1 1.061 0l1.06 1.061a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-1.06-1.06a.75.75 0 0 1 0-1.06Z"],
    "moon": ["M9.598 1.591a.749.749 0 0 1 .785-.175 7.001 7.001 0 1 1-8.967 8.967.75.75 0 0 1 .961-.96 5.5 5.5 0 0 0 7.046-7.046.75.75 0 0 1 .175-.786Zm1.616 1.945a7 7 0 0 1-7.678 7.678 5.499 5.499 0 1 0 7.678-7.678Z"],
    "copy": ["M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Z", "M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"]
  };

  function icon(name, size) {
    var sz = size || 16;
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "octicon");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("width", String(sz));
    svg.setAttribute("height", String(sz));
    svg.setAttribute("aria-hidden", "true");
    (OCTICONS[name] || []).forEach(function (d) {
      var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      svg.appendChild(p);
    });
    return svg;
  }

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
        if (!res.ok) throw new Error(data.error || ("Server returned " + res.status));
        return data;
      });
    }, function () {
      throw new Error("Cannot reach server. Check that `python3 -m web` is running.");
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
    $("go-label").textContent = on ? (label || "Searching\u2026") : "Search";
    if (on) {
      if (!state.result) {
        $("empty").hidden = true;
        $("skeleton").hidden = false;
      } else {
        $("results").classList.add("stale");
      }
    } else {
      $("skeleton").hidden = true;
      $("results").classList.remove("stale");
      if (!state.result) $("empty").hidden = false;
    }
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
    setBusy(true, "Updating\u2026");
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
      clear(btn);
      append(btn, [icon("check", 14), "Approve and place order"]);
    });
  }

  function replayToken(btn) {
    if (!state.usedToken) return;
    btn.disabled = true;
    api("/api/approve", { token: state.usedToken }).then(function (r) {
      state.gateEvent = "Replay of used token: " + r.cart.status + (r.cart.detail ? " (" + r.cart.detail + ")" : "");
      state.result.trail = state.result.trail.concat(r.trail || []);
      if (r.cart.status === "placed") state.cart = r.cart;
      renderAll();
    }, function (e) { showError(e.message); btn.disabled = false; });
  }

  function show(result) {
    state.result = result;
    state.cart = result.cart;
    state.usedToken = null;
    state.gateEvent = null;
    $("empty").hidden = true;
    $("skeleton").hidden = true;
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
      ["Condition", p.condition_min ? [chip(CONDITION[p.condition_min] || p.condition_min, "c-ink")] : []],
      ["Sizes", sizes.map(function (k) { return chip(k + " " + p.sizes[k], "c-ink"); })],
      ["Rejected", (p.rejected_sellers || []).map(function (s) { return chip(s, "c-red struck"); })],
      ["Purchased", (p.past_purchases || []).map(function (s) { return chip(s, "c-green"); })],
      ["Notes", (p.notes || []).map(function (s) { return chip(s); })]
    ];
    var known = groups.some(function (g) { return g[1].length; });
    if (!known) {
      box.appendChild(h("span", { class: "prefs-empty" },
        "No saved preferences for ", h("strong", null, p.user_id), "."));
      return;
    }
    box.appendChild(h("dl", { class: "pref" }, h("dt", null, "User"), h("dd", null, p.user_id)));
    groups.forEach(function (g) {
      if (!g[1].length) return;
      box.appendChild(h("dl", { class: "pref" }, h("dt", null, g[0]), h("dd", null, g[1])));
    });
  }

  function citeNode(c) {
    c = c || {};
    var url = c.url && safeUrl(c.url);
    var dated = /^\d{4}-\d{2}-\d{2}$/.test(c.as_of || "");
    var src = c.source || "no source";
    return h("div", { class: "cite" },
      url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "src" }, src) : h("span", { class: "src" }, src),
      h("span", { class: "asof" + (dated ? "" : " warn"), title: dated ? "Rule date" : "Undated value" },
        c.as_of || "undated"));
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
      chip(CONDITION[l.condition] || l.condition, "c-ink"),
      chip(SOURCE[l.source] || l.source),
      chip(l.seller),
      chip(l.ships_abroad ? "Ships abroad" : "Via proxy", l.ships_abroad ? "c-green" : "c-amber")
    ];
    var buyeeUrl = null;
    Object.keys(l.fields || {}).forEach(function (k) {
      var val = l.fields[k];
      if (k === "buyee_url") { buyeeUrl = safeUrl(String(val || "")); return; }
      if (/^https?:\/\//i.test(String(val || ""))) return;
      chips.push(chip(k.replace(/_/g, " ") + ": " + val));
    });

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
          inCart ? h("span", { class: "tag-cart" + (placed ? " placed" : "") },
            icon(placed ? "check-circle" : "package", 12), placed ? "Ordered" : "In cart") : null),
        h("h3", null, l.title_en),
        h("p", { class: "ja", lang: "ja" }, l.title_ja),
        h("div", { class: "chips" }, chips)),
      h("div", { class: "total" },
        h("div", { class: "total-top" },
          h("span", { class: "total-label" }, "Landed to " + req.ship_to),
          h("span", { class: "total-sub" },
            room >= 0 ? h("span", { class: "under" }, usd(room) + " under") : h("span", { class: "over" }, usd(-room) + " over"),
            " " + usd0(budget))),
        h("div", { class: "total-num" }, usd(total)),
        bar,
        h("div", { class: "bar-cap" }, h("span", null, "$0"), h("span", null, usd0(budget)))),
      lines,
      h("div", { class: "fx" }, "FX " + cost.fx_jpy_per_usd + " JPY/USD"),
      h("p", { class: "why" }, h("b", null, "Rank " + Number(pick.score).toFixed(2)), pick.reason),
      h("div", { class: "pick-actions" },
        h("div", { class: "pick-links" },
          url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer", class: "ext-link" },
            "Open listing", icon("link-external", 14)) : h("span", { class: "step-sub" }, "No listing link"),
          buyeeUrl ? h("a", { href: buyeeUrl, target: "_blank", rel: "noopener noreferrer", class: "ext-link" },
            "Buyee", icon("link-external", 14)) : null),
        h("button", { type: "button", class: "btn btn-small btn-danger", onclick: function () { rejectSeller(l.seller); } },
          icon("blocked", 14), "Reject seller")));
  }

  function renderPicks() {
    var r = state.result, box = clear($("picks"));
    var cartId = state.cart && state.cart.listing_id;
    $("picks-sub").textContent = r.picks.length
      ? r.picks.length + " within " + usd0(r.request.max_total_usd)
      : "0 matches";
    if (!r.picks.length) {
      box.appendChild(h("div", { class: "none-found" },
        h("strong", null, "No listing within " + usd0(r.request.max_total_usd)),
        "Raise the budget or lower the minimum condition."));
    }
    r.picks.forEach(function (p, i) { box.appendChild(pickCard(p, i, p.listing.id === cartId)); });

    var skipped = r.trail.map(function (t) { return /^skip (\S+): (.*)$/.exec(t); }).filter(Boolean);
    var sk = clear($("skipped"));
    sk.hidden = !skipped.length;
    if (skipped.length) {
      sk.appendChild(h("h3", null, "Filtered out (" + skipped.length + ")"));
      sk.appendChild(h("ul", null, skipped.map(function (m) {
        var budget = /budget/i.test(m[2]);
        return h("li", null, chip(budget ? "Over budget" : "Blocked", budget ? "c-amber" : "c-red"), h("code", null, m[1]), m[2]);
      })));
    }
  }

  var GATE_ICONS = { awaiting: "shield-lock", placed: "check-circle", stopped: "x-circle", idle: "inbox" };

  function gateShell(kind, kicker, note, item, total, actions) {
    var g = $("gate");
    clear(g).className = "gate " + kind;
    g.appendChild(h("div", { class: "gate-body" },
      h("div", { class: "gate-state" },
        h("div", { class: "gate-icon", "aria-hidden": "true" }, icon(GATE_ICONS[kind] || "inbox", 16)),
        h("div", null, h("div", { class: "gate-kicker" }, kicker), h("div", { class: "gate-note" }, note))),
      h("div", { class: "gate-item" }, item),
      total === null ? h("div") : h("div", { class: "gate-total" }, h("div", { class: "k" }, "Total landed"), h("div", { class: "v" }, usd(total))),
      h("div", { class: "gate-actions" }, actions)));
  }

  function renderGate() {
    var r = state.result, cart = state.cart;
    if (!cart) {
      gateShell("idle", "No cart created", "No listing fit within budget.", null, null, null);
      return;
    }
    var pick = r.picks.filter(function (p) { return p.listing.id === cart.listing_id; })[0] || null;
    var total = pick ? totalOf(pick.cost) : null;
    var item = [
      pick ? h("div", { class: "t" }, pick.listing.title_en) : null,
      cart.detail ? h("div", { class: "d" }, cart.detail) : null
    ];
    var cartUrl = cart.cart_url && safeUrl(cart.cart_url);
    if (cartUrl) {
      item.push(h("div", { class: "d" },
        h("a", { href: cartUrl, target: "_blank", rel: "noopener noreferrer", class: "ext-link" },
          "Open filled cart", icon("link-external", 12))));
    }

    if (cart.status === "awaiting_approval") {
      var copyBtn = h("button", {
        type: "button", class: "btn btn-ghost btn-copy", "aria-label": "Copy approval token",
        onclick: function () {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(cart.approval_token).then(function () {
              clear(copyBtn); append(copyBtn, [icon("check", 12), "Copied"]);
              setTimeout(function () { clear(copyBtn); append(copyBtn, [icon("copy", 12), "Copy"]); }, 1400);
            }).catch(function () {});
          }
        }
      }, icon("copy", 12), "Copy");
      item.push(h("div", { class: "tok-row" }, h("span", { class: "tok" }, "token " + cart.approval_token), copyBtn));
      gateShell("awaiting", "Awaiting approval", "Cart filled. Nothing paid.", item, total, [
        h("button", { type: "button", class: "btn btn-primary", id: "approve", onclick: function (e) { approve(e.currentTarget); } },
          icon("check", 14), "Approve and place order"),
        pick ? h("button", { type: "button", class: "btn btn-small btn-danger", id: "reject", onclick: function () { rejectSeller(pick.listing.seller); } },
          icon("blocked", 14), "Reject " + pick.listing.seller) : null
      ]);
    } else if (cart.status === "placed") {
      item.push(h("div", { class: "tok" }, "token consumed (single-use)"));
      if (state.gateEvent) item.push(h("div", { class: "ev" }, state.gateEvent));
      gateShell("placed", "Order placed", "Approved. Token cannot be reused.", item, total, [
        state.usedToken ? h("button", { type: "button", class: "btn btn-small", id: "replay", onclick: function (e) { replayToken(e.currentTarget); } },
          icon("sync", 14), "Test token replay") : null
      ]);
    } else {
      gateShell("stopped", cart.status === "failed" ? "Checkout failed" : "Blocked", "Nothing paid.", item, total, null);
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
    var m = clear($("mode"));
    m.hidden = false;
    m.className = "mode" + (meta.live ? " live" : "");
    append(m, [icon(meta.live ? "globe" : "shield-check", 14), meta.live ? "Live" : "Offline dry run"]);
    var w = meta.wiring || {};
    $("foot").textContent = Object.keys(w).map(function (k) { return k + "=" + w[k]; }).join(" · ") +
      (meta.live ? "" : " · offline dry run");
  }

  /* ---------- theme & keyboard ---------- */

  function currentTheme() {
    var saved = null;
    try { saved = localStorage.getItem("jsa-theme"); } catch (e) {}
    if (saved === "light" || saved === "dark") return saved;
    return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
  }
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var btn = $("theme-toggle");
    if (btn) {
      clear(btn);
      btn.appendChild(icon(theme === "dark" ? "sun" : "moon", 16));
      btn.setAttribute("title", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
    }
  }
  applyTheme(currentTheme());
  var themeBtn = $("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem("jsa-theme", next); } catch (e) {}
      applyTheme(next);
    });
  }

  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
      var tag = (document.activeElement && document.activeElement.tagName) || "";
      if (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
        e.preventDefault();
        $("query").focus();
        $("query").select();
      }
    }
  });

  /* ---------- boot ---------- */

  $("form").addEventListener("submit", runSearch);
  document.querySelectorAll(".example-chip").forEach(function (btn) {
    btn.addEventListener("click", function () {
      $("query").value = btn.getAttribute("data-q") || "";
      $("budget").value = btn.getAttribute("data-budget") || "900";
      runSearch();
    });
  });

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
