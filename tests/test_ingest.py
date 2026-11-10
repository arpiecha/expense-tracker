"""What /ingest must do with an alert that looks like one we already have.

Built from the real November rows, where a second 7-Eleven run disappeared
into the first one.

Run: DATABASE_URL="sqlite:///:memory:" python -I tests/test_ingest.py
"""

import os
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.pop("ANTHROPIC_API_KEY", None)      # never reach the network
os.environ["INGEST_SECRET"] = ""
os.environ["ADMIN_PASSWORD"] = ""

import app as flask_app                                            # noqa: E402
from db import SessionLocal, Transaction                           # noqa: E402

flask_app._startup()
client = flask_app.app.test_client()
failures: list[str] = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {label}")


def seed(when, merchant, amount, source, category="Other"):
    with SessionLocal() as s:
        s.add(Transaction(gmail_id=f"{source}:{uuid.uuid4()}", date=date.fromisoformat(when),
                          merchant=merchant, merchant_raw=merchant, amount=amount,
                          category=category, source=source, card_last4="5508"))
        s.commit()


def alert(merchant, amount, when, card="5508"):
    body = (f"Hi there,\n\nAs requested, we're notifying you that on {when}, at {merchant}, "
            f"a pending authorization or purchase in the amount of ${amount:.2f} was placed "
            f"or charged on your Venture Credit Card.\n"
            f"About your Venture Credit Card ending in {card}\n")
    r = client.post("/ingest", json={"gmail_id": str(uuid.uuid4()),
                                     "subject": "A new transaction was charged to your account",
                                     "body": body, "date": "2026-11-03T20:16:00Z"})
    return r.get_json()


print("\nA second run to the same shop is not the first one again")
seed("2026-11-02", "7-Eleven", 16.53, "email", "Gas")
# The real one: $12.99 at 7-Eleven on Nov 3, dropped into Nov 2's $16.53.
check("different day, smaller amount -> logged",
      alert("7-Eleven", 12.99, "Nov. 3, 2026")["status"], "inserted")

print("\nThe same purchase alerted twice is still one purchase")
seed("2026-11-05", "SAPORI TRATTORIA", 68.34, "email", "Food & Drink")
check("same day, same amount -> ignored",
      alert("SAPORI TRATTORIA", 68.34, "Nov. 5, 2026")["status"], "ignored")

print("\nA tip is only ever added by the statement, never between two alerts")
seed("2026-11-06", "DUBLINS BAR AND GRILL", 135.55, "email", "Food & Drink")
check("alert vs alert, tip-sized gap -> logged, they are two rounds",
      alert("DUBLINS BAR AND GRILL", 155.55, "Nov. 6, 2026")["status"], "inserted")

seed("2026-11-07", "TST* KINCADE'S", 64.03, "statement", "Food & Drink")
check("alert vs statement, tip-sized gap -> ignored, that is the posted one",
      alert("KINCADE'S", 53.36, "Nov. 7, 2026")["status"], "ignored")

print("\nStill only this card")
check("another card -> ignored", alert("WHOLE FOODS", 20.00, "Nov. 3, 2026", card="4846")["status"],
      "ignored")

print("\nRent covering two people is stored as one share")
check("the double charge is halved",
      flask_app.split_if_shared("APF*Goldberg & Perl, Inc", 3398.67)[0], 1699.34)
check("and says so in the note",
      "full charge was $3,398.67" in flask_app.split_if_shared("APF*Goldberg & Perl, Inc", 3398.67)[1],
      True)
check("a single share is left exactly alone",
      flask_app.split_if_shared("APF*Goldberg & Perl, Inc.", 1699.33), (1699.33, None))
check("so is a rent rise that is still one share",
      flask_app.split_if_shared("APF*Goldberg & Perl, Inc.", 1850.00), (1850.00, None))
check("a rent rise on the shared charge still halves",
      flask_app.split_if_shared("APF*Goldberg & Perl, Inc.", 3600.00)[0], 1800.00)
check("a refund from them is never split",
      flask_app.split_if_shared("APF*Goldberg & Perl, Inc.", -3398.67), (-3398.67, None))
check("nobody else is touched",
      flask_app.split_if_shared("NOBU CHICAGO", 3398.67), (3398.67, None))

# And end to end, through a real alert.
r = alert("APF*Goldberg & Perl, Inc", 3398.67, "Dec. 9, 2026")
check("alert for the double charge -> logged at half", r["transaction"]["amount"], 1699.34)
check("alert for the double charge -> noted",
      "3,398.67" in r["transaction"]["note"], True)
r = alert("APF*Goldberg & Perl, Inc", 1699.33, "Dec. 10, 2026")
check("alert for a single share -> logged in full", r["transaction"]["amount"], 1699.33)

print("\nA refund still files itself under Returns / Sales")
r = client.post("/ingest", json={
    "gmail_id": str(uuid.uuid4()), "subject": "You have a credit from LAKAI",
    "body": "LAKAI recently credited your account for $286.00 on Nov. 3, 2026.\n"
            "Venture Credit Card...5508\n"})
check("credit -> inserted", r.get_json()["status"], "inserted")
check("credit -> Returns / Sales", r.get_json()["transaction"]["category"], "Returns / Sales")
check("credit -> stored negative", r.get_json()["transaction"]["amount"], -286.00)

print()
if failures:
    print(f"{len(failures)} FAILED")
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("All ingest tests passed.")
