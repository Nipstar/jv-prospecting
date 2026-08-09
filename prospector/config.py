"""Central configuration for prospector.

Secrets are read from .env (never hardcoded) and reused from Andy's existing
geo-prospecting project — prospector/.env is a symlink to
geo-prospecting/.env by default, so both tools share one set of credentials.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT
DB_PATH = DATA_DIR / "prospector.db"
EXPORTS_DIR = ROOT / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# --- Secrets (read from .env, never hardcoded) ------------------------------
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")

# --- API endpoints -----------------------------------------------------------
SERPAPI_BASE_URL = "https://serpapi.com/search"
COMPANIES_HOUSE_BASE_URL = "https://api.company-information.service.gov.uk"
APIFY_BASE_URL = "https://api.apify.com/v2"

APIFY_ACTOR_FB_ADS = "curious_coder/facebook-ads-library-scraper"
APIFY_ACTOR_GOOGLE_ADS = "scrapesage/google-ads-transparency-scraper"

# --- Retry / backoff ----------------------------------------------------------
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 1.5

# --- Apify pricing estimate (per spec) ---------------------------------------
APIFY_COST_PER_FB_RUN = 0.00075
APIFY_COST_PER_GOOGLE_RUN = 0.002  # avg for ~10 ads

# --- Google search geo defaults -----------------------------------------------
GOOGLE_DOMAIN = "google.co.uk"
HL = "en"
GL = "gb"


def missing_keys() -> list[str]:
    """Return names of any required secrets that are blank."""
    missing = []
    if not SERPAPI_KEY:
        missing.append("SERPAPI_KEY")
    if not COMPANIES_HOUSE_API_KEY:
        missing.append("COMPANIES_HOUSE_API_KEY")
    if not APIFY_TOKEN:
        missing.append("APIFY_TOKEN")
    return missing
