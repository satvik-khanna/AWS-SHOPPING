/* Module: Agent's browser (from ui-mockup) + live plan strip, wired to real search & results.
   Safe DOM construction (textContent only, no innerHTML on untrusted data). */
(function (root) {
  "use strict";

  var STEPS = [
    "Recall",
    "Search JP",
    "Landed cost",
    "Rank",
    "Fill cart"
  ];

  function initAgent(onStop) {
    var ui = root.JSA.ui;
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
    var timers = [], clock = null, startAt = 0, aborted = false, browserOpen = true;

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

    function renderSearchStage(q) {
      showStage("jp.mercari.com/search?keyword=" + encodeURIComponent(q), [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-top" },
            ui.h("span", { class: "ab-logo ab-mercari" }, "mercari"),
            ui.h("div", { class: "ab-input", id: "ab-q" }, q)),
          ui.h("div", { class: "ab-grid" },
            [0, 1, 2].map(function (i) {
              return ui.h("div", { class: "ab-tile", id: "ab-tile-" + i },
                ui.h("div", { class: "ab-thumb" }, ui.icon("package", 16)),
                ui.h("div", { class: "ab-sk" }),
                ui.h("div", { class: "ab-sk ab-sk-sm" }));
            })))
      ], "Searching Japanese marketplaces for " + q + "\u2026");
      setTimeout(function () { pointAt("ab-tile-0", true); }, 80);
    }

    function renderTranslateStage(q) {
      showStage("jp.mercari.com/item/...", [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-top" },
            ui.h("span", { class: "ab-logo ab-mercari" }, "mercari"),
            ui.h("div", { class: "ab-input" }, q)),
          ui.h("div", { class: "ab-detail" },
            ui.h("div", { class: "ab-thumb ab-thumb-lg" }, ui.icon("package", 20)),
            ui.h("div", { class: "ab-detail-copy" },
              ui.h("div", { class: "ab-tr" }, q + " · translating JP listing"),
              ui.h("div", { class: "ab-jp" }, "状態・付属品・国内送料を抽出中…"),
              ui.h("span", { class: "ab-fakebtn", id: "ab-buy" }, "購入手続きへ"))))
      ], "Translating listing & extracting landed cost rules\u2026");
      setTimeout(function () { pointAt("ab-buy", false); }, 80);
    }

    function renderCartStage(result) {
      var pick = result && result.picks && result.picks[0];
      if (!pick) {
        showStage("buyee.jp/cart", [
          ui.h("div", { class: "ab-site" },
            ui.h("div", { class: "ab-top" }, ui.h("span", { class: "ab-logo ab-buyee" }, "buyee"), ui.h("div", { class: "ab-input" }, "Cart")),
            ui.h("div", { class: "ab-paused" }, "No item within budget. Cart left empty."))
        ], "No candidate within budget. Nothing added.");
        return;
      }
      var l = pick.listing;
      var placed = result.cart && result.cart.status === "placed";
      showStage("buyee.jp/cart", [
        ui.h("div", { class: "ab-site" },
          ui.h("div", { class: "ab-top" },
            ui.h("span", { class: "ab-logo ab-buyee" }, "buyee"),
            ui.h("div", { class: "ab-input" }, "Cart · 1 item")),
          ui.h("div", { class: "ab-cartrow", id: "ab-cart-row" },
            ui.h("span", { class: "ab-cart-title" }, l.title_en),
            ui.h("strong", null, ui.usd(ui.totalOf(pick.cost)))),
          ui.h("div", { class: "ab-paused" + (placed ? " placed" : "") },
            placed ? "Order placed (approved)." : "Paused before payment. Waiting for your approval."))
      ], placed ? "Order placed." : "Cart filled. Paused before payment.");
      setTimeout(function () { pointAt("ab-cart-row", true); }, 60);
    }

    function clearTimers() {
      timers.forEach(clearTimeout);
      timers = [];
      clearInterval(clock);
      clock = null;
    }

    function start(query) {
      aborted = false;
      clearTimers();
      if (strip) strip.hidden = false;
      setBrowserVisible(true);
      if (dot) { dot.className = "live-dot live"; dot.textContent = "Live"; }
      if (stopBtn) stopBtn.disabled = false;
      startAt = Date.now();
      if (elapsedEl) elapsedEl.textContent = "0s";
      clock = setInterval(function () {
        if (elapsedEl) elapsedEl.textContent = Math.round((Date.now() - startAt) / 1000) + "s";
      }, 250);
      renderSteps(0);
      renderSearchStage(query || "item");
      timers.push(setTimeout(function () { renderSteps(1); }, 120));
      timers.push(setTimeout(function () { renderSteps(2); renderTranslateStage(query || "item"); }, 260));
      timers.push(setTimeout(function () { renderSteps(3); }, 420));
    }

    function finish(result, summaryMsg) {
      clearTimers();
      renderSteps(STEPS.length);
      renderCartStage(result);
      if (dot) { dot.className = "live-dot paused"; dot.textContent = "Paused"; }
      if (stopBtn) stopBtn.disabled = true;
      if (summaryMsg) notify(summaryMsg);
    }

    function updateCart(result) {
      renderCartStage(result);
      if (result && result.cart && result.cart.status === "placed" && dot) {
        dot.className = "live-dot paused";
        dot.textContent = "Done";
      }
    }

    function stop() {
      aborted = true;
      clearTimers();
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
      start: start,
      finish: finish,
      updateCart: updateCart,
      stop: stop,
      notify: notify,
      isAborted: function () { return aborted; }
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.agent = { init: initAgent };
})(window);
