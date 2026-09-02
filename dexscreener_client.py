"""
dexscreener_client.py -- small helper to fetch current liquidity for a token
using DexScreener's public API. This implementation is defensive: it won't
raise if the optional `requests` package is missing, and it returns `None`
when the fetch or parsing fails so callers can retry later.
"""

try:
    import requests
except Exception:
    requests = None

API_BASE = "https://api.dexscreener.com/latest/dex/tokens"


def get_current_liquidity_usd(token_address: str, timeout: int = 10) -> float | None:
    """Return an approximate USD liquidity for the given token address, or
    None if the value can't be determined (fetch failure, parsing error,
    or requests not installed).

    Note: DexScreener's responses vary by chain and pair; we defensively
    inspect returned pairs and try common fields like `liquidityUsd`.
    """
    if requests is None:
        print("  [dex] 'requests' package not installed; cannot fetch liquidity.")
        return None

    try:
        resp = requests.get(f"{API_BASE}/{token_address}", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"  [dex] fetch failed for {token_address}: {e}")
        return None
    except ValueError:
        print(f"  [dex] invalid JSON response for {token_address}")
        return None

    pairs = data.get("pairs") or data.get("pairs", [])
    if not pairs:
        return None

    # Look for a numeric liquidity field in common names
    for p in pairs:
        for key in ("liquidityUsd", "liquidity_usd", "liquidity", "reserveUsd", "reserve_usd"):
            val = p.get(key)
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue

    # Nothing found we can parse
    return None


__all__ = ["get_current_liquidity_usd"]
