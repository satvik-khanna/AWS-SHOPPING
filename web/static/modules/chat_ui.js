/* Module: Generative Chat UI renderer — AI emits typed UI components directly inside chat messages:
   - product_list (sortable list with Buy button on every row)
   - agent_browser (don-tacos dt-ax Chrome Computer Use live viewport)
   - swipe_pay (Swipe-to-Pay checkout approval slider)
   - cost_breakdown (inline cited landed cost breakdown) */
(function (root) {
  "use strict";

  function initChatUI() {
    var ui = root.JSA.ui;
    var stream = ui.$("chat-stream");

    function scrollBottom() {
      if (stream) {
        requestAnimationFrame(function () { stream.scrollTop = stream.scrollHeight; });
      }
    }

    function appendUser(text) {
      var msg = ui.h("div", { class: "msg msg-user" }, ui.h("div", { class: "bubble" }, text));
      stream.appendChild(msg);
      scrollBottom();
      return msg;
    }

    function appendSkeleton() {
      var sk = ui.h("div", { class: "msg msg-agent chat-sk" },
        ui.h("div", { class: "sk-row" }),
        ui.h("div", { class: "sk-row" }),
        ui.h("div", { class: "sk-row" }));
      stream.appendChild(sk);
      scrollBottom();
      return sk;
    }

    function appendAgentBlock(caption, bodyNode) {
      var msg = ui.h("div", { class: "msg msg-agent" },
        caption ? ui.h("div", { class: "agent-caption" }, ui.h("span", { class: "dia-orb dia-orb-sm", "aria-hidden": "true" }), caption) : null,
        bodyNode);
      stream.appendChild(msg);
      scrollBottom();
      return msg;
    }

    return {
      appendUser: appendUser,
      appendSkeleton: appendSkeleton,
      appendAgentBlock: appendAgentBlock,
      scrollBottom: scrollBottom
    };
  }

  root.JSA = root.JSA || {};
  root.JSA.chatUI = { init: initChatUI };
})(window);
