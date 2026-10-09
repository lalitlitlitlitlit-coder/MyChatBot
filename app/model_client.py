"""The only module that talks to a model provider (NFR-04).

Everything else calls `ModelClient.complete(purpose, system, messages)`. To
change provider, add a subclass here and select it in `build_model_client`.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

log = logging.getLogger(__name__)

# What a call is for. Providers may ignore it; the fake model scripts by it.
COACH, SAFETY, SUMMARY = "coach", "safety", "summary"
PURPOSES = (COACH, SAFETY, SUMMARY)

_workers = ThreadPoolExecutor(max_workers=16, thread_name_prefix="model")


class ModelError(Exception):
    """The provider failed or returned something unusable."""


class ModelTimeout(ModelError):
    """The provider did not answer within the timeout (NFR-07)."""


class ModelClient:
    name = "unknown"  # shown in the reviewer's inspector

    def __init__(self, timeout_seconds=30.0):
        self.timeout_seconds = timeout_seconds

    def complete(self, purpose, system, messages):
        """Return the model's text reply.

        `messages` is a list of {"role": "user" | "assistant", "content": str}.
        Raises ModelTimeout after `timeout_seconds`, ModelError on any other failure.
        """
        return self.complete_with_usage(purpose, system, messages)[0]

    def complete_with_usage(self, purpose, system, messages):
        """Like `complete`, but returns (reply, usage).

        `usage` holds prompt_tokens and completion_tokens when the provider reports them.
        """
        future = _workers.submit(self._send, purpose, system, messages)
        try:
            reply = future.result(timeout=self.timeout_seconds)
        except FutureTimeout:
            future.cancel()
            log.warning("model call timed out purpose=%s", purpose)
            raise ModelTimeout("The model took too long to reply.")
        except ModelError:
            log.warning("model call failed purpose=%s", purpose)
            raise
        except Exception as exc:
            # Log the error type only: provider errors can echo message content (NFR-06).
            log.warning("model call failed purpose=%s error=%s", purpose, type(exc).__name__)
            raise ModelError("The model call failed.") from exc
        reply, usage = reply if isinstance(reply, tuple) else (reply, {})
        if not isinstance(reply, str) or not reply.strip():
            raise ModelError("The model returned an empty reply.")
        return reply.strip(), usage if isinstance(usage, dict) else {}

    def _send(self, purpose, system, messages):
        """Return the reply text, or (text, usage dict)."""
        raise NotImplementedError


class GroqClient(ModelClient):
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key, model, timeout_seconds=30.0):
        super().__init__(timeout_seconds)
        self._api_key = api_key
        self._model = model
        self.name = model

    def _send(self, purpose, system, messages):
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}] + messages,
            # Classification and summaries should be repeatable.
            "temperature": 0.7 if purpose == COACH else 0,
        }
        request = urllib.request.Request(
            self.URL,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                # Groq's CDN rejects urllib's default User-Agent.
                "User-Agent": "TennisMentalPerformancePrototype/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.load(response)
            return body["choices"][0]["message"]["content"], body.get("usage") or {}
        except urllib.error.HTTPError as exc:
            raise ModelError(f"Groq returned HTTP {exc.code}") from None
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"Groq call failed: {type(exc).__name__}") from None


class Sleep:
    """A scripted step for the fake model: wait, then reply normally."""

    def __init__(self, seconds):
        self.seconds = seconds


class FakeModelClient(ModelClient):
    """Scripted replies with no network (NFR-05).

    `script(purpose, *steps)` queues steps for that purpose. A step is a reply
    string, an Exception to raise, or `Sleep(seconds)`. With nothing queued it
    gives a plain default, so the app can also be run locally without an API key.
    """

    name = "fake"
    # Lets a local tester trigger the safety flow by hand when using the fake.
    FLAG_PHRASE = "flag this message"

    def __init__(self, timeout_seconds=30.0):
        super().__init__(timeout_seconds)
        self.calls = []
        self._scripts = {purpose: deque() for purpose in PURPOSES}

    def script(self, purpose, *steps):
        self._scripts[purpose].extend(steps)

    def calls_for(self, purpose):
        return [call for call in self.calls if call["purpose"] == purpose]

    def _send(self, purpose, system, messages):
        self.calls.append({"purpose": purpose, "system": system, "messages": messages})
        queue = self._scripts[purpose]
        step = queue.popleft() if queue else None
        if isinstance(step, Sleep):
            time.sleep(step.seconds)
            step = None
        if isinstance(step, Exception):
            raise step
        return step if step is not None else self._default(purpose, messages)

    def _default(self, purpose, messages):
        if purpose == SAFETY:
            flagged = self.FLAG_PHRASE in messages[-1]["content"].lower()
            return "self_harm" if flagged else "none"
        if purpose == SUMMARY:
            return json.dumps(
                {"summary": ["Fake model summary of the session"], "result": "Fake model result"}
            )
        return "This is a scripted reply from the fake model."


def build_model_client(settings):
    if settings.model_provider == "fake":
        return FakeModelClient(settings.model_timeout_seconds)
    return GroqClient(settings.groq_api_key, settings.groq_model, settings.model_timeout_seconds)
