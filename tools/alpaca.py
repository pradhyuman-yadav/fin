"""Shared Alpaca HTTP helper for the microservices. Read-only market data."""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

DATA_URL = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
TRADE_URL = os.getenv("ALPACA_TRADE_URL", "https://paper-api.alpaca.markets")
FEED = os.getenv("ALPACA_FEED", "iex")
CRYPTO_LOC = os.getenv("ALPACA_CRYPTO_LOC", "us")

_KEY = os.getenv("ALPACA_API_KEY_ID", "")
_SECRET = os.getenv("ALPACA_API_SECRET_KEY", "")


def headers():
    if not _KEY or not _SECRET:
        sys.exit("Missing Alpaca credentials (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY).")
    return {"APCA-API-KEY-ID": _KEY, "APCA-API-SECRET-KEY": _SECRET}


def get(url, params=None, timeout=30):
    """GET JSON, raising on non-200."""
    resp = requests.get(url, headers=headers(), params=params or {}, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"{url} -> {resp.status_code}: {resp.text[:200]}")
    return resp.json()
