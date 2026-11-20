/* Module: Dia-style live agent plan checklist, rainbow working ring, elapsed timer, and notifications
   Blended from origin/ui-mockup (web/mockup/agent.html) into real API pipeline. */
(function (root) {
  "use strict";

  var STEPS = [
    "Recall preferences",
    "Search & translate JP",
    "Landed cost (cited)",
    "Rank matches",
    "Fill cart & pause"
  ];

  function initAgent(onStop) {
    var ui = root.JSA.ui;
    var ring = ui.$("composer-ring");
    var strip = ui.$("agent-plan");
    var stepsEl = ui.$("plan-steps");
    var dot = ui.$("plan-dot");
    var elapsedEl = ui.$("plan-elapsed");
    var stopBtn = ui.$("plan-stop");
    var notifyToggle = ui.$("notify-toggle");
    var timers = [], clock = null, startAt = 0, aborted = false;

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

    function clearTimers() {
      timers.forEach(clearTimeout);
      timers = [];
      clearInterval(clock);
      clock = null;
    }

    function start() {
      aborted = false;
      clearTimers();
      if (ring) ring.classList.add("working");
      if (strip) strip.hidden = false;
      if (dot) { dot.className = "live-dot live"; dot.textContent = "Live"; }
      if (stopBtn) stopBtn.disabled = false;
      startAt = Date.now();
      if (elapsedEl) elapsedEl.textContent = "0s";
      clock = setInterval(function () {
        if (elapsedEl) elapsedEl.textContent = Math.round((Date.now() - startAt) / 1000) + "s";
      }, 250);
      renderSteps(0);
      timers.push(setTimeout(function () { renderSteps(1); }, 110));
      timers.push(setTimeout(function () { renderSteps(2); }, 230));
      timers.push(setTimeout(function () { renderSteps(3); }, 340));
    }

    function finish(summaryMsg) {
      clearTimers();
      if (ring) ring.classList.remove("working");
      renderSteps(STEPS.length);
      if (dot) { dot.className = "live-dot paused"; dot.textContent = "Paused"; }
      if (stopBtn) stopBtn.disabled = true;
      if (summaryMsg) notify(summaryMsg);
    }

    function stop() {
      aborted = true;
      clearTimers();
      if (ring) ring.classList.remove("working");
      if (dot) { dot.className = "live-dot stopped"; dot.textContent = "Stopped"; }
      if (stopBtn) stopBtn.disabled = true;
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
      stop: stop,
      notify: notify,
      isAborted: function () { return aborted; }
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.agent = { init: initAgent };
})(window);
