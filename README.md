# Tennis mental performance chatbot (v1 prototype)

A text chatbot that gives mental performance support to tennis players: a
post-loss debrief, a pre-match focus routine, and help with nerves. It is not
clinical mental health support. The same app can run other chatbots beside it
for testing; see "Chatbots" below.

**This is a prototype for adult testers role-playing made-up scenarios. It is
not for real academy students or minors.**

- Testers open the app with no login; the app recognizes them by a browser cookie.
- Every player message is safety-checked before any coaching reply.
- A reviewer reads all test transcripts at `/review`, behind a shared access code.

## Setup

Needs Python 3.9 or newer. One command:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp -n .env.example .env
```

Then fill in `.env`:

| Variable               | Required            | Purpose |
| ---------------------- | ------------------- | ------- |
| `REVIEWER_ACCESS_CODE` | yes                 | Shared code for `/review`. At least 12 characters. |
| `SESSION_SECRET`       | yes                 | Signs the reviewer cookie. Any long random string. |
| `GROQ_API_KEY`         | with the real model | Model API key from <https://console.groq.com/keys>. |
| `MODEL_PROVIDER`       | no (`groq`)         | `groq` for the real model, `fake` for scripted replies with no key. |
| `GROQ_MODEL`           | no                  | Defaults to `openai/gpt-oss-120b`. |
| `DATABASE_PATH`        | no                  | Defaults to `data/app.db`. |
| `HOST`, `PORT`         | no                  | Default to `127.0.0.1` and `8000`. |

The app refuses to start, with a message saying what is missing, if a required
value is not set. Secrets are read from the environment only; `.env` is not
committed.

## Run

```sh
.venv/bin/python -m app
```

- Testers: <http://127.0.0.1:8000>
- Reviewer: <http://127.0.0.1:8000/review>

With `MODEL_PROVIDER=fake` the bot gives scripted replies, and a message
containing "flag this message" triggers the safety flow.

## Tests

```sh
.venv/bin/pytest
```

The tests use the fake model and fail if anything opens a network connection.
Test files are named after the build milestones in the spec.

## Chatbots

The main page lets a tester choose a chatbot. Each chatbot is a folder under
`bots/`; the folder name is its id. The tennis coach (`bots/tennis/`) is the one
described in the spec. `bots/assistant/` is a minimal example.

### Adding a chatbot

1. Copy `bots/assistant/` to a new folder, for example `bots/study-buddy/`
   (lowercase letters, digits, `-` and `_`).
2. Edit `bot.json`: the name and description shown on the main page, and the
   tasks, each of which becomes a button.
3. Write the instructions in `rules.txt`.

It appears on the main page on the next reload. No restart or code change is
needed, and the same is true of any later edit to these files.

```json
{
  "name": "Study buddy",
  "description": "Helps plan a revision session.",
  "tasks": [
    {"id": "plan", "button": "Plan a session", "title": "Session plan"}
  ]
}
```

### Files in a chatbot folder

| File                 | Required | What it does |
| -------------------- | -------- | ------------ |
| `bot.json`           | yes      | Name, description, tasks. Optional: `order` (position on the main page), `player_profile` (ask for the tennis player profile and include it in the prompt) |
| `rules.txt`          | yes      | The instructions sent with every reply |
| `<task id>.txt`      | no       | Extra instructions for that task |
| `opening.txt`        | no       | If present, the bot writes the first message of a session |
| `summary.txt`        | no       | If present, the bot has memory: a summary is written when a session ends and given to later sessions with this bot |
| `safety_check.txt`   | no       | Instructions for the safety classifier (default in `bots/_shared/`) |
| `safety_message.txt` | no       | The fixed message shown when a message is flagged (default in `bots/_shared/`) |

Every chatbot gets the safety check on every message; there is no setting to
turn it off. Memory is kept separately for each chatbot.

### Inspector

The **Inspector** button on the chat screen opens a side panel listing every
API call to the model for the current session: what was sent, what came back,
how long it took, and the token counts. The reviewer sees the same in the
**Model calls** tab of a session.

## How it is put together

| Path                   | Role |
| ---------------------- | ---- |
| `app/main.py`          | Builds the FastAPI app and serves the frontend |
| `app/config.py`        | Settings from the environment, checked at startup |
| `app/identity.py`      | Player cookie, reviewer cookie, access-code attempt limit |
| `app/routes/`          | Tester and reviewer HTTP endpoints |
| `app/sessions.py`      | Session lifecycle: start, message, flag, end, summarize |
| `app/safety.py`        | Safety checker, run on every player message before coaching |
| `app/bots.py`          | Loads the chatbots from `bots/` |
| `app/conversation.py`  | Builds a bot's prompt from its rules, task, profile, memory, messages |
| `app/inspector.py`     | Records every model call for the inspector |
| `app/summarizer.py`    | Memory summary and takeaway or outcome at session end |
| `app/model_client.py`  | The only module that calls a model provider; also the fake model |
| `app/db.py`            | SQLite schema and access |
| `bots/`                | One folder per chatbot: its settings and instruction files |
| `static/`              | Tester app (`index.html`, `app.js`) and reviewer app (`review.html`, `review.js`) |

To change model provider, add a client class in `app/model_client.py` and
select it in `build_model_client`. Nothing else imports a provider.

## Data

Everything is stored in one SQLite file (`data/app.db`). The prototype never
deletes data on its own. The only deletion is a player deleting their memory
summaries; sessions, messages, and safety events are always kept for review.
