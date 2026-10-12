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

print("\nAnother cardholder on the same account is not your spending")
r = parse(
    "A new transaction was charged to Roland's account",
    "Hi Aege,\n\nAmazon charged your account for $309.66 on Oct. 7, 2026.\n"
    "Venture Credit Card...5508\n",
)
check("Roland's card -> ignored", r.status, "ignored")
check("reason names the cardholder", r.reason, "Roland's card, not yours")

r = parse(
    "A new transaction was charged to Aege's account",
    "Hi Aege,\n\nSHELL OIL 574 charged your account for $48.20 on Oct. 6, 2026.\n",
)
check("your own name is still your spending", r.status, "transaction")

r = parse(
    "A new transaction was charged to your account",
    "Hi Aege,\n\nSHELL OIL 574 charged your account for $48.20 on Oct. 6, 2026.\n",
)
check("'your account' is unaffected", r.status, "transaction")

print("\nA bounced payment is not a $13,996 purchase")
for desc in [
    "Returned Online ACH Payme",
    "RETURNED ONLINE ACH PAYMENT",
    "PAYMENT REVERSAL",
    "ACH PAYMENT RETURNED",
]:
    check(f"card payment? {desc}", email_parser.looks_like_card_payment(desc), True)
r = parse("Your payment was returned", "Your payment of $13,996.22 has been returned.")
check("returned-payment email -> ignored", r.status, "ignored")

print("\nTelling two rows apart is about the name, not the spacing")
same = __import__("categorize").same_merchant
for a, b, want in [
    ("TRADER JOES", "TRADER JOE S #703", True),
    ("SQ *AQUALUX AUTO SPA L", "SQ *AQUALUX AUTO SPA LLC", True),
    ("AMOCO", "AMOCO#1776350GREENWOOD A", True),
    ("AUTOZONE", "AUTOZONE #4437", True),
    ("UBER *TRIP", "UBER   *TRIP", True),
    ("TRADER JOES", "WHOLE FOODS", False),
    ("SHELL OIL 574", "SHELL OIL 10009015008", True),
    ("LAKAI", "LA MICHOACANA PREMIUM", False),
    ("DUBLINS BAR AND GRILL", "KIRKWOOD BAR + GRILL", False),
]:
    check(f"same merchant? {a} / {b}", same(a, b), want)

print("\nThe card number says whose spending it is")
r = parse("A new transaction was charged to Roland's account",
          "As requested, we're notifying you that on Oct. 9, 2026, at AMOCO, a pending\n"
          "authorization in the amount of $95.62 was placed on Roland's Venture X Card.\n"
          "About Roland's Venture X Card ending in 6644\n")
check("another card -> ignored", r.status, "ignored")
r = parse("A new transaction was charged to your account",
          "SHELL OIL 574 charged your account for $48.20 on Oct. 6, 2026.\n"
          "Venture Credit Card...5508\n")
check("our card -> logged", r.status, "transaction")
r = parse("A new transaction was charged to your account",
          "SHELL OIL 574 charged your account for $48.20 on Oct. 6, 2026.\n")
check("no card named -> still logged", r.status, "transaction")

print("\nAn alert and its posted statement line are the same purchase")
cz = __import__("categorize")
# Every one of these is a real pair from the October catch-up.
for alert, posted, want in [
    ("AVLI TAVERNA - LINCO", "TST* AVLI TAVERNA - LINCO", True),
    ("LE PETIT MARCEL", "TST* LE PETIT MARCEL", True),
    ("PURE GREEN LINCOLN", "SQ *PURE GREEN LINCOLN AV", True),
    ("Uber Technologies", "UBER *TRIP", True),
    ("THE BURWOOD TAP", "THE BURWOOD TAP.", True),
    ("DUBLINS BAR AND GRILL", "DUBLINS BAR AND GRILL", True),
    ("GLF*CLOCKTOWER", "GLF*CLOCKTOWER", True),
    ("Amazon", "AMAZON MKTPL*5E6EC3TF1", True),
    ("7-Eleven", "7-ELEVEN 24606", True),
    ("KINCADE'S", "TST* KINCADE'S", True),
    # Two different places that happen to start the same way.
    ("THE BURWOOD TAP", "TST* THE CARLYLE CLUB", False),
    ("DUBLINS BAR AND GRILL", "KIRKWOOD BAR + GRILL", False),
    ("LAKAI", "LA MICHOACANA PREMIUM", False),
    ("TST* LA LUNA", "TST*KING OF CUPS", False),
    ("BELMONT HARBOR GAS DOCK", "BURNHAM HARBOR BOAT LAUN", False),
]:
    check(f"same merchant? {alert} / {posted}", cz.same_merchant(alert, posted), want)

print("\nA tip goes on after the alert, so the posted amount may be higher")
for alert, posted, want in [
    (135.55, 155.55, True),      # Dublins
    (7.56, 10.56, True),
    (8.00, 10.00, True),
    (113.03, 143.03, True),      # Le Petit Marcel
    (26.20, 31.20, True),        # Clocktower
    (21.00, 28.00, True),        # Burwood
    (17.37, 17.37, True),        # Pure Green, no tip
    (34.16, 28.65, False),       # a second 7-Eleven visit, not the first one
    (10.00, 100.00, False),      # nowhere near
    (-30.92, -30.92, True),      # a refund posts for what it is
    (-30.92, -40.00, False),
]:
    check(f"posted {posted} covers alert {alert}?",
          cz.posted_covers_alert(alert, posted), want)

print("\nInterest the card charges is its own category, not a bill")
cz = __import__("categorize")
check("statement category maps there", cz.from_statement_category("Fee/Interest Charge"),
      "Interest Charges")
check("the rule covers the real descriptor",
      cz.normalise("INTEREST CHARGE:PURCHASES").startswith("INTEREST CHARGE "), True)
check("it is a real category", "Interest Charges" in cz.CATEGORIES, True)

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

print("\nStatement lines from the real August-September export")
for desc, want in [
    ("CAPITAL ONE ONLINE PYMT", True),
    ("CAPITAL ONE MOBILE PYMT", True),
    ("ONLINE PAYMENT THANK YOU", True),
    ("PAYMENT - THANK YOU", True),
    ("AUTOPAY PYMT", True),
    # A merchant's name next to the word payment means it is their refund.
    ("COMED PAYMENT", False),
    ("AMAZON MKTPLACE PMTS", False),
    ("PEOPLES GAS PAYMENT", False),
    ("SHELL OIL 574", False),
    ("TST* LA LUNA", False),
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
