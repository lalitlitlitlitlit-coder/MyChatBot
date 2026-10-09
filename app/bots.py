"""The chatbots this app can run. Each one is a folder under bots/.

A bot folder holds `bot.json` (name and tasks) and editable text files (NFR-10):
`rules.txt` is required; `<task id>.txt`, `opening.txt`, and `summary.txt` are
optional and switch on task instructions, a bot-written opening message, and
memory. Safety files fall back to bots/_shared/. Folders are re-read on every
use, so edits and new bots apply without a restart or a code change.

Not part of SPEC.md, which describes the tennis bot only.
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

SHARED = "_shared"
BOT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class BotPrompts:
    """A bot's text files, with bots/_shared/ as the fallback."""

    def __init__(self, directory, shared):
        self._directories = (Path(directory), Path(shared))

    def _path(self, name):
        for directory in self._directories:
            path = directory / f"{name}.txt"
            if path.is_file():
                return path
        return None

    def has(self, name):
        return self._path(name) is not None

    def get(self, name):
        path = self._path(name)
        if path is None:
            raise FileNotFoundError(f"No prompt file named {name}.txt")
        return path.read_text(encoding="utf-8").strip()


class Bot:
    def __init__(self, bot_id, config, prompts):
        self.id = bot_id
        self.name = config["name"]
        self.description = config.get("description", "")
        self.order = config.get("order", 100)
        # Asks for the tennis player profile at first use and puts it in the prompt.
        self.needs_profile = bool(config.get("player_profile", False))
        self.tasks = [
            {
                "id": task["id"],
                "button": task.get("button") or task.get("title") or task["id"],
                "title": task.get("title") or task["id"],
                "result_label": task.get("result_label") or "Result",
            }
            for task in config["tasks"]
        ]
        self.prompts = prompts

    @property
    def has_memory(self):
        return self.prompts.has("summary")

    @property
    def has_opening(self):
        return self.prompts.has("opening")

    def task(self, task_id):
        return next((task for task in self.tasks if task["id"] == task_id), None)

    def public(self):
        """What the browser needs to show this bot."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "needs_profile": self.needs_profile,
            "has_memory": self.has_memory,
            "tasks": self.tasks,
        }


class Bots:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _load(self, folder):
        try:
            config = json.loads((folder / "bot.json").read_text(encoding="utf-8"))
            prompts = BotPrompts(folder, self.directory / SHARED)
            bot = Bot(folder.name, config, prompts)
            if not bot.tasks or not prompts.has("rules"):
                raise ValueError("a bot needs at least one task and a rules.txt")
            return bot
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("skipping bot folder %s: %s", folder.name, type(exc).__name__)
            return None

    def all(self):
        if not self.directory.is_dir():
            return []
        folders = [f for f in self.directory.iterdir() if f.is_dir() and BOT_ID.match(f.name)]
        bots = [bot for bot in map(self._load, folders) if bot]
        return sorted(bots, key=lambda bot: (bot.order, bot.name.lower()))

    def get(self, bot_id):
        if not isinstance(bot_id, str) or not BOT_ID.match(bot_id):
            return None
        folder = self.directory / bot_id
        return self._load(folder) if folder.is_dir() else None
