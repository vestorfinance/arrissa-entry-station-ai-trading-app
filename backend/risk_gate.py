"""The check between pressing BUY and the order going out.

The brief was "optimised for entering without asking" — so the expensive part of
this file is the part that decides there is NOTHING to say. When a trade fits the
trader's own rules it goes straight out: no modal, no model, no round trip. That
is the common case and it has to feel like a button, not an interview.

So the gate is DETERMINISTIC and the agent is an ADVISOR, not a gatekeeper. Size,
drawdown and session are arithmetic against settings the trader wrote down; an
LLM in that path would add latency and cost to every click and could refuse a
trade for a reason nobody can audit. Only once something is actually wrong — or a
materially better size exists — is a model asked to explain it, and then exactly
once, with a concrete alternative rather than a discussion.

`reason` on every issue is written for the person, not the log. It appears in the
modal as the whole explanation of why their button did not simply work.
"""
from datetime import datetime, time as _time, timedelta, timezone

import db

# Below this the difference is not worth a modal. Somebody typing 0.10 when the
# rule says 0.108 does not need to be stopped and told about it.
TOLERANCE = 0.15          # 15% over the sized volume before it is "too big"
STYLE_DEFAULT = "intraday"


def settings_for(user_id, account="") -> dict:
    """The account's rules, falling back to the profile default.

    Per-account overrides exist so a funded account can be governed differently
    from a demo, and a missing field on the override inherits rather than
    resetting to nothing — half a rule is not a rule."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM risk_settings WHERE user_id = %s AND account IN ('', %s)",
            (user_id, str(account or ""))).fetchall()
    base = next((dict(r) for r in rows if r["account"] == ""), {}) or {}
    over = next((dict(r) for r in rows if r["account"] != ""), {}) or {}
    out = dict(base)
    for k, v in over.items():
        if v is not None and v != "" and not (k == "trading_hours" and not v):
            out[k] = v
    return out



def _money_at(level, vol, planned):
    """What that level is worth in cash AT THIS VOLUME.

    The engine prices its plan at the size IT chose, so the number attached to a
    stop is only true for that size. Money scales linearly with lots, so the
    trader's own volume is a ratio away — and showing the plan's figure next to a
    different lot count would be quietly wrong in the one place it matters."""
    if not isinstance(level, dict) or not planned:
        return None
    m = level.get("money")
    if m in (None, ""):
        return None
    try:
        return round(abs(float(m)) * float(vol) / float(planned), 2)
    except Exception:
        return None


def _level(v):
    """The engine returns sl/tp as a rich dict (price, distance, money, basis).
    Everything downstream — the modal, the order — wants the PRICE. Passing the
    dict through renders as an object in React and reaches place_order as a
    non-number, so it is flattened once, here, rather than at each caller."""
    if isinstance(v, dict):
        return v.get("price")
    return v


def _in_hours(cfg) -> tuple:
    """(inside, why). No configured hours means every hour is allowed."""
    hours = cfg.get("trading_hours") or []
    if not hours:
        return True, ""
    tzname = cfg.get("trading_tz") or "UTC"
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tzname))
    except Exception:
        now = datetime.now(timezone.utc)
        tzname = "UTC"
    cur = now.time()
    for w in hours:
        try:
            sh, sm = [int(x) for x in str(w.get("start")).split(":")]
            eh, em = [int(x) for x in str(w.get("end")).split(":")]
        except Exception:
            continue
        start, end = _time(sh, sm), _time(eh, em)
        # A window that ends before it starts wraps past midnight, which is an
        # ordinary way to describe a session and not a mistake to reject.
        if (start <= cur <= end) if start <= end else (cur >= start or cur <= end):
            return True, ""
    windows = ", ".join(f"{w.get('start')}–{w.get('end')}" for w in hours)
    return False, (f"It is {cur.strftime('%H:%M')} {tzname} and you trade {windows}.")


def _realised(trader, since) -> float:
    """Closed P/L since `since`, in account currency. 0.0 if it cannot be read —
    a drawdown rule must never BLOCK a trade because history was unavailable."""
    try:
        rows = trader.closed_trades(since=since.isoformat()) or []
        if isinstance(rows, dict):
            rows = rows.get("trades") or rows.get("positions") or []
        return float(sum((r.get("profit") or 0) + (r.get("commission") or 0)
                         + (r.get("swap") or 0) for r in rows))
    except Exception:
        return 0.0


def _drawdown_issues(trader, cfg, basis_amt) -> list:
    """Whether today/this week/this month has already lost more than allowed."""
    out = []
    if basis_amt <= 0:
        return out
    now = datetime.now(timezone.utc)
    spans = (
        ("max_dd_day", "today", now.replace(hour=0, minute=0, second=0, microsecond=0)),
        ("max_dd_week", "this week", now - timedelta(days=now.weekday())),
        ("max_dd_month", "this month", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)),
    )
    for key, label, since in spans:
        limit = cfg.get(key)
        if not limit:
            continue
        pl = _realised(trader, since)
        if pl >= 0:
            continue
        used_pct = abs(pl) / basis_amt * 100
        if used_pct >= float(limit):
            out.append({
                "code": key, "severity": "block",
                "title": f"Your {label} loss limit is already reached",
                "reason": (f"You are down {abs(pl):,.2f} {label} — {used_pct:.1f}% of the account, "
                           f"and your limit is {float(limit):.1f}%. This trade would be taken "
                           f"past a line you set yourself."),
            })
    return out


def check(user_id, account, symbol, side, volume, *, sl=None, tp=None) -> dict:
    """Is this trade inside the trader's own rules?

    Returns {ok, issues, suggestion, plan}. `ok` true means: place it now, say
    nothing. Anything else is worth one interruption and no more.
    """
    import trading_api
    trader = trading_api.trader(account=account) if account else trading_api.trader()

    cfg = settings_for(user_id, account)
    style = (cfg.get("trade_style") or STYLE_DEFAULT).lower()
    basis = (cfg.get("risk_basis") or "equity").lower()
    risk_pct = cfg.get("risk_pct")
    rr = cfg.get("reward_rr")

    issues, suggestion, plan = [], None, None
    bal = {}
    try:
        bal = trader.balance() or {}
    except Exception:
        pass
    basis_amt = float(bal.get(basis) or bal.get("equity") or bal.get("balance") or 0)

    # 1. Session. Cheap, and independent of everything else.
    inside, why = _in_hours(cfg)
    if not inside:
        issues.append({"code": "hours", "severity": "warn",
                       "title": "Outside your trading hours", "reason": why})

    # 2. Drawdown already spent.
    issues.extend(_drawdown_issues(trader, cfg, basis_amt))

    # 3. Size against the risk budget. This is the one that needs the engine:
    #    what a lot is WORTH depends on the symbol, and a stop is what turns
    #    volume into money at risk.
    if risk_pct:
        try:
            plan = trader.auto_sltp(symbol, side, style=style, risk_pct=float(risk_pct),
                                    rr=float(rr) if rr else None, basis=basis)
            want = float(plan.get("volume") or 0)
            if want > 0 and float(volume) > want * (1 + TOLERANCE):
                over = float(volume) / want
                issues.append({
                    "code": "size", "severity": "block",
                    "title": f"That is {over:.1f}× your usual size",
                    "reason": (f"At {volume} lots with a {style} stop this risks about "
                               f"{abs(float((plan.get('sl') or {}).get('money') or 0)) * float(volume) / want:,.2f} "
                               f"— your rule is {float(risk_pct):.2f}% of {basis}, which is "
                               f"{want} lots here."),
                })
                suggestion = {"volume": want,
                              "sl": _level(plan.get("sl")), "tp": _level(plan.get("tp")),
                              "risk_money": _money_at(plan.get("sl"), want, want),
                              "reward_money": _money_at(plan.get("tp"), want, want),
                              "reason": f"{want} lots keeps this trade at your {float(risk_pct):.2f}%."}
            elif want > 0 and not sl:
                # Not an issue — a free improvement. Offered, never enforced.
                suggestion = {"volume": float(volume),
                              "sl": _level(plan.get("sl")), "tp": _level(plan.get("tp")),
                              "risk_money": _money_at(plan.get("sl"), volume, want),
                              "reward_money": _money_at(plan.get("tp"), volume, want),
                              "reason": "Stop and target from structure, sized to your rule.",
                              "advisory": True}
        except Exception as e:
            issues.append({"code": "engine", "severity": "warn",
                           "title": "Could not size this trade",
                           "reason": f"The risk engine could not price {symbol}: {e}. "
                                     f"The trade is not blocked, but it is unchecked."})

    blocking = [i for i in issues if i["severity"] == "block"]
    # What the trade in front of them is worth either way, at THEIR size — the
    # question anybody actually has before pressing the button.
    outcome = None
    if plan:
        want = float(plan.get("volume") or 0)
        outcome = {"risk_money": _money_at(plan.get("sl"), volume, want),
                   "reward_money": _money_at(plan.get("tp"), volume, want),
                   "sl": _level(plan.get("sl")), "tp": _level(plan.get("tp")),
                   # From the PLAN, not the balance: balance() has no currency
                   # field, and the plan states the one its money is quoted in,
                   # which is the number being shown.
                   "currency": (plan.get("account_currency") or "")}

    return {
        "ok": not blocking,
        "issues": issues,
        "suggestion": suggestion,
        "outcome": outcome,
        "plan": plan,
        "settings": {"risk_pct": risk_pct, "reward_rr": rr, "style": style,
                     "basis": basis, "has_rules": bool(cfg)},
        "trade": {"symbol": symbol, "side": side, "volume": float(volume),
                  "sl": sl, "tp": tp, "account": account},
    }


def advise(user_id, ctx, message="") -> dict:
    """One short reply from the Risk Settings agent — only ever called when the
    deterministic gate already found something, or when the trader answers back.

    A model is not in the path of a good trade. It is in the path of a
    conversation that is happening anyway."""
    import ai_keys
    import json as _json

    issues = "; ".join(f"{i['title']}: {i['reason']}" for i in (ctx.get("issues") or []))
    sug = ctx.get("suggestion") or {}
    trade = ctx.get("trade") or {}
    st = ctx.get("settings") or {}

    system = (
        "You are the Risk Settings agent inside a trading app. The trader has just pressed "
        "BUY or SELL and an automatic check found something. Your job is ONE short reply, then "
        "they act.\n\n"
        "Rules:\n"
        "- Be brief. Two or three sentences. They are mid-trade.\n"
        "- Their rules are THEIRS. You advise; you never lecture and never refuse.\n"
        "- If they push back or propose their own number, evaluate THAT number against their "
        "rules and answer it directly. Do not repeat your first suggestion unchanged.\n"
        "- Always end with a concrete number they can act on.\n"
        "- Never ask a question you can answer yourself from the numbers given.\n"
        'Reply as JSON only: {"message": "<what to say>", "volume": <lots or null>, '
        '"sl": <price or null>, "tp": <price or null>}'
    )
    user_msg = (
        f"Trade: {trade.get('side')} {trade.get('volume')} lots of {trade.get('symbol')}.\n"
        f"Their rules: risk {st.get('risk_pct')}% of {st.get('basis')}, "
        f"reward:risk {st.get('reward_rr')}, style {st.get('style')}.\n"
        f"What the check found: {issues or 'nothing blocking'}.\n"
        f"Suggested instead: {_json.dumps(sug)}.\n"
        + (f"\nThe trader replies: {message}\n" if message else "")
    )

    try:
        import main as app_main
        alias = app_main._user_analysis_model(user_id)
    except Exception:
        alias = None
    provider, model, key = ai_keys.resolve(user_id, alias)
    if not key:
        return {"message": (sug.get("reason") or "Adjust the size to match your rules."),
                **{k: sug.get(k) for k in ("volume", "sl", "tp")}}

    try:
        import requests
        if provider == "anthropic":
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=45,
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": model, "max_tokens": 700, "system": system,
                                    "messages": [{"role": "user", "content": user_msg}]})
            data = r.json()
            text = "".join(b.get("text", "") for b in (data.get("content") or []))
        else:
            base = ai_keys.OPENAI_WIRE.get(provider)
            r = requests.post(f"{base}/chat/completions", timeout=45,
                              headers={"Authorization": f"Bearer {key}",
                                       "content-type": "application/json"},
                              json={"model": model, "messages": [
                                  {"role": "system", "content": system},
                                  {"role": "user", "content": user_msg}]})
            data = r.json()
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        t = text.strip()
        if not t.startswith("{"):
            import re
            m = re.search(r"\{.*\}", t, re.S)
            t = m.group(0) if m else "{}"
        out = _json.loads(t)
    except Exception:
        # The advice is a nicety; the numbers behind it are not. Falling back to
        # the engine's own suggestion keeps the modal useful with no model at all.
        out = {}

    return {"message": out.get("message") or sug.get("reason")
            or "Adjust the size to match your rules.",
            "volume": out.get("volume", sug.get("volume")),
            "sl": out.get("sl", sug.get("sl")),
            "tp": out.get("tp", sug.get("tp"))}


# ── the agent that carries this in the UI ────────────────────────────────────
# A system agent, so it is visible and editable in the agent list but cannot be
# deleted: other things call it by this fixed id, and deleting it would break a
# feature rather than remove one.
from pathlib import Path

AGENT_ID = "00000000-0000-4000-a000-000000000002"
TEMPLATE = Path(__file__).parent.parent / "templates" / "risk-settings-agent.json"


def seed(log=print) -> bool:
    """Put the Risk Settings agent on this instance if it is not already here.

    Only ever INSERTs. An upgrade path that overwrites would throw away a
    trader's edits to their own risk wording, which is exactly the mistake the
    watch-list seeder was fixed for."""
    import json as _json
    from psycopg.types.json import Json
    try:
        if not TEMPLATE.exists():
            return False
        tpl = _json.loads(TEMPLATE.read_text())
        with db.connect() as conn:
            row = conn.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return False                      # nobody to own it yet
            conn.execute(
                "INSERT INTO analysis_agents (id, user_id, name, description, status, flow, is_system) "
                "VALUES (%s,%s,%s,%s,'active',%s,TRUE) ON CONFLICT (id) DO NOTHING",
                (AGENT_ID, row["id"], tpl["name"], tpl.get("description", ""),
                 Json(tpl["flow"])))
            conn.commit()
        return True
    except Exception as e:
        log(f"[risk-gate] seed failed: {e!r}")
        return False
