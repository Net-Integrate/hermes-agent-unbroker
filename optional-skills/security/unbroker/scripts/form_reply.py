"""Form-completion lane: classify a broker's email reply that demands a web form.

`email_modes.extract_verification_link` covers the common case - a reply that just wants a
confirmation link clicked. Some brokers reply instead with "we do not process opt-outs by
email; complete the form at <url>" - a suppression center, an iframe-embedded widget, a
support-ticket portal, or a PDF. Treating that reply as the final step stalls the case; this
module reads it into a structured task record (form_url, required_fields, hidden_gates,
due_date) so the agent can drive the form or route it to a human task on missing data or a
hard gate. Network-free (stdlib regex only) so the hermetic tests pass; the agent supplies the
reply text it already read via IMAP or webmail.
"""
from __future__ import annotations

import re

import dossier as dossier_mod

_LINK_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)

_FORM_HINTS = ("complete the form", "complete this form", "fill out", "fill in the form",
               "submit a request at", "submit the following form", "click here to opt out",
               "visit the following link to opt out", "use our opt-out form",
               "our privacy portal", "our preference center", "suppression center")

_FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "full_name": ("full name", "your name", "first and last name"),
    "first_name": ("first name",),
    "last_name": ("last name",),
    "contact_email": ("email address", "your email"),
    "street": ("street address", "mailing address", "address line"),
    "city": ("city",),
    "state": ("state", "province"),
    "postal": ("zip code", "postal code"),
    "phone": ("phone number", "telephone number"),
    "date_of_birth": ("date of birth", "birthdate", "dob"),
}

_GATE_HINTS: dict[str, tuple[str, ...]] = {
    "checkbox_consent": ("check the box", "checkbox", "i consent", "i agree to"),
    "captcha": ("captcha", "recaptcha", "hcaptcha", "prove you're human", "prove you are human",
                "verify you are human"),
    "account_verification": ("verify your account", "log in to your account", "create an account",
                             "sign in to your account"),
    "email_verification": ("confirm your email", "verification email", "click the link below to verify",
                           "verify your email address"),
    "id_verification": ("government-issued id", "government issued id", "photo id",
                        "upload your id", "proof of identity", "copy of your driver"),
}

# Gates that a headless/browser agent cannot clear on its own -- these MUST become a human
# task rather than a guessed submission (mirrors autopilot._HUMAN_GATES / T3 routing).
HARD_GATES = frozenset({"captcha", "id_verification", "account_verification"})

_FORM_KIND_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("pdf", (".pdf",)),
    ("iframe", ("iframe", "embedded form")),
    ("support_portal", ("support ticket", "help desk", "zendesk", "ticket number", "case number")),
    ("suppression_center", ("suppression center", "preference center", "privacy portal")),
]

_DUE_RE = re.compile(
    r"\b(?:within|by|before)\s+"
    r"([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d+\s+(?:calendar\s+|business\s+)?days?)",
    re.IGNORECASE,
)


def _hits(low: str, hints: tuple[str, ...]) -> bool:
    return any(h in low for h in hints)


def requires_form(text: str) -> bool:
    """True when the reply's own wording demands a form, not just a verification click."""
    return _hits((text or "").lower(), _FORM_HINTS)


def find_form_url(text: str, broker: dict | None = None) -> str | None:
    """Best-guess form URL: prefer a link that both mentions the broker's own domain and reads
    form-ish; fall back to any link on the broker's domain, then the first link in the reply."""
    candidates = _LINK_RE.findall(text or "")
    if not candidates:
        return None
    domain = ""
    if broker:
        url = (broker.get("optout") or {}).get("url") or (broker.get("search") or {}).get("url") or ""
        m = re.search(r"https?://([^/]+)", url)
        if m:
            domain = m.group(1).lower().removeprefix("www.")
    best_score, best = -1, candidates[0]
    for link in candidates:
        low = link.lower()
        score = 0
        if any(h.replace(" ", "") in low.replace("-", "").replace("_", "") for h in ("form", "optout", "opt-out")):
            score += 2
        if domain and domain in low:
            score += 3
        if score > best_score:
            best_score, best = score, link
    return best


def required_fields(text: str) -> list[str]:
    low = (text or "").lower()
    return sorted({field for field, hints in _FIELD_HINTS.items() if _hits(low, hints)})


def hidden_gates(text: str) -> list[str]:
    low = (text or "").lower()
    return sorted({gate for gate, hints in _GATE_HINTS.items() if _hits(low, hints)})


def form_kind(text: str) -> str:
    low = (text or "").lower()
    for kind, hints in _FORM_KIND_HINTS:
        if _hits(low, hints):
            return kind
    return "web_form"


def due_date(text: str) -> str | None:
    m = _DUE_RE.search(text or "")
    return m.group(1).strip() if m else None


def classify_reply(text: str, broker: dict | None = None) -> dict:
    """Structured read of a broker's reply: does it demand a form, and if so what does it need."""
    form_needed = requires_form(text)
    return {
        "form_required": form_needed,
        "form_url": find_form_url(text, broker) if form_needed else None,
        "form_kind": form_kind(text) if form_needed else None,
        "required_fields": required_fields(text) if form_needed else [],
        "hidden_gates": hidden_gates(text),
        "due_date": due_date(text),
    }


def build_task(subject_id: str, broker_id: str, classification: dict) -> dict:
    """Normalize a classification into the task record tracked on the ledger case."""
    return {
        "broker": broker_id,
        "subject": subject_id,
        "lane": "email_reply_requires_form",
        "form_url": classification.get("form_url"),
        "form_kind": classification.get("form_kind"),
        "required_fields": list(classification.get("required_fields") or []),
        "hidden_gates": list(classification.get("hidden_gates") or []),
        "due_date": classification.get("due_date"),
        "status": "awaiting_form_completion",
    }


def missing_fields(dossier: dict, required: list[str]) -> list[str]:
    """Required fields the dossier can't answer - these must stop the fill, not guess a value."""
    if not required:
        return []
    available = dossier_mod.select_disclosure(dossier, required)
    return [f for f in required if f not in available]


def blocking_gates(gates: list[str]) -> list[str]:
    """The subset of hidden_gates that must halt automated submission (captcha / gov-ID /
    account creation) - everything else (checkbox consent, email verification) the agent
    clears itself as part of driving the form."""
    return [g for g in gates or [] if g in HARD_GATES]
