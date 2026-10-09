"""Safety checker: classifies each player message before any coaching (FR-18)."""
from .model_client import SAFETY, ModelError

CATEGORIES = ("self_harm", "suicide", "harm_to_others", "abuse")
SAFE = "none"
CONTEXT_MESSAGES = 6
SPEAKERS = {"player": "Player", "bot": "Coach"}


def check(model, prompts, earlier, content):
    """Return a category from CATEGORIES, or None if the message is safe.

    Raises ModelError if the check cannot be completed or its answer is not one
    of the expected words, so the caller fails closed (FR-20).
    """
    context = "\n".join(
        f"{SPEAKERS[m['sender']]}: {m['content']}"
        for m in earlier[-CONTEXT_MESSAGES:]
        if m["sender"] in SPEAKERS
    )
    request = f"Earlier messages:\n{context or '(none)'}\n\nLatest player message:\n{content}"
    answer = model.complete(SAFETY, prompts.get("safety_check"), [{"role": "user", "content": request}])
    answer = answer.strip().strip(".\"'`").lower()
    if answer == SAFE:
        return None
    if answer in CATEGORIES:
        return answer
    raise ModelError("The safety check gave an unexpected answer.")
