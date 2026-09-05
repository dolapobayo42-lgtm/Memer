import asyncio
import json
import os
import requests
import websockets

import journal
import dexscreener_client as dex

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
CHECK_INTERVAL_MINUTES = 15
STALL_MIN_AGE_HOURS = 5.0
MC_BAND_LOW = float(os.environ.get("MC_BAND_LOW", "10000"))
MC_BAND_HIGH = float(os.environ.get("MC_BAND_HIGH", "15000"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(text: str, timeout: int = 10) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] Not configured -- printing instead:")
        print(text)
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=timeout,
        )
        return resp.ok
    except requests.RequestException as e:
        print(f"[telegram] Send failed: {e}")
        return False


def handle_message(msg: dict):
    """
    Routes both new-token and migration events. PumpPortal sends a distinct
    event shape for migrations -- exact field names verified against docs,
    not a live call, so flag it if this silently never fires once deployed.
    """
    if msg.get("txType") == "migrate" or "migration" in str(msg.get("method", "")).lower():
        mint = msg.get("mint")
        if mint:
            journal.mark_bonded(mint)
            print(f"[bonded] {mint[:8]}... has migrated/bonded.")
        return

    mint = msg.get("mint")
    deployer = msg.get("traderPublicKey") or msg.get("creator")
    name = msg.get("name", "")
    symbol = msg.get("symbol", "")
    if not mint or not deployer:
        return  # silent skip -- NOT printed, this is the fix for the log flood

    journal.log_launch(mint, deployer, name, symbol)  # no print here either


async def listen():
    backoff = 1
    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                print("[listener] Connected, subscribed to new tokens + migrations.")
                backoff = 1
                async for raw_msg in ws:
                    try:
                        handle_message(json.loads(raw_msg))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"[listener] Connection error: {e} -- reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def run_stall_check_cycle():
    candidates = journal.get_stalled_candidates(min_age_hours=STALL_MIN_AGE_HOURS)
    print(f"[stall-check] {len(candidates)} unbonded launches old enough to check.")
    for row in candidates:
        mc = dex.get_current_market_cap_usd(row["mint"])
        if mc is None:
            continue
        if MC_BAND_LOW <= mc <= MC_BAND_HIGH:
            alert = (
                f"\U0001F7E1 STALLED SURVIVOR (unbonded, {STALL_MIN_AGE_HOURS}h+, "
                f"mc in ${MC_BAND_LOW:.0f}-${MC_BAND_HIGH:.0f} band)\n"
                f"Token: {row['name']} ({row['symbol']})\n"
                f"Mint: {row['mint']}\n"
                f"Current MC: ${mc:.0f}\n"
                f"Deployer: {row['deployer']}\n"
                f"-- watch-only, no trade placed."
            )
            send_telegram(alert)
            journal.mark_alerted(row["mint"], mc)
            print(f"  ALERTED: {row['symbol']} at ${mc:.0f} mc.")
        else:
            # Still mark alerted=no (leave as-is) so it gets re-checked next
            # cycle in case it moves into the band later. No print -- avoids
            # re-flooding logs with every miss every 15 minutes.
            pass


async def periodic_stall_checker():
    while True:
        try:
            run_stall_check_cycle()
        except Exception as e:
            print(f"[stall-check] Unexpected error: {e}")
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


async def main():
    print("=" * 50)
    print("Stalled Unbonded Survivor Tracker -- starting")
    print(f"Journal path: {os.path.abspath(journal.JOURNAL_PATH)}")
    print(f"MC band: ${MC_BAND_LOW:.0f}-${MC_BAND_HIGH:.0f}, min age: {STALL_MIN_AGE_HOURS}h")
    print("=" * 50)
    
    # Send startup checklist to Telegram
    startup_msg = (
        f"✅ Tracker Online\n"
        f"✓ PumpPortal listener: subscribeNewToken + subscribeMigration\n"
        f"✓ Journal: {os.path.abspath(journal.JOURNAL_PATH)}\n"
        f"✓ Market cap band: ${MC_BAND_LOW:.0f}–${MC_BAND_HIGH:.0f}\n"
        f"✓ Stall check interval: {CHECK_INTERVAL_MINUTES} min\n"
        f"✓ Min age for check: {STALL_MIN_AGE_HOURS}h\n"
        f"— Scanning silently, alerts on findings."
    )
    send_telegram(startup_msg)
    
    await asyncio.gather(listen(), periodic_stall_checker())


if __name__ == "__main__":
    asyncio.run(main())
