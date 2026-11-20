/* Module: Agent's browser — opens the REAL destination website (via don-tacos dt-ax Chrome Computer Use
   when server is running, or real browser tab via window.open in standalone file:// mode) and displays
   live URL, real Chrome accessibility controls, and checkout readiness. */
(function (root) {
  "use strict";

  var STEPS = [
    "Open real site",
    "Vault session",
    "Inspect page",
    "Cart ready",
    "Swipe to pay"
  ];

  function resolveRealUrl(listing) {
    var ui = root.JSA.ui;
    var buyee = listing && listing.fields && listing.fields.buyee_url;
    return ui.safeUrl(buyee) || ui.safeUrl(listing && listing.url) || "https://buyee.jp/";
  }

  function initAgent(onStop) {
    var ui = root.JSA.ui;
    var ring = ui.$("composer-ring");
    var strip = ui.$("agent-plan");
    var stepsEl = ui.$("plan-steps");
    var dot = ui.$("plan-dot");
    var elapsedEl = ui.$("plan-elapsed");
    var stopBtn = ui.$("plan-stop");
    var toggleBtn = ui.$("browser-toggle");
    var browserWrap = ui.$("agent-browser");
    var urlEl = ui.$("ab-url");
    var screenEl = ui.$("ab-screen");
    var cursorEl = ui.$("ab-cursor");
    var captionEl = ui.$("ab-caption");
    var notifyToggle = ui.$("notify-toggle");
    var clock = null, startAt = 0, aborted = false, browserOpen = false;

    function setBrowserVisible(on) {
      browserOpen = on;
      if (browserWrap) browserWrap.hidden = !on;
      if (toggleBtn) toggleBtn.classList.toggle("active", on);
    }

    function renderSteps(activeIdx) {
      if (!stepsEl) return;
      ui.clear(stepsEl);
      STEPS.forEach(function (label, i) {
        var cls = i < activeIdx ? "done" : (i === activeIdx ? "now" : "");
        stepsEl.appendChild(ui.h("li", { class: cls },
          ui.h("span", { class: "step-dot" }, i < activeIdx ? "\u2713" : String(i + 1)),
          ui.h("span", null, label)));
      });
    }

    function showStage(url, nodes, caption) {
      if (urlEl) urlEl.textContent = url;
      if (screenEl) { ui.clear(screenEl); ui.append(screenEl, nodes); }
      if (captionEl) captionEl.textContent = caption;
      if (cursorEl) cursorEl.hidden = true;
    }

    function clearTimers() {
      clearInterval(clock);
      clock = null;
    }

    function startSearch() { if (ring) ring.classList.add("working"); }
    function finishSearch() { if (ring) ring.classList.remove("working"); }

    function beginRealBuy(pick, profile) {
      aborted = false;
      clearTimers();
      var l = pick.listing, realUrl = resolveRealUrl(l);
      var cardStr = profile && profile.card ? (profile.card.brand + " •••• " + profile.card.last4) : "Saved card";

      if (ring) ring.classList.add("working");
      if (strip) strip.hidden = false;
      setBrowserVisible(true);
      if (dot) { dot.className = "live-dot live"; dot.textContent = "Chrome · Opening"; }
      if (stopBtn) stopBtn.disabled = false;
      startAt = Date.now();
      if (elapsedEl) elapsedEl.textContent = "0s";
      clock = setInterval(function () {
        if (elapsedEl) elapsedEl.textContent = Math.round((Date.now() - startAt) / 1000) + "s";
      }, 250);

      renderSteps(1);
      showStage(realUrl, [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-cartrow" },
            ui.h("span", { class: "ab-cart-title" }, "Opening real website in Chrome: " + l.title_en),
            ui.h("a", { href: realUrl, target: "_blank", rel: "noopener noreferrer", class: "btn btn-small btn-primary" },
              ui.icon("link-external", 12), "Open site")),
          ui.h("div", { class: "ab-jp" }, "Vault: " + (profile && profile.userId || "default") + " · " + cardStr))
      ], "Launching real Chrome window via don-tacos dt-ax…");

      return realUrl;
    }

    function completeRealBuy(pick, profile, res, realUrl) {
      if (aborted) return;
      clearTimers();
      renderSteps(STEPS.length);
      if (ring) ring.classList.remove("working");
      if (stopBtn) stopBtn.disabled = true;

      var cua = (res && res.cua) || {};
      var openedInChrome = Boolean(cua.opened_in_chrome);
      var targetUrl = cua.opened_url || realUrl;
      var controls = Array.isArray(cua.controls) ? cua.controls : [];
      var l = pick.listing, total = ui.totalOf(pick.cost);

      // In standalone file:// preview (no backend server), open the real website directly in a new browser tab
      if (!openedInChrome && location.protocol === "file:") {
        try { window.open(targetUrl, "_blank", "noopener,noreferrer"); } catch (e) {}
      }

      if (dot) {
        dot.className = "live-dot paused";
        dot.textContent = openedInChrome ? "Chrome · Live" : "Tab opened";
      }

      var statusText = openedInChrome
        ? ("Chrome window " + (cua.window || "") + " opened via don-tacos dt-ax" + (controls.length ? (" · " + controls.length + " controls read") : ""))
        : (cua.error ? ("Fallback tab opened (" + cua.error + ")") : "Opened real website in new browser tab.");

      var controlChips = controls.length
        ? ui.h("div", { class: "ab-controls" }, controls.slice(0, 5).map(function (c) {
            return ui.h("span", { class: "chip" }, c.label);
          }))
        : null;

      showStage(targetUrl, [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-cartrow" },
            ui.h("span", { class: "ab-cart-title" }, l.title_en + " · " + ui.usd(total)),
            ui.h("a", { href: targetUrl, target: "_blank", rel: "noopener noreferrer", class: "btn btn-small" },
              ui.icon("link-external", 12), "Open tab")),
          controlChips,
          ui.h("div", { class: "ab-paused" }, statusText + " · Swipe below to pay."))
      ], openedInChrome ? "Real Chrome tab ready · swipe to pay" : "Real site opened · swipe to pay");

      notify("Ready — swipe to pay " + ui.usd(total));
    }

    function markPaid(pick) {
      var l = pick ? pick.listing : { title_en: "Order" };
      var realUrl = resolveRealUrl(l);
      showStage(realUrl, [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-paused placed" }, "Paid & ordered: " + l.title_en))
      ], "Order placed.");
      if (dot) { dot.className = "live-dot paused"; dot.textContent = "Paid"; }
    }

    function stop() {
      aborted = true;
      clearTimers();
      if (ring) ring.classList.remove("working");
      if (dot) { dot.className = "live-dot stopped"; dot.textContent = "Stopped"; }
      if (stopBtn) stopBtn.disabled = true;
      if (captionEl) captionEl.textContent = "Stopped. Nothing paid.";
      ui.toast("Stopped · nothing paid");
      if (onStop) onStop();
    }

    function notify(msg) {
      ui.toast(msg);
      if (notifyToggle && notifyToggle.checked && document.hidden && "Notification" in window && Notification.permission === "granted") {
        try { new Notification("Shopping Agent", { body: msg }); } catch (e) {}
      }
    }

    if (stopBtn) stopBtn.addEventListener("click", stop);
    if (toggleBtn) toggleBtn.addEventListener("click", function () { setBrowserVisible(!browserOpen); });
    if (notifyToggle) {
      notifyToggle.addEventListener("change", function () {
        if (notifyToggle.checked && "Notification" in window && Notification.permission === "default") {
          Notification.requestPermission().catch(function () {});
        }
      });
    }

    return {
      startSearch: startSearch,
      finishSearch: finishSearch,
      beginRealBuy: beginRealBuy,
      completeRealBuy: completeRealBuy,
      markPaid: markPaid,
      stop: stop,
      notify: notify,
      isAborted: function () { return aborted; }
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.agent = { init: initAgent };
})(window);
