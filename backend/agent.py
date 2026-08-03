"""
Arrissa trading agent — turns natural-language instructions into trading API
actions, across one or many accounts, streaming its thinking + actions.

Providers: Anthropic (Claude), OpenAI, DeepSeek (OpenAI-compatible).
Tools are token-efficient (search symbols, fetch one symbol's info — never dump
the whole instrument list). Multi-account: tools take an `account` argument and
the model issues parallel tool calls the runner executes concurrently.
"""
import json
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading_api
import db
import registry
import ai_keys

MEMORY_CAP = 6000   # keep MEMORY.md tight — trim oldest lines past this

# There is no model catalogue here. There was one — a hand-kept list per
# provider — and its own comment recorded the day two DeepSeek names were
# deprecated, which is the argument against it: a list compiled here is wrong by
# the next release and nobody notices until a call fails. Models come from the
# provider, live, via ai_keys.list_models(). The only names this app defines are
# the branded tiers in billing.MODELS.

# ── tools (Anthropic input_schema shape; converted for OpenAI) ───────────────────
ACC = {"type": "integer", "description": "Account number to act on."}
SYM = {"type": "string", "description": "Instrument symbol, e.g. XAUUSD."}

TOOLS = [
    {"name": "list_accounts", "description": "List the trading accounts available to the user (number, type, real/demo).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search_symbols", "description": "Search tradable symbols by substring. Returns matching symbol names only (token-efficient). Use before acting on a symbol you're unsure of.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "account": ACC}, "required": ["query", "account"]}},
    {"name": "list_symbols", "description": "List the account's actual TRADABLE symbols in a named group. ALWAYS call this to enumerate a set BEFORE scanning/analysing several — e.g. 'any major pair ready?', 'scan the indices', 'check the metals'. Never list group members from memory or chat history. group: majors | crosses (a.k.a. minors) | metals | energy | crypto | indices.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "group": {"type": "string", "description": "majors | crosses | metals | energy | crypto | indices"}}, "required": ["account", "group"]}},
    {"name": "symbol_info", "description": "Full details for ONE symbol: spec, live bid/ask, and open positions on it.",
     "input_schema": {"type": "object", "properties": {"symbol": SYM, "account": ACC}, "required": ["symbol", "account"]}},
    {"name": "price", "description": "Live bid/ask price for a symbol.",
     "input_schema": {"type": "object", "properties": {"symbol": SYM, "account": ACC}, "required": ["symbol", "account"]}},
    {"name": "positions", "description": "Open positions on an account. Each profit is in the account's deposit currency (see the returned `account_currency`) — report it in that currency, not USD.",
     "input_schema": {"type": "object", "properties": {"account": ACC}, "required": ["account"]}},
    {"name": "orders", "description": "Pending orders on an account.",
     "input_schema": {"type": "object", "properties": {"account": ACC}, "required": ["account"]}},
    {"name": "account_stats", "description": "Balance, equity, free margin and floating profit for an account — all in the account's deposit currency (see the returned `account_currency`), which may not be USD.",
     "input_schema": {"type": "object", "properties": {"account": ACC}, "required": ["account"]}},
    {"name": "history", "description": "Realised P/L SUMMARY ONLY (totals: net/gross profit, wins, losses, win rate) for a period — NOT per-trade detail. range: today|yesterday|this_week|last_week|last_2_weeks|last_month|last_3_months|last_6_months. For which trades hit TP/SL or per-trade breakdown, use closed_trades instead.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "range": {"type": "string"}}, "required": ["account", "range"]}},
    {"name": "closed_trades", "description": "DETAILED closed trades for a period — each with symbol, side, volume, entry & exit price, profit, and the CLOSE REASON (take_profit / stop_loss / manual / stop_out …). SURGICAL: always filter (range, symbol, only=profit|loss, reason) and keep a small limit so it never dumps thousands of rows; the result's `matched`/`truncated` tell you if more exist. Use THIS (not history, which is only totals) when the user asks which trades hit TP vs SL vs manual, or wants a per-trade breakdown.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "range": {"type": "string", "description": "today|yesterday|this_week|last_week|last_month|last_3_months"}, "symbol": SYM, "only": {"type": "string", "enum": ["profit", "loss"]}, "reason": {"type": "string", "description": "take_profit | stop_loss | manual | stop_out | expert | rollover"}, "limit": {"type": "integer", "description": "max rows, most-recent first (default 50, cap 200)"}}, "required": ["account", "range"]}},
    {"name": "place_order", "description": "Open a market order. SL/TP as points (sl_points/tp_points) or absolute (sl/tp). Free margin is checked first and the order is refused if there isn't enough — set emergency=true ONLY when the user flags the trade as urgent/emergency to skip that check. Volume is auto-adjusted to the symbol's MINIMUM allowed size (some indices like US500 have a minimum of 0.03 lots) — if the result contains `volume_adjusted`, tell the user the trade was placed at that instrument's MINIMUM size (state the minimum, e.g. 'US500's minimum is 0.03 lots'). Do NOT mention 'volume step' — the relevant limit is the minimum volume.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "side": {"type": "string", "enum": ["buy", "sell"]}, "volume": {"type": "number"}, "sl_points": {"type": "integer"}, "tp_points": {"type": "integer"}, "sl": {"type": "number"}, "tp": {"type": "number"}, "emergency": {"type": "boolean", "description": "Skip the margin pre-check and open immediately."}}, "required": ["account", "symbol", "side", "volume"]}},
    {"name": "pending_order", "description": "Place a pending order. side: buy_limit|sell_limit|buy_stop|sell_stop. price required.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "side": {"type": "string"}, "price": {"type": "number"}, "volume": {"type": "number"}, "sl_points": {"type": "integer"}, "tp_points": {"type": "integer"}}, "required": ["account", "symbol", "side", "price", "volume"]}},
    {"name": "close", "description": "Close positions: one (position_id), all on a symbol, or ALL if neither. only=profit|loss to close just winners/losers.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "position_id": {"type": "string"}, "only": {"type": "string", "enum": ["profit", "loss"]}}, "required": ["account"]}},
    {"name": "break_even", "description": "Move SL to entry (skips losing trades). Targets one/symbol/all.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "position_id": {"type": "string"}, "offset_points": {"type": "integer"}}, "required": ["account"]}},
    {"name": "lock_profit", "description": "Trail SL to lock a % of current profit. Targets one/symbol/all.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "percent": {"type": "number"}, "symbol": SYM, "position_id": {"type": "string"}}, "required": ["account", "percent"]}},
    {"name": "delete_sltp", "description": "Remove SL and/or TP (which=both|sl|tp) without closing. Targets one/symbol/all.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "position_id": {"type": "string"}, "which": {"type": "string"}}, "required": ["account"]}},
    {"name": "modify_position", "description": "Set SL/TP on a position by id.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "position_id": {"type": "string"}, "sl": {"type": "number"}, "tp": {"type": "number"}}, "required": ["account", "position_id"]}},
    {"name": "cancel_orders", "description": "Cancel pending orders: one (ticket), all on a symbol, or ALL.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "ticket": {"type": "string"}}, "required": ["account"]}},
    {"name": "calc_sltp", "description": "Versatile SL/TP calculator — convert between MONEY, POINTS and a PRICE level for a trade, in ANY direction. All money is in the ACCOUNT'S OWN currency (USD, ZAR, EUR…), cross-rated automatically — the result's `account_currency` tells you which; report figures in that currency, not always dollars. Use it for anything like 'where do I put TP/SL to make/lose X', 'what's my P/L if price hits Y', 'how many points is X worth', 'what's the value per point'. NEVER estimate these by hand. Give symbol, volume, side, and the input(s): to CONVERT, pass exactly one of money (target, with mode tp|sl), points (distance, with mode), or level (a price → returns the resulting P/L and detects tp vs sl). To VALIDATE an already-formed trade that has a stop and/or a target, pass sl and/or tp as PRICE levels (or sl_points/tp_points as distances) and it returns the P/L for each leg — you may pass both in one call. entry defaults to the live quote.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "side": {"type": "string", "enum": ["buy", "sell"]}, "volume": {"type": "number"}, "entry": {"type": "number", "description": "Entry/open price. Omit to use the live quote."}, "money": {"type": "number", "description": "Target amount in the ACCOUNT currency (use with mode)."}, "points": {"type": "number", "description": "Distance in points (use with mode)."}, "level": {"type": "number", "description": "A single price level → returns the resulting P/L in the account currency (auto-detects tp vs sl)."}, "sl": {"type": "number", "description": "Stop-loss PRICE of a formed trade → returns its P/L. Can combine with tp."}, "tp": {"type": "number", "description": "Take-profit PRICE of a formed trade → returns its P/L. Can combine with sl."}, "sl_points": {"type": "number", "description": "Stop distance in points (instead of sl price)."}, "tp_points": {"type": "number", "description": "Target distance in points (instead of tp price)."}, "mode": {"type": "string", "enum": ["tp", "sl"], "description": "tp=take profit / sl=stop loss (for money or points input)."}}, "required": ["account", "symbol", "side", "volume"]}},
    {"name": "risk_plan", "description": "RISK-MANAGEMENT engine — the ONE call for position sizing and stop/target placement. Use it whenever risk is stated as a PERCENT or a fixed money amount ('risk 2% of the account', 'risk $50 on this trade'), or when you must size a trade / place a stop / check what a trade risks. It is symbol-aware (contract size, point size, volume step/min) and returns everything in the ACCOUNT'S OWN currency — never size or place a stop by hand. risk_money = stop_distance × $/price × volume; give ANY TWO of {risk, stop, volume} and it solves the third: SIZE (risk + stop → the exact VOLUME), STOP (risk + volume → WHERE the stop sits), VALIDATE (stop + volume → what the trade actually risks). Risk = risk_pct (percent of basis equity|balance) OR risk_money. Stop = sl (a PRICE) OR sl_points (a distance). Optional target: rr (reward:risk ratio → TP at rr× the stop) OR tp/tp_points. entry defaults to the live quote. Returns the sized volume (snapped to the symbol's step/min), sl & tp prices with distances and money, realised risk/reward and rr, per-point value, and the margin needed — then place/modify with those exact levels.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "side": {"type": "string", "enum": ["buy", "sell"]}, "entry": {"type": "number", "description": "Entry price. Omit to use the live quote."}, "risk_pct": {"type": "number", "description": "Risk as a percent of the account (e.g. 2 = 2%)."}, "risk_money": {"type": "number", "description": "Risk as an absolute amount in the account currency."}, "sl": {"type": "number", "description": "Stop-loss PRICE."}, "sl_points": {"type": "number", "description": "Stop distance in points (instead of an sl price)."}, "tp": {"type": "number", "description": "Take-profit PRICE (optional)."}, "tp_points": {"type": "number", "description": "Target distance in points (optional)."}, "rr": {"type": "number", "description": "Reward:risk ratio → TP placed at rr× the stop distance."}, "volume": {"type": "number", "description": "Fixed lot size. Omit to have it SIZED from risk + stop."}, "basis": {"type": "string", "enum": ["equity", "balance"], "description": "What risk_pct is a percent of (default equity)."}}, "required": ["account", "symbol", "side"]}},
    {"name": "auto_sltp", "description": "SMART, hands-off SL/TP + lot sizing — give it just a symbol, side and (optionally) a trade STYLE and it reads live market data and decides everything: it places the STOP at real market structure (recent swing high/low) with an ATR volatility floor and the broker's stop-level as guards, sets the TARGET from a style-based reward:risk (scalp 1.5R, intraday 2R, swing 2.5R, position 3R — override with rr), and SIZES the lot so hitting that stop loses exactly the risk budget. Use this whenever the user wants a trade set up 'properly' / 'with risk management' / 'find my SL and TP' / 'size it for me' / 'risk 2% on gold' without giving you the levels. style = scalp | intraday | swing | position (pick from their words: 'scalp'→scalp, 'day trade'→intraday, 'swing'→swing, 'long term'→position; default intraday). Risk budget: risk_pct (% of the account) or risk_money; if you give NEITHER it falls back to the user's saved default-risk setting. Returns volume, sl & tp prices, realised risk/reward, the ATR and structure it used, and margin — all in the account currency. Place the trade with the returned volume, sl.price and tp.price verbatim.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "symbol": SYM, "side": {"type": "string", "enum": ["buy", "sell"]}, "style": {"type": "string", "enum": ["scalp", "intraday", "swing", "position"], "description": "Trade horizon → sets the timeframe, stop tightness and default reward:risk."}, "entry": {"type": "number", "description": "Entry price. Omit to use the live quote."}, "risk_pct": {"type": "number", "description": "Risk as a % of the account."}, "risk_money": {"type": "number", "description": "Risk as an absolute amount (account currency)."}, "rr": {"type": "number", "description": "Override the style's default reward:risk ratio for the TP."}, "basis": {"type": "string", "enum": ["equity", "balance"], "description": "What risk_pct is a % of (default equity)."}, "sl_mode": {"type": "string", "enum": ["structure", "atr", "swing"], "description": "How to place the stop: structure (swing+ATR floor, default) | atr (pure volatility) | swing (structure only)."}}, "required": ["account", "symbol", "side"]}},
    {"name": "risk_status", "description": "The user's live RISK DASHBOARD for an account, built from THEIR configured risk parameters. Returns: realised drawdown today / this week / this month vs their max-drawdown limits (open floating folded in), whether their trading-hours windows allow a trade RIGHT NOW, and their per-trade risk% and reward:risk. Call this BEFORE opening any new trade to respect the user's own rules — `can_trade_now` is the gate: if it's false, do NOT open new trades and tell the user why (a drawdown limit is hit, or it's outside their trading hours). All money in the account currency.",
     "input_schema": {"type": "object", "properties": {"account": ACC}, "required": ["account"]}},
    {"name": "calc_basket", "description": "Set SL/TP levels across MANY trades at once (defaults to the account's LIVE open trades; filter by symbol). Two modes: (1) single `target` + `mode` — spread ONE total so all trades hitting sums to it. (2) BRACKET: pass `sl_money` and/or `tp_money` as SIGNED account-currency totals — POSITIVE = a locked PROFIT at that level, NEGATIVE = a loss. A stop-loss CAN sit in profit: sl_money=100 places a protective STOP that still nets +100 if hit (locks gains), tp_money=500 targets +500 — set both in one call. Each leg reports sl_valid/tp_valid (is it a legal level vs the live price). split=weighted → same distance each; equal → same money each. apply=true writes the levels onto the live positions.",
     "input_schema": {"type": "object", "properties": {"account": ACC, "target": {"type": "number", "description": "Single-mode: total amount across all trades (profit for tp, loss for sl)."}, "sl_money": {"type": "number", "description": "Bracket: SIGNED total P/L at the STOP (+ = locked profit, − = loss)."}, "tp_money": {"type": "number", "description": "Bracket: SIGNED total P/L at the TARGET (usually + profit)."}, "mode": {"type": "string", "enum": ["tp", "sl"], "description": "Single-target mode only."}, "split": {"type": "string", "enum": ["equal", "weighted"]}, "symbol": SYM, "apply": {"type": "boolean", "description": "Write each computed level onto the matching live position."}, "positions": {"type": "array", "description": "Optional explicit trades instead of live ones, each {symbol, entry, volume, side}.", "items": {"type": "object"}}}, "required": ["account"]}},
    # ── analysis-agent authoring (build & refine the user's flow agents) ──
    {"name": "list_analysis_agents", "description": "List the user's analysis agents (id, name, description, status, node count). Use first when the user wants to inspect, improve or edit an analysis agent.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_analysis_agent", "description": "Inspect ONE analysis agent's flow — every node's id, kind and its instruction text, plus how they connect. Call this before editing so you know the node ids and current wording.",
     "input_schema": {"type": "object", "properties": {"agent": {"type": "string", "description": "Agent id or name."}}, "required": ["agent"]}},
    {"name": "edit_analysis_node", "description": "Edit ONE node of an analysis agent — most often its instruction `text` (what that node tells its data source to do). Give the agent (id or name) and node_id (from get_analysis_agent), plus any of: text, name, description, model (\"provider:model\"), requirement (Trigger node only). Keep `text` BRIEF — one or two sentences of concrete direction, concise but complete; tighten wording, never pad or restate context.",
     "input_schema": {"type": "object", "properties": {"agent": {"type": "string"}, "node_id": {"type": "string"}, "text": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "model": {"type": "string"}, "requirement": {"type": "string"}}, "required": ["agent", "node_id"]}},
    {"name": "update_analysis_agent", "description": "Update an analysis agent's metadata and/or REPLACE its whole flow — for broad changes ('rebuild it to also read news then decide', rename, activate). Provide any of name, description, status (draft|active|paused), flow. `flow` is a SIMPLIFIED graph: {nodes:[{id, kind, text?, name?, description?, model?, requirement?, x?, y?}], edges:[{source, target, branch?}]} (branch = 'true'|'false' for an If node). Node kinds: trigger-agent-call, trigger-interval, market-data, time-session, artificial-sentiment, risk-management (smart SL/TP + position sizing), if, respond, versatile, call-agent (plus any node an installed module adds — ask for the palette rather than assuming). Always start a flow with one trigger-agent-call and end with a respond. Keep each node's `text` BRIEF — one or two sentences, concise but complete, no filler. Inspect with get_analysis_agent first.",
     "input_schema": {"type": "object", "properties": {"agent": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "status": {"type": "string", "enum": ["draft", "active", "paused"]}, "flow": {"type": "object"}}, "required": ["agent"]}},
    {"name": "create_analysis_agent", "description": "Create a NEW analysis agent from a simplified flow (same shape as update_analysis_agent's `flow`). Give name, optional description/status, and the flow. Start with a trigger-agent-call and end with a respond node. Keep each node's `text` BRIEF — one or two sentences, concise but complete.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "status": {"type": "string", "enum": ["draft", "active", "paused"]}, "flow": {"type": "object"}}, "required": ["name"]}},
    {"name": "schedule_action", "description": "Schedule ANY trading action to run LATER on the server (e.g. 'close gold in 30 seconds', 'buy 0.1 gold at 15:30', 'break even all in 5 minutes'). Give the target action, its params (same args you'd pass that action, minus account), and timing (seconds/minutes/hours from now, OR run_at as an ISO datetime).",
     "input_schema": {"type": "object", "properties": {
        "account": ACC,
        "action": {"type": "string", "description": "Action to run later: close | place_order | pending_order | break_even | lock_profit | delete_sltp | modify_position | cancel_orders."},
        "params": {"type": "object", "description": "Arguments for that action, e.g. close→{\"symbol\":\"gold\",\"only\":\"profit\"}; place_order→{\"symbol\":\"gold\",\"side\":\"sell\",\"volume\":0.1}."},
        "seconds": {"type": "integer"}, "minutes": {"type": "integer"}, "hours": {"type": "integer"},
        "run_at": {"type": "string", "description": "Absolute ISO datetime instead of a relative delay."}},
      "required": ["account", "action", "params"]}},
    {"name": "show_chart", "description": "THE DEFAULT WAY TO SHOW A CHART IN THIS APP. Render a LIVE, INTERACTIVE candlestick chart inside the web app — it updates from the tick stream and marks the account's own open trades with entry, stop loss and take profit lines. It produces NO IMAGE FILE: it exists only inside this app's chat window, so it cannot be sent to a messaging app, attached, saved or shared, and there is no url for it. Use it when the user is in the app and wants something live to look at. If an image is what is wanted — to send, to keep, or because they are not in the app — draw one instead. After calling it, comment briefly on what the chart shows; do not re-list the candles as text.",
     "input_schema": {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "e.g. 'gold', 'EURUSD', 'nasdaq'."},
        "timeframe": {"type": "string", "description": "M1|M3|M5|M10|M15|M30|H1|H2|H4|D1|W1|MN1 (default M15)."},
        "count": {"type": "integer", "description": "How many candles to draw (default 150)."},
        "account": {"type": "integer", "description": "Account whose trades to mark on the chart."}},
      "required": ["symbol"]}},
    {"name": "candles", "description": "OHLC price history for an instrument, live from Exness. Give the symbol, a timeframe (M1 M3 M5 M10 M15 M30 H1 H2 H4 D1 W1 MN1) and how many candles back. Use it to read structure, levels and momentum before deciding anything. MULTI: pass a COMMA-SEPARATED symbol and/or timeframe to get several series at once (e.g. symbol='XAUUSD,GBPUSD,gold', timeframe='H4,H1,M15') — the reply is {multi:true, series:[…one per symbol×timeframe…]}. Prefer this over many single calls when you need multiple pairs or a multi-timeframe read.",
     "input_schema": {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "e.g. 'gold', 'EURUSD', 'nasdaq' — or a comma list 'XAUUSD,GBPUSD,gold' for several at once."},
        "timeframe": {"type": "string", "description": "M1|M3|M5|M10|M15|M30|H1|H2|H4|D1|W1|MN1 (default M15) — or a comma list 'H4,H1,M15' for a multi-timeframe read."},
        "count": {"type": "integer", "description": "How many candles back (default 100, max 5000). Applies to every series."},
        "price": {"type": "string", "description": "bid (default) or ask series."},
        "end": {"type": "string", "description": "Optional ISO moment to walk back from, instead of now."}},
      "required": ["symbol"]}},
    {"name": "artificial_sentiment", "description": "Who controls a market, reconstructed from ITS OWN CANDLES — not from broker data. Where the `sentiment` tool reports how Myfxbook's retail users are positioned (one number, only for symbols it covers), this reads price structure directly — swings, liquidity sweeps, volume and wick absorption — so it works on ANY instrument and ANY timeframe. Returns bulls/bears %, each side's estimated average entry and how much of each side is TRAPPED (underwater = future forced flow). Numbers only — no bias label, no commentary: read them yourself. Set compare=true to get Myfxbook's retail read beside it: when retail leans one way and the price footprint leans the other, the crowd is usually the side that gets squeezed. It is a MODEL, not a measurement — say so when you quote it.",
     "input_schema": {"type": "object", "properties": {
        "symbol": {"type": "string", "description": "Instrument, e.g. 'gold', 'XAUUSD', 'nasdaq'."},
        "timeframe": {"type": "string", "description": "M1|M5|M15|M30|H1|H4|D1 (default M15). The read is timeframe-specific: H4 tells you about swing positioning, M5 about intraday."},
        "count": {"type": "integer", "description": "Candles to reconstruct from (40-1000, default 200)."},
        "compare": {"type": "boolean", "description": "Also return Myfxbook's real retail positioning for the same symbol, and the gap between them."}},
      "required": ["symbol"]}},
    {"name": "remember", "description": "Save a durable, noteworthy fact ABOUT THE USER to long-term memory (their preferences, risk appetite, account nicknames, typical instruments, goals). Use sparingly — only genuinely useful, lasting facts, not chat trivia or one-off data. Do not save things already in your memory.",
     "input_schema": {"type": "object", "properties": {"note": {"type": "string", "description": "One concise fact about the user."}}, "required": ["note"]}},
]

# The names core owns. A module may not take one of these — and knowing which
# they are is what lets a module tool be dispatched EARLY in execute_tool
# without letting it shadow anything core provides.
CORE_TOOL_NAMES = {t["name"] for t in TOOLS}

# actions that may be scheduled to run later (subset of tool names)
SCHEDULABLE_ACTIONS = {"place_order", "pending_order", "close", "break_even", "lock_profit",
                       "delete_sltp", "modify_position", "cancel_orders", "positions",
                       "orders", "account_stats", "price", "symbol_info", "history"}


# ── per-user long-term memory (MEMORY.md) ────────────────────────────────────────
def read_memory(user_id):
    if not user_id:
        return ""
    with db.connect() as conn:
        row = conn.execute("SELECT content FROM user_memory WHERE user_id = %s", (user_id,)).fetchone()
    return (row["content"] if row else "") or ""


def append_memory(user_id, note):
    if not user_id or not note:
        return {"error": "nothing to remember"}
    note = note.strip().lstrip("-").strip()
    current = read_memory(user_id)
    if note.lower() in current.lower():
        return {"ok": True, "note": note, "skipped": "already known"}
    line = f"- {note}"
    updated = (current.rstrip() + "\n" + line).strip() if current.strip() else line
    if len(updated) > MEMORY_CAP:      # drop oldest bullet lines to stay small
        lines = updated.split("\n")
        while len("\n".join(lines)) > MEMORY_CAP and len(lines) > 1:
            lines.pop(0)
        updated = "\n".join(lines)
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO user_memory (user_id, content, updated_at) VALUES (%s, %s, now())
               ON CONFLICT (user_id) DO UPDATE SET content = EXCLUDED.content, updated_at = now()""",
            (user_id, updated),
        )
        conn.commit()
    return {"ok": True, "note": note}


# cheap models used for the automatic post-turn memory extraction
# No cheap-model table either. It named three models to save a fraction of a
# cent on a memory-extraction call, and every one of them was a name that would
# eventually stop existing. The extraction runs on whatever model the turn was
# already running on.


# No cap on the OpenAI-compatible call, for the same reason as the analysis
# engine: any ceiling we pick is one a reasoning model can exhaust mid-thought,
# returning an empty string. Anthropic requires one, so it gets a generous one.
_ANTHROPIC_MAX_TOKENS = 4000


def _quick_json_facts(provider, api_key, system, user, model=None):
    """One cheap non-streaming call → list[str]. Empty list on any problem."""
    # Whatever the turn itself ran on. One extra call on a slightly dearer
    # model is cheaper than a stale hardcoded name that fails outright.
    if not model:
        return []
    system += ' Respond with ONLY a JSON object: {"facts": ["...", ...]} (empty array if none).'
    try:
        if provider == "anthropic":
            import anthropic
            c = anthropic.Anthropic(api_key=api_key, timeout=ai_keys.LLM_TIMEOUT)
            msg = c.messages.create(model=model, max_tokens=_ANTHROPIC_MAX_TOKENS, system=system,
                                    messages=[{"role": "user", "content": user}])
            text = "".join(b.text for b in msg.content if b.type == "text")
        else:
            from openai import OpenAI
            c = OpenAI(api_key=api_key, base_url=ai_keys.base_url(provider),
                       timeout=ai_keys.LLM_TIMEOUT, max_retries=1)
            r = c.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"})
            text = r.choices[0].message.content
        data = json.loads(text)
        facts = data.get("facts") if isinstance(data, dict) else data
        return [str(f).strip() for f in facts if str(f).strip()] if isinstance(facts, list) else []
    except Exception:
        return []


def extract_memory(provider, api_key, user_msg, assistant_reply, current_memory, user_id,
                   model=None):
    """After a turn, pull any NEW durable facts about the user and save them.
    Returns the list actually stored (deduped)."""
    if not user_id or not (user_msg or assistant_reply):
        return []
    system = (
        "You maintain long-term memory about a trading-app user. From the exchange below, extract "
        "ONLY new, durable, genuinely useful facts about the USER worth recalling in future chats — "
        "their preferences, risk appetite, favourite instruments/markets, account nicknames, goals, "
        "constraints, trading style. IGNORE transient data (live prices, balances, one-off requests), "
        "app mechanics, and anything already known. Keep each fact short (max ~10 words).\n\n"
        f"Already known about this user:\n{current_memory.strip() or '(nothing yet)'}"
    )
    user = f"User said: {user_msg}\n\nAssistant replied: {(assistant_reply or '')[:2000]}"
    saved = []
    for f in _quick_json_facts(provider, api_key, system, user, model):
        r = append_memory(user_id, f)
        if r.get("ok") and not r.get("skipped"):
            saved.append(f)
    return saved


# ── tool execution ──────────────────────────────────────────────────────────────
def _accounts_context():
    """User's accounts (compact) for the ACTIVE broker, whichever it is. Archived
    accounts are included but flagged, so an explicitly-selected archived account
    is still addressable."""
    import trading_api, brokers
    uid = trading_api._active_user_ctx.get()
    broker = trading_api._active_broker_ctx.get() or brokers.default_broker() or ""
    p = brokers.get(broker)
    fn = getattr(p, "accounts_view", None) if p else None
    if not fn:
        return []
    try:
        res = fn(uid)
        if res.get("error"):
            return {"error": res["error"]}
        out = []
        for a in res.get("accounts", []):
            entry = {"account": a.get("account_number"), "type": a.get("account_type"),
                     "real": a.get("is_real"), "currency": a.get("currency")}
            if a.get("is_archived"):
                entry["archived"] = True
            out.append(entry)
        return out
    except Exception as e:
        return {"error": str(e)}


# common spoken names → symbol fragments to match (gold→XAU, nasdaq→US100…)
SYMBOL_ALIASES = {
    "GOLD": ["XAU"], "SILVER": ["XAG"], "PLATINUM": ["XPT"], "PALLADIUM": ["XPD"],
    "OIL": ["OIL", "WTI", "BRENT", "USOIL", "UKOIL"], "CRUDE": ["WTI", "USOIL"],
    "BRENT": ["BRENT", "UKOIL"], "WTI": ["WTI", "USOIL"], "GAS": ["NGAS", "NATGAS"],
    "BITCOIN": ["BTC"], "ETHEREUM": ["ETH"], "ETHER": ["ETH"], "RIPPLE": ["XRP"],
    "LITECOIN": ["LTC"], "DOGECOIN": ["DOGE"], "SOLANA": ["SOL"],
    "NASDAQ": ["USTEC", "NAS100", "US100", "NDX"], "NAS": ["USTEC", "NAS100", "US100"],
    "DOW": ["US30", "WS30", "DJ30"], "DOWJONES": ["US30", "WS30"],
    "SP500": ["US500", "SPX"], "SANDP": ["US500", "SPX"], "SPX": ["US500", "SPX"], "SP": ["US500"],
    "DAX": ["DE30", "DE40", "GER30", "GER40"], "FTSE": ["UK100"], "FTSE100": ["UK100"],
    "NIKKEI": ["JP225"], "CAC": ["FR40", "FRA40"],
    "EURO": ["EUR"], "POUND": ["GBP"], "STERLING": ["GBP"], "CABLE": ["GBPUSD"],
    "YEN": ["JPY"], "AUSSIE": ["AUDUSD"], "KIWI": ["NZDUSD"], "LOONIE": ["USDCAD"],
    "SWISSIE": ["USDCHF"],
}

# A user's group word → the instruments API's OWN `category` value. No symbol lists
# are hardcoded: list_symbols filters live instruments by category, so it stays
# token-light and always matches what the account actually trades.
SYMBOL_GROUP_CATEGORY = {
    "majors": "Majors", "major": "Majors", "fx": "Majors", "forex": "Majors",
    "crosses": "Minors", "cross": "Minors", "minors": "Minors", "minor": "Minors",
    "metals": "Metals", "metal": "Metals",
    "indices": "Indices", "index": "Indices", "indice": "Indices",
    "energy": "Energies", "energies": "Energies", "commodities": "Energies",
    "crypto": "Crypto", "cryptos": "Crypto",
    "stocks": "Stocks", "stock": "Stocks", "shares": "Stocks",
    "exotic": "Exotic", "exotics": "Exotic",
}


def _acct_ccy(t):
    """The active account's deposit currency (USD, ZAR, EUR…). Every broker money
    figure — profit, balance, equity, margin — is in THIS currency, never assume USD."""
    try:
        return t.account_currency()
    except Exception:
        return "USD"


def _resolve_symbol(t, name):
    """Map a user's word ('gold', 'nasdaq', 'btc') to an actual tradable symbol.
    Returns the input unchanged if nothing matches (engine then errors clearly)."""
    if not name:
        return name
    up = str(name).upper().strip().replace("/", "").replace(" ", "")
    instruments = t.instruments()
    for i in instruments:                                   # 1. exact symbol
        if i["symbol"].upper() == up:
            return i["symbol"]
    terms = SYMBOL_ALIASES.get(up, []) + [up]               # 2. alias / substring
    matches = []
    for i in instruments:
        sym = i["symbol"].upper()
        intl = (i.get("international") or "").upper().replace("/", "")
        if any(term in sym or term in intl for term in terms):
            matches.append(i["symbol"])
    if matches:   # prefer the canonical one: shortest, USD-quoted first
        matches.sort(key=lambda s: (len(s), 0 if s.upper().endswith("USD") else 1))
        return matches[0]
    return name


def _schedule_action(user_id, args):
    from datetime import datetime, timedelta
    action = args.get("action")
    if action not in SCHEDULABLE_ACTIONS:
        return {"error": f"cannot schedule '{action}'"}
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return {"error": "params must be an object"}
    if args.get("run_at"):
        try:
            when = datetime.fromisoformat(args["run_at"])
            when = when.astimezone() if when.tzinfo is None else when
        except Exception:
            return {"error": "run_at must be ISO datetime"}
    else:
        total = (args.get("hours") or 0) * 3600 + (args.get("minutes") or 0) * 60 + (args.get("seconds") or 0)
        if total <= 0:
            return {"error": "provide seconds/minutes/hours or run_at"}
        when = datetime.now().astimezone() + timedelta(seconds=total)
    from psycopg.types.json import Json
    with db.connect() as conn:
        row = conn.execute(
            """INSERT INTO scheduled_actions (user_id, account, action, params, run_at)
               VALUES (%s,%s,%s,%s,%s) RETURNING id, run_at""",
            (user_id, args.get("account"), action, Json(params), when),
        ).fetchone()
        conn.commit()
    secs = (row["run_at"] - datetime.now(row["run_at"].tzinfo)).total_seconds()
    return {"ok": True, "id": str(row["id"]), "scheduled_action": action, "params": params,
            "account": args.get("account"), "run_at": row["run_at"].isoformat(),
            "seconds_until": round(secs, 1)}


# ── analysis agents exposed as tools ─────────────────────────────────────────────
def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "agent"


def _trigger_requirement(flow):
    """The Trigger node's stated input requirement, if the user filled one in."""
    for n in (flow or {}).get("nodes", []):
        data = n.get("data") or {}
        if (data.get("kind") in ("trigger-agent-call", "trigger")):
            return ((data.get("values") or {}).get("requirement") or "").strip()
    return ""


def list_agent_tools(user_id):
    """Every ACTIVE analysis agent the user owns, as callable tool defs. Returns
    (tools, {tool_name: agent_id}). Tool names are unique `analysis_<slug>`."""
    if not user_id:
        return [], {}
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, description, flow FROM analysis_agents "
                "WHERE user_id = %s AND status = 'active' ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
    except Exception:
        return [], {}
    tools, mapping, seen = [], {}, set()
    for r in rows:
        if not (r["flow"] or {}).get("nodes"):
            continue                                  # empty flow — nothing to run
        base = ("analysis_" + _slug(r["name"]))[:48]
        nm, i = base, 2
        while nm in seen:
            nm = f"{base}_{i}"; i += 1
        seen.add(nm)
        req = _trigger_requirement(r["flow"])
        desc = (r["description"] or "").strip()
        full = f"Analysis agent '{r['name']}'."
        if desc:
            full += f" {desc}"
        if req:
            full += f" Needs: {req}."
        full += " Runs the agent's flow and returns its analysis. Pass `request` describing what to analyse."
        tools.append({
            "name": nm, "description": full[:1000],
            "input_schema": {"type": "object", "properties": {
                "request": {"type": "string", "description": "What you want this analysis agent to look at."}},
                "required": []},
        })
        mapping[nm] = str(r["id"])
    return tools, mapping


def _run_analysis_agent(ctx, name, args):
    agent_id = (ctx.get("agent_tools") or {}).get(name)
    if not agent_id:
        return {"error": f"unknown analysis agent tool {name}"}
    try:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT name, flow FROM analysis_agents WHERE id = %s AND user_id = %s",
                (agent_id, ctx.get("user_id")),
            ).fetchone()
    except Exception as e:
        return {"error": str(e)}
    if not row:
        return {"error": "analysis agent not found"}
    import analysis_agent
    request = args.get("request") or args.get("input") or ctx.get("last_user") or ""
    # Run with its OWN usage meter so this run is priced (and cached) precisely, then
    # roll its real cost into the TURN meter (charged at the end of the chat turn).
    sub = dict(ctx)
    sub["_usage"] = {"in": 0, "out": 0, "cache_hit": 0, "calls": 0}
    res = analysis_agent.run_flow(row["flow"], request, sub, agent_id=agent_id, source="chat")
    try:
        u = ctx.get("_usage")
        if u is not None:
            with analysis_agent._USAGE_LOCK:
                u["extra_cost_usd"] = float(u.get("extra_cost_usd", 0)) + float(res.get("cost_usd") or 0)
                if res.get("cached"):
                    u["cache_hits"] = int(u.get("cache_hits", 0)) + 1
    except Exception:
        pass
    return {"agent": row["name"], "response": res.get("response"), "trace": res.get("trace"),
            "error": res.get("error"), "cached": bool(res.get("cached"))}


# ── analysis-agent authoring helpers ─────────────────────────────────────────────
# palette kind → React Flow node type (mirrors frontend flow/palette.js)
NODE_KIND_TYPE = {
    "trigger-agent-call": "trigger", "trigger-interval": "triggerInterval",
    "market-data": "marketData", "time-session": "timeSession",
    "artificial-sentiment": "artificialSentiment",
    "risk-management": "riskManagement",
    "if": "if", "respond": "respond", "versatile": "versatile",
    "call-agent": "callAgent",
}


def node_kind_type() -> dict:
    """Core node kinds plus any a module registered — the authoring round-trip
    must know a module's node or it silently drops it from an edited flow."""
    return {**NODE_KIND_TYPE, **registry.node_types()}

# Node values that survive the simplify → author → rebuild round-trip. Anything
# missing here is DROPPED when the chat agent edits a flow, so a new node's
# settings must be added on both sides.
_VALUE_KEYS = ("name", "description", "model", "requirement", "agent_id", "agent_name",
               "mode", "every", "unit", "cron", "cron_brief")


def _value_keys() -> tuple:
    """Core value keys plus every key a module's node declares."""
    return tuple(dict.fromkeys(_VALUE_KEYS + registry.node_values()))


def _find_agent_row(user_id, ref):
    if not (user_id and ref):
        return None
    with db.connect() as conn:
        return conn.execute(
            "SELECT id, name, description, status, flow FROM analysis_agents "
            "WHERE user_id = %s AND (id::text = %s OR lower(name) = lower(%s)) "
            "ORDER BY updated_at DESC LIMIT 1",
            (user_id, str(ref), str(ref)),
        ).fetchone()


def _simplify_flow(flow):
    """The full React Flow graph → the compact shape the model authors/reads."""
    nodes = []
    for n in (flow or {}).get("nodes", []):
        d = n.get("data") or {}
        v = d.get("values") or {}
        nodes.append({"id": n.get("id"), "kind": d.get("kind"), "text": v.get("text", ""),
                      **{k: v[k] for k in _value_keys() if v.get(k)}})
    edges = [{"source": e.get("source"), "target": e.get("target"),
              **({"branch": e["sourceHandle"]} if e.get("sourceHandle") else {})}
             for e in (flow or {}).get("edges", [])]
    return {"nodes": nodes, "edges": edges}


def _build_flow(simple):
    """The compact authored shape → a full React Flow graph the canvas renders."""
    if not isinstance(simple, dict):
        raise ValueError("flow must be an object with `nodes` and `edges`")
    nodes, ids = [], set()
    for i, n in enumerate(simple.get("nodes") or []):
        kind = n.get("kind")
        kinds = node_kind_type()
        typ = kinds.get(kind)
        if not typ:
            raise ValueError(f"unknown node kind {kind!r} — use one of: {', '.join(kinds)}")
        nid = n.get("id") or f"{typ}_{i}"
        ids.add(nid)
        values = {k: n[k] for k in ("text",) + _value_keys() if n.get(k) is not None}
        nodes.append({"id": nid, "type": typ,
                      "position": {"x": n.get("x", 300), "y": n.get("y", 40 + i * 150)},
                      "data": {"kind": kind, "values": values}})
    edges = []
    for j, e in enumerate(simple.get("edges") or []):
        s, t = e.get("source"), e.get("target")
        if s in ids and t in ids:
            ed = {"id": e.get("id") or f"e{j}_{s}_{t}", "type": "connector", "source": s, "target": t}
            if e.get("branch"):
                ed["sourceHandle"] = e["branch"]
            edges.append(ed)
    return {"nodes": nodes, "edges": edges}


def _save_agent_fields(user_id, agent_id, fields):
    from psycopg.types.json import Json
    sets, params = [], []
    for k in ("name", "description", "status"):
        if fields.get(k) is not None:
            sets.append(f"{k} = %s"); params.append(fields[k])
    if fields.get("flow") is not None:
        sets.append("flow = %s"); params.append(Json(fields["flow"]))
    if not sets:
        return
    sets.append("updated_at = now()")
    params += [agent_id, user_id]
    with db.connect() as conn:
        conn.execute(f"UPDATE analysis_agents SET {', '.join(sets)} WHERE id = %s AND user_id = %s", params)
        conn.commit()


def _list_analysis_agents(user_id):
    if not user_id:
        return {"error": "no user"}
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, name, description, status, flow FROM analysis_agents "
            "WHERE user_id = %s ORDER BY updated_at DESC", (user_id,)).fetchall()
    return {"agents": [{"id": str(r["id"]), "name": r["name"], "description": r["description"],
                        "status": r["status"], "nodes": len((r["flow"] or {}).get("nodes", []))} for r in rows]}


def _get_analysis_agent(user_id, ref):
    row = _find_agent_row(user_id, ref)
    if not row:
        return {"error": f"no analysis agent matching {ref!r}"}
    s = _simplify_flow(row["flow"])
    return {"id": str(row["id"]), "name": row["name"], "description": row["description"],
            "status": row["status"], "nodes": s["nodes"], "edges": s["edges"]}


def _edit_analysis_node(user_id, args):
    row = _find_agent_row(user_id, args.get("agent"))
    if not row:
        return {"error": f"no analysis agent matching {args.get('agent')!r}"}
    flow = row["flow"] or {"nodes": [], "edges": []}
    nid = args.get("node_id")
    node = next((n for n in flow.get("nodes", []) if n.get("id") == nid), None)
    if not node:
        return {"error": f"node {nid!r} not found — call get_analysis_agent to see node ids"}
    values = node.setdefault("data", {}).setdefault("values", {})
    changed = []
    for k in ("text", "name", "description", "model", "requirement"):
        if args.get(k) is not None:
            values[k] = args[k]; changed.append(k)
    if not changed:
        return {"error": "nothing to change — pass text (or name/description/model/requirement)"}
    _save_agent_fields(user_id, row["id"], {"flow": flow})
    return {"ok": True, "agent": row["name"], "node_id": nid,
            "kind": (node.get("data") or {}).get("kind"), "changed": changed, "values": values}


def _update_analysis_agent(user_id, args):
    row = _find_agent_row(user_id, args.get("agent"))
    if not row:
        return {"error": f"no analysis agent matching {args.get('agent')!r}"}
    fields = {k: args[k] for k in ("name", "description", "status") if args.get(k) is not None}
    if fields.get("status") and fields["status"] not in ("draft", "active", "paused"):
        return {"error": "status must be draft | active | paused"}
    if args.get("flow") is not None:
        try:
            fields["flow"] = _build_flow(args["flow"])
        except Exception as e:
            return {"error": str(e)}
    if not fields:
        return {"error": "nothing to update — pass name, description, status or flow"}
    _save_agent_fields(user_id, row["id"], fields)
    out = {"ok": True, "agent": fields.get("name") or row["name"], "updated": list(fields)}
    if "flow" in fields:
        out["nodes"] = len(fields["flow"]["nodes"])
    return out


def _create_analysis_agent(user_id, args):
    from psycopg.types.json import Json
    name = (args.get("name") or "").strip()
    if not (user_id and name):
        return {"error": "name required"}
    flow = {"nodes": [], "edges": []}
    if args.get("flow") is not None:
        try:
            flow = _build_flow(args["flow"])
        except Exception as e:
            return {"error": str(e)}
    status = args.get("status") if args.get("status") in ("draft", "active", "paused") else "draft"
    with db.connect() as conn:
        r = conn.execute(
            "INSERT INTO analysis_agents (user_id, name, description, status, flow) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_id, name[:80], (args.get("description") or "").strip(), status, Json(flow)),
        ).fetchone()
        conn.commit()
    return {"ok": True, "id": str(r["id"]), "name": name, "status": status, "nodes": len(flow["nodes"])}


def execute_tool(name, args, user_id=None, ctx=None):
    _sess_token = None
    if user_id:
        try:
            import user_session
            _sess_token = user_session.bind(user_id)   # THIS user's session + active account
        except Exception:
            _sess_token = None
    try:
        if ctx and name in (ctx.get("agent_tools") or {}):
            return _run_analysis_agent(ctx, name, args)
        if name == "list_analysis_agents":
            return _list_analysis_agents(user_id)
        if name == "get_analysis_agent":
            return _get_analysis_agent(user_id, args.get("agent"))
        if name == "edit_analysis_node":
            return _edit_analysis_node(user_id, args)
        if name == "update_analysis_agent":
            return _update_analysis_agent(user_id, args)
        if name == "create_analysis_agent":
            return _create_analysis_agent(user_id, args)
        if name == "remember":
            return append_memory(user_id, args.get("note", ""))
        if name == "schedule_action":
            return _schedule_action(user_id, args)
        if name == "show_chart":
            import market
            return market.chart(args["symbol"], timeframe=args.get("timeframe", "M15"),
                                count=args.get("count", 150), account=args.get("account"))
        if name == "candles":
            import market
            sym, tf = args["symbol"], args.get("timeframe", "M15")
            # comma list in either field ⇒ many series in one call
            if len(market._split_csv(sym)) > 1 or len(market._split_csv(tf)) > 1:
                return market.candles_multi(sym, tf, count=args.get("count", 100),
                                            price=args.get("price", "bid"), end=args.get("end"),
                                            account=args.get("account"))
            return market.candles(sym, timeframe=tf, count=args.get("count", 100),
                                  price=args.get("price", "bid"), end=args.get("end"),
                                  account=args.get("account"))
        if name == "artificial_sentiment":
            import artificial_sentiment as art
            try:
                fn = art.compare if args.get("compare") else art.read
                return fn(args["symbol"], timeframe=args.get("timeframe") or "M15",
                          count=int(args.get("count") or art.DEFAULT_COUNT))
            except Exception as e:
                return {"error": str(e)}
        # A MODULE tool is dispatched HERE, before the account prelude — every
        # line past this point assumes it is holding an account tool, and the
        # `sym = _resolve_symbol(t, …)` below runs unconditionally with a `t`
        # that is None unless an account was named. So a module tool taking a
        # `symbol` died on None.instruments() before it was ever reached, while
        # one without a symbol worked fine.
        # Core still wins a name clash, because a core name never gets here.
        if name not in CORE_TOOL_NAMES and registry.has_tool(name):
            return registry.dispatch(name, args, user_id=user_id, ctx=ctx)

        # Listing accounts must work when there is no active one — that is
        # precisely when someone asks — so it comes before the adapter.
        if name == "list_accounts":
            return _accounts_context()
        # Everything below acts on an account, so the adapter is built once here.
        # `None` means "the active one", which trader() resolves for itself and
        # complains clearly about when there is none. The old guard left `t` as
        # None instead, so every tool the model called WITHOUT naming an account
        # — most of them, most of the time — died on
        # "'NoneType' object has no attribute 'positions'".
        acct = args.get("account")
        t = trading_api.trader(acct)
        if name == "search_symbols":
            q = str(args["query"]).upper().strip().replace("/", "").replace(" ", "")
            terms = SYMBOL_ALIASES.get(q, []) + [q]
            syms = []
            for i in t.instruments():
                sym = i["symbol"].upper()
                intl = (i.get("international") or "").upper().replace("/", "")
                if any(term in sym or term in intl for term in terms):
                    syms.append(i["symbol"])
            return {"symbols": syms[:25], "count": len(syms)}
        if name == "list_symbols":
            g = str(args.get("group", "")).lower().strip()
            cat = SYMBOL_GROUP_CATEGORY.get(g)
            if not cat:
                return {"error": f"unknown group {g!r}",
                        "groups": ["majors", "crosses", "metals", "indices", "energy", "crypto", "stocks"]}
            syms = [i["symbol"] for i in t.instruments()
                    if (i.get("category") or "").lower() == cat.lower()]
            return {"group": cat, "symbols": syms, "count": len(syms)}
        if name == "symbol_info":
            s = _resolve_symbol(t, args["symbol"])
            ins = next((i for i in t.instruments() if i["symbol"].upper() == s.upper()), None)
            if not ins:
                return {"error": f"unknown symbol {args['symbol']}"}
            keep = ["symbol", "international", "category", "digits", "contract_size", "volume_min", "volume_max", "volume_step"]
            pos = [p for p in t.positions() if (p.get("instrument") or "").upper() == s.upper()]
            return {"spec": {k: ins.get(k) for k in keep},
                    "bid": t.price(s, "bid"), "ask": t.price(s, "ask"),
                    "open_positions": len(pos)}
        if name == "price":
            s = _resolve_symbol(t, args["symbol"])
            return {"symbol": s, "bid": t.price(s, "bid"), "ask": t.price(s, "ask")}
        if name == "positions":
            ccy = _acct_ccy(t)
            return {"account_currency": ccy,
                    "money_note": f"profit is in {ccy} (the account's deposit currency) — report it as {ccy}, NOT USD/$.",
                    "positions": [{k: p.get(k) for k in ("position_id", "instrument", "type", "volume", "open_price", "sl", "tp", "profit")} for p in t.positions()]}
        if name == "orders":
            return {"orders": t.orders()}
        if name == "account_stats":
            b = t.balance()
            b["floating_profit"] = round(float(b.get("equity", 0)) - float(b.get("balance", 0)), 2)
            ccy = _acct_ccy(t)
            b["account_currency"] = ccy
            b["money_note"] = f"all money figures (balance, equity, margin, profit) are in {ccy} — report them as {ccy}, NOT USD/$."
            return b
        if name == "history":
            frm, to = trading_api._range_bounds(args["range"])
            return {"account_currency": _acct_ccy(t), "summary": t.pnl_summary(frm, to)}
        if name == "closed_trades":
            frm, to = trading_api._range_bounds(args["range"])
            sym = _resolve_symbol(t, args["symbol"]) if args.get("symbol") else None
            res = t.closed_trades(frm, to, instrument=sym, limit=args.get("limit", 50),
                                   only=args.get("only"), reason=args.get("reason"))
            if isinstance(res, dict):
                res.setdefault("account_currency", _acct_ccy(t))
            return res
        if name == "place_order":
            return t.place_order(_resolve_symbol(t, args["symbol"]), args["volume"], args["side"],
                                 sl=args.get("sl", 0), tp=args.get("tp", 0),
                                 sl_points=args.get("sl_points"), tp_points=args.get("tp_points"),
                                 emergency=bool(args.get("emergency", False)))
        if name == "pending_order":
            return t.pending_order(_resolve_symbol(t, args["symbol"]), args["volume"], args["side"], args["price"],
                                   sl_points=args.get("sl_points"), tp_points=args.get("tp_points"))
        # Resolving a symbol needs an account to resolve it AGAINST. Without one,
        # pass it through and let whichever tool uses it fail in its own words.
        sym = _resolve_symbol(t, args["symbol"]) if (t and args.get("symbol")) \
            else args.get("symbol")
        if name == "risk_status":
            return trading_api.risk_status(user_id, args.get("account"))
        if name == "risk_plan":
            rset = trading_api._risk_settings(user_id, args.get("account"))
            rp, rm = args.get("risk_pct"), args.get("risk_money")
            if args.get("volume") is None and (args.get("sl") is not None or args.get("sl_points") is not None):
                rp, rm = trading_api._effective_risk(rset, rp, rm)   # size from the user's risk
            rr = args.get("rr")
            if rr is None and args.get("tp") is None and args.get("tp_points") is None:
                rr = rset["reward_rr"]
            return t.risk_plan(_resolve_symbol(t, args["symbol"]), args["side"],
                               entry=args.get("entry"), risk_pct=rp, risk_money=rm,
                               sl=args.get("sl"), sl_points=args.get("sl_points"),
                               tp=args.get("tp"), tp_points=args.get("tp_points"),
                               rr=rr, volume=args.get("volume"),
                               basis=args.get("basis") or rset["risk_basis"])
        if name == "auto_sltp":
            rset = trading_api._risk_settings(user_id, args.get("account"))
            rp, rm = trading_api._effective_risk(rset, args.get("risk_pct"), args.get("risk_money"))
            rr = args.get("rr") if args.get("rr") is not None else rset["reward_rr"]
            return t.auto_sltp(_resolve_symbol(t, args["symbol"]), args["side"],
                               style=args.get("style") or rset["trade_style"] or "intraday",
                               entry=args.get("entry"), risk_pct=rp, risk_money=rm,
                               rr=rr, basis=args.get("basis") or rset["risk_basis"],
                               sl_mode=args.get("sl_mode", "structure"))
        if name == "calc_sltp":
            rsym = _resolve_symbol(t, args["symbol"])
            vol, side, entry = args["volume"], args["side"], args.get("entry")
            # Validate an already-formed trade: sl and/or tp given as prices (or as
            # *_points distances) → return P/L for each leg. Both may be present.
            legs = {}
            if args.get("sl") is not None:
                legs["sl"] = t.sltp_calc(rsym, vol, side, entry=entry, level=float(args["sl"]), mode="sl")
            elif args.get("sl_points") is not None:
                legs["sl"] = t.sltp_calc(rsym, vol, side, entry=entry, points=float(args["sl_points"]), mode="sl")
            if args.get("tp") is not None:
                legs["tp"] = t.sltp_calc(rsym, vol, side, entry=entry, level=float(args["tp"]), mode="tp")
            elif args.get("tp_points") is not None:
                legs["tp"] = t.sltp_calc(rsym, vol, side, entry=entry, points=float(args["tp_points"]), mode="tp")
            if legs:
                return legs
            # Otherwise a single conversion: exactly one of money / points / level.
            return t.sltp_calc(rsym, vol, side,
                               entry=entry, level=args.get("level"),
                               points=args.get("points"), money=args.get("money"),
                               mode=args.get("mode", "tp"))
        if name == "calc_basket":
            mode = args.get("mode", "tp")
            if args.get("positions"):
                rows = [{"symbol": _resolve_symbol(t, p.get("symbol")),
                         "entry": p.get("entry") if p.get("entry") is not None else p.get("open_price"),
                         "volume": p.get("volume"), "side": p.get("side")}
                        for p in args["positions"]]
            else:
                rows = []
                for p in t.positions():
                    if sym and (p.get("instrument") or "").upper() != sym.upper():
                        continue
                    rows.append({"symbol": p.get("instrument"),
                                 "entry": float(p.get("open_price") or 0),
                                 "volume": float(p.get("volume") or 0),
                                 "side": "buy" if int(p.get("type", 0)) % 2 == 0 else "sell",
                                 "position_id": p.get("position_id"),
                                 "sl": p.get("sl") or 0, "tp": p.get("tp") or 0})
            if not rows:
                return {"error": "no positions to compute (none open / none matched)"}
            # Signed-bracket mode: set an SL and/or TP to realise a chosen P/L at each
            # level (a positive sl_money is a PROFIT-LOCKING stop). Both can be set at once.
            if args.get("sl_money") is not None or args.get("tp_money") is not None:
                res = t.basket_bracket(rows, sl_money=args.get("sl_money"),
                                       tp_money=args.get("tp_money"),
                                       split=args.get("split", "equal"))
                if args.get("apply"):
                    applied = []
                    for leg, row in zip(res["legs"], rows):
                        pid = leg.get("position_id")
                        if not pid:
                            applied.append({"position_id": None, "ok": False, "error": "no position_id"})
                            continue
                        sl = leg["sl"] if leg["sl"] is not None else float(row.get("sl") or 0)
                        tp = leg["tp"] if leg["tp"] is not None else float(row.get("tp") or 0)
                        try:
                            t.modify_position(pid, sl=sl, tp=tp)
                            applied.append({"position_id": pid, "ok": True, "sl": sl, "tp": tp})
                        except Exception as e:
                            applied.append({"position_id": pid, "ok": False, "error": str(e)})
                    res["applied"] = applied
                return res
            if args.get("target") is None:
                return {"error": "pass target (single total) OR sl_money/tp_money (signed brackets)"}
            res = t.basket_target(rows, args["target"], mode=mode, split=args.get("split", "equal"))
            for leg, row in zip(res["legs"], rows):
                leg["position_id"] = row.get("position_id")
            if args.get("apply"):
                applied = []
                for leg, row in zip(res["legs"], rows):
                    pid = leg.get("position_id")
                    if not pid:
                        applied.append({"position_id": None, "ok": False, "error": "no position_id"})
                        continue
                    kw = ({"tp": leg["level"], "sl": float(row.get("sl") or 0)} if mode == "tp"
                          else {"sl": leg["level"], "tp": float(row.get("tp") or 0)})
                    try:
                        t.modify_position(pid, **kw)
                        applied.append({"position_id": pid, "ok": True, **kw})
                    except Exception as e:
                        applied.append({"position_id": pid, "ok": False, "error": str(e)})
                res["applied"] = applied
            return res
        if name == "close":
            return {"result": t.close(position_id=args.get("position_id"), symbol=sym, only=args.get("only"))}
        if name == "break_even":
            return {"result": t.break_even(position_id=args.get("position_id"), symbol=sym, offset_points=args.get("offset_points", 0))}
        if name == "lock_profit":
            return {"result": t.lock_profit(args["percent"], position_id=args.get("position_id"), symbol=sym)}
        if name == "delete_sltp":
            w = args.get("which", "both")
            return {"result": t.remove_levels(position_id=args.get("position_id"), symbol=sym, sl=w in ("both", "sl"), tp=w in ("both", "tp"))}
        if name == "modify_position":
            return t.modify_position(args["position_id"], sl=args.get("sl", 0), tp=args.get("tp", 0))
        if name == "cancel_orders":
            return {"result": t.cancel_orders(ticket=args.get("ticket"), symbol=sym)}
        # Not core — a module may have registered it. Modules are checked LAST so
        # a module can never shadow a core tool by claiming its name.
        if registry.has_tool(name):
            return registry.dispatch(name, args, user_id=user_id, ctx=ctx)
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if _sess_token is not None:
            import user_session
            user_session.reset(_sess_token)   # never leak the binding to the next task


def _run_tools_concurrently(calls, user_id=None, ctx=None):
    """calls: [(id, name, args)] → {id: result}. Parallel across accounts/symbols."""
    out = {}
    with ThreadPoolExecutor(max_workers=min(8, len(calls) or 1)) as ex:
        futs = {ex.submit(execute_tool, n, a, user_id, ctx): cid for (cid, n, a) in calls}
        for f in futs:
            cid = futs[f]
            try:
                out[cid] = f.result()
            except Exception as e:
                out[cid] = {"error": str(e)}
    return out


def _for_llm(result):
    """Trim heavy DISPLAY-only fields before feeding a tool result back to the
    model — the frontend still receives the FULL result for rich rendering
    (charts/tables). Keeps the model's context lean: candle arrays collapse to a
    summary, an agent trace to a note, and other long lists are capped."""
    if not isinstance(result, dict):
        return result
    out = {}
    for k, v in result.items():
        if k == "trace" and isinstance(v, list):
            out[k] = f"[{len(v)} node steps shown to the user]"
        elif k == "candles" and isinstance(v, list) and len(v) > 6:
            cl = [c.get("close") for c in v if isinstance(c, dict) and c.get("close") is not None]
            out[k] = (f"[{len(v)} candles hidden for brevity — first {cl[0] if cl else '?'}, "
                      f"last {cl[-1] if cl else '?'}, low {min(cl) if cl else '?'}, "
                      f"high {max(cl) if cl else '?'}]")
        elif isinstance(v, list) and len(v) > 25:
            out[k] = v[:25] + [f"…(+{len(v) - 25} more)"]
        else:
            out[k] = v
    return out


def risk_profile_summary(user_id, accounts):
    """A compact block of the user's OWN risk parameters for the system prompt. If a
    single account is in play, resolve that account's settings (override → profile);
    otherwise the profile-wide defaults. When nothing is set, tells the agent to use
    2% and nudge the user to configure their risk profile."""
    if not user_id:
        return ""
    import trading_api
    acct = accounts[0].get("account") if isinstance(accounts, list) and len(accounts) == 1 else None
    try:
        s = trading_api._risk_settings(user_id, acct)
    except Exception:
        return ""
    dd = [(lbl, v) for lbl, v in (("day", s["max_dd_day"]), ("week", s["max_dd_week"]),
                                  ("month", s["max_dd_month"])) if v]
    has_any = any([s["risk_pct"], s["reward_rr"], dd, s["trading_hours"]])
    if not has_any:
        return ("\n\nRISK PROFILE: the user has NOT set any risk parameters yet. Use a DEFAULT of 2% "
                "risk per trade when sizing, and proactively suggest they set their risk profile (risk "
                "% per trade, reward:risk, daily/weekly/monthly max drawdown, trading hours) in Risk "
                "Settings so every trade follows their own rules.")
    lines = [f"risk per trade {s['risk_pct']}%" if s["risk_pct"] is not None else "risk per trade not set (use 2%)"]
    if s["reward_rr"]:
        lines.append(f"reward:risk {s['reward_rr']}R")
    if dd:
        lines.append("max drawdown " + ", ".join(f"{lbl} {v}%" for lbl, v in dd))
    if s["trading_hours"]:
        lines.append(f"trading hours ({s['trading_tz']}) " +
                     ", ".join(f"{w['start']}-{w['end']}" for w in s["trading_hours"]))
    return ("\n\nRISK PROFILE (" + s["scope"] + "-level — FOLLOW it on every trade): " + "; ".join(lines) +
            ". Size trades to the risk %, target the reward:risk on TPs, and BEFORE opening any new trade "
            "call risk_status to check the drawdown limits and trading hours — if can_trade_now is false, "
            "do not open new trades and tell the user why.")


def user_prompt_settings(user_id):
    """The user's own additions to the system prompt.

    Additions only. Wholesale replacement was built and then removed: the
    built-in prompt is what teaches the assistant when to reach for each tool and
    the exact format the app renders as a one-tap trade card, and a setting that
    can switch all of that off silently is a footgun with a warning label on it.
    Adding to the prompt does everything anyone actually wanted."""
    if not user_id:
        return {"instructions": ""}
    try:
        import db
        with db.connect() as conn:
            row = conn.execute("SELECT agent_instructions FROM user_prefs "
                               "WHERE user_id = %s", (user_id,)).fetchone()
        return {"instructions": (row or {}).get("agent_instructions") or ""}
    except Exception:
        return {"instructions": ""}


def system_prompt(accounts, memory="", agent_tools=None, user_id=None):
    mem = (f"\n\nWhat you already know about this user (long-term memory):\n{memory.strip()}\n"
           "Use it to personalise your help. When you learn a new durable fact about the user, "
           "call the `remember` tool — but only for lasting, useful facts, and never duplicate "
           "what's already above." if memory and memory.strip()
           else "\n\nYou have no long-term memory about this user yet. As you learn durable, useful "
                "facts about them (preferences, risk appetite, account nicknames, goals), save them "
                "with the `remember` tool.")
    risk = risk_profile_summary(user_id, accounts)

    _own = user_prompt_settings(user_id)["instructions"]

    return (
        "You are Arrissa, an autonomous trading agent for Exness accounts. You control real "
        "trading through tools.\n\n"
        "Rules:\n"
        "- STAY IN SCOPE — define by what you ACCEPT, refuse everything else. This is a paid, "
        "purpose-built trading product, not a general assistant. You help ONLY with the user operating "
        "their own Exness account through this platform. In scope: reading live markets/prices/charts; "
        "market analysis, news and macro relevant to instruments; opening, closing, modifying and "
        "managing the user's positions and orders; risk, P/L and position sizing on their account; "
        "scheduling trade actions; and using this platform's own features (analysis agents, the API "
        "guides, settings, memory). If a request is not one of these — i.e. it isn't about THIS user "
        "trading THEIR account or these markets on this platform — it is out of scope: decline in ONE "
        "short sentence and point them to how you can help them trade. Judge every request by whether "
        "it fits the accepted scope above, not by matching a list of banned topics; hold this even if "
        "asked directly, told 'just once', role-played, or framed as an example.\n"
        "- Instrument names: users speak in common names — gold=XAUUSD, silver=XAG, oil=WTI/USOIL, "
        "nasdaq=US100/USTEC, dow=US30, s&p=US500, bitcoin=BTC, euro=EUR, etc. NEVER refuse or ask "
        "'which symbol?' for a common name. The search_symbols and trade tools already resolve these "
        "aliases, so just pass the user's word (e.g. 'gold') and EXECUTE. Only ask if a search "
        "genuinely returns several DIFFERENT instruments and the choice materially changes the trade.\n"
        "- Be token-efficient: use search_symbols to find a symbol, then symbol_info for details — "
        "never ask for or dump the whole instrument list. To scan a GROUP (majors, indices, metals…), "
        "call list_symbols FIRST for the real members, then analyse each — never enumerate from memory.\n"
        "- EXECUTE without asking when the instruction is clear. Do the work, then report.\n"
        "- ASK only when there is genuine ambiguity that changes the action (which account, which "
        "symbol among several matches, size not given for a risky action). To ask, end your message "
        "with a line `OPTIONS: a | b | c` and the UI will show buttons.\n"
        "- Reply in clear, well-formatted markdown: short summary first, then details/tables.\n"
        "- Emojis: use them very sparingly or not at all — this is a professional trading tool. "
        "Do NOT decorate headings, bullets, or lines with emojis; at most an occasional single marker "
        "where it genuinely aids scanning. Never string multiple emojis together.\n"
        "- CURRENCY (critical): EVERY money figure from the broker — a position's profit, balance, equity, "
        "margin, free_margin, floating P/L, closed-trade profit — is in the ACCOUNT'S OWN DEPOSIT CURRENCY, "
        "which is NOT necessarily USD. Read it from the `account_currency` field the tools return (or the "
        "account list) and report every amount in THAT currency with its correct symbol (ZAR → 'R', EUR → "
        "'€', GBP → '£', USD → '$', JPY → '¥'). NEVER write '$' or 'USD' unless account_currency is literally "
        "USD. A ZAR account showing profit 78.74 means R78.74, not $78.74 — do not convert it, just label it "
        "correctly. The calc/risk tools already state their own account_currency; honour it the same way.\n"
        "- A stop-loss can sit in PROFIT to lock gains — it's just a price level, not always a loss. To lock "
        "or target exact amounts across trades, use calc_basket with sl_money/tp_money (signed: + profit, − loss).\n"
        "- Points vs price: sl_points/tp_points are distances in points; sl/tp are absolute prices.\n"
        "- MARGIN: before opening any trade, free margin is checked automatically and the order is REFUSED "
        "if there isn't enough — this is a safety guard. NEVER pass emergency=true to skip it unless the "
        "user explicitly says the trade is urgent/must-fire. If an order is refused for insufficient margin, "
        "tell the user the shortfall and offer a smaller volume that fits (use check_margin / account_stats "
        "to size it) — do not silently retry with emergency.\n"
        "- SMART setups (no levels given): when the user wants a trade set up 'properly' / 'with risk "
        "management' / 'find my SL and TP' / 'size it for me' / 'risk 2% on gold' but does NOT give you "
        "the stop, use auto_sltp — it reads live market structure + ATR to place the stop, sets the "
        "target by reward:risk, and sizes the lot to the risk budget. Pick the style from their words "
        "(scalp / day-trade→intraday / swing / long-term→position). Then place with the returned volume, "
        "sl.price and tp.price. Use risk_plan instead when the user already knows the stop.\n"
        "- RISK sizing: whenever risk is a PERCENT or a fixed money amount and the STOP is known — 'risk "
        "2% with SL at 3990', 'risk $50', 'size this so my stop loses 1%', 'where's my stop for 2% on 0.5 "
        "lots', 'what does this trade risk' — ALWAYS call risk_plan. It is symbol-aware and solves for "
        "whichever of {volume, stop, risk} you don't give: pass risk (risk_pct|risk_money) + a stop "
        "(sl|sl_points) to get the exact VOLUME; add rr for the TP. Use its `volume`, `sl.price` and "
        "`tp.price` verbatim — never size a lot or place a stop by hand. Honour any standing rule (e.g. a "
        "memory saying 'risk 2% every trade') by defaulting the risk to it; if risk isn't stated at all, "
        "both auto_sltp and risk_plan fall back to the user's saved default-risk setting.\n"
        "- SL/TP maths: for ANY dollars⇄points⇄price question on a KNOWN volume — 'TP to make $X', 'SL to "
        "risk $X', 'P/L if price hits Y', '$ per point', 'how many points is $X' — ALWAYS call calc_sltp "
        "(or calc_basket to spread one total target across several trades). Never estimate by hand; a "
        "point is the smallest price step, NOT a dollar. Then you can place/modify with the exact level.\n"
        "- Analysis-agent authoring: you can build and refine the user's analysis agents (the flow-graph "
        "tools). When they ask to improve/reword a node or change an agent, call get_analysis_agent first "
        "to see node ids and current text, then edit_analysis_node for a single node's wording, or "
        "update_analysis_agent to rename/activate or replace the whole flow (start with trigger-agent-call, "
        "end with respond); create_analysis_agent to build a new one. Confirm what changed. "
        "Keep every node's instruction BRIEF — a sentence or two of concrete direction, no filler, no "
        "restating context the node already has; concise but complete. Don't bloat a node's text when "
        "editing — tighten it, don't pad it.\n"
        "- Actionable analysis: whenever you present market analysis (your own or from an analysis "
        "agent), END with a concrete, actionable trade setup — never leave analysis without a clear next "
        "step. For the single BEST opportunity, emit a fenced ```trade block containing ONLY JSON: "
        "{\"symbol\": \"XAUUSD\", \"side\": \"buy\"|\"sell\", "
        "\"order_type\": \"market\"|\"limit\"|\"stop\", \"entry\": <price>, \"sl\": <price>, "
        "\"tp\": <price>, \"volume\": <lots, optional>, \"confidence\": <integer 1-5>, "
        "\"at\": \"<when to open it, optional>\", \"rationale\": \"<one line>\"}. "
        "`order_type` is NOT optional and it is not cosmetic — it decides what happens when the "
        "user taps the card. Work it out from `entry` against the CURRENT price, which you must "
        "know before you emit a card: a sell above the market and a buy below it are LIMIT orders; "
        "a sell below the market and a buy above it are STOP orders; only an entry at the price "
        "right now is `market`. A retest, a pullback, 'sell the bounce', 'buy the dip' — none of "
        "those are market orders, and labelling one as `market` fills it instantly at a worse "
        "price and skips the level the whole idea depended on. If you are not certain where price "
        "is, read it first.\n"
        "Include `at` ONLY when the setup is meant to open at a particular time rather than now "
        "— a level that matters at the London open, a trade to place after a data release, "
        "anything the user asked to happen later. Write it as an ISO datetime "
        "(2026-08-03T07:00:00Z) when you know the moment, or plainly ('in 30 minutes') when the "
        "user said it that way. The card turns into a SCHEDULE button and the trade is placed by "
        "the server at that time — so do not add `at` for a setup that should be taken now, and "
        "do not omit it for one that should not. ALWAYS include `confidence`: an integer 1-5 rating how good and "
        "ripe this trade is right now — 5 = high-conviction, everything aligns and it's ripe to trade; "
        "1 = weak/speculative. Base it on how strongly the gathered evidence agrees (structure, news, "
        "macro, sentiment, sessions) and, if an analysis agent already stated a confidence, use it. The "
        "app renders each ```trade block as a one-tap trade card (confidence shown as 1-5 stars). Use "
        "real levels (key S/R, and the SL/TP calculator for exact prices). If there is genuinely no clean "
        "setup, say so plainly instead.\n"
        "- CARRY OUT WHAT WAS ASKED, ALL OF IT. When the user names a sequence — 'analyse gold "
        "and enter', 'check the news then close my losers', 'find the best setup and take it' — "
        "that is ONE instruction, not a question followed by a question. Do every step and finish "
        "it. Do not stop after the analysis to ask whether to proceed, do not offer the trade as a "
        "suggestion, do not ask which account when the accounts are already known: they told you "
        "what they wanted before you started. Pausing to confirm what was already confirmed wastes "
        "the turn they were trying to save.\n"
        "  Stop and ask ONLY when you genuinely cannot proceed: a detail they never gave and you "
        "cannot derive (a lot size with no risk setting to compute one from), an account that is "
        "archived or refused, or a result that contradicts the premise — if they said 'analyse "
        "gold and enter' and the analysis says there is no setup, say so and do NOT invent one. "
        "Finishing the instruction means finishing it honestly, not forcing a trade.\n"
        "- After actions, confirm concisely what was done on which account."
        + (("\n- Analysis agents (CHOOSE BY CONTEXT): the user has built custom analysis agents, each "
            "exposed as an `analysis_…` tool that runs a saved analysis flow. Before you analyse a market, "
            "look at what each available agent is FOR and pick the one that best fits the user's intent — "
            "e.g. a fundamentals/macro agent for 'fundamental setups', a scalping agent for intraday, the "
            "general one when nothing more specific fits. Do NOT just default to the first tool. Available "
            "agents:\n"
            + "\n".join(f"  • {t['name']} — {t['description']}" for t in agent_tools)
            + "\nCall the chosen agent with `request`, then use its analysis in your answer. IMPORTANT: when "
              "the request spans SEVERAL assets (e.g. 'best setups for the major pairs', 'most interesting "
              "assets to trade next week'), first resolve the concrete members with list_symbols, then run "
              "the chosen agent ONCE PER asset — issue those calls in the SAME turn — and rank the results "
              "to answer which are most interesting. Never answer a multi-asset question from a single "
              "agent run on one asset.")
           if agent_tools else "")
        # What the INSTALLED modules want the assistant to know. Core cannot
        # describe a capability it may not have — a module that ships a tool
        # ships the sentence that explains when to reach for it.
        + (("\n- " + registry.notes().strip()) if registry.notes().strip() else "")
        # ── everything below here VARIES; everything above is identical for every
        # user on this instance ────────────────────────────────────────────────
        #
        # That split is the whole point of the ordering. A provider caches on a
        # matching PREFIX, so one changing byte early invalidates the rest: the
        # account list used to sit at character 236 of an 11,400-character
        # prompt, and a balance moving by $1.50 cost a full-price re-read of the
        # other 98%. Measured cache hits were 5%. Cached input is 50x cheaper
        # than fresh on DeepSeek, so this ordering is worth more than any amount
        # of trimming.
        #
        # Recency helps here too: the account list is what every tool call must
        # obey, and it now sits closest to the conversation instead of buried
        # above ten rules.
        + f"\n\nThe accounts you may act on — the ONLY ones — are: {json.dumps(accounts)}. Every "
          "tool call's `account` MUST be one of these exact numbers; never use any other account. "
          "If the user names an account by type ('the real account', 'demo', 'pro', 'standard'), "
          "map it via the `real`/`type` fields. If they don't say which and there is one account, "
          "use it; if there are several, act on ALL of them and issue the tool calls in the SAME "
          "turn so they run concurrently. An `archived` account may reject trades — if a tool "
          "errors because the account is archived/disabled, report that error; do NOT silently "
          "switch to another account."
        + risk
        + mem
        # The user's own standing instructions go LAST, so they qualify
        # everything above rather than being qualified by it. Marked as theirs so
        # the model can tell house rules from the user's.
        + (f"\n\nThe user's own standing instructions for you — follow these, and where they "
           f"conflict with a preference above, these win:\n{_own.strip()}" if _own.strip() else "")
    )


# ── runners (stream dict events) ────────────────────────────────────────────────
def run_anthropic(api_key, model, messages, accounts, memory="", user_id=None, meter=None):
    import anthropic
    import analysis_agent
    client = anthropic.Anthropic(api_key=api_key, timeout=ai_keys.LLM_TIMEOUT)
    agent_tools, agent_map = list_agent_tools(user_id)
    tools = TOOLS + registry.tool_schemas() + agent_tools
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    ctx = {"provider": "anthropic", "api_key": api_key, "model": model,
           "user_id": user_id, "agent_tools": agent_map, "last_user": last_user}
    if meter is not None:
        ctx["_usage"] = meter               # main chat + any analysis runs accumulate here
    convo = list(messages)
    for _ in range(12):
        tool_uses = []
        with client.messages.stream(
            model=model, max_tokens=8000,
            thinking={"type": "adaptive", "display": "summarized"},
            system=system_prompt(accounts, memory, agent_tools, user_id), tools=tools, messages=convo,
        ) as stream:
            for ev in stream:
                if ev.type == "content_block_delta":
                    if ev.delta.type == "thinking_delta":
                        yield {"type": "thinking", "text": ev.delta.thinking}
                    elif ev.delta.type == "text_delta":
                        yield {"type": "text", "text": ev.delta.text}
            final = stream.get_final_message()
        try:
            u = final.usage
            analysis_agent._accum_usage(ctx, model, getattr(u, "input_tokens", 0),
                                        getattr(u, "output_tokens", 0),
                                        getattr(u, "cache_read_input_tokens", 0) or 0)
        except Exception:
            pass
        convo.append({"role": "assistant", "content": final.content})
        tool_uses = [b for b in final.content if b.type == "tool_use"]
        if final.stop_reason != "tool_use" or not tool_uses:
            yield {"type": "done"}
            return
        calls = [(b.id, b.name, b.input) for b in tool_uses]
        for b in tool_uses:
            yield {"type": "tool_call", "id": b.id, "name": b.name, "input": b.input}
        results = _run_tools_concurrently(calls, user_id, ctx)
        for b in tool_uses:
            yield {"type": "tool_result", "id": b.id, "name": b.name, "result": results[b.id]}
        convo.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": cid, "content": json.dumps(_for_llm(results[cid]), default=str)}
            for (cid, _, _) in calls]})
    yield {"type": "done"}


def run_openai(api_key, model, messages, accounts, base_url=None, memory="", user_id=None, provider="openai", meter=None):
    from openai import OpenAI
    import analysis_agent
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=ai_keys.LLM_TIMEOUT)
    agent_tools, agent_map = list_agent_tools(user_id)
    tools = TOOLS + registry.tool_schemas() + agent_tools
    oai_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                  "parameters": t["input_schema"]}} for t in tools]
    last_user = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    ctx = {"provider": provider, "api_key": api_key, "model": model,
           "user_id": user_id, "agent_tools": agent_map, "last_user": last_user}
    if meter is not None:
        ctx["_usage"] = meter               # main chat + any analysis runs accumulate here
    convo = [{"role": "system", "content": system_prompt(accounts, memory, agent_tools, user_id)}] + list(messages)
    for _ in range(12):
        stream = client.chat.completions.create(model=model, messages=convo, tools=oai_tools,
                                                stream=True, stream_options={"include_usage": True})
        text = ""
        tool_calls = {}
        usage = None
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage             # final chunk carries usage (include_usage)
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if d.content:
                text += d.content
                yield {"type": "text", "text": d.content}
            for tc in (d.tool_calls or []):
                slot = tool_calls.setdefault(tc.index, {"id": tc.id, "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
        if usage:
            try:
                hit = getattr(usage, "prompt_cache_hit_tokens", None)
                if hit is None:
                    hit = (getattr(usage, "model_extra", None) or {}).get("prompt_cache_hit_tokens", 0)
                analysis_agent._accum_usage(ctx, model, getattr(usage, "prompt_tokens", 0),
                                            getattr(usage, "completion_tokens", 0), hit or 0)
            except Exception:
                pass
        if not tool_calls:
            yield {"type": "done"}
            return
        assistant_msg = {"role": "assistant", "content": text or None, "tool_calls": [
            {"id": s["id"], "type": "function", "function": {"name": s["name"], "arguments": s["args"] or "{}"}}
            for s in tool_calls.values()]}
        convo.append(assistant_msg)
        calls = []
        for s in tool_calls.values():
            try:
                args = json.loads(s["args"] or "{}")
            except Exception:
                args = {}
            calls.append((s["id"], s["name"], args))
            yield {"type": "tool_call", "id": s["id"], "name": s["name"], "input": args}
        results = _run_tools_concurrently(calls, user_id, ctx)
        for (cid, nm, _) in calls:
            yield {"type": "tool_result", "id": cid, "name": nm, "result": results[cid]}
            convo.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(_for_llm(results[cid]), default=str)})
    yield {"type": "done"}


def run_agent(provider, model, api_key, messages, accounts, memory="", user_id=None, meter=None):
    """`meter` (optional dict) accumulates the WHOLE turn's real token usage — the
    main chat calls plus every analysis-agent run they trigger — so the caller can
    debit the true cost afterwards."""
    try:
        if provider == "anthropic":
            yield from run_anthropic(api_key, model, messages, accounts, memory, user_id, meter=meter)
        elif ai_keys.speaks_openai(provider):
            # Gemini, Grok, Groq and OpenRouter are all the OpenAI wire format
            # behind a different base URL, so they need no branch of their own.
            yield from run_openai(api_key, model, messages, accounts,
                                  base_url=ai_keys.base_url(provider), memory=memory,
                                  user_id=user_id, provider=provider, meter=meter)
        else:
            yield {"type": "error", "error": f"unknown provider {provider}"}
    except Exception as e:
        yield {"type": "error", "error": str(e)}
