/* Module: search bar, instant filter, natural-language query parser, and recent history */
(function (root) {
  "use strict";

  var HISTORY_KEY = "jsa-search-history";

  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]").slice(0, 6); }
    catch (e) { return []; }
  }
  function saveHistory(entry) {
    var list = loadHistory().filter(function (x) { return x.q !== entry.q || String(x.budget) !== String(entry.budget); });
    list.unshift(entry);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, 6))); } catch (e) {}
    return list.slice(0, 6);
  }

  function parseQuery(rawQuery, fallbackBudget) {
    var q = String(rawQuery || "").trim();
    var budget = fallbackBudget;
    var m = q.match(/\$\s*(\d+(?:\.\d+)?)/);
    if (m) budget = m[1];
    return { query: q, budget: budget };
  }

  function initSearch(onSubmit) {
    var ui = root.JSA.ui;
    var filterInput = ui.$("quick-filter");

    function renderHistory() {
      var box = ui.$("history-list");
      if (!box) return;
      ui.clear(box);
      var items = loadHistory();
      if (!items.length) {
        box.appendChild(ui.h("li", { class: "drawer-empty" }, "No searches yet"));
        return;
      }
      items.forEach(function (it) {
        box.appendChild(ui.h("li", null,
          ui.h("button", {
            type: "button", class: "drawer-item",
            onclick: function () {
              ui.$("query").value = it.q;
              ui.$("budget").value = it.budget;
              if (it.ship_to) ui.$("ship_to").value = it.ship_to;
              onSubmit();
            }
          }, ui.icon("history", 14), ui.h("span", { class: "drawer-item-text" }, it.q), ui.h("span", { class: "drawer-item-meta" }, "$" + it.budget))));
      });
    }

    // Header search icon focuses input; `/` or Cmd/Ctrl+K focuses search
    var focusBtn = ui.$("search-focus");
    if (focusBtn) {
      focusBtn.addEventListener("click", function () { ui.$("query").focus(); ui.$("query").select(); });
    }
    document.addEventListener("keydown", function (e) {
      var modK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      var slash = e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey;
      if (modK || slash) {
        var tag = (document.activeElement && document.activeElement.tagName) || "";
        if (modK || (tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT")) {
          e.preventDefault();
          ui.$("query").focus();
          ui.$("query").select();
        }
      }
    });

    renderHistory();

    return {
      parseQuery: parseQuery,
      record: function (q, budget, shipTo) { saveHistory({ q: q, budget: budget, ship_to: shipTo }); renderHistory(); },
      getFilter: function () { return filterInput ? filterInput.value.trim().toLowerCase() : ""; },
      onFilterChange: function (fn) { if (filterInput) filterInput.addEventListener("input", fn); }
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.search = { init: initSearch };
})(window);
