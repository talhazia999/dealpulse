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

# Which CRM the /api/deals endpoints read: "sample" (the public demo) or "hubspot" (one account,
# using HUBSPOT_ACCESS_TOKEN from a HubSpot private app or service key).
CRM_PROVIDER = os.getenv("CRM_PROVIDER", "sample")
HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")

# The public HubSpot app (OAuth install + deal card).
HUBSPOT_CLIENT_ID = os.getenv("HUBSPOT_CLIENT_ID", "")
HUBSPOT_CLIENT_SECRET = os.getenv("HUBSPOT_CLIENT_SECRET", "")
HUBSPOT_REDIRECT_URI = os.getenv("HUBSPOT_REDIRECT_URI", "")
# The public https URL of this API, e.g. https://api.dealpulse.app. Needed behind a proxy to verify signatures.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "dealpulse.db")
