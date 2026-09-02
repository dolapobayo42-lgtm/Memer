"""
main.py -- listens for new Pump.fun launches in real time (PumpPortal
WebSocket, confirmed free/real per official docs and multiple independent
examples), logs every launch, periodically re-checks 24h+-old launches for
liquidity to build real per-deployer death-rate history, and alerts on
Telegram when a launch comes from a known serial-rugger deployer.

This is WATCH-ONLY / DATA-COLLECTION for now -- it places no trades. The
goal is the same as the weather bot's first phase: build a real, verified
dataset before trusting any filter enough to act on it.
"""

import asyncio
import json
import os
import time
try:
    import requests
except Exception:
    # Defer graceful handling to send_telegram so the process can still run
    # when requests isn't installed (e.g., Telegram not configured in this
    # container). We still add requirements.txt so containers can opt-in to
    # install it.
    requests = None
try:
    import websockets
except Exception:
    # If websockets isn't installed, we disable the live listener rather
    # than crashing at import time. The rest of the background checks can
    # still run.
    websockets = None

import journal
import dexscreener_client as dex
import birdeye_client as birdeye

PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
LIQUIDITY_CHECK_INTERVAL_MINUTES = 30
MIN_HISTORY_FOR_CLASSIFICATION = 3

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_telegram(text: str, timeout: int = 10) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[telegram] Not configured -- printing instead:")
        print(text)
        return False
    if requests is None:
        # If requests isn't available, fall back to printing rather than
        # crashing at import time. This keeps the service running for
        # environments that don't need Telegram alerts.
        print("[telegram] 'requests' package not installed; printing instead:")
        print(text)
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=timeout,
        )
        return resp.ok
    except Exception as e:
        # Use a broad Exception here because requests may not be the exception
        # type on some failures, and we already guard against requests missing.
        print(f"[telegram] Send failed: {e}")
        return False


def handle_new_token_message(msg: dict):
    """
    Called for every subscribeNewToken event. Real PumpPortal payload
    includes at minimum: mint, name, symbol, and the creator/deployer
    address (field name confirmed as part of the creation event schema
    per official examples -- verify exact key name against a live message
    on first real run, since PumpPortal's docs show the general shape but
    this sandbox can't confirm the literal field name against a live feed).
    """
    mint = msg.get("mint")
    deployer = msg.get("traderPublicKey") or msg.get("creator")
    name = msg.get("name", "")
    symbol = msg.get("symbol", "")
    initial_liq = msg.get("vSolInBondingCurve")  # rough proxy at launch time

    if not mint or not deployer:
        print(f"  [listener] Skipping malformed message (missing mint/deployer): {msg}")
        return

    classification = journal.classify_deployer(deployer)
    stats = journal.deployer_stats(deployer)

    print(f"  New launch: {symbol} ({mint[:8]}...) by {deployer[:8]}... "
          f"[deployer classification: {classification}, history: {stats['checked']} checked, "
          f"death_rate: {stats['death_rate']}]")

    journal.log_launch(mint, deployer, name, symbol, initial_liq)

    if classification == "serial_rugger":
        alert = (
            f"\u26a0\ufe0f SERIAL RUGGER LAUNCH DETECTED\n"
            f"Token: {name} ({symbol})\n"
            f"Mint: {mint}\n"
            f"Deployer: {deployer}\n"
            f"Deployer history: {stats['checked']} launches, "
            f"{stats['death_rate']:.0%} death rate\n"
            f"-- watch-only, no trade placed. Logged for tracking."
        )
        send_telegram(alert)


async def listen_for_launches():
    """Long-running WebSocket listener. Reconnects with backoff on failure --
    important per PumpPortal's own docs: don't hammer new connections, reuse
    one persistent connection."""
    if websockets is None:
        # Defensive guard in case this function is accidentally called even
        # though the import failed earlier.
        print("[listener] 'websockets' package not installed; listener disabled.")
        return

    backoff = 1
    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print("[listener] Connected and subscribed to new token launches.")
                backoff = 1  # reset on successful connect
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                        handle_new_token_message(msg)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            print(f"[listener] Connection error: {e} -- reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def run_liquidity_check_cycle():
    """Call periodically (separate from the WebSocket loop) to re-check
    24h+-old launches and update deployer death-rate history."""
    pending = journal.get_pending_checks(min_age_hours=24.0)
    print(f"[liquidity-check] {len(pending)} launches ready for re-check.")
    for row in pending:
        liq = dex.get_current_liquidity_usd(row["mint"])
        if liq is None:
            continue  # fetch failed, try again next cycle
        journal.mark_checked(row["mint"], liq)
        print(f"  {row['symbol']} ({row['mint'][:8]}...): "
              f"${liq:.0f} liquidity -> {'DEAD' if liq < 1000 else 'ALIVE'}")


async def periodic_liquidity_checker():
    while True:
        try:
            run_liquidity_check_cycle()
        except Exception as e:
            print(f"[liquidity-check] Unexpected error: {e}")
        await asyncio.sleep(LIQUIDITY_CHECK_INTERVAL_MINUTES * 60)


async def periodic_stage1_birdeye_checker():
    while True:
        try:
            run_stage1_and_birdeye_cycle()
        except Exception as e:
            print(f"[stage1/birdeye] Unexpected error: {e}")
        await asyncio.sleep(5 * 60)  # every 5 min -- Stage 1's own 15-min age
                                       # gate controls actual check timing


async def main():
    print("=" * 50)
    print("Deployer Reputation Tracker -- starting")
    print(f"Journal path: {os.path.abspath(journal.JOURNAL_PATH)}")
    print("=" * 50)

    # Build the task list dynamically so missing optional packages don't
    # cause import-time crashes; if websockets is missing we skip the
    # listener and keep the background cycles running.
    tasks = [periodic_liquidity_checker(), periodic_stage1_birdeye_checker()]
    if websockets is not None:
        tasks.insert(0, listen_for_launches())
    else:
        print("[main] 'websockets' package not installed; WebSocket listener disabled.")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())


def run_stage1_and_birdeye_cycle():
    """
    The two-stage filter: Stage 1 is free (DexScreener, ~15min wait) and cuts
    ~96% of dead-on-arrival launches before Stage 2 (budgeted Birdeye) ever
    gets called on the survivors. This is required given real observed volume
    (~24,000 launches/day) vs. Birdeye's free-tier 30,000/month total budget.
    """
    stage1_pending = journal.get_pending_stage1_checks(min_age_minutes=15.0)
    print(f"[stage1] {len(stage1_pending)} launches ready for free liquidity pre-check.")
    for row in stage1_pending:
        liq = dex.get_current_liquidity_usd(row["mint"])
        if liq is None:
            continue
        passed = liq >= 1000.0  # still has real liquidity after 15 min
        journal.mark_stage1_result(row["mint"], passed)
        if passed:
            print(f"  [stage1] {row['symbol']} PASSED (${liq:.0f} liquidity) -> queued for Birdeye check.")

    birdeye_pending = journal.get_pending_birdeye_checks()
    print(f"[birdeye] {len(birdeye_pending)} Stage-1 survivors ready for holder-quality check.")
    for row in birdeye_pending:
        profile = birdeye.get_holder_profile(row["mint"])
        if profile is None:
            continue  # budget exhausted or fetch failed -- try again next cycle, don't lose it
        passed = birdeye.passes_holder_filter(profile)
        journal.mark_birdeye_result(row["mint"], passed)
        print(f"  [birdeye] {row['symbol']}: {'PASSED' if passed else 'FAILED'} holder-quality filter "
              f"(top10={profile.get('top10_concentration_pct')}%, insider={profile.get('insider_pct')}%)")

        if passed:
            deployer_class = journal.classify_deployer(row["deployer"])
            if deployer_class != "serial_rugger":
                alert = (
                    f"\U0001F7E2 CANDIDATE (passed all free + paid filters)\n"
                    f"Token: {row['name']} ({row['symbol']})\n"
                    f"Mint: {row['mint']}\n"
                    f"Deployer: {row['deployer']} [{deployer_class}]\n"
                    f"-- watch-only, no trade placed. This is a rare event by design."
                )
                send_telegram(alert)
