"""
Analysis-agent execution engine.

An analysis agent is a flow graph (React Flow {nodes, edges}) the user draws on
the canvas. Each node carries a free-text instruction; this engine walks the
graph from the Trigger node and executes each node — turning its text into calls
against the analysis APIs (Truth Social, News, FedWatch, Economic Calendar,
Sentiment, Market Data) — accumulating a shared context, and finally returning
the Respond node's answer to whoever called the agent (the main chat agent).

Design:
  - Data nodes call the analysis MODULES directly (account-free), so a flow never
    needs a trading account. An LLM turns the node's text (+ upstream context)
    into that source's parameters; empty text ⇒ sensible per-source defaults.
  - If nodes branch on an LLM boolean; Respond composes the final text; Versatile
    runs a small bounded planner that may consult any source.
  - ctx = {provider, api_key, model, user_id}. A node's "Preferred AI model"
    overrides the model when it names the same provider we have a key for.

Everything is defensive: any node error becomes {"error": ...} in the context
rather than aborting the whole run.
"""
import json
import re

import registry

MAX_STEPS = 40          # hard cap on node executions per run (cycle/blow-up guard)
MAX_VISITS = 3          # how many times a single node may run (loops)
# There is NO output cap on an OpenAI-compatible call any more.
#
# A reasoning model spends its output budget thinking before it writes a word,
# and `max_tokens` caps thinking and answer together — so any number we choose is
# a number at which the model stops mid-thought and returns an EMPTY string with
# finish_reason=length. 1200 did that to every reasoning model silently; 8000 did
# it to deepseek-v4-flash on a hard question. There is no right value to guess,
# because it depends on the question, so the provider's own limit is used
# instead. It only ever charged for what was generated anyway.
#
# Anthropic is the exception: its API REQUIRES max_tokens, so that one keeps a
# ceiling — deliberately generous, for the same reason.
ANTHROPIC_MAX_TOKENS = 16000

OCTO_ROUNDS = 4         # how many times an Octo body may think before answering
OCTO_CALLS = 12         # tentacle calls in one run, across all rounds — retries included
MAX_CALL_DEPTH = 2      # how deep a call-agent node may nest (recursion/loop guard)
_CTX_CHARS = 6000       # how much context we feed an LLM step (synthesis needs room)

# USD per 1,000,000 tokens. `in` = input cache-MISS, `in_cache` = input cache-HIT,
# `out` = output. Matched by substring against the model name; unknown models get
# token counts but no cost. Update here when provider pricing changes.
LLM_PRICING = {
    "deepseek-v4-flash": {"in": 0.14,  "in_cache": 0.0028,   "out": 0.28},
    "deepseek-v4-pro":   {"in": 0.435, "in_cache": 0.003625, "out": 0.87},
    "deepseek-chat":     {"in": 0.14,  "in_cache": 0.0028,   "out": 0.28},   # flash-tier default
    "deepseek-reasoner": {"in": 0.435, "in_cache": 0.003625, "out": 0.87},
}


def _price_for(model):
    m = (model or "").lower()
    return next((p for k, p in LLM_PRICING.items() if k in m), None)


def _usage_since(ctx, mark: dict) -> dict:
    """What this step alone burned, and what that cost.

    Usage accumulates on the ctx for the whole run, so a single node's share is
    the difference across it. Worth reporting per step rather than per run: a
    total says a run was expensive, a breakdown says WHICH node was — and on a
    nine-node flow those are very different pieces of information.
    """
    now = ctx.get("_usage") or {}
    d = {k: int(now.get(k, 0)) - int(mark.get(k, 0)) for k in ("in", "out", "cache_hit", "calls")}
    if not any(d.values()):
        return {}
    return {"usage": d, "cost": _usage_cost(d, ctx.get("_usage_model") or ctx.get("model"))}


def _usage_cost(usage, model):
    """Estimated USD cost for a run's accumulated token usage (canonical pricing +
    gpt-4.1-mini live in billing.LLM_PRICING). None for unpriced models."""
    import billing
    if not billing.price_for(model) or not usage:
        return None
    return round(billing.cost_of(usage, model), 6)


import threading
import time
import hashlib
_USAGE_LOCK = threading.Lock()

# ── 5-second result cache ────────────────────────────────────────────────────────
# Within a few seconds the underlying market data is literally unchanged (the
# fetchers poll every 10–120s) so an identical (agent, user, request, flow) run
# yields the SAME output. We serve that cached result and bill only a fraction of
# its cost. Keyed on the EXACT inputs, so it never serves a different-intent answer.
_CACHE = {}                  # key -> (expires_at, payload)
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 5.0             # seconds
CACHE_HIT_FRACTION = 0.2     # a hit is billed at 20% of the cached run's real cost


def _cache_key(agent_id, user_id, request, flow):
    norm = " ".join((request or "").lower().split())
    fh = hashlib.sha256(json.dumps(flow, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return hashlib.sha256(f"{agent_id}|{user_id}|{norm}|{fh}".encode()).hexdigest()


def _cache_get(key):
    with _CACHE_LOCK:
        v = _CACHE.get(key)
        if not v:
            return None
        if v[0] < time.time():
            _CACHE.pop(key, None)
            return None
        return v[1]


def _cache_put(key, payload):
    with _CACHE_LOCK:
        _CACHE[key] = (time.time() + _CACHE_TTL, payload)
        if len(_CACHE) > 1000:      # opportunistic prune of expired entries
            now = time.time()
            for k in [k for k, (exp, _) in list(_CACHE.items()) if exp < now]:
                _CACHE.pop(k, None)


def _accum_usage(ctx, model, in_tok, out_tok, cache_hit=0):
    """Add one LLM call's token usage to the shared run/turn total on ctx. Thread-safe
    because tools (incl. concurrent analysis-agent runs) accumulate into one ctx."""
    with _USAGE_LOCK:
        u = ctx.setdefault("_usage", {"in": 0, "out": 0, "cache_hit": 0, "calls": 0})
        u["in"] += int(in_tok or 0)
        u["out"] += int(out_tok or 0)
        u["cache_hit"] += int(cache_hit or 0)
        u["calls"] += 1
        if model:
            ctx["_usage_model"] = model


# ── LLM helpers ──────────────────────────────────────────────────────────────────
def _model_for(ctx, node_values):
    """(provider, model, key) for a node.

    A node's Preferred AI model wins whenever it can actually be RUN, which means
    finding the key for that provider rather than assuming the flow's. Before,
    a node set to a provider other than the one the flow was running on fell
    silently back to the flow's model: the setting looked applied and never was.
    The alias is resolved the same way chat resolves one, so a branded tier works
    on cloud and a provider:model works on a community box."""
    pref = (node_values or {}).get("model") or ""
    if not pref:
        return ctx.get("provider"), ctx.get("model"), ctx.get("api_key")
    import ai_keys
    try:
        if ":" in pref:
            # An explicit provider:model is a literal instruction and must NOT go
            # through the branded-alias mapper, which knows only its own tiers
            # and answers with the default for anything else — asking for
            # deepseek-reasoner and quietly getting deepseek-chat.
            prov, mdl = pref.split(":", 1)
            key = ai_keys.key_for(ctx.get("user_id"), prov)
            if key:
                return prov, mdl, key
            if prov == ctx.get("provider") and ctx.get("api_key"):
                return prov, mdl, ctx["api_key"]
        else:
            prov, mdl, key = ai_keys.resolve(ctx.get("user_id"), pref)
            if prov and mdl and key:
                return prov, mdl, key
    except Exception:
        pass
    return ctx.get("provider"), ctx.get("model"), ctx.get("api_key")


def _model_label(ctx, provider, model) -> str:
    """What to CALL the model in anything the user reads.

    arrissa-chat and arrissa-pro are the product; which provider is behind them
    is ours, not theirs. An error saying "deepseek/deepseek-v4-flash hit its
    output limit" hands that away — and to a user who never chose DeepSeek it
    names something they have no idea about and cannot act on.

    A user running on their OWN key gets the real name, because there it IS the
    thing they picked and the thing they are paying for."""
    return ctx.get("_model_label") or _brand(ctx, provider, model) or f"{provider}/{model}"


def _brand(ctx, provider, model) -> str | None:
    """The branded name for a house model, worked out from the model itself.

    The label used to arrive only if the caller passed one, and three of the six
    things that start a run did not — which is how the run history came to print
    the house model next to a run the user had chosen "arrissa-pro" for. Asking
    the tier table closes that off for every caller at once, including the ones
    that do not exist yet.

    Nothing is masked for a user on their own key: they chose that model and are
    being billed for it by name.

    Which is why the test is the KEY the run actually used, not whether the user
    happens to own one. Someone can have a DeepSeek key connected AND select
    arrissa-chat — that run goes out on the house key and must still be masked.
    Asking "does this user have a DeepSeek key" answers a different question and
    gets that case backwards."""
    if not provider or not model:
        return None
    try:
        import ai_keys, billing
        house = ai_keys.admin_key(provider)
        used = ctx.get("api_key")
        if used and house:
            if used != house:
                return None                   # their own key: their own model's name
        elif ai_keys.on_own_key(ctx.get("user_id"), provider):
            return None                       # no key on the ctx — fall back to the weaker test
        return billing.public_model(provider, model)
    except Exception:
        return None


def _llm(ctx, node_values, system, user, want_json=False):
    """One non-streaming completion. Returns text (or a dict if want_json).
    Returns None when there's no usable key/provider or on any error, so callers
    fall back to defaults."""
    provider, model, key = _model_for(ctx, node_values)
    if not (provider and model and key):
        return None
    if want_json:
        system += ' Respond with ONLY a single JSON object, no prose.'
    try:
        import ai_keys
        if provider == "anthropic":
            import anthropic
            c = anthropic.Anthropic(api_key=key, timeout=ai_keys.LLM_TIMEOUT)
            msg = c.messages.create(model=model, max_tokens=ANTHROPIC_MAX_TOKENS, system=system,
                                    messages=[{"role": "user", "content": user}])
            text = "".join(b.text for b in msg.content if b.type == "text")
            u = getattr(msg, "usage", None)
            if u:
                _accum_usage(ctx, model, getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
                             getattr(u, "cache_read_input_tokens", 0) or 0)
        else:
            from openai import OpenAI
            c = OpenAI(api_key=key, base_url=ai_keys.base_url(provider),
                       timeout=ai_keys.LLM_TIMEOUT, max_retries=1)
            kw = {"response_format": {"type": "json_object"}} if want_json else {}
            r = c.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], **kw)
            ch = r.choices[0]
            text = ch.message.content
            if not text and ch.finish_reason == "length":
                # With no cap of ours, this means the PROVIDER's own limit was
                # reached — the question is too big for this model, not for our
                # settings.
                raise RuntimeError(
                    f"{_model_label(ctx, provider, model)} hit its output limit while thinking "
                    "and never answered — try a model with a larger output budget")
            u = getattr(r, "usage", None)
            if u:
                # DeepSeek reports cache hits in prompt_cache_hit_tokens (via the SDK's extras).
                hit = getattr(u, "prompt_cache_hit_tokens", None)
                if hit is None:
                    hit = (getattr(u, "model_extra", None) or {}).get("prompt_cache_hit_tokens", 0)
                _accum_usage(ctx, model, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), hit or 0)
        if not want_json:
            return (text or "").strip()
        return _loads(text)
    except Exception as e:
        # Record the failure so the flow can surface it instead of silently degrading
        # (every node then falls back to defaults — e.g. a data node loses its symbol).
        msg = str(e)
        if "insufficient_quota" in msg or "exceeded your current quota" in msg:
            msg = f"{_model_label(ctx, provider, model)} quota exhausted — add credits/billing, or switch provider on the Connections page"
        elif ("invalid_api_key" in msg or "Incorrect API key" in msg or " 401" in msg
                or "Invalid API Key" in msg or "valid API key" in msg
                or "API key not valid" in msg):
            # Gemini answers 400 "Please pass a valid API key" where the others
            # answer 401, so matching on the status code alone left one provider
            # showing a raw stack-trace string to the user.
            msg = f"{_model_label(ctx, provider, model)} could not be reached — its key was rejected"
        elif "rate_limit" in msg or " 429" in msg:
            msg = f"{provider} rate limit hit — try again shortly"
        try:
            ctx["_llm_error"] = msg[:300]
        except Exception:
            pass
        return None


def _loads(text):
    """Parse a JSON object out of a model reply, tolerating ```json fences / prose."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.strip("`")
        s = s[4:].strip() if s.lower().startswith("json") else s.strip()
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a != -1 and b != -1 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except Exception:
                return None
    return None


def _ctx_json(context, extra=None):
    """Compact JSON of the running context for an LLM prompt."""
    blob = {"request": context.get("request"),
            "steps": [{"node": s.get("node"), "kind": s.get("kind"), "result": _short(s.get("result"))}
                      for s in context.get("steps", [])[-6:]],
            "last": _short(context.get("last"))}
    if extra:
        blob["gathered"] = {k: _short(v) for k, v in extra.items()}
    s = json.dumps(blob, default=str)
    return s[:_CTX_CHARS]


def _chain_text(context):
    """The running chain-of-thought reads, formatted for a prompt (each node's
    contribution, in order)."""
    lines = []
    for c in context.get("chain") or []:
        label = c.get("name") or c.get("kind")
        read = (c.get("read") or "").strip()
        if read:
            lines.append(f"- [{label}] {read}")
    return "\n".join(lines)


def _emit(ctx, event: dict) -> None:
    """Tell whoever is watching what this run is doing, if anyone is.

    A flow takes one to three minutes and used to show a spinner for all of it,
    so a run that was working and a run that was wedged looked identical — and
    when it ended on a proxy timeout the user had watched nothing for two
    minutes and then got a parse error.

    Optional by design: no `_progress` on the ctx and this costs one dict lookup.
    A watcher that raises must never take the run down with it — the run is the
    point, the commentary is not."""
    fn = ctx.get("_progress")
    if not fn:
        return
    try:
        fn(event)
    except Exception:
        pass


def _short(v):
    """Trim a result so context prompts stay small."""
    try:
        s = json.dumps(v, default=str)
    except Exception:
        s = str(v)
    return s if len(s) <= 900 else s[:900] + "…"


def _trace_result(v):
    """A VALID, size-bounded COPY of a node result for the user-facing trace, so
    the frontend can still render it richly (charts/tables) — caps long arrays and
    trims very long strings instead of truncating the JSON mid-way (which the old
    _short did, producing unparseable output)."""
    if isinstance(v, dict):
        return {k: _trace_result(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_trace_result(x) for x in v[:200]]
    if isinstance(v, str) and len(v) > 600:
        return v[:600] + "…"
    return v


# ── variables ────────────────────────────────────────────────────────────────
# A trigger can declare the inputs its agent expects — `symbol`, `trade_type` —
# and those become variables every node downstream can use. Two things then stop
# being guesswork: a node can say `symbol={{symbol}}` instead of hoping a model
# reads the right instrument out of the request, and a node can choose WHICH call
# to make based on what came in.
_VAR_RE = _re_vars = __import__("re").compile(r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}|\$([a-zA-Z_][\w]*)")


def _declared_vars(nodes):
    """What the trigger says this agent needs: [{key, required}, …]."""
    for n in nodes or []:
        d = n.get("data") or {}
        if (d.get("kind") or "") in ("trigger-agent-call", "trigger", "trigger-interval",
                                     "triggerInterval"):
            out = []
            for v in ((d.get("values") or {}).get("vars") or []):
                key = (v.get("key") or "").strip()
                if key:
                    out.append({"key": key, "required": bool(v.get("required"))})
            if out:
                return out
    return []


def _collect_vars(declared, request, given, ctx, nv):
    """Fill the declared variables: what the caller passed, else what the request
    says.

    The caller wins outright. A chat agent that was told `symbol=US30` knows it
    better than any reading of the sentence around it, and re-deriving something
    already stated is how a request for US30 quietly becomes one for gold.

    Only when something is missing is a model asked, and only about the missing
    ones — asking about all of them would pay to re-derive what arrived."""
    if not declared:
        return dict(given or {})
    got = {k: v for k, v in (given or {}).items() if v not in (None, "")}
    missing = [d["key"] for d in declared if d["key"] not in got]
    if missing and (request or "").strip():
        try:
            out = _llm(ctx, nv,
                       "You read one instruction and pull out named values. Output ONLY a JSON "
                       "object with these keys, and omit any the instruction does not give: "
                       + ", ".join(missing) + ". Never invent a value.",
                       f"Instruction: {request}", want_json=True)
            if isinstance(out, dict):
                for k in missing:
                    v = out.get(k)
                    if v not in (None, ""):
                        got[k] = str(v)
        except Exception:
            pass
    return got


def _fill_vars(text, variables):
    """Replace {{name}} and $name with what the trigger received.

    An unknown name is left exactly as written rather than blanked. A parameter
    that silently became `symbol=` would send a request nobody meant to send;
    left as `symbol={{sybmol}}` it is a typo somebody can see."""
    if not text or not variables:
        return text

    def sub(m):
        name = m.group(1) or m.group(2)
        v = variables.get(name)
        return str(v) if v not in (None, "") else m.group(0)
    return _VAR_RE.sub(sub, text)


def _rule_matches(when, variables):
    """`trade_type=scalper`, `symbol=US30&side=buy`, `impact!=low`.

    Empty matches everything, which is what makes the last rule a default. All
    conditions must hold — `&` is AND, because a rule that fired on any one of
    several conditions could not express "US30 on a scalp"."""
    when = (when or "").strip()
    if not when:
        return True
    for part in when.replace("\n", "&").split("&"):
        part = part.strip()
        if not part:
            continue
        neg = "!=" in part
        key, _, want = part.partition("!=" if neg else "=")
        got = str(variables.get(key.strip(), "")).strip().lower()
        want = want.strip().lower()
        if neg and got == want:
            return False
        if not neg and got != want:
            return False
    return True


def _explicit_params(nv, variables=None):
    """The parameters the USER typed on the node, if they typed any.

    `symbol=XAUUSD&count=15&timeframe=M15`, or the same one per line. Query-string
    shape because that is what the API guides already show, so somebody copying a
    line out of a guide into a node has it work.

    Values arrive as strings and stay strings: every handler already coerces what
    it needs (`int(p.get("count"))`), and guessing types here would only differ
    from what the LLM path produces."""
    variables = variables or {}

    # A node may carry SEVERAL calls, each with the condition it applies under —
    # "if trade_type=scalper fetch M1, if symbol=US30 fetch the index feed". They
    # are tried in order and the first match wins, so the specific rules go above
    # the general one and a rule with no condition at the end is the default.
    raw = ""
    for rule in (nv.get("api_rules") or []):
        if _rule_matches(rule.get("when"), variables):
            raw = (rule.get("params") or "").strip()
            break
    if not raw:
        raw = (nv.get("api_params") or "").strip()
    raw = _fill_vars(raw, variables)
    if not raw:
        return None
    from urllib.parse import parse_qsl
    pairs = parse_qsl(raw.replace("\n", "&").replace(";", "&"), keep_blank_values=False)
    out = {k.strip(): v.strip() for k, v in pairs if k.strip() and v.strip()}
    return out or None


def _params(ctx, node_values, source_name, fields, text, context, default):
    """This source's parameters — stated by the user, or worked out by the model.

    Stated wins, and skips the model entirely. Asking an LLM which symbol to fetch
    when the node already says `symbol=XAUUSD` is paying for a guess at something
    that was not in question — and on a schedule that runs every fifteen minutes
    it is the same guess, bought again, for ever.

    The defaults still apply underneath, so a node can pin the one field it cares
    about and leave the rest alone."""
    stated = _explicit_params(node_values, context.get("vars"))
    if stated:
        merged = dict(default)
        merged.update(stated)
        return merged

    system = (
        f"You configure a call to the {source_name} API inside a trading-analysis "
        f"flow. {fields} Read the USER'S REQUEST, the node's instruction and the flow "
        "context, and output ONLY the fields that apply as a JSON object. CRITICAL: "
        "when the node instruction refers to 'the requested instrument/symbol/pair/"
        "market' (or is generic), take the actual instrument FROM THE USER'S REQUEST "
        "(e.g. request 'Analyze XAUUSD now' ⇒ symbol 'XAUUSD'; 'how's gold?' ⇒ 'gold'). "
        "If the instruction is empty or vague, choose sensible defaults for this "
        "source. Never invent fields that aren't listed.")
    user = (f"The user's request (what to analyse): {context.get('request') or '(none)'}\n\n"
            f"Node instruction: {text or '(empty — use defaults)'}\n\n"
            f"Flow context: {_ctx_json(context)}")
    out = _llm(ctx, node_values, system, user, want_json=True)
    if not isinstance(out, dict):
        return dict(default)
    merged = dict(default)
    merged.update({k: v for k, v in out.items() if v not in (None, "")})
    return merged


def _boolean(ctx, node_values, text, context):
    system = ("You are the condition of an If node in an analysis flow. Decide if "
              "the condition holds given the user's request and the gathered context. "
              "Output JSON {\"answer\": true|false}.")
    user = (f"The user's request: {context.get('request') or '(none)'}\n\n"
            f"Condition: {text}\n\nFlow context: {_ctx_json(context)}")
    out = _llm(ctx, node_values, system, user, want_json=True)
    if isinstance(out, dict) and "answer" in out:
        return bool(out["answer"])
    return True   # default: take the 'true' branch when undecidable


def _compose(ctx, node_values, text, context, extra=None):
    system = ("You write the final response of an analysis agent back to the agent "
              "that called it. Be concise and factual, use the gathered data, and do "
              "exactly what the node instruction asks. Plain text (markdown ok). "
              "If a data node reports it is UNAVAILABLE for this instrument (marked "
              "'unavailable' — the source is fresh but this instrument simply has none, e.g. some "
              "instruments have no retail sentiment), OMIT that dimension entirely, do NOT mention "
              "it, and do NOT penalise the instrument or lower confidence for it — treat it as if "
              "that node were not in the flow. Only flag a source as a problem if it actually "
              "ERRORED or is STALE. "
              "ALWAYS end with an actionable trade setup AND a confidence rating out of 5 "
              "(state it as 'Confidence: N/5', N an integer 1-5, where 5 = high-conviction, "
              "everything aligns and it's ripe to trade; 1 = weak/speculative). Base the "
              "confidence on how strongly the gathered evidence agrees; if the node instruction "
              "says how to judge confidence, follow it.")
    if ctx.get("cot"):
        system += (" A CHAIN OF THOUGHT was built step by step (below) — SYNTHESISE it into one "
                   "coherent decision, showing how each step led to the next and to the conclusion, "
                   "not a list of isolated findings.")
    # With a chain, the chain IS the context: every node's read is already in it,
    # and re-sending the raw results they were read FROM doubles the prompt to say
    # the same thing twice. Without one there are no reads, so the data itself is
    # all there is to write from.
    chain = _chain_text(context)
    use_chain = bool(ctx.get("cot") and chain)
    user = (f"The user's request (what to analyse): {context.get('request') or '(none)'}\n\n"
            + (f"Reasoning chain (step by step):\n{chain}\n\n" if use_chain else "")
            + f"Node instruction: {text or 'Summarise the analysis.'}\n\n"
            + (f"Anything gathered outside the chain: {_ctx_json({}, extra)}\n" if use_chain and extra
               else f"Flow context: {_ctx_json(context, extra)}"))
    out = _llm(ctx, node_values, system, user)
    if out:
        return out
    # LLM unavailable: say WHY (quota/key/rate) so the user can fix it, rather than
    # returning a confusing raw digest that looks like the agent "worked".
    if ctx.get("_llm_error"):
        return (f"⚠️ This agent's AI model could not be reached: {ctx['_llm_error']}. "
                "The data below was still gathered, but no analysis/decision could be written. "
                "Fix the provider key on the Connections page and run again.")
    last = context.get("last")
    return f"Analysis complete. Latest result: {_short(last)}" if last else "No data gathered."


# ── data-node handlers (account-free analysis modules) ───────────────────────────
def _ensure_window(p, key="hours", val=24):
    if not any(p.get(k) for k in ("minutes", "hours", "days", "range")):
        p[key] = val
    return p





def _n_market(text, context, ctx, nv):
    import market
    p = _params(ctx, nv, "market price / candles",
                "Fields: symbol (REQUIRED, e.g. gold, XAUUSD, nasdaq — or a COMMA LIST "
                "'XAUUSD,GBPUSD,gold' for several pairs at once); kind (quote|candles, "
                "default candles); timeframe (M1|M5|M15|M30|H1|H4|D1, default M15 — or a "
                "COMMA LIST 'H4,H1,M15' for a multi-timeframe read); count (int, default 100).",
                text, context, {"kind": "candles", "timeframe": "M15", "count": 100})
    sym = p.get("symbol")
    if not sym:
        return {"error": "market node needs a symbol — name it in the node text"}
    tf = p.get("timeframe", "M15")
    try:
        if (p.get("kind") or "candles").lower() == "quote":
            return market.quote(sym)
        # comma list in either field ⇒ many series in one read
        if len(market._split_csv(sym)) > 1 or len(market._split_csv(tf)) > 1:
            return market.candles_multi(sym, tf, count=int(p.get("count") or 100))
        return market.candles(sym, timeframe=tf, count=int(p.get("count") or 100))
    except Exception as e:
        return {"error": str(e)}


# Reading a symbol and a timeframe out of node text WITHOUT asking a model. The
# node's fields are normally parsed by _params, which is an LLM call — so when the
# model is rate-limited, out of quota or simply down, a node whose text plainly
# says "XAUUSD positioning on M15" failed with "needs a symbol". Cheap, exact
# matches are checked first; the model is still the fallback for anything phrased
# less literally.
_ART_TF_RE = re.compile(r"\b(M1|M5|M15|M30|H1|H4|D1)\b", re.I)
# Named instruments, matched exactly. NOT a generic six-letter pattern: uppercased
# text makes "who controls NASDAQ" and "the DOLLAR index" look like tickers, and
# NASDAQ is not even the symbol we trade (USTEC is).
_ART_TICKERS = ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD", "XCUUSD",
                "US30", "US500", "USTEC", "NAS100", "SPX500", "DE30", "DE40",
                "UK100", "JP225", "HK50", "STOXX50", "AUS200", "DXY",
                "USOIL", "UKOIL", "XNGUSD",
                "BTCUSD", "ETHUSD", "XRPUSD", "SOLUSD", "ADAUSD", "DOGEUSD")
_ART_TICKER_RE = re.compile(r"\b(" + "|".join(_ART_TICKERS) + r")\b", re.I)
# An FX pair is six letters that are TWO KNOWN CURRENCY CODES — which is what
# separates EURUSD from any other six-letter word.
_ART_CCY = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "ZAR", "SGD",
            "HKD", "CNH", "SEK", "NOK", "DKK", "PLN", "MXN", "TRY", "INR", "BRL"}
_ART_PAIR_RE = re.compile(r"\b([A-Za-z]{3})[/ ]?([A-Za-z]{3})\b")
_ART_NAMES = {
    "dollar index": "DXY", "nasdaq": "USTEC", "s&p 500": "US500", "s&p": "US500",
    "sp500": "US500", "dow jones": "US30", "dow": "US30", "dax": "DE30",
    "ftse": "UK100", "nikkei": "JP225", "hang seng": "HK50",
    "gold": "XAUUSD", "silver": "XAGUSD", "platinum": "XPTUSD", "copper": "XCUUSD",
    "bitcoin": "BTCUSD", "ethereum": "ETHUSD", "solana": "SOLUSD", "ripple": "XRPUSD",
    "brent": "UKOIL", "crude": "USOIL", "oil": "USOIL", "natural gas": "XNGUSD",
}


def _art_from_text(text):
    """(symbol, timeframe) read literally from the node's own words, or (None, None).

    Exact tickers first, then real FX pairs (two known currency codes), then the
    friendly names — so "nasdaq" resolves to USTEC rather than to itself, and an
    ordinary six-letter word never becomes a symbol."""
    t = text or ""
    tf = _ART_TF_RE.search(t)
    tf = tf.group(1).upper() if tf else None

    m = _ART_TICKER_RE.search(t)
    if m:
        return m.group(1).upper(), tf
    low = t.lower()
    for name, sym in _ART_NAMES.items():          # before the pair scan: "dollar index"
        if name in low:
            return sym, tf
    for a, b in _ART_PAIR_RE.findall(t):
        if a.upper() in _ART_CCY and b.upper() in _ART_CCY:
            return (a + b).upper(), tf
    return None, tf


def _n_artificial_sentiment(text, context, ctx, nv):
    """Positioning read from the candles themselves — works on any instrument and
    any timeframe, unlike the Myfxbook node, which only knows what its own users
    hold and only for the symbols it covers."""
    import artificial_sentiment as art
    p = _params(ctx, nv, "artificial sentiment (positioning from price structure)",
                "Fields: symbol (REQUIRED, e.g. gold, XAUUSD, nasdaq); timeframe "
                "(M1|M5|M15|M30|H1|H4|D1, default M15); count (candles, 40-1000, "
                "default 200); compare (true to also fetch Myfxbook's real retail read "
                "and the gap between them — use it when the flow cares about crowd "
                "positioning, since a crowd leaning against the footprint is the "
                "classic squeeze setup).",
                text, context, {"timeframe": "M15", "count": 200, "compare": True})
    lit_sym, lit_tf = _art_from_text(text)
    sym = p.get("symbol") or lit_sym
    if not sym:
        return {"error": "artificial-sentiment node needs a symbol — name it in the node text "
                         "(e.g. 'XAUUSD positioning on M15')"}
    try:
        fn = art.compare if p.get("compare", True) else art.read
        return fn(sym, timeframe=p.get("timeframe") or lit_tf or "M15",
                  count=int(p.get("count") or art.DEFAULT_COUNT))
    except Exception as e:
        return {"error": str(e)}


def _n_risk(text, context, ctx, nv):
    """Smart SL/TP + position-size node — the risk-management engine as a flow step.
    Reads live structure + ATR to place the stop, sets the target by reward:risk and
    sizes the lot to the account risk budget. Direction can be inferred from the
    flow's bias so far (so a preceding analysis feeds the size)."""
    import trading_api, market
    p = _params(ctx, nv, "SL/TP + position-sizing engine",
                "Fields: symbol (REQUIRED, e.g. gold, XAUUSD, nasdaq); side (buy|sell — if not "
                "explicit, infer it from the directional bias reached so far in the flow); style "
                "(scalp|intraday|swing|position, default intraday); risk_pct (percent of account) OR "
                "risk_money (absolute amount); rr (reward:risk override); basis (equity|balance); "
                "sl_mode (structure|atr|swing, default structure); entry (price, optional — omit for live).",
                text, context, {"style": "intraday", "sl_mode": "structure"})
    sym = p.get("symbol")
    side = str(p.get("side") or "").lower()
    if not sym:
        return {"error": "risk node needs a symbol — name it in the node text"}
    if not side.startswith(("buy", "sell")):
        return {"error": "risk node needs a side (buy|sell) — state it or infer it from the flow's bias"}

    def _num(key):
        v = p.get(key)
        return float(v) if v not in (None, "") else None

    try:
        rp, rm, rr, entry = _num("risk_pct"), _num("risk_money"), _num("rr"), _num("entry")
        import trading_api as _ta
        rset = _ta._risk_settings(ctx.get("user_id"), _ta._active_ctx.get())
        rp, rm = _ta._effective_risk(rset, rp, rm)         # the user's own risk, else 2%
        if rr is None:
            rr = rset["reward_rr"]
        resolved = market.resolve_symbol(sym)
        return trading_api.trader().auto_sltp(
            resolved, "buy" if side.startswith("buy") else "sell",
            style=p.get("style") or rset["trade_style"] or "intraday", entry=entry,
            risk_pct=rp, risk_money=rm, rr=rr, basis=p.get("basis") or rset["risk_basis"],
            sl_mode=p.get("sl_mode") or "structure")
    except Exception as e:
        return {"error": str(e)}



def _n_time_session(text, context, ctx, nv):
    """Current UTC time + which forex sessions are open (no API, computed)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    h = now.hour + now.minute / 60.0
    sessions = {"Sydney": (21, 6), "Tokyo": (0, 9), "London": (7, 16), "New York": (12, 21)}

    def _open(s, e):
        return (s <= h < e) if s < e else (h >= s or h < e)   # handles midnight wrap

    weekend = now.weekday() >= 5
    open_now = [] if weekend else [n for n, (s, e) in sessions.items() if _open(s, e)]
    overlaps = []
    if "London" in open_now and "New York" in open_now:
        overlaps.append("London/New York — highest liquidity & volatility")
    if "Sydney" in open_now and "Tokyo" in open_now:
        overlaps.append("Sydney/Tokyo")
    return {
        "utc_time": now.strftime("%H:%M"),
        "utc_iso": now.isoformat(),
        "weekday": now.strftime("%A"),
        "date": now.strftime("%Y-%m-%d"),
        "date_readable": now.strftime("%A, %d %B %Y"),
        "market_open": not weekend,
        "open_sessions": open_now,
        "closed_sessions": [n for n in sessions if n not in open_now],
        "overlaps": overlaps,
        "session_hours_utc": {n: f"{s:02d}:00–{e:02d}:00" for n, (s, e) in sessions.items()},
        "note": "Forex market is closed for the weekend." if weekend else None,
    }


# palette key → data handler (source name used by the Versatile planner too)
def _n_api_request(text, context, ctx, nv):
    """Call one of this app's own endpoints, with the parameters the user typed.

    For the sources that have a node of their own the URL is already decided and
    only the parameters matter — that is `api_params` on those nodes, and it costs
    no model call. This node is the other case: any endpoint at all, named by the
    user, for the things no dedicated node covers.

    No API key is asked for and none could be used: keys are stored hashed, so
    even this process cannot recover the user's. A short-lived token is minted for
    the user the flow is running as instead, which is the same authority they
    would have had anyway and expires on its own.

    Nothing here consults a model. If the node also wants an opinion it gets one
    afterwards, through the same per-node mechanism every other node uses — and
    if it does not, this node costs nothing but the request."""
    url = (nv.get("api_url") or text or "").strip()
    if not url:
        return {"error": "this node needs an endpoint — put it in the node's URL field, "
                         "e.g. /api/market/chart"}
    if "://" in url:
        # Only this app's own API. A node that could call anywhere would be a way
        # to make the server fetch arbitrary URLs on somebody's behalf.
        return {"error": "give a path on this app, not a full URL — e.g. /api/market/chart"}
    if not url.startswith("/"):
        url = "/" + url
    if not url.startswith("/api/"):
        url = "/api" + url

    params = _explicit_params(nv) or {}
    try:
        import auth
        import db
        import requests as _rq
        from urllib.parse import urlencode

        uid = ctx.get("user_id")
        with db.connect() as conn:
            row = conn.execute("SELECT email FROM users WHERE id = %s", (uid,)).fetchone()
        if not row:
            return {"error": "this flow has no user to run the request as"}
        token = auth.make_token(uid, row["email"])
        full = f"http://127.0.0.1:8000{url}" + (f"?{urlencode(params)}" if params else "")
        r = _rq.get(full, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if r.status_code >= 400:
            return {"error": f"{url} answered {r.status_code}", "body": r.text[:400]}
        return r.json()
    except Exception as e:
        return {"error": f"{url}: {type(e).__name__}: {e}"}


_DATA = {
    "market-data": _n_market, "time-session": _n_time_session,
    "risk-management": _n_risk,
    "artificial-sentiment": _n_artificial_sentiment,
    "api-request": _n_api_request, "apiRequest": _n_api_request,
}
_PLANNER_SOURCE = {
    "market": _n_market, "time": _n_time_session, "risk": _n_risk,
    "artificial_sentiment": _n_artificial_sentiment,
}


def _n_versatile(name, description, text, context, ctx, nv):
    """A small bounded planner: repeatedly pick a source to consult (or finish),
    then compose an answer from everything gathered."""
    goal = f"{name or 'Task'}: {description or ''}\nInstruction: {text or ''}".strip()
    gathered = {}
    for _ in range(3):
        choice = _params(
            ctx, nv, "an analysis planner",
            "Fields: source (one of truth|news|fed|calendar|sentiment|bonds|market|done — "
            "'done' when you have enough); query (the text instruction to hand that source).",
            goal + "\n\nData gathered so far is in the context.",
            {**context, "gathered": gathered} if False else context,   # keep context shape
            {"source": "done"})
        src = (choice.get("source") or "done").lower()
        handler = _PLANNER_SOURCE.get(src)
        if src == "done" or not handler:
            break
        try:
            res = handler(choice.get("query") or text, {**context, "gathered": gathered}, ctx, nv)
        except Exception as e:
            res = {"error": str(e)}
        gathered[f"{src}_{len(gathered) + 1}"] = res
    resp = _compose(ctx, nv, text or description or name, context, extra=gathered)
    return {"response": resp, "data": gathered}


def _n_call_agent(text, context, ctx, nv):
    """Call ANOTHER of the user's analysis agents and fold its response into this
    flow. The current agent invokes the selected agent, WAITS for it, and its
    response becomes this node's result — passed on to the next node (and, with
    chain of thought on, appended to the reasoning chain). When chain of thought is
    on, the reasoning gathered so far is also passed INTO the called agent so it
    continues from it rather than starting cold."""
    import db
    agent_id = str(nv.get("agent_id") or nv.get("agent") or "").strip()
    if not agent_id:
        return {"error": "call-agent node has no agent selected — pick one in the node settings"}

    depth = int(ctx.get("_call_depth", 0))
    if depth >= MAX_CALL_DEPTH:
        return {"error": f"agent-call nesting limit ({MAX_CALL_DEPTH}) reached — stopping to avoid a loop"}
    if agent_id == str(ctx.get("_agent_id") or ""):
        return {"error": "an agent can't call itself"}

    try:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id, name, flow FROM analysis_agents WHERE id::text = %s AND user_id = %s",
                (agent_id, ctx.get("user_id"))).fetchone()
    except Exception as e:
        return {"error": str(e)}
    if not row:
        return {"error": "the selected agent was not found (it may have been deleted)"}

    # The request handed to the called agent: this node's instruction (or the flow's
    # own request), plus the reasoning chain when chain of thought is on.
    base = (text or "").strip() or context.get("request") or ""
    if ctx.get("cot"):
        chain = _chain_text(context)
        if chain:
            base = (base + "\n\nReasoning so far (chain of thought) — continue from it:\n" + chain).strip()

    sub = dict(ctx)
    sub["_usage"] = {"in": 0, "out": 0, "cache_hit": 0, "calls": 0}
    sub["_call_depth"] = depth + 1
    res = run_flow(row["flow"], base, sub, agent_id=str(row["id"]), source="agent-call")

    # Roll the called run's real cost into THIS run's meter (tokens stay separate,
    # mirroring how the chat agent bills a sub-agent call).
    try:
        u = ctx.get("_usage")
        if u is not None:
            with _USAGE_LOCK:
                u["extra_cost_usd"] = float(u.get("extra_cost_usd", 0)) + float(res.get("cost_usd") or 0)
    except Exception:
        pass

    return {"called_agent": row["name"], "called_agent_id": str(row["id"]),
            "request_sent": base, "response": res.get("response"),
            "sub_trace": res.get("trace"), "cached": bool(res.get("cached")),
            "error": res.get("error")}


# ── graph traversal ──────────────────────────────────────────────────────────────
def _kind(node):
    return (node.get("data") or {}).get("kind") or node.get("type")


def _values(node):
    return (node.get("data") or {}).get("values") or {}


def _opinion(ctx, nv, source, text, result, context):
    """When a data node has 'Require opinion' on, form a SHORT analytical read of the
    data it just fetched, attached as an output for downstream nodes. Uses the node's
    Opinion AI model if set, else the node's own model, else the agent default."""
    op_nv = {**nv, "model": nv.get("opinion_model") or nv.get("model")}
    cot = ctx.get("cot")
    system = (
        f"You are a trading analyst. You have just fetched data from the '{source}' source. "
        "Give a SHORT (2-4 sentences) analytical opinion of THIS data only: what it implies for "
        "the instrument/market, the likely directional bias, and any notable signal or risk. Do "
        "not restate the raw data or add preamble — just the read.")
    if cot:
        system += (" You are ONE link in a CHAIN OF THOUGHT: explicitly CONNECT this data to the "
                   "reasoning so far — confirm, extend or challenge it — so the analysis compounds "
                   "instead of standing alone.")
    # The chain carries READS, not raw data. This used to append the whole flow
    # context too — the last six nodes' fetched results, up to 900 characters
    # each — on top of a chain that already summarised exactly those nodes. So
    # every node re-sent data the node before it had already analysed, the input
    # grew with each step, and a nine-node run spent 32k input tokens to make
    # fifteen calls. A node opines on ITS OWN data; what came before it arrives
    # as the previous nodes' conclusions, which is the whole point of a chain.
    chain = _chain_text(context)
    user = (f"The user's request (what to analyse): {context.get('request') or '(none)'}\n\n"
            + (f"Reasoning chain so far:\n{chain}\n\n" if cot and chain else "")
            + f"Node instruction: {text or '(defaults)'}\n\nFetched data: {_short(result)}")
    return _llm(ctx, op_nv, system, user) or None


OCTO_SYSTEM = (
    "You are the BODY of an Octo agent inside a trading-analysis flow. Attached to you are "
    "TENTACLES — other nodes you may call, listed below with what each one does. Nothing has "
    "been called yet unless it appears under GATHERED.\n"
    "Work out which tentacles the brief actually needs, and call them. Call several at once "
    "when they do not depend on each other; call again in a later round when one answer decides "
    "the next question. Do not call a tentacle whose answer you already have, and do not call "
    "everything reflexively — an unused tentacle costs nothing, a needless call costs money and "
    "time.\n"
    "ASK NARROW. You pay twice for a vague instruction: once for the tool to gather everything it "
    "can, and again to read it all back. Name exactly what you need and no more — the timeframe "
    "and how many candles, the window in hours or days, the specific figure. 'Give me market data "
    "for BTCUSD' is a bad instruction; 'BTCUSD H1, last 50 candles — latest close, the high and "
    "low of the range, and ATR(14)' is a good one. Before you write a call, finish this sentence "
    "to yourself: I need this in order to decide ___. If you cannot finish it, do not make the "
    "call.\n"
    "ASK ONCE. One instruction per question. Do not split a single question across two calls to "
    "the same tentacle, and do not call a tentacle for something you can work out from what is "
    "already GATHERED — arithmetic on numbers you hold is free, another call is not.\n"
    "STOP WHEN THE BRIEF IS ANSWERABLE. Rounds are for questions a previous answer raised, not for "
    "confirming something you already believe. A second source that would not change your "
    "conclusion is not worth its price — return done.\n"
    "SOME TENTACLES ACT ON THE WORLD: they send a message, place or close a trade, post something. "
    "Reading data is cheap and undoable; acting is neither. Call an acting tentacle ONLY when the "
    "request asked for that action in so many words. 'Show me the gold chart' is a request to draw "
    "one, not to send it to anybody.\n"
    "YOU GIVE THE ORDERS. A tentacle knows nothing about the request — it sees only the sentence "
    "you hand it, so that sentence must be COMPLETE and CONCRETE. Name the actual instrument every "
    "time, taken from the user's request: write 'XAUUSD' or 'gold', never 'the requested "
    "instrument' or 'the pair'. Supply everything the tool needs to act without asking — a side "
    "(buy or sell) for anything that sizes or places a trade, a timeframe for price data, a window "
    "for news. If the flow has not decided a side yet, decide it from what you have gathered and "
    "SAY it; if you genuinely cannot, tell the tool to infer it from the bias and say what the bias "
    "is.\n"
    "IF A TENTACLE COMPLAINS, FIX IT AND CALL IT AGAIN. An answer that says a field is missing, or "
    "that it could not tell which instrument you meant, is not a result — it is a question to you. "
    "You have more rounds: supply what it asked for and repeat the call. Never pass a tool's "
    "complaint on as the analysis.\n"
    'Answer ONLY JSON: {"call":[{"id":"<tentacle id>","text":"what it should do",'
    '"need":["the exact figures you want back"]}],"why":"one short line"} to reach for more, '
    'or {"done":true,"answer":"..."} when you have enough to answer the brief. '
    "Never invent a tentacle id.\n"
    '`need` is SURGICAL and it is not optional. List the specific figures you will actually use — '
    '["latest close","ATR(14)","range high","range low"], ["next high-impact USD event","its time"], '
    '["net long %","net short %"] — and only those are kept from the answer; the rest is discarded '
    "before you ever see it. Naming five fields you will use beats receiving fifty you will not, "
    "and the discipline is the point: if you cannot name the figure, you do not need the call. "
    "Leave `need` out only when you genuinely want the whole answer, and expect to pay for it.")


def _surgical(result, need):
    """Keep only the fields the body said it needed.

    The body re-reads GATHERED on every round, so an answer it does not use is
    not paid for once — it is paid for again each round it stays. A market-data
    node returns three timeframes of candles; the body wanted the latest close
    and an ATR. Sending back the other 95% is the single most wasteful thing an
    Octo run does.

    Deterministic on purpose: matching key names costs nothing, whereas asking a
    model to summarise the answer would spend a call to save a call.

    Nothing matched means the ask and the payload disagree — the shape is not
    what the body assumed — and dropping everything would be worse than
    verbose. So a miss returns the whole (trimmed) answer and lets the body see
    what it actually got.
    """
    terms = [re.sub(r"[^a-z0-9]", "", str(n).lower()) for n in (need or []) if n]
    terms = [t for t in terms if len(t) >= 3]
    if not terms:
        return _short(result)

    kept: dict = {}

    def walk(v, path=""):
        if len(kept) >= 24:
            return
        if isinstance(v, dict):
            for k, sub in v.items():
                key = re.sub(r"[^a-z0-9]", "", str(k).lower())
                # The key may CONTAIN the term ("latest_close" for "latestclose"),
                # or be a shorter word inside it ("close" for "latest close") —
                # but only if it is a word rather than a letter. Allowing any
                # substring made a candle's "c", "o", "h" and "l" match nearly
                # every term ever asked for, so "give me the ATR" came back with
                # a hundred candles.
                if key and any(t in key or (len(key) >= 4 and key in t) for t in terms):
                    kept[f"{path}{k}"] = sub
                else:
                    walk(sub, f"{path}{k}.")
        elif isinstance(v, list):
            for i, sub in enumerate(v[:6]):
                walk(sub, f"{path}{i}.")

    walk(result)
    return _short(kept) if kept else _short(result)


def _looks_failed(v) -> bool:
    """Did this tentacle answer, or ask a question back?

    A node that wants a side, or could not resolve a symbol, returns prose that
    reads like data until you read it. Treating that as a result is how "Risk
    node needs a side" ended up being reported to the user as the analysis."""
    blob = json.dumps(v, default=str).lower() if not isinstance(v, str) else v.lower()
    return any(w in blob for w in ('"error"', "needs a", "must be", "not set",
                                   "could not", "couldn't", "unknown", "unavailable",
                                   "specify", "no such", "missing"))


def _octo_menu(ctx, nid):
    """The tentacles attached to this body.

    A tentacle is an edge leaving the octo node on its `tools` handle, which is
    why the walk in run_flow steps over those edges: they are a CALL LIST, not a
    path through the flow."""
    flow = ctx.get("_flow") or {}
    nodes, edges = flow.get("nodes") or {}, flow.get("edges") or []
    out = []
    for e in edges:
        if e.get("source") != nid or e.get("sourceHandle") != "tools":
            continue
        n = nodes.get(e.get("target"))
        if not n:
            continue
        nv = _values(n)
        out.append({"id": n["id"], "kind": _kind(n),
                    "name": nv.get("name") or _kind(n),
                    "does": nv.get("description") or nv.get("text") or "",
                    "_node": n})
    return out


def _n_octo(text, context, ctx, nv):
    """An agent whose tools are the nodes hanging off it.

    Every other node in this engine runs because an edge pointed at it. A
    tentacle runs because the body DECIDED it should, which is the whole point:
    the flow author wires up what is available and writes the brief, and what
    actually gets called is chosen per run against the question asked."""
    nid = ctx.get("_node_id")
    menu = _octo_menu(ctx, nid)
    if not menu:
        return {"error": "this Octo agent has no tentacles yet — connect some nodes to the "
                         "handle underneath it and it will decide which to use."}

    by_id = {m["id"]: m for m in menu}
    listing = "\n".join(
        f'- id={m["id"]} · {m["name"]} ({m["kind"]})'
        + (f' — {m["does"][:160]}' if m["does"] else " — no instruction of its own; write one")
        for m in menu)

    gathered, called, trace = {}, 0, []
    answer = None
    for _round in range(OCTO_ROUNDS):
        # Failures are shown SEPARATELY and first. Buried among the successes they
        # read as data, and the body reported the complaint as its analysis
        # instead of answering the question the tool had asked.
        problems = {k: v for k, v in gathered.items() if _looks_failed(v)}
        good = {k: v for k, v in gathered.items() if k not in problems}
        rounds_left = OCTO_ROUNDS - _round - 1
        user = (f"The user's request (THE INSTRUMENT IS IN HERE — use it): "
                f"{context.get('request') or '(none)'}\n\n"
                f"Your brief: {text or 'Answer the request using whatever tentacles it needs.'}\n\n"
                f"TENTACLES:\n{listing}\n\n"
                + (f"CALLS THAT FAILED OR ASKED FOR SOMETHING — fix the instruction and call them "
                   f"again, you have {rounds_left} round(s) left:\n"
                   f"{json.dumps(problems, default=str)[:2000]}\n\n" if problems else "")
                + f"GATHERED so far: "
                  f"{json.dumps(good, default=str)[:_CTX_CHARS] if good else '(nothing yet)'}")
        plan = _llm(ctx, nv, OCTO_SYSTEM, user, want_json=True)
        if not isinstance(plan, dict):
            break
        if plan.get("done") or not plan.get("call"):
            answer = plan.get("answer")
            break
        for want in (plan.get("call") or [])[:OCTO_CALLS]:
            if called >= OCTO_CALLS:
                break
            m = by_id.get(str(want.get("id") or "").strip())
            if not m:
                continue
            # The tentacle runs with the instruction the body just wrote, unless
            # the author gave it one — an author who typed something meant it.
            node = dict(m["_node"])
            own = _values(node).get("text") or ""
            node["data"] = {**(node.get("data") or {}),
                            "values": {**_values(node), "text": own or (want.get("text") or "")}}
            asked = own or want.get("text") or ""
            # A tentacle is where the time actually goes — an Octo round can make
            # a dozen of these — so it reports for itself rather than leaving the
            # body looking stuck for a minute.
            _emit(ctx, {"type": "tool", "phase": "start", "name": m["name"],
                        "kind": m["kind"], "round": _round + 1, "asked": asked[:200]})
            _tt, _tu = time.time(), dict(ctx.get("_usage") or {})
            res = _execute_node(node, context, ctx)
            _emit(ctx, {"type": "tool", "phase": "done", "name": m["name"],
                        "kind": m["kind"], "round": _round + 1,
                        "ms": int((time.time() - _tt) * 1000),
                        "failed": _looks_failed(_short(res)),
                        "summary": _short(res)[:240],
                        **_usage_since(ctx, _tu)})
            called += 1
            # Only what was asked for. The full result still goes to by_node for
            # the trace and for any node downstream — the trimming is about what
            # the BODY carries forward, since it re-reads that every round.
            gathered[m["name"]] = _surgical(res, want.get("need"))
            context["by_node"][m["id"]] = res
            context["last"] = res
            trace.append({"tentacle": m["name"], "kind": m["kind"],
                          "asked": asked, "need": want.get("need") or None,
                          "why": plan.get("why"),
                          "failed": _looks_failed(gathered[m["name"]])})
            # A tentacle call is a step of the run like any other. Without this the
            # history showed an Octo node and nothing about what it actually did —
            # which tools it reached for, what it told them, or what came back.
            context["steps"].append({
                "node": m["id"], "kind": m["kind"], "name": m["name"],
                "text": asked, "via": "octo", "round": _round + 1,
                "input": {"request": context.get("request"), "chain_in": []},
                "opinion": res.get("opinion") if isinstance(res, dict) else None,
                "result": _trace_result(res),
            })
        if called >= OCTO_CALLS:
            break

    if not answer:
        answer = _compose(ctx, nv, text or "Answer the request from what the tentacles found.",
                          context, gathered)
    return {"response": answer, "octo": {"called": called, "tentacles": trace,
                                         "available": [m["name"] for m in menu]}}


def _execute_node(node, context, ctx):
    kind = _kind(node)
    nv = _values(node)
    text = nv.get("text") or ""
    if kind in ("trigger-agent-call", "trigger", "trigger-interval", "triggerInterval"):
        # Both triggers are entry points and do nothing themselves. The interval
        # one carries its own request (what to analyse when the clock fires); the
        # scheduler has already put that in as the run's request.
        return {"request": context.get("request")}
    if kind in ("call-agent", "callAgent"):
        return _n_call_agent(text, context, ctx, nv)
    if kind in ("octo-agent", "octoAgent"):
        return _n_octo(text, context, ctx, nv)
    # Core nodes first, then anything a module registered — same order as the
    # chat tools, and for the same reason: a module cannot shadow a core node.
    handler = _DATA.get(kind) or registry.node_handler(kind)
    if handler:
        try:
            res = handler(text, context, ctx, nv)
        except Exception as e:
            return {"error": str(e)}
        # Per-node opinion: analyse the just-fetched data. Forced ON in chain-of-thought
        # mode so every node contributes a read the next node can build on.
        if (nv.get("opinion") or ctx.get("cot")) and isinstance(res, dict):
            res["opinion"] = _opinion(ctx, nv, kind, text, res, context)
        return res
    if kind == "if":
        return {"branch": "true" if _boolean(ctx, nv, text, context) else "false",
                "condition": text}
    if kind == "respond":
        return {"response": _compose(ctx, nv, text, context)}
    if kind == "versatile":
        return _n_versatile(nv.get("name"), nv.get("description"), text, context, ctx, nv)
    return {"error": f"unknown node kind {kind}"}


def _save_run(agent_id, user_id, request, result, source, analysis_id=None):
    """Persist one flow execution (its per-node trace + opinions) for the history
    view, keeping only the most recent 50 runs per agent. Best-effort — never
    raises into the caller."""
    if not agent_id:
        return
    try:
        import db
        from psycopg.types.json import Json
        # The trace can hold datetimes/Decimals from data-node results, which plain
        # json.dumps (what psycopg's Json uses) rejects — serialise with default=str
        # so a rich trace never silently kills the whole save.
        safe_json = lambda o: json.dumps(o, default=str)
        err = result.get("error") or result.get("llm_error")   # LLM failure counts as a run error
        u = result.get("usage") or {}
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO analysis_runs (agent_id, user_id, request, response, trace,
                       steps, status, error, source, tokens_in, tokens_out, tokens_cache_hit,
                       llm_calls, usage_model, cost_usd, analysis_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (agent_id, user_id, request, result.get("response"),
                 Json(result.get("trace") or [], dumps=safe_json), result.get("steps"),
                 "error" if err else "ok", err, source,
                 u.get("in"), u.get("out"), u.get("cache_hit"), u.get("calls"),
                 result.get("usage_model"), result.get("cost_usd"), analysis_id))
            conn.execute(
                """DELETE FROM analysis_runs WHERE agent_id = %s AND id NOT IN (
                       SELECT id FROM analysis_runs WHERE agent_id = %s
                       ORDER BY created_at DESC LIMIT 50)""",
                (agent_id, agent_id))
            conn.commit()
    except Exception as e:
        print(f"[analysis_runs] save failed for agent {agent_id}: {e!r}", flush=True)


def run_flow(flow, request, ctx, agent_id=None, source="chat", variables=None):
    """Execute an analysis-agent flow. Returns {response, trace} (or {error}).
    `request` is the free-text the caller wants analysed; `ctx` carries the LLM
    provider/key/model + user_id used by the reasoning nodes. When `agent_id` is
    given the run (trace + opinions) is saved to analysis_runs for the history view."""
    flow = flow or {}
    nodes = {n["id"]: n for n in flow.get("nodes", []) if n.get("id")}
    edges = flow.get("edges", [])
    if not nodes:
        res = {"error": "this agent has no nodes yet", "response": None}
        _save_run(agent_id, ctx.get("user_id"), request, res, source, ctx.get("_analysis_id"))
        return res

    # 5-second cache: identical inputs within the window ⇒ same output. Serve it and
    # bill 20% of the cached run's cost (via cost_usd, which callers meter).
    ck = _cache_key(agent_id, ctx.get("user_id"), request, flow) if agent_id else None
    if ck:
        hit = _cache_get(ck)
        if hit:
            out = dict(hit)
            out["cached"] = True
            out["cost_usd"] = round(CACHE_HIT_FRACTION * float(hit.get("cost_usd") or 0), 6)
            return out

    # Either trigger is a valid entry point. When a flow has both — callable AND
    # scheduled — the one that matches how this run started goes first, so the
    # walk begins where the run actually came from.
    triggers = [n for n in nodes.values()
                if _kind(n) in ("trigger-agent-call", "trigger", "trigger-interval", "triggerInterval")]
    wanted = ("trigger-interval", "triggerInterval") if source == "schedule" \
        else ("trigger-agent-call", "trigger")
    trigger = next((n for n in triggers if _kind(n) in wanted), None) or (triggers[0] if triggers else None)
    start = trigger["id"] if trigger else next(iter(nodes))   # fall back to any node

    # Chain of thought: when on, every node forms a read that builds on the running
    # chain, and each read is fed forward as context to the next node (vs isolated).
    ctx["cot"] = bool(flow.get("cot"))
    if agent_id:
        ctx["_agent_id"] = str(agent_id)   # so a call-agent node can refuse to call itself
    # An Octo body decides which of the nodes attached to it to call, so it needs
    # the graph. Nothing else reads this.
    ctx["_flow"] = {"nodes": nodes, "edges": edges}
    # The variables this run has to work with. Declared on the trigger, filled from
    # what the caller passed and — only for anything still missing — read out of
    # the request. Every node sees them: `symbol={{symbol}}` in a parameter, and
    # `trade_type=scalper` as the condition on which of several calls to make.
    declared = _declared_vars(flow.get("nodes") or [])
    run_vars = _collect_vars(declared, request, variables, ctx, {})
    missing = [d["key"] for d in declared if d.get("required") and not run_vars.get(d["key"])]
    if missing:
        # Refused rather than run half-blind. An agent told it needs a symbol and
        # given none would otherwise fetch whatever a model guessed and present
        # the result as though it had been asked for.
        return {"error": "this agent needs " + ", ".join(missing)
                         + " — pass them, or name them in the request",
                "missing": missing, "vars": run_vars}

    context = {"request": request, "vars": run_vars, "by_node": {}, "steps": [],
               "last": None, "chain": []}
    response, steps, visits = None, 0, {}
    queue = [start]

    while queue and steps < MAX_STEPS:
        nid = queue.pop(0)
        node = nodes.get(nid)
        if not node:
            continue
        visits[nid] = visits.get(nid, 0) + 1
        if visits[nid] > MAX_VISITS:
            continue
        steps += 1

        kind = _kind(node)
        nv = _values(node)
        # Snapshot what is fed INTO this node: the user request + the chain-of-thought
        # reads accumulated from every prior node (empty on the first / when CoT off).
        chain_in = [dict(c) for c in context.get("chain", [])]
        ctx["_node_id"] = nid
        _emit(ctx, {"type": "node", "phase": "start", "node": nid, "kind": kind,
                    "name": nv.get("name") or None, "step": steps})
        _t0, _u0 = time.time(), dict(ctx.get("_usage") or {})
        result = _execute_node(node, context, ctx)
        _emit(ctx, {"type": "node", "phase": "done", "node": nid, "kind": kind,
                    "name": nv.get("name") or None, "step": steps,
                    "ms": int((time.time() - _t0) * 1000),
                    "error": (result or {}).get("error") if isinstance(result, dict) else None,
                    "summary": _short(result)[:240],
                    **_usage_since(ctx, _u0)})
        context["by_node"][nid] = result
        context["last"] = result
        context["steps"].append({
            "node": nid, "kind": kind,
            "name": nv.get("name") or None,       # for versatile / custom-titled nodes
            "text": nv.get("text") or None,       # the node's instruction (its "reasoning")
            "input": {"request": request, "chain_in": chain_in},   # what was fed into the node
            "opinion": result.get("opinion") if isinstance(result, dict) else None,
            "result": _trace_result(result),
        })

        # Chain of thought: append THIS node's read (its opinion, else a short digest)
        # to the running chain so every later node reasons on top of it.
        if ctx.get("cot") and kind not in ("trigger-agent-call", "trigger",
                                           "trigger-interval", "triggerInterval"):
            op = result.get("opinion") if isinstance(result, dict) else None
            read = op or (result.get("response") if isinstance(result, dict) and result.get("response") else None)
            # A chain link is a READ. When a node produced no opinion there is
            # nothing to reason from, so it contributes a short digest — not the
            # 900-character dump the fallback used to paste in, which every later
            # node then carried for the rest of the run.
            context["chain"].append({"kind": kind, "name": nv.get("name"),
                                     "read": read if isinstance(read, str) else _short(result)[:220]})

        if isinstance(result, dict) and result.get("response") is not None:
            response = result["response"]
            if kind == "respond":
                break   # Respond is terminal

        # A `tools` edge is a tentacle — a CALL LIST for an Octo body, not a path
        # onward. Walking it here would run every tentacle a second time, and run
        # the ones the body deliberately did not choose.
        outs = [e for e in edges if e.get("source") == nid
                and e.get("sourceHandle") != "tools"]
        if kind == "if" and isinstance(result, dict):
            branch = result.get("branch")
            picked = [e for e in outs if e.get("sourceHandle") == branch]
            outs = picked or [e for e in outs if not e.get("sourceHandle")]
        for e in outs:
            if e.get("target"):
                queue.append(e["target"])

    if response is None:
        response = _compose(ctx, {}, "Summarise the analysis for the calling agent.", context)
    result = {"response": response, "trace": context["steps"], "steps": steps}
    if ctx.get("_llm_error"):
        result["llm_error"] = ctx["_llm_error"]   # AI model failed → nodes ran on defaults
    usage = ctx.get("_usage")
    if usage:
        um = ctx.get("_usage_model") or ctx.get("model")
        result["usage"] = usage
        # The REAL model prices the run; the LABEL is what anyone reads. The
        # usage line in the run history was printing "deepseek-v4-flash" beside
        # a run the user had chosen "arrissa-pro" for — the same leak as the
        # error messages, in the one place they look most often.
        result["usage_model"] = ctx.get("_model_label") or _brand(ctx, ctx.get("provider"), um) or um
        result["provider"] = ctx.get("provider")
        result["cost_usd"] = _usage_cost(usage, um)
    result["cached"] = False
    _save_run(agent_id, ctx.get("user_id"), request, result, source, ctx.get("_analysis_id"))
    # cache successful runs for the short window (fresh, real result only)
    if ck and result.get("response") and not result.get("error"):
        _cache_put(ck, {k: result.get(k) for k in ("response", "trace", "usage", "usage_model", "cost_usd")})
    return result


# ── signal extraction (the machine-readable form of a finished run) ─────────────
# A flow's Respond node writes prose for a human/agent to read. The programmatic
# API needs ONE structured signal instead, so we distil the finished run into
# {symbol, direction, order_type, quality, entry, sl, tp, ...}:
#   1. the risk-management node's engine output, when the flow has one — those
#      numbers are computed, not written by a model, so they always win;
#   2. an LLM read of the final text for whatever the engine didn't give us;
#   3. the "Confidence: N/5" the Respond node is instructed to end with → quality.
#
# There are exactly THREE answers a caller can act on: no trade (NONE), a trade to
# take NOW (MARKET), or a resting order at a level (BUY_STOP/SELL_STOP/BUY_LIMIT/
# SELL_LIMIT). An analysis that says "short on a confirmed break of 64,527" is NOT
# a market sell — it's a SELL_STOP at 64,527, and reporting it as a plain SELL
# would have the caller enter at the wrong price. So whenever the entry is
# conditional we name the pending type, and we derive WHICH pending type from the
# geometry (trigger vs the current price), never from the model's wording.
_CONF_RE = re.compile(r"confidence[^0-9]{0,16}([0-5])(?:\s*(?:/|out of)\s*5)?", re.I)

# how far the trigger must sit from the current price to be a real pending order
# (inside this band a "break" entry is effectively at market)
_PENDING_BAND = 0.0005          # 0.05% of price
# how far a plan's entry may sit from the signal's entry and still be the same
# trade — beyond it the engine's stop/target belong to a different entry
_PLAN_MATCH_BAND = 0.002        # 0.2% of price


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None   # drop NaN/inf


def _direction(v):
    s = str(v or "").strip().lower()
    if s.startswith(("buy", "long")):
        return "BUY"
    if s.startswith(("sell", "short")):
        return "SELL"
    return None


def _plan_from_trace(trace):
    """The last risk-management node's plan — exact, engine-computed levels."""
    for step in reversed(trace or []):
        if step.get("kind") != "risk-management":
            continue
        r = step.get("result")
        if not isinstance(r, dict) or r.get("error"):
            continue
        sl, tp = r.get("sl") or {}, r.get("tp") or {}
        # NB: the engine's sized volume is deliberately NOT carried into the signal.
        # Position size belongs to whoever places the order — it depends on THAT
        # account's balance and risk, which this analysis knows nothing about.
        return {
            "symbol": (r.get("symbol") or "").upper() or None,
            "direction": _direction(r.get("side")),
            "entry": _num(r.get("entry")),
            "sl": _num(sl.get("price") if isinstance(sl, dict) else sl),
            "tp": _num(tp.get("price") if isinstance(tp, dict) else tp),
            "rr": _num(r.get("rr")),
        }
    return {}


def _confidence(text):
    m = _CONF_RE.search(text or "")
    return int(m.group(1)) if m else None


def _market_price(trace):
    """The current price the run actually saw, from a market-data node: a quote's
    mid, else the newest candle close. Only used as the reference for pending-order
    geometry, so it must be the LATEST price — a trace whose candle list was capped
    (see _trace_result) no longer ends at 'now', so it's skipped."""
    def _from_candles(r):
        c = r.get("candles")
        if not isinstance(c, list) or not c or len(c) != r.get("count"):
            return None                       # missing or truncated ⇒ not the latest bar
        return _num(c[-1].get("close"))       # candles are oldest-first

    for step in reversed(trace or []):
        if step.get("kind") != "market-data":
            continue
        r = step.get("result")
        if not isinstance(r, dict) or r.get("error"):
            continue
        bid, ask = _num(r.get("bid")), _num(r.get("ask"))
        if bid and ask:
            return (bid + ask) / 2
        if bid or ask:
            return bid or ask
        got = _from_candles(r)
        if got:
            return got
        for s in (r.get("series") or []):     # multi-symbol/timeframe read
            got = _from_candles(s) if isinstance(s, dict) else None
            if got:
                return got
    return None


def _live_price(symbol):
    """Best-effort live mid for `symbol` — the reference of last resort when the
    flow gathered no price of its own. Needs a bound broker session; never raises."""
    if not symbol:
        return None
    try:
        import market
        q = market.quote(symbol)
        bid, ask = _num(q.get("bid")), _num(q.get("ask"))
        return (bid + ask) / 2 if bid and ask else (bid or ask)
    except Exception:
        return None


# How far from the live price a level may sit before it stops being a level.
# Generous on purpose: this is here to catch a signal that belongs to a
# different market, not to second-guess a wide swing stop.
_LEVEL_BAND_PCT = 10.0


def _implausible(sig: dict, price):
    """Why this signal cannot belong to `price` — or None when it can.

    A language model can be confidently wrong about SCALE. BTCUSD trading at
    62,707 came back as a buy limit at 63.47 with a stop at 63.15: the right
    digits, the wrong thousand. Every downstream check passed it, because every
    downstream check reasons about the levels RELATIVE to each other — the stop
    was below the entry, the target above, the geometry was a textbook long. The
    one fact that exposes it is the live price, so it is checked against the
    live price, here, once, before the number leaves the building.

    The EA repeats this against its own broker's quote, which is the copy that
    actually stops a trade. This one stops the CARD: a user should never be
    shown a setup that the terminal will refuse, and an API caller who is not
    our EA has no other guard at all.
    """
    price = _num(price)
    if not price or price <= 0:
        return None                     # nothing to measure against; not a verdict
    entry, sl, tp = _num(sig.get("entry")), _num(sig.get("sl")), _num(sig.get("tp"))
    at = entry or price
    buy = sig.get("direction") == "BUY"

    def away(v):
        return abs(v - price) / price * 100.0

    # Scale. The target gets more rope than the entry and the stop, since a
    # target legitimately sits further out — but not by a factor of a thousand.
    for name, level, limit in (("entry", entry, _LEVEL_BAND_PCT),
                               ("stop", sl, _LEVEL_BAND_PCT * 3),
                               ("target", tp, _LEVEL_BAND_PCT * 5)):
        if level and away(level) > limit:
            return (f"the {name} {level:g} is {away(level):.1f}% away from the live price "
                    f"{price:g} (limit {limit:.0f}%) — that is the shape of a scale error")

    # Side. A stop on the wrong side of the entry is not a stop, and the loss it
    # describes has no bound.
    if sl and buy and sl >= at:
        return f"it is a BUY with the stop {sl:g} at or above the entry {at:g}"
    if sl and not buy and sl <= at:
        return f"it is a SELL with the stop {sl:g} at or below the entry {at:g}"
    if tp and ((buy and tp <= at) or (not buy and tp >= at)):
        return f"the target {tp:g} is on the losing side of the entry {at:g}"

    # Tightness — the dangerous direction, because a stop that is too close does
    # not look wrong, it looks like a good trade. Anyone sizing from it divides a
    # budget by almost nothing and gets almost everything.
    if sl and abs(at - sl) / at * 100.0 < 0.02:
        return (f"the stop {sl:g} is {abs(at - sl) / at * 100:.4f}% from the entry {at:g} — "
                f"too tight to size from")
    return None


def _pending_type(direction, entry, price):
    """Which resting order a trigger implies. Pure geometry: above the market is a
    STOP for a buy and a LIMIT for a sell, below it the other way round."""
    if not (direction in ("BUY", "SELL") and entry and price):
        return None
    if abs(entry - price) / abs(price) < _PENDING_BAND:
        return "MARKET"                       # the trigger IS the market — enter now
    above = entry > price
    if direction == "BUY":
        return "BUY_STOP" if above else "BUY_LIMIT"
    return "SELL_LIMIT" if above else "SELL_STOP"


def _same_trade(a, b):
    """Are these two entry prices the same trade (so engine SL/TP still apply)?"""
    return bool(a and b and abs(a - b) / abs(b) <= _PLAN_MATCH_BAND)


_PENDING_TYPES = {"BUY_STOP", "SELL_STOP", "BUY_LIMIT", "SELL_LIMIT"}


def sig_symbol(plan, out):
    """The instrument, engine-resolved first (it's the broker's own symbol)."""
    return plan.get("symbol") or (str(out.get("symbol")).upper().strip() if out.get("symbol") else None)


def extract_signal(result, ctx, request=""):
    """Distil a finished run into one machine-readable signal.

    Returns {symbol, direction, order_type, quality, entry, sl, tp, rr, price,
    note} — no position size: that belongs to whoever places the order. `direction` is BUY | SELL | NONE (NONE = no trade); `order_type`
    is NONE | MARKET (take it now) | BUY_STOP | SELL_STOP | BUY_LIMIT | SELL_LIMIT
    (rest an order at `entry`) | PENDING (conditional at `entry`, but no price was
    available to tell stop from limit); `quality` is the 1-5 confidence (5 = ripe to
    trade, 0 = no setup); `price` is the market price the analysis was written
    against."""
    text = result.get("response") or ""
    plan = _plan_from_trace(result.get("trace"))
    price = _market_price(result.get("trace")) or plan.get("entry")

    system = (
        "You convert a finished trading analysis into ONE machine-readable signal. "
        "Read the analysis text and output JSON with exactly these keys: "
        '{"symbol": the instrument as written (e.g. "BTCUSD", "XAUUSD"), '
        '"direction": "BUY" | "SELL" | "NONE", '
        '"entry_type": "MARKET" | "PENDING" | "NONE", '
        '"entry": price or null, "sl": stop-loss PRICE or null, '
        '"tp": take-profit PRICE or null, '
        '"order_type": "MARKET" | "BUY_STOP" | "SELL_STOP" | "BUY_LIMIT" | "SELL_LIMIT" '
        '| "NONE" — your best read (a STOP triggers beyond the current price in the '
        'trade\'s direction, a LIMIT waits for price to come back to it), '
        '"quality": integer 0-5 — the analysis\'s own confidence rating out of 5 '
        "(use the 'Confidence: N/5' line when present; 5 = high conviction, ripe to "
        'trade; 0 only when there is no setup), '
        '"note": one short sentence saying why}.\n'
        "ENTRY TYPE is the important call. MARKET = the analysis says to get in NOW, "
        "at the current price, with no precondition. PENDING = entry is CONDITIONAL: "
        "it waits for a level — 'on a confirmed break of X', 'on a retest of X', "
        "'above/below X', 'if it closes beyond X'. For PENDING, `entry` MUST be that "
        "trigger price. Do not call a conditional entry MARKET just because the "
        "request asked to enter immediately — report what the analysis actually says. "
        "If the analysis says to stay out or wait with no level, direction and "
        'entry_type are both "NONE". '
        "Use ONLY numbers that appear in the analysis — never invent or round levels.")
    user = (f"The request that was analysed: {request or '(none)'}\n\n"
            f"The analysis:\n{text or '(empty)'}")
    out = _llm(ctx, {}, system, user, want_json=True) or {}

    # The written verdict outranks the plan on WHETHER to trade: a risk node is a
    # calculator that still runs a side when the conclusion is "stay out".
    said_none = str(out.get("direction") or "").strip().upper() == "NONE" \
        or str(out.get("entry_type") or "").strip().upper() == "NONE"
    direction = "NONE" if said_none else (plan.get("direction") or _direction(out.get("direction")) or "NONE")
    llm_entry = _num(out.get("entry"))
    pending = str(out.get("entry_type") or "").strip().upper() == "PENDING"

    # A pending setup enters at its TRIGGER, not at the price the engine sized from.
    entry = (llm_entry if pending and llm_entry else None) \
        or plan.get("entry") or llm_entry
    order_type = "NONE"
    if direction != "NONE" and not pending:
        order_type = "MARKET"
    elif direction != "NONE":
        if price is None:                     # the flow read no price — go and get one
            price = _live_price(sig_symbol(plan, out))
        # geometry is authoritative; the model's own read is the fallback, and a bare
        # "PENDING" is the honest answer when neither settles stop vs limit
        hint = str(out.get("order_type") or "").strip().upper()
        order_type = _pending_type(direction, entry, price) \
            or (hint if hint in _PENDING_TYPES else None) \
            or ("PENDING" if entry else "MARKET")

    # The engine's stop/target belong to the engine's entry — keep them only while
    # that's still the trade being described, else use the levels from the analysis.
    coherent = not pending or _same_trade(plan.get("entry"), entry)
    sl = (plan.get("sl") if coherent and plan.get("sl") is not None else None)
    tp = (plan.get("tp") if coherent and plan.get("tp") is not None else None)
    sig = {
        "symbol": sig_symbol(plan, out),
        "direction": direction,
        "order_type": order_type,
        "quality": _confidence(text),
        "entry": entry if order_type != "MARKET" else (plan.get("entry") or entry),
        "sl": sl if sl is not None else _num(out.get("sl")),
        "tp": tp if tp is not None else _num(out.get("tp")),
        "rr": plan.get("rr") if coherent else None,
        "price": price,
        "note": (str(out.get("note"))[:300] if out.get("note") else None),
    }
    if sig["quality"] is None:
        q = _num(out.get("quality"))
        sig["quality"] = int(max(0, min(5, q))) if q is not None else None
    # Last gate: does this trade belong to this market at all? Voided rather
    # than repaired — a level out by a factor of a thousand is not a trade with
    # a typo in it, and correcting it would invent a setup nobody analysed.
    if sig["direction"] != "NONE":
        why = _implausible(sig, sig.get("price") or _live_price(sig["symbol"]))
        if why:
            print(f"[analysis] signal VOIDED for {sig['symbol']}: {why}", flush=True)
            sig["direction"] = sig["order_type"] = "NONE"
            sig["quality"] = 0
            sig["note"] = f"No trade — the levels do not fit the market: {why}."

    if sig["direction"] == "NONE":
        # no trade ⇒ no levels; a stray plan/level must not read as one
        sig["quality"] = sig["quality"] if sig["quality"] is not None else 0
        sig["entry"] = sig["sl"] = sig["tp"] = sig["rr"] = None
    return sig


# ── AI flow builder ───────────────────────────────────────────────────────────
# Server-side mirror of frontend/src/components/flow/palette.js. The chat box on
# the flow page hands the LLM a plain-language brief; it returns a compact plan
# (list of node kinds + how they connect) that we expand into a real React-Flow
# {nodes, edges} the canvas can render and autosave.
_PALETTE = {
    "trigger-agent-call": {"type": "trigger",          "args": ["requirement"]},
    "trigger-interval":   {"type": "triggerInterval",
                           "args": ["mode", "every", "unit", "cron", "cron_brief", "text"]},
    # `api_params` on a data node states the call instead of having a model guess
    # it, which skips the model entirely — see _params.
    "artificial-sentiment": {"type": "artificialSentiment", "args": ["text", "api_params"]},
    "market-data":        {"type": "marketData",        "args": ["text", "api_params"]},
    "risk-management":    {"type": "riskManagement",     "args": ["text", "api_params"]},
    "time-session":       {"type": "timeSession",       "args": ["text", "api_params"]},
    # Any endpoint this app has, for what no dedicated node covers.
    "api-request":        {"type": "apiRequest",        "args": ["api_url", "api_params", "text"]},
    "if":                 {"type": "if",                "args": ["text"], "outputs": ["true", "false"]},
    "respond":            {"type": "respond",           "args": ["text"]},
    "versatile":          {"type": "versatile",         "args": ["name", "description", "text"]},
    "call-agent":         {"type": "callAgent",         "args": ["agent_id", "agent_name", "text"]},
    "octo-agent":         {"type": "octoAgent",         "args": ["text"]},
}

def _palette() -> dict:
    """Core node kinds plus any a module registered."""
    return {**_PALETTE, **{k: {"type": n["type"], "args": list(n.get("values") or ["text"])}
                           for k, n in registry.nodes().items()}}


def _opinion_kinds() -> set:
    return _OPINION_KINDS | registry.opinion_kinds()


def _build_catalog() -> str:
    """What the AI builder is told it may use — core, then modules."""
    return _BUILD_CATALOG + registry.catalog()


# data nodes that can carry a per-node opinion (analyse what they fetched)
_OPINION_KINDS = {"market-data", "artificial-sentiment"}

_BUILD_CATALOG = (
    # Two capabilities the builder could not use because nothing told it they
    # exist. Both save real money on every run, so they belong at the top.
    "HOW A FLOW GETS ITS INPUTS. The trigger can DECLARE the values the agent "
    "needs — values.vars is a list of {key, required} (e.g. [{'key':'symbol',"
    "'required':true},{'key':'trade_type','required':false}]). A required one that "
    "is not supplied REFUSES the run rather than letting a node guess. Declare a "
    "variable whenever the brief names a thing the agent is 'given' or 'told'.\n"
    "USING THEM. Every node downstream can write {{symbol}} inside values.api_params, "
    "and can branch on them: values.api_rules is an ORDERED list of "
    "{when, params} — e.g. [{'when':'trade_type=scalper','params':'symbol={{symbol}}"
    "&timeframe=M1'},{'when':'','params':'symbol={{symbol}}&timeframe=H1'}]. First "
    "match wins; '&' is AND; an empty `when` is the default and goes last.\n"
    "STATING THE CALL SAVES A MODEL CALL. Any data node accepts values.api_params "
    "(a query string, e.g. 'symbol=XAUUSD&timeframe=M15&count=100'). When it is set "
    "the node fetches directly and NO model is consulted for that node — prefer it "
    "whenever the brief fixes the values, and leave it empty only when the call "
    "genuinely has to be worked out from the request.\n"
    "trigger-agent-call — the entry point; values.requirement describes what the agent expects "
    "(e.g. 'an instrument to analyse'). Exactly ONE, always first.\n"
    "trigger-data — an entry point that runs the agent when something HAPPENS rather than on a "
    "clock: a new Truth Social post, a new market news story, news naming particular instruments, "
    "a set time BEFORE an economic release lands, or a set time AFTER one prints. values.conditions "
    "is a list of {kind, ...}: kind is truth|news|news_symbols|before_event|after_event; "
    "news_symbols takes symbols (comma list); before_event and after_event take amount and unit "
    "(seconds|minutes|hours); all but news_symbols take impact (any|high|medium|low). "
    "values.combine is 'or' (any one fires it) or 'and' (all of them, same moment). The thing that "
    "fired it arrives as the request, and as {{event}}, {{text}} and, for news about instruments, "
    "{{symbol}} — so downstream nodes can use it without fetching anything again. Use this when "
    "the user says 'when', 'as soon as', 'whenever' or names a release; use trigger-interval when "
    "they name a frequency.\n"
    "trigger-interval — a SECOND entry point that runs the agent on a clock instead of waiting to "
    "be called. Add it ONLY when the brief explicitly asks for something recurring ('every hour', "
    "'each morning before London'). values.mode = 'every' with values.every (a number) and "
    "values.unit (seconds|minutes|hours|days), OR values.mode = 'cron' with a 5-field UTC "
    "values.cron. values.text is what to analyse when it fires, since no caller is there to say "
    "(e.g. 'Analyse XAUUSD for an intraday trade'). Connect it to the same chain as the call "
    "trigger — both are starts, not steps.\n"
    "market-data — live prices/candles; values.text says what to read (e.g. 'gold structure and momentum'). "
    "Can read SEVERAL instruments and/or SEVERAL timeframes in ONE node — say so in the text (e.g. "
    "'XAUUSD, GBPUSD and gold on H4, H1 and M15') and it returns a series per symbol×timeframe "
    "(ideal for a multi-timeframe or multi-pair read without extra nodes).\n"
    "starting soon)? values.text names the symbol (e.g. 'HMR for gold'); returns in_hmr, the leverage "
    "cap, when it lifts and the next window. Useful before sizing, since HMR raises margin needed.\n"
    "risk-management — the SMART SL/TP + position-size engine: from a symbol + side (+ optional style "
    "scalp|intraday|swing|position) it reads live structure & ATR to place the STOP, sets the TARGET by "
    "reward:risk, and SIZES the lot to the account risk budget. values.text names the symbol, the side "
    "(or says to infer it from the flow's bias), the style and the risk (e.g. 'size a swing long on gold "
    "risking 2%'); risk defaults to the user's saved setting. Put it LATE in the flow, after the bias is "
    "decided, so the Respond node can hand back exact volume/SL/TP.\n"
    "artificial-sentiment — who CONTROLS the market, reconstructed from its own candles (swings, "
    "liquidity sweeps, wick absorption): bulls/bears %, each side's average entry, and how much of "
    "each side is TRAPPED underwater. Works on any instrument and any timeframe, unlike "
    "retail sentiment, which only covers the source's own symbols. values.text names the symbol and "
    "optionally the timeframe (e.g. 'gold positioning on H1'). Pair it with retail sentiment when "
    "the brief cares about the crowd: retail leaning against the footprint is a squeeze setup.\n"
    "time-session — current time/date/day + open forex sessions; values.text optional.\n"
    "if — branch on a condition; values.text is the condition (e.g. 'is the bias clearly bullish?'); "
    "it has two outputs, 'true' and 'false'.\n"
    "versatile — free-form step; values.name, values.description, values.text (its instruction).\n"
    "octo-agent — a body with TENTACLES: connect any number of nodes to its `tools` handle and it "
    "decides at run time which of them to call for the question actually asked, in as many rounds as "
    "it needs. values.text is its brief in plain words. Wire each tentacle with an edge from the "
    "octo-agent whose sourceHandle is \"tools\" (the tentacles are NOT chained to each other and "
    "nothing else points at them), and leave each tentacle's values.text EMPTY — the body writes the "
    "instruction per call. Use it when the brief says 'work out what it needs' or 'depending on', or "
    "when which sources matter depends on the instrument; use a plain chain when the steps are always "
    "the same, because a fixed order is cheaper and easier to read.\n"
    "respond — terminal node; values.text tells it how to write the final answer. It MUST end with an "
    "actionable trade setup (direction, entry, stop, target) AND a confidence rating out of 5 "
    "('Confidence: N/5', 5 = ripe to trade). Always include exactly one, last.\n\n"
    "OPINION option (data nodes only — market-data, artificial-sentiment and "
    "most module nodes): set values.opinion = true on a data node when it should also "
    "form its OWN short analysis of what it just fetched and attach it as an output for the Respond "
    "node to use. Turn it on for the data sources most central to the brief; leave it off (or omit) "
    "otherwise. You may leave the model to the agent default — do not set opinion_model unless asked."
)


def build_flow(instruction, current_flow, ctx):
    """Author or edit an analysis-agent flow from a plain-language brief.

    Returns {"flow": {nodes, edges}, "name", "description", "note"} on success,
    or {"error": ...} when there's no usable AI key or the reply doesn't parse."""
    if not (ctx.get("provider") and ctx.get("model") and ctx.get("api_key")):
        return {"error": "No AI model is configured. Connect a provider on the Connections page first."}

    existing = current_flow or {}
    cur_nodes = existing.get("nodes") or []
    cur_summary = ", ".join(_kind(n) for n in cur_nodes) or "(empty canvas)"

    system = (
        "You build 'analysis agents' — visual flows for a TRADING assistant. Each agent must analyse a "
        "financial market/instrument (forex, metals, indices, crypto, oil, etc.) using the available "
        "data sources and end with an actionable trade decision. That is the ONLY thing you build.\n\n"
        "SCOPE (accept vs refuse): only build an agent when the brief is about analysing markets / "
        "instruments to reach a trading decision. If the brief is anything else — a general-knowledge "
        "or fact bot, a chatbot, code, or any non-trading task — do NOT build a flow; instead output "
        'exactly {"refuse": "<one short sentence saying you only build market-analysis agents>"}.\n\n'
        "Available node kinds:\n\n" + _build_catalog() + "\n\n"
        "Rules: start with exactly one trigger-agent-call and end with exactly one respond. Put the "
        "data-gathering nodes in a sensible order between them, connected in a single chain unless an "
        "'if' branch is clearly warranted. Only add nodes the brief needs — keep it focused. Write a "
        "concise, specific values.text for every data/respond node.\n\n"
        "OCTO-AGENT WIRING (only when the brief asks for one, or says the agent should work out "
        "what it needs): give the octo-agent an edge from the trigger, an edge to the respond node, "
        "and one edge PER TOOL with the third element \"tools\" — e.g. [1, 3, \"tools\"]. A "
        "tentacle is reached ONLY by its \"tools\" edge: nothing else points at it, it points at "
        "nothing, and its values.text stays EMPTY because the body writes the instruction per call. "
        "Put the brief in the octo-agent's own values.text.\n\n"
        "Output ONLY one of these JSON shapes:\n"
        '{"name": "short agent name", "description": "one line", '
        '"nodes": [{"kind": "<kind>", "values": {..}}, ...], '
        '"edges": [[fromIndex, toIndex], [fromIndex, toIndex, "true"|"false"], ...]}\n'
        '  — or, if out of scope —  {"refuse": "..."}\n'
        "Edge indices refer to positions in the nodes array. The optional third edge element is the "
        "'if' branch label. Do not invent kinds outside the list."
    )
    user = (
        f"Brief: {instruction}\n\n"
        f"Current flow node kinds (edit these if the brief is a change, else replace): {cur_summary}"
    )
    plan = _llm(ctx, {}, system, user, want_json=True)
    if isinstance(plan, dict) and plan.get("refuse"):
        return {"error": str(plan["refuse"])[:200]}
    if not isinstance(plan, dict) or not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
        return {"error": "The AI couldn't turn that into a flow. Try describing the agent differently."}

    # Expand the plan into React-Flow nodes/edges.
    nodes, ids = [], []
    for i, raw in enumerate(plan["nodes"]):
        kind = (raw or {}).get("kind")
        meta = _palette().get(kind)
        if not meta:
            continue
        allowed = set(meta["args"]) | ({"opinion", "opinion_model"} if kind in _opinion_kinds() else set())
        vals = {k: v for k, v in ((raw or {}).get("values") or {}).items() if k in allowed}
        if "opinion" in vals:
            vals["opinion"] = bool(vals["opinion"])
        nid = f"{meta['type']}_{i}_{abs(hash((kind, i))) % 100000}"
        ids.append(nid)
        nodes.append({
            "id": nid,
            "type": meta["type"],
            "position": {"x": 240, "y": 60 + i * 130},
            "data": {"kind": kind, "values": vals},
        })
    if not nodes:
        return {"error": "The AI produced no valid nodes. Try again."}

    edges = []
    for e in (plan.get("edges") or []):
        try:
            a, b = int(e[0]), int(e[1])
        except (ValueError, TypeError, IndexError):
            continue
        if not (0 <= a < len(ids) and 0 <= b < len(ids)):
            continue
        edge = {"id": f"e{ids[a]}-{ids[b]}", "source": ids[a], "target": ids[b], "type": "connector"}
        handle = e[2] if len(e) > 2 else None
        # "tools" is how an Octo body says "this is a tentacle, not a next step".
        # Dropping it — which is what happened while the whitelist was the two if
        # branches — turned every tentacle into a plain chain edge, so the body
        # had no tools and the flow ran them all in a row instead.
        if handle in ("true", "false", "tools"):
            edge["sourceHandle"] = handle
            edge["id"] += f"-{handle}"
        edges.append(edge)

    # If the model gave nodes but no/broken edges, chain them top-to-bottom.
    if not edges and len(ids) > 1:
        edges = [{"id": f"e{ids[i]}-{ids[i+1]}", "source": ids[i], "target": ids[i+1],
                  "type": "connector"} for i in range(len(ids) - 1)]

    return {
        "flow": {"nodes": nodes, "edges": edges},
        "name": (plan.get("name") or "").strip() or None,
        "description": (plan.get("description") or "").strip() or None,
        "note": f"Built {len(nodes)} nodes.",
        **_usage_of(ctx),
    }


def _usage_of(ctx):
    """The tokens a one-shot helper burned on `ctx`, priced. Building a flow and
    writing a cron expression are model calls like any other, so they carry the
    same {usage, usage_model, cost_usd} the caller meters against the account."""
    usage = ctx.get("_usage")
    if not usage:
        return {}
    model = ctx.get("_usage_model") or ctx.get("model")
    # Priced on the real model, named by the branded one — the cost is ours to
    # get right and the name is the user's to read.
    return {"usage": usage,
            "usage_model": ctx.get("_model_label") or _brand(ctx, ctx.get("provider"), model) or model,
            "cost_usd": _usage_cost(usage, model)}


# ── plain language → cron ──────────────────────────────────────────────────────
_CRON_HINT = (
    "Return a standard 5-field cron expression: minute hour day-of-month month day-of-week, "
    "in UTC. Weekday 0 = Sunday. Use * for 'any', */n for 'every n', a-b for ranges and a,b "
    "for lists. Do not use seconds, years, @shorthands or non-standard extensions."
)


def suggest_cron(brief, ctx):
    """Turn 'every weekday at 7am before London opens' into '0 7 * * 1-5'.

    The expression is VALIDATED here before it is returned — a model writing a
    6-field or out-of-range expression would otherwise be stored as a schedule
    that silently never fires. Returns {cron, explanation, ...usage} or {error},
    and reports usage either way: the tokens were spent whether or not the answer
    parsed, and the caller bills what was actually spent."""
    if not (brief or "").strip():
        return {"error": "Describe when you want it to run."}
    if not (ctx.get("provider") and ctx.get("model") and ctx.get("api_key")):
        return {"error": "No AI model is configured."}

    import agent_schedule
    system = (
        "You convert a plain-language schedule into cron. " + _CRON_HINT + "\n"
        "The user is scheduling a market-analysis agent, so assume trading context: "
        "'weekdays' = Monday to Friday (1-5), market sessions are London 07:00-16:00 UTC and "
        "New York 12:00-21:00 UTC. If a time zone is named, convert it to UTC yourself.\n"
        'Output ONLY {"cron": "<expression>", "explanation": "<one short sentence in plain '
        'English, saying UTC>"}. If the request cannot be expressed in cron (e.g. it needs '
        'seconds, or "every 90 minutes" which cron cannot do), output {"error": "<one sentence '
        'saying why, and what the nearest thing cron CAN do is>"}.'
    )
    out = _llm(ctx, {}, system, f"Schedule: {brief}", want_json=True)
    usage = _usage_of(ctx)
    if not isinstance(out, dict):
        return {"error": ctx.get("_llm_error") or "The AI didn't return a schedule. Try rewording it.",
                **usage}
    if out.get("error"):
        return {"error": str(out["error"])[:300], **usage}

    expr = (out.get("cron") or "").strip()
    try:
        agent_schedule.parse_cron(expr)
    except ValueError as e:
        return {"error": f"The AI wrote {expr!r}, which is not a valid cron expression ({e}). "
                         "Try describing the schedule differently.", **usage}
    return {"cron": expr,
            "explanation": (out.get("explanation") or "").strip() or agent_schedule.describe_cron(expr),
            "reads_as": agent_schedule.describe_cron(expr), **usage}
