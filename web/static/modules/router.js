/* Module: client-side page router (shop / item / orders / store / connect / memory) */
(function (root) {
  "use strict";

  var PAGES = ["shop", "item", "orders", "store", "connect", "memory", "vault"];
  var TITLES = {
    shop: "Shopping Agent",
    item: "Item detail",
    orders: "Orders",
    store: "Store link",
    connect: "Add link",
    memory: "Memory & trace",
    vault: "User vault"
  };

  function initRouter(onChange) {
    var ui = root.JSA.ui;
    var current = "shop";

    function go(page, payload) {
      if (PAGES.indexOf(page) === -1) page = "shop";
      current = page;
      PAGES.forEach(function (p) {
        var el = ui.$("page-" + p);
        if (el) el.hidden = (p !== page);
      });
      var titleEl = ui.$("page-title");
      if (titleEl) titleEl.textContent = TITLES[page] || "Shopping Agent";
      var backBtn = ui.$("nav-back");
      if (backBtn) backBtn.hidden = (page === "shop");
      document.querySelectorAll("[data-nav-page]").forEach(function (btn) {
        btn.classList.toggle("active", btn.getAttribute("data-nav-page") === page);
      });
      if (onChange) onChange(page, payload);
    }

    var backBtn = ui.$("nav-back");
    if (backBtn) backBtn.addEventListener("click", function () { go("shop"); });

    document.querySelectorAll("[data-nav-page]").forEach(function (btn) {
      btn.addEventListener("click", function () { go(btn.getAttribute("data-nav-page")); });
    });

    return { go: go, current: function () { return current; } };
  }

  root.JSA = root.JSA || {};
  root.JSA.router = { init: initRouter };
})(window);
