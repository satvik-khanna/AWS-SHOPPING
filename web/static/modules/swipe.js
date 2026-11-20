/* Module: Swipe-to-Pay slider control (pointer + touch + keyboard accessible) */
(function (root) {
  "use strict";

  function createSwipePay(opts) {
    var ui = root.JSA.ui;
    var label = opts.label || "Slide to pay";
    var onConfirm = opts.onConfirm;
    var disabled = false, dragging = false, startX = 0, curX = 0, maxX = 0;

    var fill = ui.h("div", { class: "swipe-fill" });
    var text = ui.h("span", { class: "swipe-label" }, label);
    var thumb = ui.h("button", {
      type: "button",
      id: "approve",
      class: "swipe-thumb",
      "aria-label": label + " (swipe right or press Enter)"
    }, ui.icon("arrow-right", 14));
    var track = ui.h("div", { class: "swipe-track", role: "group", "aria-label": label }, fill, text, thumb);

    function setPos(px) {
      curX = Math.max(0, Math.min(maxX, px));
      thumb.style.transform = "translateX(" + curX + "px)";
      fill.style.width = (curX + 34) + "px";
      text.style.opacity = String(Math.max(0, 1 - (maxX ? curX / (maxX * 0.7) : 0)));
    }

    function complete() {
      if (disabled) return;
      disabled = true;
      track.classList.add("confirmed");
      thumb.disabled = true;
      text.textContent = "Paying\u2026";
      text.style.opacity = "1";
      if (onConfirm) onConfirm(thumb);
    }

    thumb.addEventListener("pointerdown", function (e) {
      if (disabled) return;
      dragging = true;
      maxX = Math.max(40, track.clientWidth - thumb.offsetWidth - 8);
      startX = e.clientX - curX;
      track.classList.add("dragging");
      thumb.setPointerCapture(e.pointerId);
    });

    thumb.addEventListener("pointermove", function (e) {
      if (!dragging || disabled) return;
      setPos(e.clientX - startX);
    });

    function endDrag() {
      if (!dragging) return;
      dragging = false;
      track.classList.remove("dragging");
      if (maxX > 0 && curX >= maxX * 0.82) {
        setPos(maxX);
        complete();
      } else {
        setPos(0);
      }
    }

    thumb.addEventListener("pointerup", endDrag);
    thumb.addEventListener("pointercancel", endDrag);
    thumb.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        maxX = Math.max(40, track.clientWidth - thumb.offsetWidth - 8);
        setPos(maxX);
        complete();
      }
    });

    return track;
  }

  root.JSA = root.JSA || {};
  root.JSA.swipe = { create: createSwipePay };
})(window);
