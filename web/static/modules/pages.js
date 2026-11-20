/* Module: secondary pages — Orders, Store Link-in-Bio, Connect Store */
(function (root) {
  "use strict";

  var STORES = [
    { slug: "tokyo-camera", name: "Tokyo Camera Archive", source: "https://linktr.ee/tokyo-camera-archive", q: "Fujifilm X100V", budget: 900 },
    { slug: "seiko-vintage", name: "Seiko Vintage Tokyo", source: "https://linktr.ee/seiko-vintage-jp", q: "Seiko SARB033", budget: 600 },
    { slug: "retro-handheld", name: "Akihabara Handhelds", source: "https://linktr.ee/akiba-handhelds", q: "Game Boy Micro", budget: 250 }
  ];

  function loadStores() {
    try {
      var extra = JSON.parse(localStorage.getItem("jsa-stores") || "[]");
      return STORES.concat(Array.isArray(extra) ? extra : []);
    } catch (e) { return STORES.slice(); }
  }
  function saveStore(store) {
    try {
      var extra = JSON.parse(localStorage.getItem("jsa-stores") || "[]");
      extra = (Array.isArray(extra) ? extra : []).filter(function (s) { return s.slug !== store.slug; });
      extra.unshift(store);
      localStorage.setItem("jsa-stores", JSON.stringify(extra.slice(0, 10)));
    } catch (e) {}
  }

  function renderOrders(state, handlers) {
    var ui = root.JSA.ui, box = ui.clear(ui.$("orders-list"));
    var cart = state.cart, r = state.result;

    if (cart) {
      var pick = r && r.picks ? r.picks.filter(function (p) { return p.listing.id === cart.listing_id; })[0] : null;
      box.appendChild(ui.h("div", { class: "section-kicker" }, "Active cart"));
      box.appendChild(ui.h("div", { class: "flat-row" },
        ui.icon(cart.status === "placed" ? "check-circle" : "shield-lock", 15),
        ui.h("div", { class: "row-main" },
          ui.h("div", { class: "row-title" }, pick ? pick.listing.title_en : (cart.listing_id || "Cart")),
          ui.h("div", { class: "row-meta" }, cart.status)),
        cart.status === "awaiting_approval"
          ? ui.h("button", { type: "button", class: "btn btn-primary btn-small", onclick: function (e) { handlers.onApprove(e.currentTarget); } }, ui.icon("check", 12), "Approve")
          : null));
    }

    var past = (r && r.preferences && r.preferences.past_purchases) || [];
    box.appendChild(ui.h("div", { class: "section-kicker" }, "Purchased (" + past.length + ")"));
    if (!past.length) {
      box.appendChild(ui.h("div", { class: "drawer-empty" }, "No orders placed yet"));
    } else {
      past.forEach(function (id) {
        box.appendChild(ui.h("div", { class: "flat-row" },
          ui.icon("check-circle", 14),
          ui.h("span", { class: "mono-text" }, id)));
      });
    }

    box.appendChild(ui.h("div", { class: "section-kicker" }, "Safety gate"));
    box.appendChild(ui.h("div", { class: "flat-row" },
      ui.icon("shield-check", 14),
      ui.h("span", null, "Single-use approval token enforced · Firedrill 26/26 held")));
  }

  function renderStore(slug, state, handlers) {
    var ui = root.JSA.ui;
    var stores = loadStores();
    var store = stores.filter(function (s) { return s.slug === slug; })[0] || stores[0];
    var head = ui.clear(ui.$("store-head"));
    var origin = (location.origin && location.origin !== "null") ? location.origin : "https://aws-shopping.local";
    var base = origin + "/s/" + store.slug;

    head.appendChild(ui.h("div", { class: "store-title-row" },
      ui.h("strong", null, store.name),
      ui.h("span", { class: "chip c-green" }, "MCP · UCP · ACP")));
    head.appendChild(ui.h("div", { class: "proto-strip" },
      [["MCP", base + "/mcp"], ["UCP", base + "/.well-known/ucp"], ["ACP", base + "/acp/checkout_sessions"]].map(function (ep) {
        return ui.h("button", {
          type: "button", class: "proto-pill", title: "Copy " + ep[1],
          onclick: function () { handlers.onCopy(ep[1], ep[0] + " URL copied"); }
        }, ep[0], ui.icon("copy", 12));
      }),
      ui.h("button", {
        type: "button", class: "btn btn-primary btn-small",
        onclick: function () { handlers.onRunStore(store); }
      }, ui.icon("search", 12), "Load catalog")));

    var list = ui.clear(ui.$("store-items"));
    var picks = (state.result && state.result.picks) || [];
    if (!picks.length) {
      list.appendChild(ui.h("div", { class: "none-found" }, "Click Load catalog to fetch landed-cost items."));
      return;
    }
    picks.forEach(function (p, i) {
      var l = p.listing;
      list.appendChild(ui.h("article", {
        class: "pick-row", style: "--i:" + i,
        onclick: function () { handlers.onOpenItem(i); }
      },
        ui.h("span", { class: "rank" }, "#" + (i + 1)),
        ui.h("div", { class: "row-main" },
          ui.h("div", { class: "row-title" }, l.title_en),
          ui.h("div", { class: "row-meta" }, l.condition + " · " + l.id)),
        ui.h("span", { class: "row-price" }, ui.usd(ui.totalOf(p.cost))),
        ui.icon("chevron-right", 14)));
    });
  }

  function initConnect(onCreated, onCopy) {
    var ui = root.JSA.ui;
    var form = ui.$("connect-form");
    var out = ui.$("connect-result");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var url = ui.$("connect-url").value.trim();
      var name = ui.$("connect-name").value.trim() || "Creator Store";
      var q = ui.$("connect-query").value.trim() || "Fujifilm X100V";
      var slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "store";
      var store = { slug: slug, name: name, source: url, q: q, budget: 900 };
      saveStore(store);

      var origin = (location.origin && location.origin !== "null") ? location.origin : "https://aws-shopping.local";
      var storeUrl = origin + "/s/" + slug;
      ui.clear(out);
      out.hidden = false;
      out.appendChild(ui.h("div", { class: "flat-row" },
        ui.icon("check-circle", 15),
        ui.h("div", { class: "row-main" },
          ui.h("div", { class: "row-title" }, store.name),
          ui.h("div", { class: "row-meta mono-text" }, storeUrl)),
        ui.h("button", { type: "button", class: "btn btn-ghost btn-icon", title: "Copy link", onclick: function () { onCopy(storeUrl, "Store link copied"); } }, ui.icon("copy", 14)),
        ui.h("button", { type: "button", class: "btn btn-primary btn-small", onclick: function () { onCreated(store); } }, "Open")));
    });
  }

  root.JSA = root.JSA || {};
  root.JSA.pages = { renderOrders: renderOrders, renderStore: renderStore, initConnect: initConnect, loadStores: loadStores };
})(window);
