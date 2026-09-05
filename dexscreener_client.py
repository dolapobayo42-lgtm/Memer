"""
dexscreener_client.py -- unchanged in spirit from before, but returns
market cap now (needed for the 10k-15k mc band check), not just liquidity.
Same caveat as before: exact endpoint built from consistent third-party
descriptions, not a directly-fetched official doc page -- verify against
live behavior on first real deploy.
"""

import requests

BASE_URL = "https://api.dexscreener.com/latest/dex/tokens"


def get_current_market_cap_usd(mint: str, timeout: int = 10):
    """Returns current USD market cap, or None if fetch failed. Returns
    0.0 if no pairs found at all (token likely dead or never got liquidity)."""
    try:
        resp = requests.get(f"{BASE_URL}/{mint}", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [dexscreener] fetch failed for {mint}: {e}")
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return 0.0

    mcs = [float(p["marketCap"]) for p in pairs if p.get("marketCap") is not None]
    return max(mcs) if mcs else 0.0
