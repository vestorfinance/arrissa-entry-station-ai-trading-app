"""
TradeLocker HTTP/auth layer — the low-level transport for the adapter.

Auth model (TradeLocker Trade API):
  - POST {base}/backend-api/auth/jwt/token  {email, password, server}
      -> { accessToken, refreshToken, expireDate }
  - POST {base}/backend-api/auth/jwt/refresh {refreshToken}
      -> { accessToken, (refreshToken?) }
  - Every /trade/* call needs:  Authorization: Bearer <accessToken>
      and the header  accNum: <n>   (the account's ordinal).
  - The app-level developer key goes in the  tl-developer-api-key  header for
    higher rate limits (optional but we always send it when configured).

`base` is chosen by environment: demo vs live are separate hosts.

`TLSession` wraps one login's tokens with transparent refresh + a persistence
callback (`on_rotate`) so rotated tokens are written back to the DB.
"""
import time
import json
import base64

from curl_cffi import requests as creq

DEV_HEADER = "tl-developer-api-key"
PREFIX = "/backend-api"
REFRESH_BUFFER = 120  # seconds before expiry we proactively refresh

BASES = {
    "demo": "https://demo.tradelocker.com",
    "live": "https://live.tradelocker.com",
}


def base_url(environment: str) -> str:
    return BASES.get((environment or "demo").lower(), BASES["demo"])


def _dev_key():
    try:
        import tradelocker
        return tradelocker.dev_key()
    except Exception:
        return None


def _headers(access: str = None, acc_num=None, json_body=False) -> dict:
    h = {"Accept": "application/json"}
    if json_body:
        h["Content-Type"] = "application/json"
    if access:
        h["Authorization"] = f"Bearer {access}"
    if acc_num is not None:
        h["accNum"] = str(acc_num)
    dev = _dev_key()
    if dev:
        h[DEV_HEADER] = dev
    return h


def _jwt_exp(token: str) -> float:
    try:
        p = token.split(".")[1]
        p += "=" * (4 - len(p) % 4)
        return float(json.loads(base64.urlsafe_b64decode(p)).get("exp", 0))
    except Exception:
        return 0.0


def _needs_refresh(token: str) -> bool:
    return (not token) or time.time() >= (_jwt_exp(token) - REFRESH_BUFFER)


def _is_maintenance(r) -> bool:
    loc = (r.headers.get("location") or "").lower()
    return "maintenance" in loc or r.status_code == 503


def _json_or_error(r, what: str):
    """Return parsed JSON, or raise a CLEAR error. TradeLocker under maintenance
    answers with a 302→*-maintenance redirect or a 503 (HTML), which would
    otherwise surface as a cryptic 'Expecting value: line 1 column 1'."""
    if r.status_code in (401, 403):
        raise TradeLockerAuthError("TradeLocker rejected the request (check credentials / server).")
    if _is_maintenance(r):
        raise TradeLockerError(
            "TradeLocker appears to be under maintenance right now (their API is "
            "temporarily unavailable) — please try again shortly.")
    if r.status_code >= 300:
        raise TradeLockerError(f"{what} failed: HTTP {r.status_code} {r.text[:200]}")
    try:
        return r.json()
    except Exception:
        ct = r.headers.get("content-type", "")
        raise TradeLockerError(
            f"{what}: unexpected non-JSON response from TradeLocker "
            f"(HTTP {r.status_code}, {ct or 'unknown type'}). It may be under maintenance.")


class TradeLockerError(RuntimeError):
    pass


class TradeLockerAuthError(TradeLockerError):
    """Credentials rejected, or refresh token no longer valid (reconnect needed)."""


# ── raw login / account discovery (used at connect time) ────────────────────────
def login_tokens(email: str, password: str, server: str, environment: str) -> dict:
    """POST the credentials, return {accessToken, refreshToken, expireDate}."""
    s = creq.Session(impersonate="chrome")
    url = base_url(environment) + PREFIX + "/auth/jwt/token"
    r = s.post(url, json={"email": email, "password": password, "server": server},
               headers=_headers(json_body=True), timeout=30, allow_redirects=False)
    data = _json_or_error(r, "login")
    access = data.get("accessToken") or data.get("access_token")
    refresh = data.get("refreshToken") or data.get("refresh_token")
    if not access or not refresh:
        raise TradeLockerError(f"login response missing tokens: {json.dumps(data)[:200]}")
    return {"accessToken": access, "refreshToken": refresh,
            "expireDate": data.get("expireDate")}


def all_accounts(access: str, environment: str) -> list[dict]:
    """GET all accounts for a login: normalized [{account_id, acc_num, currency,
    name, environment}]. Native TL fields: accounts[].id (accountId),
    accounts[].accNum, accounts[].currency, accounts[].name."""
    s = creq.Session(impersonate="chrome")
    url = base_url(environment) + PREFIX + "/auth/jwt/all-accounts"
    r = s.get(url, headers=_headers(access), timeout=30, allow_redirects=False)
    data = _json_or_error(r, "all-accounts")
    accts = (data.get("accounts") if isinstance(data, dict) else data) or []
    out = []
    for a in accts:
        out.append({
            "account_id": str(a.get("id") or a.get("accountId") or a.get("account_id") or ""),
            "acc_num": str(a.get("accNum") if a.get("accNum") is not None
                           else a.get("accnum", "")),
            "currency": a.get("currency"),
            "name": a.get("name") or a.get("accountName"),
            "environment": environment,
            # TradeLocker sends "ACTIVE" for a live account and something else
            # for one that has been archived or restricted. It was being thrown
            # away, so archived accounts sat in the picker looking tradable.
            "status": (a.get("status") or a.get("accountStatus") or "").upper() or None,
            "balance": a.get("accountBalance"),
        })
    return [a for a in out if a["account_id"]]


# ── a live, self-refreshing session bound to one login ──────────────────────────
class TLSession:
    def __init__(self, access, refresh, environment, acc_num=None, on_rotate=None):
        self.access = access
        self.refresh = refresh
        self.environment = environment
        self.acc_num = acc_num
        self.on_rotate = on_rotate          # (access, refresh) -> persist
        self.base = base_url(environment)
        self._s = creq.Session(impersonate="chrome")

    # -- token lifecycle -----------------------------------------------------------
    def _do_refresh(self):
        url = self.base + PREFIX + "/auth/jwt/refresh"
        r = self._s.post(url, json={"refreshToken": self.refresh},
                         headers=_headers(json_body=True), timeout=30, allow_redirects=False)
        if r.status_code in (401, 403):
            raise TradeLockerAuthError(
                "TradeLocker session expired — reconnect your account.")
        data = _json_or_error(r, "refresh")
        self.access = data.get("accessToken") or data.get("access_token") or self.access
        new_refresh = data.get("refreshToken") or data.get("refresh_token")
        if new_refresh:
            self.refresh = new_refresh
        if self.on_rotate:
            try:
                self.on_rotate(self.access, self.refresh)
            except Exception:
                pass

    def _ensure_fresh(self):
        if _needs_refresh(self.access):
            self._do_refresh()

    # -- requests ------------------------------------------------------------------
    def request(self, method, path, with_accnum=True, **kw):
        """Send a request, retrying up to 5x on transient failures: rate limits
        (429), server errors (5xx), network exceptions, and expired tokens
        (401/403 → refresh then retry). Non-retryable client responses (2xx/other
        4xx like 404) return immediately. Backoff is exponential (capped)."""
        self._ensure_fresh()
        url = self.base + PREFIX + path
        acc = self.acc_num if with_accnum else None
        json_body = "json" in kw
        kw.setdefault("allow_redirects", False)
        last_exc, r = None, None
        for attempt in range(5):
            try:
                r = self._s.request(method, url,
                                    headers=_headers(self.access, acc, json_body=json_body),
                                    timeout=30, **kw)
            except Exception as e:
                last_exc = e
                time.sleep(min(0.5 * (2 ** attempt), 4.0))
                continue
            if r.status_code in (401, 403):
                try:
                    self._do_refresh()
                except Exception:
                    pass
                continue                                      # retry with a fresh token
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(0.5 * (2 ** attempt), 4.0))    # rate-limited / server error
                continue
            return r                                          # 2xx or a real client error
        if r is not None:
            return r                                          # last response (caller handles)
        raise last_exc if last_exc else TradeLockerError(f"{method} {path}: request failed")

    def get_json(self, path, params=None, with_accnum=True):
        r = self.request("GET", path, params=params or {}, with_accnum=with_accnum)
        return _json_or_error(r, f"GET {path}")

    def send_json(self, method, path, body, with_accnum=True):
        r = self.request(method, path, json=body, with_accnum=with_accnum)
        if _is_maintenance(r) or r.status_code >= 300:
            return _json_or_error(r, f"{method} {path}")
        try:
            return r.json()
        except Exception:
            return {"ok": True}
