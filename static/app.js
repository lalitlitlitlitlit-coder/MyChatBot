const messagesEl = document.getElementById("messages");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const clearBtn = document.getElementById("clear");

const EMPTY_HTML = messagesEl.innerHTML;
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

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
}

// Reads Groq's server-sent events and calls onToken for each content chunk.
async function readStream(response, onToken) {
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
      if (chunk.error) throw new Error(chunk.error.message || "Stream error");
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
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Request failed (${response.status})`);
    }
    await readStream(response, (token) => {
      reply += token;
      botEl.textContent = reply;
      messagesEl.scrollTop = messagesEl.scrollHeight;
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
  if (busy) return;
  history = [];
  messagesEl.innerHTML = EMPTY_HTML;
  input.focus();
});
