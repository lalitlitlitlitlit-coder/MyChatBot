"""Conversation service: builds a bot's prompt and asks the model (FR-11)."""
from .model_client import COACH

LEVEL_LABELS = {
    "beginner": "beginner",
    "intermediate": "intermediate",
    "advanced": "advanced",
    "national_junior": "national junior",
    "international_junior": "international junior",
    "college": "college",
    "professional": "professional",
}
ROLES = {"player": "user", "bot": "assistant"}


def profile_block(profile):
    lines = [
        "Player profile",
        f"- Name: {profile['display_name']}",
        f"- Age: {profile['age']}",
        f"- Playing level: {LEVEL_LABELS[profile['playing_level']]}",
        f"- Pressure points: {profile['pressure_points'] or 'none given'}",
    ]
    return "\n".join(lines)


def memory_block(summaries):
    if not summaries:
        return "Notes from past sessions\nNone. Treat this as your first session with this player."
    parts = ["Notes from past sessions (newest first)"]
    for summary in summaries:
        parts.append(f"{summary['created_at'][:10]}, {summary['task_type']}:\n{summary['text']}")
    return "\n".join(parts)


def system_prompt(bot, task_type, profile, summaries):
    """The bot's rules, then whichever of these the bot uses: task, profile, memory."""
    parts = [bot.prompts.get("rules")]
    if bot.prompts.has(task_type):
        parts.append(bot.prompts.get(task_type))
    if bot.needs_profile and profile:
        parts.append(profile_block(profile))
    if bot.has_memory:
        parts.append(memory_block(summaries))
    return "\n\n".join(parts)


def model_messages(bot, messages):
    """The session so far, starting with the instruction that produced the opening, if any."""
    turns = [{"role": "user", "content": bot.prompts.get("opening")}] if bot.has_opening else []
    turns += [
        {"role": ROLES[m["sender"]], "content": m["content"]}
        for m in messages
        if m["sender"] in ROLES
    ]
    return turns


def coach_reply(model, bot, task_type, profile, summaries, messages):
    """The next bot message. With no messages yet, this is the opening message."""
    return model.complete(
        COACH,
        system_prompt(bot, task_type, profile, summaries),
        model_messages(bot, messages),
    )
