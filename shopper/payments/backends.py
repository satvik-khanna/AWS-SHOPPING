"""Payment backends. GuardedProvider (core.py) does every safety check; a backend only talks to
the provider. Fake = offline default. Stripe / PayPal = stdlib urllib, test/sandbox keys only.

Docs checked 2026-11-20 (see NOTES-cost.md):
  Stripe  https://docs.stripe.com/api/payment_intents/create , .../confirm , .../cancel ,
          https://docs.stripe.com/api/idempotent_requests , https://docs.stripe.com/keys ,
          https://docs.stripe.com/testing , https://docs.stripe.com/api/payment_methods/customer_list
  PayPal  https://developer.paypal.com/api/rest/authentication/ ,
          https://developer.paypal.com/docs/api/orders/v2/ ,
          https://developer.paypal.com/api/rest/reference/idempotency/ ,
          https://developer.paypal.com/docs/checkout/pay-with-venmo/ ,
          https://developer.paypal.com/docs/api/payment-tokens/v3/
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace

from ..models import PaymentIntent, PaymentMethodRef

STRIPE_API = "https://api.stripe.com"
STRIPE_VERSION = "2026-11-18.endive"          # pinned: the version the docs showed on 2026-11-20
STRIPE_TEST_PREFIXES = ("sk_test_", "rk_test_")
PAYPAL_SANDBOX = "https://api-m.sandbox.paypal.com"


class LiveKeyRefused(RuntimeError):
    pass


def _cents(usd: float) -> int:
    return int(round(float(usd) * 100))


# ---- offline -------------------------------------------------------------------------------
class FakeBackend:
    """Offline. Stripe's public test tokens (docs.stripe.com/testing) as saved methods; no network."""
    live = False
    METHODS = {
        "card": [PaymentMethodRef("pm_card_visa", "card", "Visa ...4242 12/34", "visa", "4242", 12, 2034),
                 PaymentMethodRef("pm_card_visa_chargeDeclined", "card", "Visa ...0002 12/34 (declines)",
                                  "visa", "0002", 12, 2034)],
        "stripe": [PaymentMethodRef("pm_card_mastercard", "stripe", "Stripe: Mastercard ...4444 12/34",
                                    "mastercard", "4444", 12, 2034)],
        "paypal": [PaymentMethodRef("paypal:checkout", "paypal", "PayPal (approve in PayPal)")],
        "venmo": [PaymentMethodRef("venmo:checkout", "venmo", "Venmo (approve in the Venmo app)")],
    }

    def __init__(self, kind: str):
        self.kind = kind
        self.charges: list[str] = []          # every simulated charge, so tests can count them

    def list_methods(self, user_id: str) -> list[PaymentMethodRef]:
        return list(self.METHODS[self.kind])

    def create(self, it: PaymentIntent) -> PaymentIntent:
        if it.method_ref not in {m.id for m in self.METHODS[self.kind]}:
            return replace(it, status="blocked", detail=f"unknown {self.kind} method {it.method_ref}")
        ref = "fake_" + hashlib.sha256(it.idempotency_key.encode()).hexdigest()[:12]
        return replace(it, provider_ref=ref, detail="offline fake; nothing will be charged")

    def charge(self, it: PaymentIntent, grant) -> PaymentIntent:
        if "Declined" in it.method_ref:
            return replace(it, status="failed", detail="card declined (fake)")
        self.charges.append(it.provider_ref)
        return replace(it, status="succeeded", detail=f"simulated charge ${it.amount_usd:.2f}; no real money moved")

    def cancel(self, it: PaymentIntent) -> PaymentIntent:
        return replace(it, status="canceled", detail="canceled (fake)")


# ---- http ----------------------------------------------------------------------------------
def _http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw)
        except ValueError:
            err = {"raw": raw[:300]}
        raise RuntimeError(f"HTTP {e.code}: {json.dumps(err)[:300]}") from None
    return json.loads(raw) if raw.strip() else {}


# ---- Stripe --------------------------------------------------------------------------------
def stripe_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise LiveKeyRefused("STRIPE_SECRET_KEY is not set")
    if not key.startswith(STRIPE_TEST_PREFIXES):
        raise LiveKeyRefused("refusing Stripe key: only sk_test_ / rk_test_ (sandbox) keys are accepted")
    return key


STRIPE_STATUS = {"succeeded": "succeeded", "canceled": "canceled", "requires_action": "payer_action_required",
                 "processing": "payer_action_required", "requires_payment_method": "failed",
                 "requires_confirmation": "requires_approval", "requires_capture": "payer_action_required"}


class StripeBackend:
    """kind 'card': PaymentIntent limited to card PaymentMethods (pm_... token).
    kind 'stripe': any PaymentMethod enabled in the Dashboard."""
    live = True

    def __init__(self, kind: str, opener=_http):
        self.kind = kind
        self.key = stripe_key()               # raises before any network call on a live key
        self.customer = os.environ.get("STRIPE_CUSTOMER_ID")
        self._http = opener

    def _call(self, method: str, path: str, params: dict | None = None, idem: str | None = None) -> dict:
        h = {"Authorization": "Basic " + base64.b64encode(f"{self.key}:".encode()).decode(),
             "Stripe-Version": STRIPE_VERSION}
        body = None
        url = STRIPE_API + path
        if params is not None and method == "GET":
            url += "?" + urllib.parse.urlencode(params)
        elif params is not None:
            body = urllib.parse.urlencode(params).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        if idem:
            h["Idempotency-Key"] = idem
        return self._http(method, url, h, body)

    def list_methods(self, user_id: str) -> list[PaymentMethodRef]:
        if not self.customer:
            return [PaymentMethodRef("pm_card_visa", self.kind, "Visa ...4242 (Stripe test token)", "visa", "4242")]
        params = {"limit": 20}
        if self.kind == "card":
            params["type"] = "card"
        data = self._call("GET", f"/v1/customers/{self.customer}/payment_methods", params)
        out = []
        for pm in data.get("data", []):
            c = pm.get("card") or {}
            label = (f"{c.get('brand', pm.get('type', '?')).title()} ...{c.get('last4', '')} "
                     f"{c.get('exp_month', '')}/{str(c.get('exp_year', ''))[-2:]}").strip()
            out.append(PaymentMethodRef(pm["id"], self.kind, label, c.get("brand"), c.get("last4"),
                                        c.get("exp_month"), c.get("exp_year")))
        return out

    def create(self, it: PaymentIntent) -> PaymentIntent:
        p = {"amount": _cents(it.amount_usd), "currency": "usd", "payment_method": it.method_ref,
             "confirm": "false", "metadata[cart_session_id]": it.cart_session_id, "metadata[local_id]": it.id}
        if self.customer:
            p["customer"] = self.customer
        if self.kind == "card":
            p["allowed_payment_method_types[]"] = "card"
        else:
            p["automatic_payment_methods[enabled]"] = "true"
        r = self._call("POST", "/v1/payment_intents", p, idem="create-" + it.idempotency_key)
        return replace(it, provider_ref=r["id"], status=STRIPE_STATUS.get(r.get("status"), "requires_approval"),
                       detail=f"stripe {r.get('status')}")

    def charge(self, it: PaymentIntent, grant) -> PaymentIntent:
        p = {}
        if os.environ.get("STRIPE_RETURN_URL"):
            p["return_url"] = os.environ["STRIPE_RETURN_URL"]
        r = self._call("POST", f"/v1/payment_intents/{it.provider_ref}/confirm", p,
                       idem="confirm-" + it.idempotency_key)
        return replace(it, status=STRIPE_STATUS.get(r.get("status"), "failed"), detail=f"stripe {r.get('status')}")

    def cancel(self, it: PaymentIntent) -> PaymentIntent:
        if not it.provider_ref:
            return replace(it, status="canceled", detail="canceled before reaching Stripe")
        r = self._call("POST", f"/v1/payment_intents/{it.provider_ref}/cancel",
                       {"cancellation_reason": "requested_by_customer"}, idem="cancel-" + it.idempotency_key)
        return replace(it, status=STRIPE_STATUS.get(r.get("status"), "canceled"), detail=f"stripe {r.get('status')}")


# ---- PayPal / Venmo (Orders v2) ------------------------------------------------------------
def paypal_base() -> str:
    env = os.environ.get("PAYPAL_ENV", "sandbox").lower()
    if env != "sandbox":
        raise LiveKeyRefused("refusing PayPal: only PAYPAL_ENV=sandbox is accepted")
    if not (os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET")):
        raise LiveKeyRefused("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
    return PAYPAL_SANDBOX                     # never the live host; live credentials fail OAuth here


class PayPalBackend:
    """kind 'paypal' or 'venmo'. Buyer-approval flow: create_intent makes a CAPTURE order the buyer
    approves (PayPal approve link / Venmo button from the JS SDK with enable-funding=venmo, US + USD
    only); confirm() captures it after the swipe. Vaulted methods (v3 payment tokens) are only sent to
    PayPal in confirm(), because an order with a vault_id can complete at creation."""
    live = True

    def __init__(self, kind: str, opener=_http):
        self.kind = kind
        self.base = paypal_base()
        self.customer = os.environ.get("PAYPAL_CUSTOMER_ID")
        self._http = opener
        self._token: str | None = None

    def _auth(self) -> str:
        if self._token is None:
            basic = base64.b64encode(f"{os.environ['PAYPAL_CLIENT_ID']}:{os.environ['PAYPAL_CLIENT_SECRET']}".encode())
            r = self._http("POST", self.base + "/v1/oauth2/token",
                           {"Authorization": "Basic " + basic.decode(),
                            "Content-Type": "application/x-www-form-urlencoded"},
                           b"grant_type=client_credentials")
            self._token = r["access_token"]
        return self._token

    def _call(self, method: str, path: str, body: dict | None = None, request_id: str | None = None) -> dict:
        h = {"Authorization": "Bearer " + self._auth(), "Content-Type": "application/json"}
        if request_id:
            h["PayPal-Request-Id"] = request_id[:38]
        return self._http(method, self.base + path, h, json.dumps(body).encode() if body is not None else None)

    def list_methods(self, user_id: str) -> list[PaymentMethodRef]:
        out = [PaymentMethodRef(f"{self.kind}:checkout", self.kind,
                                "PayPal (approve in PayPal)" if self.kind == "paypal" else "Venmo (approve in the Venmo app)")]
        if self.customer:
            data = self._call("GET", "/v3/vault/payment-tokens?" + urllib.parse.urlencode({"customer_id": self.customer}))
            for t in data.get("payment_tokens", []):
                src = t.get("payment_source") or {}
                if self.kind in src:
                    who = (src[self.kind] or {}).get("email_address") or t["id"]
                    out.append(PaymentMethodRef(t["id"], self.kind, f"{self.kind.title()} {who} (saved)"))
        return out

    def _order(self, it: PaymentIntent) -> dict:
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": it.cart_session_id[:256], "custom_id": it.id,
                                    "amount": {"currency_code": "USD", "value": f"{it.amount_usd:.2f}"}}]}
        if not it.method_ref.endswith(":checkout"):
            body["payment_source"] = {self.kind: {"vault_id": it.method_ref}}
        return body

    @staticmethod
    def _rid(prefix: str, key: str) -> str:
        return prefix + hashlib.sha256(key.encode()).hexdigest()[:32]   # PayPal-Request-Id max 38 chars

    def create(self, it: PaymentIntent) -> PaymentIntent:
        if not it.method_ref.endswith(":checkout"):
            return replace(it, detail="vaulted method: order is created and captured only after the swipe")
        r = self._call("POST", "/v2/checkout/orders", self._order(it), self._rid("cr-", it.idempotency_key))
        link = next((l["href"] for l in r.get("links", []) if l.get("rel") in ("payer-action", "approve")), None)
        return replace(it, provider_ref=r["id"], status="payer_action_required", approve_url=link,
                       detail=f"paypal order {r.get('status')}: buyer approves in "
                              + ("PayPal" if self.kind == "paypal" else "the Venmo app (JS SDK button)"))

    def charge(self, it: PaymentIntent, grant) -> PaymentIntent:
        if not it.provider_ref:               # vaulted: create now that the swipe has happened
            r = self._call("POST", "/v2/checkout/orders", self._order(it), self._rid("cr-", it.idempotency_key))
            it = replace(it, provider_ref=r["id"])
            if r.get("status") == "COMPLETED":
                return self._from_completed(it, r)
        r = self._call("POST", f"/v2/checkout/orders/{it.provider_ref}/capture", {},
                       self._rid("cp-", it.idempotency_key))
        if r.get("status") == "COMPLETED":
            return self._from_completed(it, r)
        return replace(it, status="payer_action_required", detail=f"paypal order {r.get('status')}")

    @staticmethod
    def _from_completed(it: PaymentIntent, r: dict) -> PaymentIntent:
        caps = [c for pu in r.get("purchase_units", []) for c in (pu.get("payments") or {}).get("captures", [])]
        ok = caps and all(c.get("status") == "COMPLETED" for c in caps)
        amt = sum(float((c.get("amount") or {}).get("value", 0)) for c in caps)
        if ok and _cents(amt) == _cents(it.amount_usd):
            return replace(it, status="succeeded", detail=f"paypal capture COMPLETED ${amt:.2f}")
        return replace(it, status="payer_action_required" if caps else "failed",
                       detail=f"paypal capture not completed: {[c.get('status') for c in caps]} ${amt:.2f}")

    def cancel(self, it: PaymentIntent) -> PaymentIntent:
        return replace(it, status="canceled",
                       detail="canceled locally; Orders v2 has no cancel endpoint, the unapproved order expires")
