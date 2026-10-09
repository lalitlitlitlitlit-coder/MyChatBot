const $ = (id) => document.getElementById(id);

let profile = null;
let currentScreen = "loading";

class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    // After boot, a 401 means the browser is not sending our cookie back (E-17).
    if (response.status === 401) fatal("This app needs cookies enabled to work.");
    throw new ApiError(response.status, data?.detail);
  }
  return { status: response.status, data };
}

function show(name) {
  for (const screen of document.querySelectorAll("[data-screen]")) {
    screen.hidden = screen.dataset.screen !== name;
  }
  currentScreen = name;
  placeInspector();
  window.scrollTo(0, 0);
}

function fatal(text) {
  $("fatal-text").textContent = text;
  show("fatal");
}

function setError(el, text) {
  el.textContent = text || "";
  el.hidden = !text;
}

// --- Boot: identify this browser, then the notice, then the chatbot picker -----

async function boot() {
  let player;
  try {
    const first = await api("POST", "/api/player");
    player = first.data;
    if (first.status === 201) {
      // A new player was created; make sure the cookie actually comes back.
      const second = await api("POST", "/api/player");
      if (second.data.player_id !== player.player_id) {
        fatal("This app needs cookies enabled to work.");
        return;
      }
    }
  } catch {
    fatal("Something went wrong. Refresh the page.");
    return;
  }

  // NFR-01: once per browser tab session, before any other screen.
  if (sessionStorage.getItem("noticeAcknowledged") !== "yes") {
    show("notice");
    await new Promise((resolve) => $("notice-ok").addEventListener("click", resolve, { once: true }));
    sessionStorage.setItem("noticeAcknowledged", "yes");
  }

  try {
    if (player.has_profile) profile = (await api("GET", "/api/profile")).data;
  } catch (err) {
    if (err.status !== 401) fatal("Something went wrong. Refresh the page.");
    return;
  }
  showBots();
}

// --- Onboarding ---------------------------------------------------------------

const profileForm = $("profile-form");

function fieldError(name, text) {
  profileForm.querySelector(`[data-error-for="${name}"]`).textContent = text || "";
}

function readProfileForm() {
  const values = {
    display_name: $("display_name").value.trim(),
    age: $("age").value.trim(),
    playing_level: $("playing_level").value,
    pressure_points: $("pressure_points").value.trim(),
  };
  const errors = {};
  if (!values.display_name) errors.display_name = "Enter a display name.";
  else if (values.display_name.length > 50) errors.display_name = "Use 50 characters or fewer.";
  if (!/^\d+$/.test(values.age) || Number(values.age) < 1 || Number(values.age) > 99) {
    errors.age = "Enter a whole number from 1 to 99.";
  }
  if (!values.playing_level) errors.playing_level = "Choose a playing level.";
  if (values.pressure_points.length > 500) errors.pressure_points = "Use 500 characters or fewer.";
  return { values, errors };
}

profileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const { values, errors } = readProfileForm();
  for (const name of ["display_name", "age", "playing_level", "pressure_points"]) {
    fieldError(name, errors[name]);
  }
  setError($("profile-error"), "");
  if (Object.keys(errors).length) return;

  $("profile-submit").disabled = true;
  try {
    const { data } = await api("POST", "/api/profile", {
      display_name: values.display_name,
      age: Number(values.age),
      playing_level: values.playing_level,
      pressure_points: values.pressure_points || null,
    });
    profile = data;
    showHome();
  } catch (err) {
    if (err.status === 422 && Array.isArray(err.detail)) {
      for (const item of err.detail) fieldError(item.loc[item.loc.length - 1], item.msg);
    } else if (err.status !== 401) {
      setError($("profile-error"), "Couldn't save your profile. Try again.");
    }
  } finally {
    $("profile-submit").disabled = false;
  }
});

// --- Choosing a chatbot (the main page) ----------------------------------------

let bots = [];
let currentBot = null;

const botById = (id) => bots.find((bot) => bot.id === id);

// The task a session or summary belongs to, for its title and labels.
function taskOf({ bot_id, task_type }) {
  const task = botById(bot_id)?.tasks.find((t) => t.id === task_type);
  return task || { title: task_type, result_label: "Result" };
}

// The open session's id is remembered so it can be reopened later (E-04).
const savedSessionId = () => localStorage.getItem("openSessionId");
const saveSessionId = (id) => (id ? localStorage.setItem("openSessionId", id) : localStorage.removeItem("openSessionId"));

// The remembered session with its messages, if it is still open.
async function loadOpenSession() {
  const id = savedSessionId();
  if (!id) return null;
  try {
    const { data } = await api("GET", `/api/sessions/${id}`);
    if (data.session.status === "open") return data;
    saveSessionId(null);
  } catch (err) {
    if (err.status === 404) saveSessionId(null);
  }
  return null;
}

async function showBots() {
  currentBot = null;
  try {
    bots = (await api("GET", "/api/bots")).data; // re-read, so newly added chatbots show up
  } catch (err) {
    if (err.status === 401) return;
    if (bots.length === 0) {
      fatal("Something went wrong. Refresh the page.");
      return;
    }
  }
  const list = $("bot-list");
  list.replaceChildren();
  for (const bot of bots) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "task";
    const name = document.createElement("span");
    name.textContent = bot.name;
    const description = document.createElement("span");
    description.className = "task-description";
    description.textContent = bot.description;
    button.append(name, description);
    button.addEventListener("click", () => chooseBot(bot));
    list.appendChild(button);
  }
  $("bots-empty").hidden = bots.length > 0;
  $("bots-resume").hidden = true;
  show("bots");

  const open = await loadOpenSession();
  if (open && currentScreen === "bots") {
    const name = botById(open.session.bot_id)?.name || "chatbot";
    $("bots-resume").textContent = `Continue your open session (${name})`;
    $("bots-resume").hidden = false;
  }
}

function chooseBot(bot) {
  currentBot = bot;
  // The tennis coach asks for a player profile the first time it is used.
  if (bot.needs_profile && !profile) {
    show("onboarding");
    $("display_name").focus();
    return;
  }
  showHome();
}

// --- A chatbot's Home: its tasks ---------------------------------------------------

async function showHome(note) {
  if (!currentBot) {
    showBots();
    return;
  }
  const bot = currentBot;
  $("home-title").textContent = bot.name;
  $("home-greeting").textContent =
    bot.needs_profile && profile ? `Hi, ${profile.display_name}. What do you need right now?` : bot.description;
  setError($("home-error"), "");
  setError($("home-note"), note);
  $("open-memory").hidden = !bot.has_memory;
  $("resume").hidden = true;

  const list = $("task-list");
  list.replaceChildren();
  for (const task of bot.tasks) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "task";
    button.textContent = task.button;
    button.addEventListener("click", () => startSession(bot, task));
    list.appendChild(button);
  }
  show("home");

  const open = await loadOpenSession();
  if (open && open.session.bot_id === bot.id && currentBot === bot) $("resume").hidden = false;
}

function setHomeBusy(busy) {
  for (const button of document.querySelectorAll("#task-list button, #resume, #bots-resume")) {
    button.disabled = busy;
  }
}

async function startSession(bot, task) {
  setError($("home-error"), "");
  setHomeBusy(true);
  try {
    const { data } = await api("POST", "/api/sessions", { bot_id: bot.id, task_type: task.id });
    openChat(data.session, data.opening_message ? [data.opening_message] : []);
  } catch (err) {
    if (err.status !== 401) setError($("home-error"), "Couldn't start the session just now. Try again.");
  } finally {
    setHomeBusy(false);
  }
}

async function resumeSession() {
  setHomeBusy(true);
  const open = await loadOpenSession();
  setHomeBusy(false);
  if (open) openChat(open.session, open.messages);
  else if (currentBot) showHome();
  else showBots();
}

$("resume").addEventListener("click", resumeSession);
$("bots-resume").addEventListener("click", resumeSession);
$("home-back").addEventListener("click", showBots);

// --- Chat -----------------------------------------------------------------------

const messagesEl = $("messages");
const chatInput = $("chat-input");
let session = null;
let unanswered = null; // text of a sent message that has no reply yet

function addBubble(sender, text) {
  const bubble = document.createElement("div");
  bubble.className = `message ${sender}`;
  bubble.textContent = text;
  messagesEl.appendChild(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function setChatState({ busy = false, status = "", retry = false } = {}) {
  const open = session.status === "open";
  setError($("chat-status"), status);
  $("chat-retry").hidden = !retry;
  $("composer").hidden = !open;
  $("end-session").hidden = !open;
  $("chat-closed").hidden = open;
  $("chat-closed-text").textContent =
    session.status === "flagged" ? "This session is closed for coaching." : "This session has ended.";
  chatInput.disabled = busy || retry;
  $("send").disabled = busy || retry;
  $("end-session").disabled = busy;
  $("retry").disabled = busy;
}

function openChat(openedSession, messages) {
  session = openedSession;
  currentBot = botById(session.bot_id) || currentBot;
  unanswered = null;
  inspectSession(session.id);
  saveSessionId(session.status === "open" ? session.id : null);
  $("chat-title").textContent = taskOf(session).title;
  messagesEl.replaceChildren();
  for (const message of messages) addBubble(message.sender, message.content);
  // Reopened with a message that never got its reply: offer the retry.
  const last = messages[messages.length - 1];
  if (session.status === "open" && last?.sender === "player") unanswered = last.content;
  setChatState({ retry: unanswered !== null });
  show("chat");
  if (session.status === "open" && unanswered === null) chatInput.focus();
}

async function requestReply() {
  setChatState({ busy: true, status: "Thinking…" });
  try {
    const { data } = await api("POST", `/api/sessions/${session.id}/messages`, { content: unanswered });
    unanswered = null;
    session.status = data.session_status;
    addBubble(data.reply.sender, data.reply.content);
    if (session.status !== "open") saveSessionId(null);
    setChatState();
    refreshInspector();
    if (session.status === "open") chatInput.focus();
  } catch (err) {
    if (err.status === 401) return;
    refreshInspector();
    if (err.status === 409) {
      // The session was closed somewhere else; show it as it now is.
      const { data } = await api("GET", `/api/sessions/${session.id}`);
      openChat(data.session, data.messages);
      return;
    }
    setChatState({ retry: true });
  }
}

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = chatInput.value.trim();
  if (!text || unanswered !== null) return;
  chatInput.value = "";
  chatInput.style.height = "auto";
  unanswered = text;
  addBubble("player", text);
  requestReply();
});

$("retry").addEventListener("click", requestReply);

chatInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});

chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = `${chatInput.scrollHeight}px`;
});

$("end-session").addEventListener("click", async () => {
  setChatState({ busy: true, status: "Ending the session…" });
  try {
    const { data } = await api("POST", `/api/sessions/${session.id}/end`);
    saveSessionId(null);
    refreshInspector(); // picks up the summary call
    const result = data.takeaway || data.outcome;
    showHome(result ? `Session ended. ${taskOf(data).result_label}: ${result}` : "Session ended.");
  } catch (err) {
    if (err.status === 401) return;
    if (err.status === 409) {
      saveSessionId(null);
      showHome("Session ended.");
      return;
    }
    setChatState({ status: "Couldn't end the session. Try again." });
  }
});

$("chat-home").addEventListener("click", () => showHome());

// --- Memory ---------------------------------------------------------------------

async function showMemory() {
  setError($("memory-error"), "");
  let summaries;
  try {
    summaries = (await api("GET", `/api/memory?bot_id=${encodeURIComponent(currentBot.id)}`)).data;
  } catch (err) {
    if (err.status === 401) return;
    summaries = null;
  }
  show("memory");
  if (summaries === null) {
    setError($("memory-error"), "Couldn't load your memory. Try again.");
    return;
  }
  renderMemory(summaries);
}

function renderMemory(summaries) {
  const list = $("memory-list");
  list.replaceChildren();
  for (const summary of summaries) {
    const card = document.createElement("div");
    card.className = "card";
    const heading = document.createElement("p");
    heading.className = "small muted";
    heading.textContent = `${new Date(summary.created_at).toLocaleDateString()} · ${taskOf(summary).title}`;
    const text = document.createElement("p");
    text.className = "summary-text";
    text.textContent = summary.text;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "Delete";
    remove.addEventListener("click", () =>
      deleteMemory(`/api/memory/${summary.id}`, "Delete this note? The chatbot will no longer use it.")
    );
    card.append(heading, text, remove);
    list.appendChild(card);
  }
  $("memory-empty").hidden = summaries.length > 0;
  $("memory-delete-all").hidden = summaries.length === 0;
}

async function deleteMemory(path, question) {
  if (!confirm(question)) return;
  setError($("memory-error"), "");
  try {
    await api("DELETE", path);
  } catch (err) {
    // The list on screen is left as it was.
    if (err.status !== 401) setError($("memory-error"), "Couldn't delete. Try again.");
    return;
  }
  showMemory();
}

$("open-memory").addEventListener("click", showMemory);
$("memory-back").addEventListener("click", () => showHome());
$("memory-delete-all").addEventListener("click", () =>
  deleteMemory(
    `/api/memory?bot_id=${encodeURIComponent(currentBot.id)}`,
    "Delete everything this chatbot remembers about you?"
  )
);

// --- Inspector: the model calls behind the current session --------------------
// A side panel on the right of the chat and Home screens, opened with a toggle.

const INSPECTOR_SCREENS = ["chat", "home"];
let inspectorOpen = localStorage.getItem("inspectorOpen") === "yes";
let inspectedSessionId = null;

function placeInspector() {
  const visible = inspectorOpen && INSPECTOR_SCREENS.includes(currentScreen);
  $("inspector").hidden = !visible;
  document.body.classList.toggle("inspector-open", visible);
  $("inspector-toggle").setAttribute("aria-expanded", String(inspectorOpen));
  $("home-inspector").textContent = inspectorOpen ? "Hide inspector" : "Show inspector";
}

function inspectSession(id) {
  if (id !== inspectedSessionId) {
    $("inspector-calls").replaceChildren();
    setError($("inspector-total"), "");
  }
  inspectedSessionId = id;
  refreshInspector();
}

async function refreshInspector() {
  if (!inspectorOpen || !inspectedSessionId) return;
  const id = inspectedSessionId;
  let calls;
  try {
    calls = (await api("GET", `/api/sessions/${id}/calls`)).data;
  } catch (err) {
    if (err.status !== 401) setError($("inspector-error"), "Couldn't load the requests.");
    return;
  }
  if (id !== inspectedSessionId) return; // another session was opened meanwhile
  setError($("inspector-error"), "");
  $("inspector-empty").hidden = true;
  setError($("inspector-total"), modelCallTotals(calls));
  const body = document.querySelector(".inspector-body");
  if (renderModelCalls($("inspector-calls"), calls)) body.scrollTop = body.scrollHeight;
}

function toggleInspector() {
  inspectorOpen = !inspectorOpen;
  localStorage.setItem("inspectorOpen", inspectorOpen ? "yes" : "no");
  placeInspector();
  refreshInspector();
}

for (const id of ["inspector-toggle", "inspector-close", "home-inspector"]) {
  $(id).addEventListener("click", toggleInspector);
}

// Resizing: drag the panel's left edge (or focus it and use the arrow keys).
const INSPECTOR_MIN_WIDTH = 280;
const CHAT_MIN_WIDTH = 340; // always leave the chat usable
const resizer = $("inspector-resizer");

function setInspectorWidth(width, remember) {
  const max = Math.max(INSPECTOR_MIN_WIDTH, window.innerWidth - CHAT_MIN_WIDTH);
  const clamped = Math.round(Math.min(Math.max(width, INSPECTOR_MIN_WIDTH), max));
  document.documentElement.style.setProperty("--inspector-width", `${clamped}px`);
  if (remember) localStorage.setItem("inspectorWidth", String(clamped));
  return clamped;
}

const inspectorWidth = () => $("inspector").getBoundingClientRect().width;

resizer.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  resizer.setPointerCapture(event.pointerId);
  document.body.classList.add("resizing-inspector");
});

resizer.addEventListener("pointermove", (event) => {
  if (!resizer.hasPointerCapture(event.pointerId)) return;
  setInspectorWidth(window.innerWidth - event.clientX, true);
});

for (const type of ["pointerup", "pointercancel"]) {
  resizer.addEventListener(type, () => document.body.classList.remove("resizing-inspector"));
}

resizer.addEventListener("keydown", (event) => {
  const step = { ArrowLeft: 24, ArrowRight: -24 }[event.key];
  if (!step) return;
  event.preventDefault();
  setInspectorWidth(inspectorWidth() + step, true);
});

// Keep the remembered width within the window as the window changes size.
const savedInspectorWidth = Number(localStorage.getItem("inspectorWidth")) || 440;
setInspectorWidth(savedInspectorWidth);
window.addEventListener("resize", () =>
  setInspectorWidth(Number(localStorage.getItem("inspectorWidth")) || 440)
);

boot();
