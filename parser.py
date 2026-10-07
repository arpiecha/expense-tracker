"""Turning a Capital One alert email into a transaction.

Regex first, because the alerts are formulaic and a regex costs nothing and
cannot hallucinate. Claude is the fallback for a wording we have not seen.

The one rule that matters: a payment to the card is not a purchase. Those
emails look transactional and would otherwise double every month's total.
"""

import json
import logging
import os
import re
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Subjects and bodies that are about the account rather than a purchase. If one
# of these matches we stop — no regex, no Claude, no transaction.
IGNORE_PATTERNS = [
    (r"payment is due", "payment due reminder"),
    (r"payment due", "payment due reminder"),
    (r"(received|processed) your payment", "payment to the card"),
    (r"payment of \$[\d,.]+ is scheduled", "scheduled payment"),
    (r"scheduled (a |your )?payment", "scheduled payment"),
    (r"thanks? (you )?for your payment", "payment to the card"),
    (r"your payment of \$[\d,]+", "payment to the card"),
    (r"here are the details of your payment", "payment to the card"),
    (r"charged twice", "duplicate-charge notice"),
    (r"your statement is ready", "statement"),
    (r"statement is available", "statement"),
    (r"minimum payment", "payment reminder"),
    (r"credit (score|wise)", "marketing"),
    (r"unsubscribe from (these|our) (offers|marketing)", "marketing"),
    (r"special offer", "marketing"),
    (r"earn (more|extra) (miles|cash back)", "marketing"),
    (r"security alert", "account notice"),
    (r"(did you|was this) (try|you)", "fraud check"),
]

# "MERCHANT charged your account for $42.17 on Oct. 6, 2026."
RE_CHARGED = re.compile(
    r"(?:^|\n)[ \t]*(?P<merchant>[^\n]{2,120}?)\s+charged your account for\s+"
    r"\$(?P<amount>[\d,]+\.?\d{0,2})\s+on\s+(?P<date>[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

# "A purchase of $42.17 was made at MERCHANT" (date may follow, may not)
RE_PURCHASE_OF = re.compile(
    r"purchase of\s+\$(?P<amount>[\d,]+\.?\d{0,2})\s+was made (?:at|on|with)\s+"
    r"(?P<merchant>[^\n]{2,120}?)"
    r"(?:\s+on\s+(?P<date>[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}))?\s*[\.\n]",
    re.IGNORECASE,
)

# "MERCHANT recently credited your account for $38.85 on Oct. 1, 2026."
RE_CREDITED = re.compile(
    r"(?:^|\n)[ \t]*(?P<merchant>[^\n]{2,120}?)\s+(?:recently\s+)?credited your account for\s+"
    r"\$(?P<amount>[\d,]+\.?\d{0,2})\s+on\s+(?P<date>[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

# "Venture Credit Card...5508"
RE_CARD = re.compile(r"(?:\.{2,}|ending in|x{2,}|\*{2,})\s*(\d{4})\b", re.IGNORECASE)

# Words that mean the email is about a transaction even if no regex matched.
TRANSACTIONAL_HINTS = ("charged", "purchase", "credited", "transaction", "refund")


class ParseResult:
    """What we decided about one email."""

    def __init__(self, status: str, reason: str = "", txn: dict | None = None):
        self.status = status          # 'transaction' | 'ignored' | 'failed'
        self.reason = reason
        self.txn = txn

    def __repr__(self) -> str:
        return f"ParseResult({self.status!r}, {self.reason!r}, {self.txn!r})"


def _clean_merchant(raw: str) -> str:
    """Tidy the merchant without throwing away what makes it recognisable."""
    m = raw.strip()
    m = re.sub(r"\s+", " ", m)
    # Alerts often start mid-sentence after a greeting or a line break.
    m = re.sub(r"^(hi|hello|hey)[^,]*,\s*", "", m, flags=re.IGNORECASE)
    m = re.sub(r"^(your (card|account) was used at|a charge from)\s+", "", m, flags=re.IGNORECASE)
    m = m.strip(" .,:;-–—\n\t")
    return m[:200]


def _parse_date(raw: str | None, fallback: date) -> date:
    """Capital One writes 'Oct. 6, 2026'. Accept the obvious variations."""
    if not raw:
        return fallback
    cleaned = re.sub(r"\s+", " ", raw.replace(".", "").replace(",", "")).strip()
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            pass
    logger.info("Could not read the date %r; using %s", raw, fallback)
    return fallback


def _amount(raw: str) -> float:
    return round(float(raw.replace(",", "")), 2)


# A statement line for a payment to the card. The alert emails say "we received
# your payment"; a statement just says AUTOPAY or PYMT, so it needs its own list
# or the month goes negative by the size of the payment.
# Words a payment-to-the-card line is made of. Strip them all and a real
# payment has nothing left — "ONLINE PAYMENT THANK YOU" becomes empty, while
# "COMED PAYMENT" still says COMED and is therefore a refund from ComEd, not a
# payment to Capital One.
PAYMENT_WORDS = re.compile(
    r"\b(auto\s*pay(ment)?|pymt|pymts|pmt|payment|payments|thank\s*you|thanks|"
    r"online|mobile|web|electronic|e-?pay|epay|direct\s*debit|received|posted|"
    r"to|your|the|a|of|card|credit|acct|account)\b",
    re.IGNORECASE,
)

CARD_PAYMENT_PATTERNS = [
    r"\bautopay\b",
    r"\bpymt\b",
    r"\bpmt\b",
    r"\bpayment\b",
    r"\bthank\s*you\b",
    r"\bonline\s+pay",
    r"\bepay\b",
    r"\bdirect\s*debit\b",
]


def looks_like_card_payment(description: str) -> bool:
    """Is this statement line a payment to the card rather than spending?

    A payment to Capital One says so, or is nothing but payment words. A
    merchant name alongside them means it is that merchant's refund.
    """
    text = (description or "").strip().lower()
    if not text:
        return False
    if "capital one" in text or "capitalone" in text:
        return True
    if not any(re.search(p, text) for p in CARD_PAYMENT_PATTERNS):
        return False
    # Something other than payment words left over? Then it names a merchant.
    remainder = PAYMENT_WORDS.sub(" ", text)
    remainder = re.sub(r"[^a-z0-9]+", " ", remainder).strip()
    return len(remainder) < 3


# Everything below one of these is Capital One's standard footer. It mentions
# payments, credit and fraud on every email, including real purchases.
BOILERPLATE_MARKERS = (
    "was this email relevant",
    "about this message",
    "unsubscribe with one click",
    "this email was sent to",
    "please do not reply to this message",
)


def meaningful_part(body: str) -> str:
    """The body with the footer cut off."""
    lowered = (body or "").lower()
    cut = len(body or "")
    for marker in BOILERPLATE_MARKERS:
        found = lowered.find(marker)
        if found != -1:
            cut = min(cut, found)
    return (body or "")[:cut]


def ignored_reason(subject: str, body: str) -> str | None:
    """Why this email is not a purchase, or None if it might be one."""
    haystack = f"{subject}\n{meaningful_part(body)}".lower()
    for pattern, reason in IGNORE_PATTERNS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return reason
    return None


def parse_email(subject: str, body: str, received: date, use_claude: bool = True) -> ParseResult:
    """Decide what an email is, and pull the transaction out of it if it is one."""
    subject = subject or ""
    body = body or ""

    reason = ignored_reason(subject, body)
    if reason:
        return ParseResult("ignored", reason)

    text = f"{subject}\n{meaningful_part(body)}"
    card = RE_CARD.search(text)
    card_last4 = card.group(1) if card else None

    for regex, is_credit in ((RE_CHARGED, False), (RE_CREDITED, True), (RE_PURCHASE_OF, False)):
        m = regex.search(text)
        if not m:
            continue
        merchant = _clean_merchant(m.group("merchant"))
        if not merchant:
            continue
        amount = _amount(m.group("amount"))
        groups = m.groupdict()
        return ParseResult("transaction", "regex", {
            "merchant": merchant,
            "merchant_raw": merchant,
            # Credits are stored negative so the month total is net spend.
            "amount": -amount if is_credit else amount,
            "date": _parse_date(groups.get("date"), received),
            "card_last4": card_last4,
        })

    # Whether this looks like a transaction at all is a fact about the email,
    # not about whether Claude is reachable — decide it first, so an email with
    # no transactional words is filed as ignored either way.
    if not any(h in text.lower() for h in TRANSACTIONAL_HINTS):
        return ParseResult("ignored", "not a transaction email")

    if not use_claude:
        return ParseResult("failed", "no pattern matched")

    return _ask_claude(subject, body, received, card_last4)


CLAUDE_SYSTEM = """You read Capital One credit-card alert emails and pull out the purchase.

Respond with ONLY a JSON object, no markdown and no commentary:

{"is_transaction": true, "merchant": "MERCHANT NAME", "amount": 42.17, "is_credit": false, "date": "2026-10-06"}

Rules:
- is_transaction is false for anything that is not a purchase or a refund: payment
  reminders, payments made to the card, scheduled payments, statements, security
  notices and marketing. When it is false, every other field may be null.
- amount is always positive. is_credit is true for a refund or credit.
- date is the date of the transaction in YYYY-MM-DD, not the date the email was sent.
  If no date is given, use null.
- merchant is the merchant exactly as the email writes it."""


def _ask_claude(subject: str, body: str, received: date, card_last4: str | None) -> ParseResult:
    """Fallback for a wording the regexes do not cover."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ParseResult("failed", "no pattern matched and ANTHROPIC_API_KEY is not set")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            system=CLAUDE_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Subject: {subject}\n\n{body[:4000]}",
            }],
        )
        raw = next((b.text for b in message.content if b.type == "text"), None)
        if raw is None:
            return ParseResult("failed", "Claude returned no text")
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned)
    except Exception as exc:                       # noqa: BLE001 - log and move on
        logger.exception("Claude could not read the email")
        return ParseResult("failed", f"Claude could not read it: {exc}")

    if not data.get("is_transaction"):
        return ParseResult("ignored", "Claude says it is not a purchase")

    merchant = _clean_merchant(str(data.get("merchant") or ""))
    if not merchant:
        return ParseResult("failed", "Claude did not give a merchant")
    # Only charges and refunds are ever logged. If Claude read a payment to the
    # card as a purchase, the merchant gives it away — a payment is not spending
    # and a wrong one here would be the size of a whole statement.
    if looks_like_card_payment(merchant) or "capital one" in merchant.lower():
        return ParseResult("ignored", "payment to the card")
    try:
        amount = abs(round(float(data.get("amount")), 2))
    except (TypeError, ValueError):
        return ParseResult("failed", "Claude did not give an amount")

    return ParseResult("transaction", "claude", {
        "merchant": merchant,
        "merchant_raw": merchant,
        "amount": -amount if data.get("is_credit") else amount,
        "date": _parse_date(data.get("date"), received),
        "card_last4": card_last4,
    })
