"""MyChatBot - a small chat web app backed by Groq's free tier.

Standard library only: serves the static frontend and proxies chat requests
to Groq so the API key never reaches the browser.
"""
import json
import os
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
SYSTEM_PROMPT = "You are a helpful, concise assistant."
MAX_HISTORY = 40
MAX_BODY_BYTES = 1_000_000


def load_env(path):
    """Read KEY=VALUE lines from a .env file without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def clean_messages(raw):
    """Keep only well-formed user/assistant turns from the browser."""
    if not isinstance(raw, list):
        raise ValueError("'messages' must be a list")
    messages = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role, content = item.get("role"), item.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content})
    if not messages or messages[-1]["role"] != "user":
        raise ValueError("The last message must be from the user")
    return messages[-MAX_HISTORY:]


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/chat":
            self.send_json(404, {"error": "Not found"})
            return

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            self.send_json(500, {"error": "GROQ_API_KEY is not set. Add it to .env and restart the server."})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY_BYTES:
                self.send_json(413, {"error": "Request too large"})
                return
            body = json.loads(self.rfile.read(length) or b"{}")
            messages = clean_messages(body.get("messages"))
        except (ValueError, AttributeError) as exc:
            self.send_json(400, {"error": str(exc)})
            return

        payload = {
            "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "stream": True,
        }
        request = urllib.request.Request(
            GROQ_URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # Groq's CDN rejects urllib's default User-Agent.
                "User-Agent": "MyChatBot/1.0",
            },
        )

        try:
            upstream = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            self.send_json(exc.code, {"error": groq_error_message(exc)})
            return
        except OSError as exc:
            self.send_json(502, {"error": f"Could not reach Groq: {exc}"})
            return

        # Relay Groq's server-sent events straight through to the browser.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with upstream:
            try:
                for line in upstream:
                    self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # browser went away mid-stream

    def send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def groq_error_message(exc):
    try:
        return json.loads(exc.read())["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"Groq returned HTTP {exc.code}"


def main():
    load_env(ROOT / ".env")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), partial(Handler, directory=str(STATIC_DIR)))
    print(f"MyChatBot running at http://{host}:{port}")
    if not os.environ.get("GROQ_API_KEY"):
        print("Warning: GROQ_API_KEY is not set - copy .env.example to .env and add your key.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
