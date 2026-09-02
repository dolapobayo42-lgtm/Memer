"""
dexscreener_client.py -- defensive liquidity fetch with simple rate-limiting
and in-process caching to avoid overwhelming DexScreener's public API.

Behavior summary:
- Caches recent successful liquidity values for CACHE_TTL seconds.
- Enforces a simple sliding-window rate limit (RATE_LIMIT_PER_MIN requests per minute).
- If the remote returns 429, enters a brief cooldown (COOLDOWN_ON_429 seconds)
  during which requests are suppressed and None is returned immediately.
- Never raises on network/parse errors; returns None so callers can retry later.
"""

from collections import deque
import time
from typing import Optional

try:
    import requests
except Exception:
    requests = None

API_BASE = "https://api.dexscreener.com/latest/dex/tokens"

# Rate limiting / caching policy - tweak these to match your allowed quota
RATE_LIMIT_PER_MIN = 60  # requests per 60s window
CACHE_TTL = 300  # seconds to cache a successful liquidity value
COOLDOWN_ON_429 = 60  # seconds to back off after receiving a 429

_request_timestamps = deque()  # epoch seconds of recent requests
_cooldown_until = 0.0
_cache: dict[str, tuple[float, Optional[float]]] = {}  # token -> (ts, liquidity)


def _allow_request() -> bool:
    """Return True if we should make a request right now, False otherwise.

    This implements a sliding-window rate limiter using _request_timestamps.
    When the window is full we suppress the request and set a short cooldown
    so we don't hammer the upstream until the window loosens.
    """
    global _request_timestamps, _cooldown_until
    now = time.time()
    if now < _cooldown_until:
        return False
    # prune timestamps older than 60s
    cutoff = now - 60
    while _request_timestamps and _request_timestamps[0] < cutoff:
        _request_timestamps.popleft()
    if len(_request_timestamps) >= RATE_LIMIT_PER_MIN:
        # enter a short cooldown to stop repeated immediate attempts
        _cooldown_until = now + 5
        return False
    # allow
    _request_timestamps.append(now)
    return True


def _update_cache(token: str, liquidity: Optional[float]) -> None:
    _cache[token] = (time.time(), liquidity)


def _get_cached(token: str) -> Optional[float]:
    item = _cache.get(token)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > CACHE_TTL:
        try:
            del _cache[token]
        except KeyError:
            pass
        return None
    return val


def get_current_liquidity_usd(token_address: str, timeout: int = 10) -> Optional[float]:
    """Return an approximate USD liquidity for the given token address, or
    None if the value can't be determined (fetch failure, parsing error,
    rate-limited, or requests not installed).

    This function is intentionally conservative: when rate limited it returns
    None and lets the caller retry later. Successful results are cached for
    CACHE_TTL seconds to reduce repeated calls for the same token.
    """
    global _cooldown_until
    if requests is None:
        print("  [dex] 'requests' package not installed; cannot fetch liquidity.")
        return None

    # Normalized token key for cache lookups
    token = token_address.strip()

    # Return cached value when available
    cached = _get_cached(token)
    if cached is not None:
        return cached

    now = time.time()
    if now < _cooldown_until:
        print(f"  [dex] in cooldown until {_cooldown_until:.0f}; skipping fetch for {token[:8]}...")
        return None

    if not _allow_request():
        print(f"  [dex] local rate limit reached; skipping fetch for {token[:8]}...")
        return None

    url = f"{API_BASE}/{token}"
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        print(f"  [dex] fetch failed for {token}: {e}")
        return None

    if resp.status_code == 429:
        # upstream told us to slow down; enter cooldown
        _cooldown_until = time.time() + COOLDOWN_ON_429
        print(f"  [dex] received 429 for {token[:8]}... -- entering cooldown for {COOLDOWN_ON_429}s")
        return None

    if not resp.ok:
        print(f"  [dex] fetch failed for {token}: {resp.status_code} {resp.text}")
        return None

    try:
        data = resp.json()
    except ValueError:
        print(f"  [dex] invalid JSON response for {token}")
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        _update_cache(token, None)
        return None

    # Look for a numeric liquidity field in common names
    for p in pairs:
        for key in ("liquidityUsd", "liquidity_usd", "liquidity", "reserveUsd", "reserve_usd"):
            val = p.get(key)
            if val is None:
                continue
            try:
                liq = float(val)
                _update_cache(token, liq)
                return liq
            except (TypeError, ValueError):
                continue

    # Nothing found we can parse
    _update_cache(token, None)
    return None


__all__ = ["get_current_liquidity_usd"]
