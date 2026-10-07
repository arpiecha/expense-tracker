"""Putting a merchant in a category.

Rules first, Claude second, and whatever Claude says becomes a rule — so each
merchant costs one question, once, ever. Changing a category on the dashboard
rewrites the rule, which is what makes the correction stick.
"""

import logging
import os
import re

from sqlalchemy import select

from db import MerchantRule, SessionLocal

logger = logging.getLogger(__name__)

CATEGORIES = [
    "Food & Drink",
    "Groceries",
    "Gas",
    "Shopping",
    "Subscriptions",
    "Bills & Utilities",
    "Entertainment",
    "Travel",
    "Construction / Business",
    "Transfers",
    "Other",
]

FALLBACK = "Other"


def normalise(merchant: str) -> str:
    """The key a rule is stored under.

    Card descriptors carry store numbers and reference codes — AMAZON RETA*
    5L65G7Q41 is the same merchant as AMAZON RETA* 9XK2P1A03 — so the key drops
    the noise and keeps the name.
    """
    m = (merchant or "").upper().strip()
    m = re.sub(r"[*#]+", " ", m)
    m = re.sub(r"\b\d{3,}\b", " ", m)                 # store and reference numbers
    # Reference codes mix letters and digits (5L65G7Q41); plain words do not,
    # so STARBUCKS survives and the code does not.
    m = re.sub(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,}\b", " ", m)
    m = re.sub(r"\b(INC|LLC|LTD|CO|CORP|COM|USA)\b", " ", m)
    m = re.sub(r"[^A-Z0-9 &]", " ", m)
    m = re.sub(r"\s+", " ", m).strip()
    return m[:200] or (merchant or "").upper()[:200]


def rule_for(session, merchant: str) -> str | None:
    key = normalise(merchant)
    if not key:
        return None
    rule = session.scalar(select(MerchantRule).where(MerchantRule.pattern == key))
    if rule:
        return rule.category
    # A longer descriptor that starts with a key we already know is the same
    # merchant: "STARBUCKS STORE" matches the rule stored for "STARBUCKS".
    for candidate in session.scalars(select(MerchantRule)).all():
        if candidate.pattern and (
            key.startswith(candidate.pattern + " ") or candidate.pattern.startswith(key + " ")
        ):
            return candidate.category
    return None


def remember(session, merchant: str, category: str) -> None:
    """Write (or overwrite) the rule for this merchant."""
    key = normalise(merchant)
    if not key or category not in CATEGORIES:
        return
    rule = session.scalar(select(MerchantRule).where(MerchantRule.pattern == key))
    if rule:
        rule.category = category
    else:
        session.add(MerchantRule(pattern=key, category=category))


# Capital One already categorises every statement line. Its names are not ours,
# but the mapping is obvious and free — far better than asking Claude 45 times
# for a statement it already labelled.
STATEMENT_CATEGORIES = {
    "dining": "Food & Drink",
    "restaurants": "Food & Drink",
    "food": "Food & Drink",
    "grocery": "Groceries",
    "groceries": "Groceries",
    "gas/automotive": "Gas",
    "gas": "Gas",
    "automotive": "Gas",
    "merchandise": "Shopping",
    "shopping": "Shopping",
    "department stores": "Shopping",
    "phone/cable": "Bills & Utilities",
    "utilities": "Bills & Utilities",
    "insurance": "Bills & Utilities",
    "internet": "Bills & Utilities",
    "entertainment": "Entertainment",
    "other travel": "Travel",
    "travel": "Travel",
    "airfare": "Travel",
    "lodging": "Travel",
    "car rental": "Travel",
    "professional services": "Construction / Business",
    "home improvement": "Construction / Business",
}


def from_statement_category(name: str) -> str | None:
    """Our category for one of Capital One's, if we know it."""
    return STATEMENT_CATEGORIES.get((name or "").strip().lower())


CLAUDE_SYSTEM = (
    "You put a credit-card merchant into exactly one spending category.\n\n"
    "Reply with the category name only — no punctuation, no explanation.\n\n"
    "The categories are:\n" + "\n".join(f"- {c}" for c in CATEGORIES) + "\n\n"
    "Construction / Business is for lumber yards, hardware stores, tool rental, "
    "trade suppliers and anything else that reads as job-site spending rather "
    "than household shopping. Transfers is for money moved between accounts, "
    "Venmo, Zelle and cash advances. Use Other only when nothing else fits."
)


def ask_claude(merchant: str) -> str | None:
    """The category Claude picks, or None if it could not be asked.

    None and FALLBACK are different answers: None means we never got one, and
    a guess we never got must not be written down as a rule.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.info("No ANTHROPIC_API_KEY; filing %s under %s for now", merchant, FALLBACK)
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=30,
            system=CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": merchant}],
        )
        raw = next((b.text for b in message.content if b.type == "text"), "")
        answer = (raw or "").strip().strip(".").strip()
    except Exception:                                  # noqa: BLE001
        logger.exception("Claude could not categorise %s", merchant)
        return None

    for category in CATEGORIES:
        if answer.lower() == category.lower():
            return category
    for category in CATEGORIES:                        # tolerate a near miss
        if category.lower() in answer.lower():
            return category
    logger.info("Claude answered %r for %s, which is not a category", answer, merchant)
    return None


def categorize(session, merchant: str) -> str:
    """The category for this merchant, asking Claude only the first time.

    A rule is written only when Claude actually answered. Remembering the
    fallback would file the merchant under Other for good and stop it ever
    being asked again — which is exactly what happened while the API key was
    missing.
    """
    existing = rule_for(session, merchant)
    if existing:
        return existing
    category = ask_claude(merchant)
    if category is None:
        return FALLBACK
    remember(session, merchant, category)
    return category
