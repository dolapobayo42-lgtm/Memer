"""
birdeye_client.py -- Holder Profile API, with a HARD daily budget cap enforced
in code. Free tier is 60 RPM / 30,000 total requests. Real observed launch
volume is ~24,000/day -- calling this on every launch would exhaust the
entire monthly quota in about a day. This client refuses to make a call once
the daily budget is spent, rather than silently overspending.
"""

import os
import time
import datetime as dt
import requests

API_KEY = os.environ.get("BIRDEYE_API_KEY", "")
BASE_URL = "https://public-api.birdeye.so/token/v1/holder-profile"

SAFE_DAILY_BUDGET = 1000  # 30,000 / 30 days, leaves headroom for retries
_calls_today = {"date": None, "count": 0}


def _check_and_increment_budget() -> bool:
    today = dt.date.today().isoformat()
    if _calls_today["date"] != today:
        _calls_today["date"] = today
        _calls_today["count"] = 0
    if _calls_today["count"] >= SAFE_DAILY_BUDGET:
        return False
    _calls_today["count"] += 1
    return True


def get_holder_profile(token_address: str, chain: str = "solana", timeout: int = 10):
    """
    Returns dict with top10_concentration_pct, insider_pct, bundler_pct,
    sniper_pct, dev_pct -- or None if budget exhausted or the call fails.
    NOTE: exact response field names are built from documentation, not a
    live-confirmed call -- verify against a real response on first deploy,
    same as any endpoint not directly tested from this sandbox.
    """
    if not API_KEY:
        print("  [birdeye] No API key configured -- skipping holder check.")
        return None

    if not _check_and_increment_budget():
        print(f"  [birdeye] Daily budget ({SAFE_DAILY_BUDGET}) exhausted -- "
              f"skipping check for {token_address[:8]}... (will resume tomorrow).")
        return None

    try:
        resp = requests.get(
            BASE_URL,
            params={"address": token_address, "chain": chain},
            headers={"X-API-KEY": API_KEY},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [birdeye] fetch failed for {token_address}: {e}")
        return None

    # Defensive parsing -- field names per docs, flagged for live verification.
    result = data.get("data", data)
    return {
        "top10_concentration_pct": result.get("top10HoldingsPercentage"),
        "insider_pct": result.get("insiderHeldPercentage") or result.get("insidersHoldingsPercentage"),
        "bundler_pct": result.get("bundlerHeldPercentage") or result.get("bundlersHoldingsPercentage"),
        "sniper_pct": result.get("sniperHeldPercentage") or result.get("snipersHoldingsPercentage"),
        "dev_pct": result.get("devHeldPercentage") or result.get("devHoldingsPercentage"),
    }


def passes_holder_filter(profile: dict, max_top10_pct: float = 50.0,
                          max_insider_pct: float = 15.0, max_bundler_pct: float = 10.0) -> bool:
    """Simple pass/fail gate. Thresholds are starting points, not tuned --
    log real outcomes against pass/fail to calibrate these over time,
    same discipline as the weather bot's real-data validation."""
    if profile is None:
        return False  # no data = can't verify = don't pass
    checks = [
        (profile.get("top10_concentration_pct"), max_top10_pct),
        (profile.get("insider_pct"), max_insider_pct),
        (profile.get("bundler_pct"), max_bundler_pct),
    ]
    for value, threshold in checks:
        if value is not None and value > threshold:
            return False
    return True

