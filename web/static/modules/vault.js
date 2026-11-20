/* Module: Per-user Vault — remembers site sign-ins, shipping address, and masked card info per user_id
   so the agent never asks the user to sign in or re-enter payment details on every purchase. */
(function (root) {
  "use strict";

  var STORAGE_KEY = "jsa-user-vault-v1";
  var DEFAULT_PROFILES = {
    "default": {
      userId: "default",
      name: "Default User",
      email: "buyer@example.com",
      shipTo: "US-CA",
      address: "548 Market St, San Francisco, CA 94104",
      card: { brand: "Visa", last4: "4242", exp: "08/29", holder: "A. BUYER" },
      logins: [
        { site: "buyee.jp", account: "buyer@example.com", saved: true },
        { site: "jp.mercari.com", account: "@buyer_jp", saved: true },
        { site: "auctions.yahoo.co.jp", account: "buyer_yj", saved: true }
      ]
    }
  };

  function loadAll() {
    try {
      var raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      if (raw && typeof raw === "object" && raw.profiles) return raw;
    } catch (e) {}
    return { active: "default", profiles: JSON.parse(JSON.stringify(DEFAULT_PROFILES)) };
  }

  function saveAll(db) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(db)); } catch (e) {}
  }

  function initVault(onUserChange) {
    var ui = root.JSA.ui;
    var db = loadAll();

    function activeProfile() {
      return db.profiles[db.active] || db.profiles["default"];
    }

    function render() {
      var p = activeProfile();
      var pill = ui.$("user-pill");
      if (pill) pill.textContent = p.userId + " · •••• " + (p.card && p.card.last4 ? p.card.last4 : "----");

      var box = ui.$("vault-view");
      if (!box) return;
      ui.clear(box);

      // User switcher row
      var keys = Object.keys(db.profiles);
      box.appendChild(ui.h("div", { class: "section-kicker" }, "User profile"));
      var switcher = ui.h("div", { class: "vault-users" },
        keys.map(function (k) {
          return ui.h("button", {
            type: "button",
            class: "btn btn-small" + (k === db.active ? " btn-primary" : ""),
            onclick: function () {
              db.active = k;
              saveAll(db);
              render();
              if (onUserChange) onUserChange(activeProfile());
            }
          }, k);
        }),
        ui.h("button", {
          type: "button", class: "btn btn-small btn-ghost",
          onclick: function () {
            var id = "user-" + (keys.length + 1);
            db.profiles[id] = {
              userId: id, name: "User " + (keys.length + 1), email: id + "@example.com",
              shipTo: "US-CA", address: "San Francisco, CA",
              card: { brand: "Mastercard", last4: "8812", exp: "11/28", holder: "NEW USER" },
              logins: [{ site: "buyee.jp", account: id + "@example.com", saved: true }]
            };
            db.active = id;
            saveAll(db);
            render();
            if (onUserChange) onUserChange(activeProfile());
            ui.toast("Created profile " + id);
          }
        }, "+ New user"));
      box.appendChild(switcher);

      // Saved Sign-ins
      box.appendChild(ui.h("div", { class: "section-kicker" }, "Saved sign-ins (auto-login)"));
      (p.logins || []).forEach(function (lg, idx) {
        box.appendChild(ui.h("div", { class: "flat-row" },
          ui.icon(lg.saved ? "shield-check" : "shield-lock", 14),
          ui.h("div", { class: "row-main" },
            ui.h("div", { class: "row-title" }, lg.site),
            ui.h("div", { class: "row-meta" }, lg.account)),
          ui.h("button", {
            type: "button", class: "btn btn-small",
            onclick: function () {
              lg.saved = !lg.saved;
              saveAll(db);
              render();
            }
          }, lg.saved ? "Saved" : "Off")));
      });

      // Saved Card & Shipping form
      box.appendChild(ui.h("div", { class: "section-kicker" }, "Card & shipping (for Swipe to Pay)"));
      var form = ui.h("form", {
        class: "connect-form",
        onsubmit: function (e) {
          e.preventDefault();
          p.card = {
            brand: form.querySelector("[name=brand]").value.trim() || "Visa",
            last4: (form.querySelector("[name=last4]").value.replace(/\D/g, "").slice(-4) || "4242"),
            exp: form.querySelector("[name=exp]").value.trim() || "08/29",
            holder: form.querySelector("[name=holder]").value.trim() || p.name
          };
          p.shipTo = form.querySelector("[name=shipTo]").value.trim() || "US-CA";
          p.address = form.querySelector("[name=address]").value.trim() || p.address;
          saveAll(db);
          render();
          if (onUserChange) onUserChange(activeProfile());
          ui.toast("Vault saved for " + p.userId);
        }
      },
        ui.h("div", { class: "vault-grid" },
          ui.h("label", { class: "field" }, ui.h("span", null, "Brand"), ui.h("input", { name: "brand", value: p.card.brand })),
          ui.h("label", { class: "field" }, ui.h("span", null, "Last 4"), ui.h("input", { name: "last4", maxlength: "4", value: p.card.last4 })),
          ui.h("label", { class: "field" }, ui.h("span", null, "Exp"), ui.h("input", { name: "exp", maxlength: "5", value: p.card.exp })),
          ui.h("label", { class: "field" }, ui.h("span", null, "Ship to"), ui.h("input", { name: "shipTo", value: p.shipTo }))),
        ui.h("label", { class: "field" }, ui.h("span", null, "Cardholder"), ui.h("input", { name: "holder", value: p.card.holder })),
        ui.h("label", { class: "field" }, ui.h("span", null, "Address"), ui.h("input", { name: "address", value: p.address })),
        ui.h("button", { type: "submit", class: "btn btn-primary" }, "Save vault"));
      box.appendChild(form);
    }

    render();
    return {
      get: activeProfile,
      render: render
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.vault = { init: initVault };
})(window);
