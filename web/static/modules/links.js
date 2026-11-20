/* Module: deep-link builder, incoming link jump (?q=...&item=...&store=...), and URL jump */
(function (root) {
  "use strict";

  var STORE_PRESETS = {
    "tokyo-camera": { q: "Fujifilm X100V", budget: 900, ship_to: "US-CA" },
    "seiko-vintage": { q: "Seiko SARB033", budget: 600, ship_to: "US-CA" },
    "retro-handheld": { q: "Game Boy Micro", budget: 250, ship_to: "US-CA" }
  };

  function buildDeepLink(params) {
    var base = location.origin && location.origin !== "null"
      ? (location.origin + location.pathname)
      : location.href.split("?")[0].split("#")[0];
    var qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.budget) qs.set("budget", String(params.budget));
    if (params.ship_to) qs.set("ship_to", params.ship_to);
    if (params.cond) qs.set("cond", params.cond);
    if (params.item) qs.set("item", params.item);
    qs.set("run", "1");
    return base + "?" + qs.toString();
  }

  function parseIncomingLink(raw) {
    var s = String(raw || "").trim();
    if (!s) return null;
    // Direct store slug preset
    var slugMatch = s.match(/(?:\/s\/|^)([a-z0-9-]+)$/i);
    if (slugMatch && STORE_PRESETS[slugMatch[1].toLowerCase()]) {
      return STORE_PRESETS[slugMatch[1].toLowerCase()];
    }
    // Deep link URL with query params
    try {
      var u = new URL(s);
      var p = u.searchParams;
      if (p.get("q")) {
        return { q: p.get("q"), budget: p.get("budget") || 900, ship_to: p.get("ship_to") || "US-CA", cond: p.get("cond") || "", item: p.get("item") || "" };
      }
      // Mercari / Buyee / Linktree URL -> extract readable slug as search query
      var seg = u.pathname.split("/").filter(Boolean).pop() || u.hostname;
      return { q: seg.replace(/[-_]+/g, " "), budget: 900, ship_to: "US-CA" };
    } catch (e) {
      return { q: s, budget: 900, ship_to: "US-CA" };
    }
  }

  function copyLink(url) {
    var ui = root.JSA.ui;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(function () { ui.toast("Link copied"); }).catch(function () {});
    } else {
      ui.toast(url);
    }
  }

  function initLinks(onJump, getState) {
    var ui = root.JSA.ui;

    // Header share-current-link icon button
    var shareBtn = ui.$("share-link");
    if (shareBtn) {
      shareBtn.addEventListener("click", function () {
        var st = getState();
        var item = st.selectedListingId || "";
        var url = buildDeepLink({
          q: ui.$("query").value.trim(),
          budget: ui.$("budget").value,
          ship_to: ui.$("ship_to").value,
          cond: ui.$("condition_min").value,
          item: item
        });
        copyLink(url);
      });
    }

    // Drawer: Jump from URL / Store Link form
    var jumpForm = ui.$("link-jump-form");
    var jumpInput = ui.$("link-jump-input");
    if (jumpForm && jumpInput) {
      jumpForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var target = parseIncomingLink(jumpInput.value);
        if (!target) return;
        jumpInput.value = "";
        onJump(target);
      });
    }

    // Preset store links in drawer
    document.querySelectorAll("[data-store-slug]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var slug = btn.getAttribute("data-store-slug");
        if (STORE_PRESETS[slug]) onJump(STORE_PRESETS[slug]);
      });
    });

    // Parse current URL query/hash on boot
    var qs = new URLSearchParams(location.search);
    var hash = new URLSearchParams((location.hash || "").replace(/^#/, ""));
    var storeSlug = qs.get("store") || hash.get("store");
    var initial = null;
    if (storeSlug && STORE_PRESETS[storeSlug]) {
      initial = Object.assign({ run: true }, STORE_PRESETS[storeSlug]);
    } else if (qs.get("q")) {
      initial = {
        q: qs.get("q"),
        budget: qs.get("budget") || "",
        ship_to: qs.get("ship_to") || "",
        cond: qs.get("cond") || "",
        item: qs.get("item") || hash.get("item") || "",
        run: qs.get("run") === "1"
      };
    }

    return { buildDeepLink: buildDeepLink, copyLink: copyLink, initial: initial };
  }

  root.JSA = root.JSA || {};
  root.JSA.links = { init: initLinks };
})(window);
