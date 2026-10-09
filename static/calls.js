// Renders model calls: what was sent to the model and what came back.
// Shared by the inspector panel beside the chat and the reviewer screens.
// Each API call is one card: a header, then its request, then its response.
(() => {
  const PURPOSE_LABELS = { coach: "Chatbot reply", safety: "Safety check", summary: "Session summary" };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const count = (n) => Number(n).toLocaleString();
  const shorten = (text, max = 90) => (text.length > max ? `${text.slice(0, max).trimEnd()}…` : text);

  function tokenLine(call) {
    if (call.prompt_tokens === null) {
      return call.error ? "Tokens: none counted (the request failed)" : "Tokens: not reported for this request";
    }
    const total = call.prompt_tokens + call.completion_tokens;
    return `Tokens: ${count(call.prompt_tokens)} sent + ${count(call.completion_tokens)} received = ${count(total)}`;
  }

  // One line adding up the tokens across a session's requests.
  window.modelCallTotals = (calls) => {
    const counted = calls.filter((call) => call.prompt_tokens !== null);
    const sent = counted.reduce((sum, call) => sum + call.prompt_tokens, 0);
    const received = counted.reduce((sum, call) => sum + call.completion_tokens, 0);
    const requests = `${calls.length} API ${calls.length === 1 ? "call" : "calls"}`;
    if (counted.length === 0) return `${requests} so far. No token counts reported.`;
    return `${requests} so far. Tokens: ${count(sent)} sent + ${count(received)} received = ${count(sent + received)} in total.`;
  };

  // What in the chat caused this call.
  function trigger(call) {
    const last = call.messages[call.messages.length - 1];
    if (call.purpose === "summary") return "Caused by: the session ending";
    if (call.purpose === "safety") {
      const text = last.content.split("Latest player message:\n")[1] || "";
      return `Caused by: the player's message "${shorten(text)}" (checked before any reply)`;
    }
    if (call.messages.length === 1) return "Caused by: the session starting (the bot's opening message)";
    return `Caused by: the player's message "${shorten(last.content)}"`;
  }

  function collapsed(title, content) {
    const details = el("details", "call-part");
    details.append(el("summary", "", title), content);
    return details;
  }

  function messageBox(message, label) {
    const box = el("div", "call-message");
    box.append(el("p", "call-message-label", label), el("pre", "", message.content));
    return box;
  }

  function requestSection(call) {
    const section = el("section", "call-section sent");
    section.appendChild(el("h3", "call-section-title", "↑ Request: sent to the model"));

    section.appendChild(
      collapsed(`Instructions (${count(call.system.length)} characters)`, el("pre", "", call.system))
    );

    const total = call.messages.length;
    const earlier = call.messages.slice(0, -1);
    if (earlier.length > 0) {
      const list = el("div");
      earlier.forEach((message, index) =>
        list.appendChild(messageBox(message, `Message ${index + 1} of ${total} · ${message.role}`))
      );
      section.appendChild(collapsed(`Earlier conversation resent (${earlier.length} of ${total} messages)`, list));
    }
    const last = call.messages[total - 1];
    const label = total === 1 ? `Message 1 of 1 · ${last.role}` : `Message ${total} of ${total} · ${last.role} · newest`;
    section.appendChild(messageBox(last, label));
    return section;
  }

  function responseSection(call) {
    const section = el("section", `call-section ${call.error ? "failed" : "received"}`);
    section.appendChild(
      el("h3", "call-section-title", call.error ? "↓ Response: none, the call failed" : "↓ Response: received from the model")
    );
    section.appendChild(el("pre", "", call.error || call.response));
    return section;
  }

  function callCard(call, number) {
    const card = el("article", `call ${call.error ? "failed" : ""}`);
    card.dataset.callId = call.id;

    const head = el("header", "call-head");
    const title = el("p", "row");
    title.append(
      el("strong", "", `API call ${number}`),
      el("span", "badge", PURPOSE_LABELS[call.purpose] || call.purpose)
    );
    const meta = el(
      "p",
      "small muted",
      `${call.model} · ${new Date(call.created_at).toLocaleTimeString()} · took ${(call.duration_ms / 1000).toFixed(2)} s`
    );
    head.append(title, meta, el("p", "small", trigger(call)), el("p", "tokens small", tokenLine(call)));

    card.append(head, requestSection(call), responseSection(call));
    return card;
  }

  // Adds a card for each call not already in `container`, so cards the viewer
  // has expanded stay as they are. Returns how many were added.
  window.renderModelCalls = (container, calls) => {
    const shown = new Set([...container.children].map((card) => card.dataset.callId));
    let added = 0;
    calls.forEach((call, index) => {
      if (shown.has(call.id)) return;
      container.appendChild(callCard(call, index + 1));
      added += 1;
    });
    return added;
  };
})();
