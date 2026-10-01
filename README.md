# MyChatBot

A small web chatbot that talks to [Groq](https://groq.com)'s free tier using
OpenAI's open-weight `openai/gpt-oss-120b` model. Replies stream in as they are
generated.

No dependencies: the server is plain Python (3.9+) and the frontend is plain
HTML, CSS and JavaScript.

## Setup

1. Create a free API key at <https://console.groq.com/keys>.
2. Copy the example config and paste your key into it:

   ```sh
   cp .env.example .env
   ```

3. Start the server:

   ```sh
   python3 server.py
   ```

4. Open <http://127.0.0.1:8000>.

## Configuration

Set these in `.env` or as environment variables.

| Variable       | Default               | Purpose                         |
| -------------- | --------------------- | ------------------------------- |
| `GROQ_API_KEY` | (required)            | Your Groq API key               |
| `GROQ_MODEL`   | `openai/gpt-oss-120b` | Any chat model available on Groq |
| `HOST`         | `127.0.0.1`           | Address to listen on            |
| `PORT`         | `8000`                | Port to listen on               |

## How it works

- `server.py` serves the files in `static/` and exposes `POST /api/chat`, which
  forwards the conversation to Groq's OpenAI-compatible API. The API key stays
  on the server and is never sent to the browser.
- `static/app.js` keeps the conversation in memory and renders the streamed
  reply. Refreshing the page or clicking **New chat** starts over.
