/* Module: Agent's browser (Dia-style Chrome Computer Use viewport)
   Opens a new Chrome tab on Buy, auto signs-in from User Vault, fills shipping & card,
   and pauses right before Pay so the user only needs to swipe. */
(function (root) {
  "use strict";

  var STEPS = [
    "New Chrome tab",
    "Auto sign-in (Vault)",
    "Open listing",
    "Fill shipping & card",
    "Ready to swipe"
  ];

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
    var timers = [], clock = null, startAt = 0, aborted = false, browserOpen = false;

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
    }

    function pointAt(id, click) {
      var el = document.getElementById(id);
      if (!el || !cursorEl || !cursorEl.parentElement) return;
      var a = el.getBoundingClientRect(), b = cursorEl.parentElement.getBoundingClientRect();
      if (!a.width && !a.height) return;
      cursorEl.style.left = Math.round(a.left - b.left + a.width / 2) + "px";
      cursorEl.style.top = Math.round(a.top - b.top + a.height / 2) + "px";
      screenEl.querySelectorAll(".ab-hl").forEach(function (n) { n.classList.remove("ab-hl"); });
      el.classList.add("ab-hl");
      if (click) {
        setTimeout(function () {
          cursorEl.classList.remove("click");
          void cursorEl.offsetWidth;
          cursorEl.classList.add("click");
        }, 280);
      }
    }

    function clearTimers() {
      timers.forEach(clearTimeout);
      timers = [];
      clearInterval(clock);
      clock = null;
    }

    function startSearch() {
      if (ring) ring.classList.add("working");
    }

    function finishSearch() {
      if (ring) ring.classList.remove("working");
    }

    function runComputerUseBuy(pick, profile, onReady) {
      aborted = false;
      clearTimers();
      var l = pick.listing, total = ui.totalOf(pick.cost);
      var cardStr = profile && profile.card ? (profile.card.brand + " •••• " + profile.card.last4) : "Visa •••• 4242";
      var acct = (profile && profile.email) || "buyer@example.com";
      var addr = (profile && profile.address) || "San Francisco, CA";

      if (ring) ring.classList.add("working");
      if (strip) strip.hidden = false;
      setBrowserVisible(true);
      if (dot) { dot.className = "live-dot live"; dot.textContent = "Chrome · Live"; }
      if (stopBtn) stopBtn.disabled = false;
      startAt = Date.now();
      if (elapsedEl) elapsedEl.textContent = "0s";
      clock = setInterval(function () {
        if (elapsedEl) elapsedEl.textContent = Math.round((Date.now() - startAt) / 1000) + "s";
      }, 250);

      // Step 0: Open new Chrome tab + Vault auto sign-in
      renderSteps(0);
      showStage("chrome://newtab → buyee.jp/login", [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-top" },
            ui.h("span", { class: "ab-logo ab-buyee" }, "buyee"),
            ui.h("div", { class: "ab-input" }, "Auto sign-in (" + profile.userId + ")")),
          ui.h("div", { class: "ab-cartrow", id: "ab-login" },
            ui.h("span", null, "Session vault · " + acct),
            ui.h("strong", null, "Signed in")))
      ], "Opened Chrome tab & restored session from User Vault");
      setTimeout(function () { pointAt("ab-login", true); }, 80);

      // Step 1: Navigate to listing & add to cart
      timers.push(setTimeout(function () {
        if (aborted) return;
        renderSteps(2);
        showStage("buyee.jp/item/" + encodeURIComponent(l.id), [
          ui.h("div", { class: "ab-site" },
            ui.h("div", { class: "ab-top" },
              ui.h("span", { class: "ab-logo ab-mercari" }, l.source),
              ui.h("div", { class: "ab-input" }, l.title_en)),
            ui.h("div", { class: "ab-cartrow" },
              ui.h("span", { class: "ab-cart-title" }, l.title_en),
              ui.h("strong", null, ui.usd(total))),
            ui.h("span", { class: "ab-fakebtn", id: "ab-add" }, "Add to Buyee cart"))
        ], "Clicking Add to cart via Computer Use\u2026");
        setTimeout(function () { pointAt("ab-add", true); }, 80);
      }, 450));

      // Step 2: Fill shipping & saved card, pause right before Pay
      timers.push(setTimeout(function () {
        if (aborted) return;
        renderSteps(3);
        showStage("buyee.jp/checkout/confirm", [
          ui.h("div", { class: "ab-site" },
            ui.h("div", { class: "ab-top" },
              ui.h("span", { class: "ab-logo ab-buyee" }, "buyee checkout"),
              ui.h("div", { class: "ab-input" }, cardStr + " · " + addr)),
            ui.h("div", { class: "ab-cartrow", id: "ab-pay-ready" },
              ui.h("span", { class: "ab-cart-title" }, l.title_en),
              ui.h("strong", null, ui.usd(total))),
            ui.h("div", { class: "ab-paused" }, "Paused before Pay · swipe below to purchase."))
        ], "Checkout filled. Swipe to pay.");
        setTimeout(function () { pointAt("ab-pay-ready", false); }, 80);
      }, 900));

      timers.push(setTimeout(function () {
        if (aborted) return;
        clearTimers();
        renderSteps(STEPS.length);
        if (ring) ring.classList.remove("working");
        if (dot) { dot.className = "live-dot paused"; dot.textContent = "Ready"; }
        if (stopBtn) stopBtn.disabled = true;
        notify("Ready — swipe to pay " + ui.usd(total));
        if (onReady) onReady();
      }, 1250));
    }

    function markPaid(pick) {
      var l = pick ? pick.listing : { title_en: "Order" };
      showStage("buyee.jp/checkout/complete", [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-top" },
            ui.h("span", { class: "ab-logo ab-buyee" }, "buyee"),
            ui.h("div", { class: "ab-input" }, "Order confirmed")),
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
      runComputerUseBuy: runComputerUseBuy,
      markPaid: markPaid,
      stop: stop,
      notify: notify,
      isAborted: function () { return aborted; }
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.agent = { init: initAgent };
})(window);
