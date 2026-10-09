const $ = (id) => document.getElementById(id);

function show(name) {
  for (const screen of document.querySelectorAll("[data-screen]")) {
    screen.hidden = screen.dataset.screen !== name;
  }
  window.scrollTo(0, 0);
}

function setError(el, text) {
  el.textContent = text || "";
  el.hidden = !text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

const formatDate = (iso) => new Date(iso).toLocaleString();

// Any review call without a valid reviewer cookie falls back to the code form.
async function api(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(data?.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function sessionMeta(session) {
  const meta = el("p", "row small");
  meta.append(
    el("strong", "", session.display_name || "(no profile)"),
    el("span", "muted", session.player_short_id),
    el("span", "badge", session.bot_name),
    el("span", "badge", session.task_title),
    el("span", `badge ${session.status}`, session.status),
    el("span", "muted", formatDate(session.started_at))
  );
  return meta;
}

function resultLine(session) {
  const label = session.takeaway ? "Takeaway" : "Outcome";
  return el("p", "small", `${label}: ${session.takeaway || session.outcome || "none"}`);
}

async function showList() {
  let sessions;
  try {
    sessions = await api("GET", "/api/review/sessions");
  } catch {
    show("access");
    return;
  }
  const list = $("session-list");
  list.replaceChildren();
  for (const session of sessions) {
    const card = el("button", `card ${session.status}`);
    card.type = "button";
    card.append(sessionMeta(session), resultLine(session));
    card.addEventListener("click", () => showDetail(session.id));
    list.appendChild(card);
  }
  $("list-empty").hidden = sessions.length > 0;
  show("list");
}

const SENDER_LABELS = { player: "Player", bot: "Coach", system: "System (fixed safety message)" };

let detailId = null;

function selectTab(name) {
  $("tab-transcript").setAttribute("aria-selected", String(name === "transcript"));
  $("tab-calls").setAttribute("aria-selected", String(name === "calls"));
  $("detail-body").hidden = name !== "transcript";
  $("calls-body").hidden = name !== "calls";
}

async function showDetail(id) {
  const body = $("detail-body");
  detailId = id;
  body.replaceChildren();
  $("detail-head").replaceChildren();
  $("calls-body").replaceChildren();
  selectTab("transcript");
  setError($("detail-error"), "");
  let data;
  try {
    data = await api("GET", `/api/review/sessions/${encodeURIComponent(id)}`);
  } catch (err) {
    if (err.status === 401) {
      show("access");
      return;
    }
    show("detail");
    setError($("detail-error"), err.status === 404 ? "Session not found." : "Couldn't load the session. Try again.");
    return;
  }

  const events = new Map(data.safety_events.map((event) => [event.message_id, event]));
  $("detail-head").append(sessionMeta(data.session), resultLine(data.session));

  body.appendChild(el("h2", "", "Safety events"));
  if (data.safety_events.length === 0) body.appendChild(el("p", "small muted", "None."));
  for (const event of data.safety_events) {
    const card = el("div", "card flagged");
    card.appendChild(el("p", "small", `Category: ${event.category} · ${formatDate(event.created_at)}`));
    body.appendChild(card);
  }

  body.appendChild(el("h2", "", "Transcript"));
  for (const message of data.messages) {
    const event = events.get(message.id);
    const label = `${SENDER_LABELS[message.sender]} · ${formatDate(message.created_at)}`;
    body.appendChild(el("p", "sender", event ? `${label} · flagged: ${event.category}` : label));
    body.appendChild(el("p", "transcript-text", message.content));
  }
  show("detail");
}

// --- Model calls: what was sent to the model and what came back ---------------

async function showCalls() {
  const body = $("calls-body");
  selectTab("calls");
  let calls;
  try {
    calls = await api("GET", `/api/review/sessions/${encodeURIComponent(detailId)}/calls`);
  } catch (err) {
    if (err.status === 401) {
      show("access");
      return;
    }
    body.replaceChildren(el("p", "error", "Couldn't load the model calls. Try again."));
    return;
  }
  body.replaceChildren(
    el("p", "small muted", "Every request sent to the model for this session, in order, with what came back. Open this tab again to refresh.")
  );
  if (calls.length === 0) {
    body.appendChild(el("p", "muted", "No model calls recorded. Sessions from before this tab was added have none."));
  }
  if (calls.length > 0) body.appendChild(el("p", "tokens-total small", modelCallTotals(calls)));
  const list = el("div");
  body.appendChild(list);
  renderModelCalls(list, calls);
}

$("tab-transcript").addEventListener("click", () => selectTab("transcript"));
$("tab-calls").addEventListener("click", showCalls);

$("unlock-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  setError($("unlock-error"), "");
  $("unlock-submit").disabled = true;
  try {
    await api("POST", "/api/review/unlock", { access_code: $("access_code").value });
    $("access_code").value = "";
    await showList();
  } catch (err) {
    const text =
      err.status === 429 ? "Too many attempts. Try again in a minute."
      : err.status === 401 ? "That code isn't right."
      : "Something went wrong. Try again.";
    setError($("unlock-error"), text);
  } finally {
    $("unlock-submit").disabled = false;
  }
});

$("lock").addEventListener("click", async () => {
  await api("POST", "/api/review/lock").catch(() => {});
  show("access");
});

$("back").addEventListener("click", showList);

showList();
