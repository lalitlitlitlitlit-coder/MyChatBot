"""Summarizer: the memory summary and the takeaway or outcome at session end."""
import json

from .model_client import SUMMARY, ModelError

MAX_BULLETS = 5  # FR-21
MAX_RESULT_CHARS = 500
SPEAKERS = {"player": "Player", "bot": "Coach"}


def summarize(model, prompts, task_type, messages):
    """Return (summary_text, result). `result` is the takeaway or outcome, or None.

    Raises ModelError if the model fails or its reply cannot be used.
    """
    transcript = "\n".join(
        f"{SPEAKERS[m['sender']]}: {m['content']}" for m in messages if m["sender"] in SPEAKERS
    )
    raw = model.complete(
        SUMMARY,
        prompts.get("summary"),
        [{"role": "user", "content": f"Session type: {task_type}\n\nTranscript:\n{transcript}"}],
    )
    return parse(raw)


def parse(raw):
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start : end + 1])
        if not isinstance(data["summary"], list):
            raise TypeError("summary must be a list")
        bullets = [str(b).strip().lstrip("-•* ").strip() for b in data["summary"]]
    except (ValueError, KeyError, TypeError):
        raise ModelError("The summary reply was not valid JSON.") from None
    bullets = [b for b in bullets if b][:MAX_BULLETS]
    if not bullets:
        raise ModelError("The summary reply had no bullet points.")

    result = data.get("result")
    result = result.strip()[:MAX_RESULT_CHARS] if isinstance(result, str) else None
    return "\n".join(f"- {b}" for b in bullets), result or None
