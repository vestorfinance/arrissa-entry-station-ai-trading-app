# EntryStation

**Self-hosted AI trading platform for MetaTrader 5.** Connect your own broker account at
**Exness** or **TradeLocker** and work four ways: talk to it in plain language, build
market-analysis agents on a canvas, drive everything from a plain-URL API with an API key, or
connect it to any MCP client as a **Model Context Protocol server** — so Claude, Cursor or your own
agent can read your market data and manage your account directly.

It never holds your money and never stores your broker password. Funds, margin and execution stay
at your broker; connecting captures a revocable session and discards the password. EntryStation is
the intelligence and control layer over an account that stays entirely yours.

---

## What it does

- **Trade in plain language.** *"Buy 0.1 gold with a 3000 point stop and a 5000 point target."*
  Margin is checked first and refused with the exact shortfall rather than forced through; volume is
  adjusted to the instrument's minimum and you are told when it was.
- **Build your own analysts.** A visual canvas: pull the news, check the calendar, read rate odds,
  read sentiment, look at structure, give a verdict. Each finished agent becomes a tool the
  assistant can call by name.
- **Schedule anything.** *"Close all gold in 30 minutes."* It runs server-side whether you are
  watching or not.
- **Drive it from code.** Every capability is a plain URL with an API key behind it — trading,
  analysis and scheduling alike.

## Modules

Core boots with nothing. Every capability is a signed module you install or remove on its own.

| Module | What it gives an agent |
|---|---|
| **Exness** / **TradeLocker** | Live prices and candles, orders, positions, history — your own account, your own login |
| **Economic Calendar** | Scheduled releases with the instruments each one moves, and the actual within seconds of the print |
| **Market News** | TradingView and FXStreet, impact-scored and tagged to instruments |
| **Retail Sentiment** | Myfxbook community positioning — where the crowd actually is, per instrument |
| **Bond Yields** | Government yields mapped onto your instruments — the rate differential that drives an FX pair |
| **Fed Watch** | The market's own odds on the next Fed decision, from CME FedWatch |
| **Truth Social** | Posts from watched accounts, labelled high or low market impact by an AI gate |
| **High Margin Requirements** | Exness HMR windows — when leverage is capped and sizing needs more care |
| **Telegram** | Message yourself from an agent, and talk to your agent from your phone |
| **Visuals** | Real price charts with the trade drawn on them |

The free modules install themselves on first start. The rest are bought from the store and arrive on
their own: the instance asks what it owns, proves it is itself, and applies the licence.

## Bring your own model

OpenAI, Anthropic, DeepSeek, Google Gemini, xAI Grok, Groq or OpenRouter. Your key, your bill,
nothing metered. Models are listed live from the provider rather than hardcoded, so a model released
today is selectable today.

## Install

Full guide, from a bare Ubuntu server to a running instance:
**[entrystation.com/install](https://entrystation.com/install)**

```bash
git clone https://github.com/vestorfinance/arrissa-entry-station-ai-trading-app.git /opt/entrystation
cd /opt/entrystation && python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
```

You will need PostgreSQL, a `FERNET_KEY` and a `JWT_SECRET`, and `ENTRYSTATION_EDITION=community`.
The schema creates itself on first boot. The first account created is the owner, and registration
closes permanently once it exists.

## Stack

FastAPI · PostgreSQL · React · Vite · Caddy · Playwright

## Licence

[Sustainable Use License](LICENSE.md) — free to use and modify for your own internal business
purposes, including running it for yourself. You may not sell it, host it as a service for others,
or remove the licensing.

---

> **Risk warning.** Trading foreign exchange, indices and commodities on margin carries a high level
> of risk and can result in losses exceeding your deposit. EntryStation is an analysis and control
> tool, not a broker; it does not hold client funds and does not provide financial advice. Nothing
> here is a recommendation to trade, and no figure shown anywhere in this software is a prediction of
> any future result.
