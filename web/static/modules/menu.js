/* Module: hamburger drawer menu & theme switcher */
(function (root) {
  "use strict";

  function initMenu(opts) {
    var ui = root.JSA.ui;
    var drawer = ui.$("drawer");
    var scrim = ui.$("scrim");
    var openBtn = ui.$("menu-open");
    var closeBtn = ui.$("menu-close");

    function setOpen(on) {
      drawer.classList.toggle("open", on);
      scrim.hidden = !on;
      drawer.setAttribute("aria-hidden", on ? "false" : "true");
      if (openBtn) openBtn.setAttribute("aria-expanded", on ? "true" : "false");
    }

    if (openBtn) openBtn.addEventListener("click", function () { setOpen(!drawer.classList.contains("open")); });
    if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
    if (scrim) scrim.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });

    // Theme toggle
    function currentTheme() {
      var saved = null;
      try { saved = localStorage.getItem("jsa-theme"); } catch (e) {}
      if (saved === "light" || saved === "dark") return saved;
      return (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
    }
    function applyTheme(theme) {
      document.documentElement.setAttribute("data-theme", theme);
      var btn = ui.$("theme-toggle");
      if (btn) {
        ui.clear(btn);
        btn.appendChild(ui.icon(theme === "dark" ? "sun" : "moon", 16));
        btn.setAttribute("title", theme === "dark" ? "Light theme" : "Dark theme");
      }
    }
    applyTheme(currentTheme());
    var themeBtn = ui.$("theme-toggle");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        try { localStorage.setItem("jsa-theme", next); } catch (e) {}
        applyTheme(next);
      });
    }

    return { open: function () { setOpen(true); }, close: function () { setOpen(false); } };
  }

  root.JSA = root.JSA || {};
  root.JSA.menu = { init: initMenu };
})(window);
