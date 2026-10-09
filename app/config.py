"""Settings read from environment variables (NFR-03), checked at startup."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_ACCESS_CODE_LENGTH = 12
MODEL_PROVIDERS = ("groq", "fake")


class ConfigError(Exception):
    """A required setting is missing or invalid; the app must not start."""


@dataclass(frozen=True)
class Settings:
    reviewer_access_code: str
    session_secret: str
    model_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    model_timeout_seconds: float = 30.0  # NFR-07
    database_path: Path = ROOT / "data" / "app.db"
    bots_dir: Path = ROOT / "bots"
    static_dir: Path = ROOT / "static"


def load_env_file(path):
    """Read KEY=VALUE lines from a .env file without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_settings(env=None):
    """Build Settings from the environment, refusing to start on bad config."""
    env = os.environ if env is None else env

    access_code = env.get("REVIEWER_ACCESS_CODE", "")
    if len(access_code) < MIN_ACCESS_CODE_LENGTH:
        raise ConfigError(
            f"REVIEWER_ACCESS_CODE must be set to at least {MIN_ACCESS_CODE_LENGTH} characters."
        )

    session_secret = env.get("SESSION_SECRET", "")
    if not session_secret:
        raise ConfigError("SESSION_SECRET must be set (it signs the reviewer cookie).")

    provider = env.get("MODEL_PROVIDER", "groq")
    if provider not in MODEL_PROVIDERS:
        raise ConfigError(f"MODEL_PROVIDER must be one of: {', '.join(MODEL_PROVIDERS)}.")

    groq_api_key = env.get("GROQ_API_KEY", "")
    if provider == "groq" and not groq_api_key:
        raise ConfigError("GROQ_API_KEY must be set when MODEL_PROVIDER is groq.")

    settings = {
        "reviewer_access_code": access_code,
        "session_secret": session_secret,
        "model_provider": provider,
        "groq_api_key": groq_api_key,
    }
    if env.get("GROQ_MODEL"):
        settings["groq_model"] = env["GROQ_MODEL"]
    if env.get("DATABASE_PATH"):
        settings["database_path"] = Path(env["DATABASE_PATH"])
    return Settings(**settings)
