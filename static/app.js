const messagesEl = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const clearBtn = document.getElementById("clear");

const panelEmpty = document.getElementById("panel-empty");
const panelContent = document.getElementById("panel-content");
const statsEl = document.getElementById("stats");
const limitsEl = document.getElementById("limits");
const paramsEl = document.getElementById("params");
const requestBodyEl = document.getElementById("request-body");

const personaEl = document.getElementById("persona");
const noteEl = document.getElementById("note");

// Ids match PERSONAS in server.py, which holds the actual instructions.
const PERSONAS = [
  {
    id: "tennis",
    label: "Tennis mind coach",
    intro: "Tough match? Losing streak? Tell me what's going on, or pick a place to start.",
    starters: [
      "I just lost a match I should have won",
      "I keep losing and my confidence is gone",
      "I get tight on big points",
      "I lose my temper on court",
      "Help me build a between-point routine",
      "I get really nervous before matches",
    ],
    note: "An AI coach, not a substitute for professional care. In a crisis, call or text 988 (US) or your local emergency number.",
  },
  {
    id: "executive",
    label: "The executive",
    intro: "Ask me anything to get started.",
    starters: [],
    note: "",
  },
];

for (const persona of PERSONAS) {
  personaEl.add(new Option(persona.label, persona.id));
}

let history = [];
let busy = false;

function addMessage(role, text) {
  messagesEl.querySelector(".empty")?.remove();
  const el = document.createElement("div");
  el.className = `message ${role}`;
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

// The model often emphasises with **bold**; show that as bold, safely.
function renderReply(el, text) {
  el.replaceChildren();
  text.split(/\*\*(.+?)\*\*/g).forEach((part, i) => {
    if (!part) return;
    if (i % 2) {
      const strong = document.createElement("strong");
      strong.textContent = part;
      el.appendChild(strong);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  });
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  personaEl.disabled = value;
}

// Clears the conversation and shows the selected persona's welcome screen.
function resetChat() {
  const persona = PERSONAS.find((p) => p.id === personaEl.value);
  history = [];

  const empty = document.createElement("div");
  empty.className = "empty";
  const intro = document.createElement("p");
  intro.textContent = persona.intro;
  const starters = document.createElement("div");
  starters.className = "starters";
  for (const text of persona.starters) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => {
      if (!busy) send(text);
    });
    starters.appendChild(button);
  }
  empty.append(intro, starters);
  messagesEl.replaceChildren(empty);

  noteEl.textContent = persona.note;
  noteEl.hidden = !persona.note;
  resetPanel();
  input.focus();
}

function renderList(el, rows) {
  el.replaceChildren();
  for (const [label, value] of rows) {
    if (value === undefined || value === null || value === "") continue;
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    el.append(dt, dd);
  }
}

const seconds = (ms) => (ms === undefined ? undefined : `${(ms / 1000).toFixed(2)} s`);

function renderStats(stats) {
  const usage = stats.usage || {};
  const speed = usage.completion_time
    ? `${(usage.completion_tokens / usage.completion_time).toFixed(0)} tokens/s`
    : undefined;
  renderList(statsEl, [
    ["Status", stats.status],
    ["Time to first token", seconds(stats.firstTokenMs)],
    ["Total time", seconds(stats.totalMs)],
    ["Groq queue time", usage.queue_time === undefined ? undefined : seconds(usage.queue_time * 1000)],
    ["Prompt tokens", usage.prompt_tokens],
    ["Completion tokens", usage.completion_tokens],
    ["Reasoning tokens", usage.completion_tokens_details?.reasoning_tokens],
    ["Total tokens", usage.total_tokens],
    ["Generation speed", speed],
    ["Finish reason", stats.finishReason],
    ["Request ID", stats.requestId],
  ]);
  panelEmpty.hidden = true;
  panelContent.hidden = false;
}

// Shows what the server actually sent to Groq (everything except the API key).
function renderRequest(meta) {
  const { messages, ...params } = meta.request;
  renderList(paramsEl, [
    ["Endpoint", `POST ${meta.url}`],
    ...Object.entries(params).map(([key, value]) => [key, String(value)]),
    ["messages", `${messages.length} (incl. system prompt)`],
  ]);
  renderList(
    limitsEl,
    Object.entries(meta.rate_limits).map(([name, value]) => [
      name.replace("x-ratelimit-", "").replace(/-/g, " "),
      value,
    ])
  );
  requestBodyEl.textContent = JSON.stringify(meta.request, null, 2);
}

function resetPanel() {
  panelEmpty.hidden = false;
  panelContent.hidden = true;
}

// Reads the server-sent events: our own leading "meta" event, then Groq's chunks.
async function readStream(response, { onMeta, onToken, onChunk }) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const data = line.slice(5).trim();
      if (!data || data === "[DONE]") continue;
      const chunk = JSON.parse(data);
      if (chunk.meta) {
        onMeta(chunk.meta);
        continue;
      }
      if (chunk.error) throw new Error(chunk.error.message || "Stream error");
      onChunk(chunk);
      const token = chunk.choices?.[0]?.delta?.content;
      if (token) onToken(token);
    }
  }
}

async function send(text) {
  history.push({ role: "user", content: text });
  addMessage("user", text);
  const botEl = addMessage("assistant pending", "");
  setBusy(true);

  let reply = "";
  const started = performance.now();
  const stats = { status: "Sending…" };
  limitsEl.replaceChildren();
  paramsEl.replaceChildren();
  requestBodyEl.textContent = "";
  renderStats(stats);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history, persona: personaEl.value }),
    });
    stats.status = `${response.status} ${response.statusText}`.trim();
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Request failed (${response.status})`);
    }
    await readStream(response, {
      onMeta: renderRequest,
      onToken(token) {
        if (!reply) {
          stats.firstTokenMs = performance.now() - started;
          renderStats(stats);
        }
        reply += token;
        renderReply(botEl, reply);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      },
      onChunk(chunk) {
        stats.finishReason = chunk.choices?.[0]?.finish_reason || stats.finishReason;
        stats.usage = chunk.x_groq?.usage || chunk.usage || stats.usage;
        stats.requestId = chunk.x_groq?.id || stats.requestId;
      },
    });
    if (!reply) throw new Error("The model returned an empty response.");
    history.push({ role: "assistant", content: reply });
  } catch (err) {
    if (reply) {
      // Keep the partial answer so the conversation stays consistent.
      history.push({ role: "assistant", content: reply });
    } else {
      botEl.remove();
      history.pop();
    }
    addMessage("error", err.message);
  } finally {
    stats.totalMs = performance.now() - started;
    renderStats(stats);
    botEl.classList.remove("pending");
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  input.value = "";
  input.style.height = "auto";
  send(text);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
});

clearBtn.addEventListener("click", () => {
  if (!busy) resetChat();
});

personaEl.addEventListener("change", resetChat);

resetChat();
