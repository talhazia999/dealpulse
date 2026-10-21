import os

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
# low | medium | high | xhigh | max. "medium" keeps a deal analysis fast while staying accurate.
ANTHROPIC_EFFORT = os.getenv("ANTHROPIC_EFFORT", "medium")
HAS_ANTHROPIC_CREDENTIALS = any(
    os.getenv(name) for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")
)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
