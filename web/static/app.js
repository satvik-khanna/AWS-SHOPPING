/* Orchestrator: wires 6-page router, icons, menu, search, links, commerce, and secondary pages. */
(function (root) {
  "use strict";

  var ui = root.JSA.ui;
  var state = { result: null, cart: null, usedToken: null, gateEvent: null, busy: false, selectedIdx: 0, targetItemId: "", activeStoreSlug: "tokyo-camera" };

  function api(path, body) {
    return fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
        return data;
      });
    }, function () { throw new Error("Server unreachable"); });
  }

  function showError(msg) {
    var el = ui.$("error");
    el.hidden = !msg;
    el.textContent = msg || "";
  }

  function setBusy(on) {
    state.busy = on;
    ui.$("go").disabled = on;
    if (on) {
      ui.$("empty").hidden = true;
      ui.$("results").hidden = true;
      ui.$("skeleton").hidden = false;
    } else {
      ui.$("skeleton").hidden = true;
      if (state.result) ui.$("results").hidden = false;
      else ui.$("empty").hidden = false;
    }
  }

  function requestBody() {
    var r = state.result.request;
    return { query: r.query, budget: r.max_total_usd, ship_to: r.ship_to, user_id: r.user_id, condition_min: r.condition_min };
  }

  function copyText(text, label) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { ui.toast(label || "Copied"); }).catch(function () {});
    } else {
      ui.toast(label || "Copied");
    }
  }

  function renderAll() {
    var c = root.JSA.commerce, p = root.JSA.pages;
    if (state.result) {
      c.renderPrefs(state.result);
      c.renderTrail(state.result);
      c.renderPicks(state, searchMod.getFilter(), {
        onSelect: function (i) {
          state.selectedIdx = i;
          c.renderCost(state);
          routerMod.go("item");
        },
        onReject: rejectSeller,
        onCopyItemLink: function (listingId) {
          var r = state.result.request;
          linksMod.copyLink(linksMod.buildDeepLink({ q: r.query, budget: r.max_total_usd, ship_to: r.ship_to, cond: r.condition_min || "", item: listingId }));
        }
      });
      c.renderCost(state);
      c.renderGate(state, {
        onApprove: approve,
        onReject: rejectSeller,
        onReplay: replayToken,
        onCopyToken: function (tok) { copyText(tok, "Token copied"); }
      });
    }
    p.renderOrders(state, { onApprove: approve });
    p.renderStore(state.activeStoreSlug, state, {
      onCopy: copyText,
      onRunStore: function (store) {
        ui.$("query").value = store.q;
        ui.$("budget").value = store.budget;
        runSearch(null, true);
      },
      onOpenItem: function (i) {
        state.selectedIdx = i;
        c.renderCost(state);
        routerMod.go("item");
      }
    });
  }

  function show(result, stayOnPage) {
    if (agentMod.isAborted()) return;
    state.result = result;
    state.cart = result.cart;
    state.usedToken = null;
    state.gateEvent = null;
    state.selectedIdx = 0;
    var openItem = false;
    if (state.targetItemId && result.picks) {
      var idx = result.picks.findIndex(function (p) { return p.listing.id === state.targetItemId; });
      if (idx >= 0) { state.selectedIdx = idx; openItem = true; }
      state.targetItemId = "";
    }
    ui.$("empty").hidden = true;
    ui.$("skeleton").hidden = true;
    ui.$("results").hidden = false;
    renderAll();
    renderMeta(result.meta);
    var count = (result.picks && result.picks.length) || 0;
    agentMod.finish(result, count ? (count + " matches ready · nothing paid") : "No matches within budget");
    if (openItem) routerMod.go("item");
    else if (!stayOnPage) routerMod.go("shop");
  }

  function runSearch(ev, stayOnPage) {
    if (ev) ev.preventDefault();
    if (state.busy) return;
    showError("");
    var parsed = searchMod.parseQuery(ui.$("query").value, ui.$("budget").value);
    ui.$("budget").value = parsed.budget;
    var body = {
      query: parsed.query, budget: parsed.budget, ship_to: ui.$("ship_to").value,
      condition_min: ui.$("condition_min").value || null
    };
    searchMod.record(body.query, body.budget, body.ship_to);
    ui.$("user-turn").hidden = false;
    ui.$("user-bubble").textContent = body.query + " · $" + body.budget;
    if (!stayOnPage) routerMod.go("shop");
    setBusy(true);
    agentMod.start(body.query);
    api("/api/run", body).then(function (res) { show(res, stayOnPage); }, function (e) { agentMod.stop(); showError(e.message); }).then(function () { setBusy(false); });
  }

  function rejectSeller(seller) {
    if (state.busy || !state.result) return;
    showError("");
    var body = requestBody();
    body.seller = seller;
    routerMod.go("shop");
    setBusy(true);
    agentMod.start(body.query);
    api("/api/reject", body).then(function (res) { show(res, false); }, function (e) { agentMod.stop(); showError(e.message); }).then(function () { setBusy(false); });
  }

  function approve(btn) {
    var token = state.cart && state.cart.approval_token;
    if (!token || state.busy) return;
    showError("");
    btn.disabled = true;
    api("/api/approve", { token: token }).then(function (r) {
      state.usedToken = token;
      r.cart.listing_id = r.cart.listing_id || state.cart.listing_id;
      state.cart = r.cart;
      state.result.trail = state.result.trail.concat(r.trail || []);
      renderAll();
      agentMod.updateCart(state.result);
      if (r.cart.status === "placed") agentMod.notify("Order placed");
    }, function (e) { showError(e.message); btn.disabled = false; });
  }

  function replayToken(btn) {
    if (!state.usedToken) return;
    btn.disabled = true;
    api("/api/approve", { token: state.usedToken }).then(function (r) {
      state.gateEvent = "Replay: " + r.cart.status;
      state.result.trail = state.result.trail.concat(r.trail || []);
      if (r.cart.status === "placed") state.cart = r.cart;
      renderAll();
    }, function (e) { showError(e.message); btn.disabled = false; });
  }

  function renderMeta(meta) {
    if (!meta) return;
    var m = ui.clear(ui.$("mode"));
    m.hidden = false;
    m.className = "mode" + (meta.live ? " live" : "");
    m.setAttribute("title", meta.live ? "Live adapters" : "Offline dry run");
    ui.append(m, [ui.icon(meta.live ? "globe" : "shield-check", 14), meta.live ? "Live" : "Dry"]);
  }

  // Init modules
  var agentMod = root.JSA.agent.init(function () { setBusy(false); });
  var menuMod = root.JSA.menu.init({});
  var routerMod = root.JSA.router.init(function (page) {
    menuMod.close();
    if (page === "orders" || page === "store") renderAll();
  });
  var searchMod = root.JSA.search.init(function () { menuMod.close(); runSearch(); });
  searchMod.onFilterChange(function () { if (state.result) renderAll(); });

  root.JSA.pages.initConnect(function (store) {
    state.activeStoreSlug = store.slug;
    ui.$("query").value = store.q;
    ui.$("budget").value = store.budget;
    routerMod.go("store");
    runSearch(null, true);
  }, copyText);

  var linksMod = root.JSA.links.init(function (target) {
    if (target.q) ui.$("query").value = target.q;
    if (target.budget) ui.$("budget").value = target.budget;
    if (target.ship_to) ui.$("ship_to").value = target.ship_to;
    if (target.cond !== undefined) ui.$("condition_min").value = target.cond;
    state.targetItemId = target.item || "";
    menuMod.close();
    runSearch();
  }, function () {
    var pick = state.result && state.result.picks && state.result.picks[state.selectedIdx];
    return { selectedListingId: pick ? pick.listing.id : "" };
  });

  ui.$("form").addEventListener("submit", function (e) { runSearch(e, false); });
  document.querySelectorAll(".example-chip").forEach(function (btn) {
    btn.addEventListener("click", function () {
      ui.$("query").value = btn.getAttribute("data-q") || "";
      ui.$("budget").value = btn.getAttribute("data-budget") || "900";
      runSearch();
    });
  });

  if (linksMod.initial) {
    if (linksMod.initial.q) ui.$("query").value = linksMod.initial.q;
    if (linksMod.initial.budget) ui.$("budget").value = linksMod.initial.budget;
    if (linksMod.initial.ship_to) ui.$("ship_to").value = linksMod.initial.ship_to;
    if (linksMod.initial.cond) ui.$("condition_min").value = linksMod.initial.cond;
    state.targetItemId = linksMod.initial.item || "";
  }

  renderAll();
  fetch("/api/meta").then(function (r) { return r.json(); }).then(function (meta) {
    renderMeta(meta);
    if (linksMod.initial && linksMod.initial.run && !meta.live) runSearch();
  }).catch(function () {});
})(window);
