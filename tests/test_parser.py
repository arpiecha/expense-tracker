"""What the parser must get right.

The samples are the wordings from the brief plus the ones that would quietly
double a month's total if they slipped through as purchases.

Run: python -I tests/test_parser.py
"""

import os
import pathlib
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import parser as email_parser                                  # noqa: E402

RECEIVED = date(2026, 10, 7)
failures: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        failures.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {label}")


def parse(subject: str, body: str):
    # use_claude=False so the tests never touch the network: these are the
    # wordings the regexes are supposed to handle on their own.
    return email_parser.parse_email(subject, body, RECEIVED, use_claude=False)


print("\nPurchase, 'charged your account' wording")
r = parse(
    "A new transaction was charged to your account",
    "Hi Aege,\n\nSTARBUCKS STORE 04821 charged your account for $42.17 on Oct. 6, 2026.\n\n"
    "Venture Credit Card...5508\n",
)
check("status", r.status, "transaction")
check("merchant", r.txn["merchant"], "STARBUCKS STORE 04821")
check("amount", r.txn["amount"], 42.17)
check("date", r.txn["date"], date(2026, 10, 6))
check("card", r.txn["card_last4"], "5508")

print("\nPurchase, 'a purchase of $X was made at' wording")
r = parse(
    "Transaction alert",
    "A purchase of $128.40 was made at HOME DEPOT #1204 on Oct. 5, 2026.\nVenture Credit Card...5508\n",
)
check("status", r.status, "transaction")
check("merchant", r.txn["merchant"], "HOME DEPOT #1204")
check("amount", r.txn["amount"], 128.40)
check("date", r.txn["date"], date(2026, 10, 5))

print("\nRefund is stored negative")
r = parse(
    "A credit was applied to your account",
    "AMAZON RETA* 5L65G7Q41 recently credited your account for $38.85 on Oct. 1, 2026.\n"
    "Venture Credit Card...5508\n",
)
check("status", r.status, "transaction")
check("merchant", r.txn["merchant"], "AMAZON RETA* 5L65G7Q41")
check("amount", r.txn["amount"], -38.85)
check("date", r.txn["date"], date(2026, 10, 1))

print("\nAmount with a thousands separator")
r = parse("Transaction alert", "BEST BUY 0423 charged your account for $1,500.00 on Oct. 2, 2026.")
check("amount", r.txn["amount"], 1500.00)

print("\nFull month name")
r = parse("Transaction alert", "TARGET 00019 charged your account for $62.00 on October 3, 2026.")
check("date", r.txn["date"], date(2026, 10, 3))

print("\nThese must never become purchases")
for subject, body, why in [
    ("Your payment is due soon", "Your payment is due on Oct. 20, 2026.", "payment due"),
    ("We received your payment", "We received your payment of $450.00. Thank you.", "payment made"),
    ("Your payment is scheduled", "A payment of $450.00 is scheduled for Oct. 18, 2026.", "scheduled"),
    ("Were you charged twice?", "It looks like you may have been charged twice at WALMART.", "double charge"),
    ("Your statement is ready", "Your October statement is ready to view.", "statement"),
    ("Earn more miles this fall", "Special offer: earn more miles on travel purchases.", "marketing"),
    ("Thank you for your payment", "Thank you for your payment of $1,200.00 on Oct. 4, 2026.", "payment made"),
]:
    r = parse(subject, body)
    check(f"ignored — {why}", r.status, "ignored")

print("\nAn unknown wording is handed to Claude, not guessed at")
r = parse("Account notice", "Something happened with your card today, see the app for details.")
check("no transactional words at all -> ignored", r.status, "ignored")

r = parse("Transaction alert", "Your card was used for a transaction of some amount somewhere.")
check("status with use_claude=False", r.status, "failed")

print("\nMerchant tidy-up")
check("normalise drops store numbers",
      __import__("categorize").normalise("AMAZON RETA* 5L65G7Q41"),
      __import__("categorize").normalise("AMAZON RETA* 9XK2P1A03"))
check("normalise keeps the name",
      __import__("categorize").normalise("STARBUCKS STORE 04821"), "STARBUCKS STORE")

print("\nStatement lines: payments to the card are not spending")
for desc, want in [
    ("CAPITAL ONE AUTOPAY PYMT", True),
    ("CAPITAL ONE MOBILE PYMT", True),
    ("ONLINE PAYMENT THANK YOU", True),
    ("PAYMENT - THANK YOU", True),
    ("SHELL OIL 574", False),
    ("KROGER #221", False),
    ("TARGET 00019", False),
    ("AMAZON RETA* 5L65G7Q41", False),
]:
    check(f"card payment? {desc}", email_parser.looks_like_card_payment(desc), want)

print("\nReal emails, pulled from the actual Gmail account")
SAMPLES = pathlib.Path(__file__).parent / "samples"

r = parse("You have a credit from AMAZON RETA* 5L65G7Q41",
          (SAMPLES / "real_credit.txt").read_text())
check("real credit -> transaction", r.status, "transaction")
if r.txn:
    check("real credit merchant", r.txn["merchant"], "AMAZON RETA* 5L65G7Q41")
    check("real credit amount", r.txn["amount"], -38.85)
    check("real credit date", r.txn["date"], date(2026, 10, 1))
    check("real credit card", r.txn["card_last4"], "5508")

# $13,996.22 landing as a refund would wreck every total in the app.
r = parse("We've received your payment", (SAMPLES / "real_payment.txt").read_text())
check("real payment -> ignored", r.status, "ignored")
r = parse("Your account", (SAMPLES / "real_payment.txt").read_text())
check("real payment ignored on body alone", r.status, "ignored")

# The footer says "payment", "credit" and "fraud" on every email including
# real purchases, so it must not be read as a reason to ignore one.
footer = (SAMPLES / "real_credit.txt").read_text()
footer_only = footer[footer.lower().find("was this email relevant"):]
r = parse("Transaction alert",
          "SHELL OIL 574 charged your account for $48.20 on Oct. 6, 2026.\n" + footer_only)
check("purchase survives the footer", r.status, "transaction")
if r.txn:
    check("purchase merchant past footer", r.txn["merchant"], "SHELL OIL 574")

print()
if failures:
    print(f"{len(failures)} FAILED\n")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("All parser tests passed.")
