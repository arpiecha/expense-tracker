# Expenses

Every Capital One purchase emails you. This turns those emails into a spending
dashboard on your phone, with nothing to type.

A Google Apps Script in your own Google account watches the
**Expenses CapitalOne** label and posts each new email here. This app never
touches Gmail — it only receives what the script sends.

## What the dashboard shows

- **Today** and **this month**, in Chicago time
- Daily average, what the month is on pace to reach, and the number of purchases
- A doughnut of where the money went — tap a slice or a legend row to filter
  the days below to that category, tap again to clear
- Every day, newest first. Tap a day to see the purchases on it
- A month picker, so you can look back
- **Ingest log** — the last 50 emails and whether each was logged, ignored or
  failed. This is where you check "did it catch that one?"

Tap a purchase's category chip to change it. That also teaches the merchant:
every future purchase there is filed the same way, without being asked again.
The **⋯** opens edit and delete.

### Put it on your phone

Open the site in Safari → Share → **Add to Home Screen**. It gets its own icon
and opens full screen.

## What you have to do, once

**1. Create the Railway service** — already done if you are reading this on the
live app.

**2. Import this month's statement.** Capital One → Account → Transactions →
Download, as CSV. On the dashboard tap **Import statement** and pick the file.
Purchase alerts only started on Oct 7 2026, so the statement is what fills in
everything before that. Importing the same file twice is safe — anything
already logged is skipped.

**3. Set up the Gmail script** (the 5 steps below).

**4. Add the dashboard to your home screen** (above).

## The Gmail script, step by step

1. Go to **script.google.com** → **New project**. Name it `Expense forwarder`.
2. Delete whatever is in the editor and paste the whole of
   [`gmail_forwarder.gs`](gmail_forwarder.gs) in.
3. At the top of the file, replace the two values:
   - `APP_URL` — the app's address, with no slash at the end
   - `SECRET` — the same value as `INGEST_SECRET` on the Railway service
4. Pick **forwardExpenses** in the function dropdown and press **Run**. Google
   asks for permission to read your Gmail the first time — allow it. (It will
   warn that the app is not verified: **Advanced** → **Go to Expense
   forwarder**. It is your own script.)
5. Pick **installTrigger** in the dropdown and press **Run** once. That sets it
   to check every 5 minutes, forever.

To check it is working: **Executions** in the left sidebar shows every run.

The script labels each thread `Expenses CapitalOne/Processed` once it has been
sent, which is how it knows not to send it twice. Nothing is deleted, and if
the app cannot be reached the thread is left alone and retried on the next run.

## What counts as a purchase

A purchase or a refund. These are logged as `ignored`, with the reason, so you
can see in the ingest log that nothing was lost:

- payment is due / payment received / payment scheduled
- "were you charged twice" notices
- statements, security notices, marketing and offers

Refunds are stored as negative amounts, so they reduce the month's total
instead of adding to it. The same goes for the statement import: a credit from
a merchant is a refund, but a payment to the card is not spending at all and is
left out.

## Categories

`Food & Drink`, `Groceries`, `Gas`, `Shopping`, `Subscriptions`,
`Bills & Utilities`, `Entertainment`, `Travel`, `Construction / Business`,
`Transfers`, `Other`.

A merchant is looked up in the rules table first. If it is new, Claude is asked
once and the answer becomes a rule. Changing a category on the dashboard
rewrites that rule — so correcting something once fixes it forever.

The rule key ignores store and reference numbers, so `AMAZON RETA* 5L65G7Q41`
and `AMAZON RETA* 9XK2P1A03` are the same merchant.

## Running it

One Railway project: this Flask service plus a Postgres database.

| Variable | What it is |
| --- | --- |
| `DATABASE_URL` | Postgres connection string (Railway fills this in) |
| `ADMIN_PASSWORD` | The passcode the dashboard asks for once per device |
| `INGEST_SECRET` | The shared secret the Apps Script sends. Must match `SECRET` in the script |
| `ANTHROPIC_API_KEY` | Used to read an unfamiliar email and to categorise a new merchant |
| `TZ` | `America/Chicago` — every "today" and "this month" uses this clock |

`PORT` is provided by Railway.

Without `ANTHROPIC_API_KEY` the app still runs: emails in the known wordings
are still logged, and new merchants land in `Other` until you set the category
yourself.

## How the pieces fit

```
app.py               Flask: the API and the dashboard
db.py                Tables: transactions, merchant_rules, ingest_log
parser.py            Reading a Capital One email (regex, then Claude)
categorize.py        Merchant -> category (rules, then Claude)
gmail_forwarder.gs   The Apps Script you paste into script.google.com
static/index.html    The dashboard
tests/test_parser.py The email wordings the parser must get right
```

Tables are created on startup, so a fresh database needs no migration step.

## API

Everything except `/`, `/static/*`, `/health` and `/ingest` needs the passcode
as an `X-Admin-Password` header. `/ingest` uses `X-Ingest-Secret` instead.

```
POST   /ingest                     one email from the Apps Script
GET    /summary?month=2026-10      every number the dashboard shows
GET    /transactions?date=…        or ?month=…, newest first
POST   /transactions               add one by hand
PATCH  /transactions/<id>          {category?, note?, amount?, merchant?, date?}
DELETE /transactions/<id>
POST   /import                     a Capital One statement, CSV or XLSX
GET    /ingest-log?limit=50
GET    /categories
GET    /rules
GET    /health
```

## Tests

```
DATABASE_URL="sqlite:///:memory:" python3 -I tests/test_parser.py
```

Covers both purchase wordings, refunds, thousands separators, every kind of
email that must not become a purchase, and the statement lines that are
payments to the card rather than spending.

## Notes

- **Nothing is logged twice.** Each email's Gmail id is unique in the database,
  so the same message arriving again is ignored.
- **The statement import dedupes** on the date, merchant and amount. A genuine
  second identical charge on the same day is kept; a re-import of the same file
  is not. If an emailed alert already logged a purchase, the statement row for
  it is skipped.
- **Amounts are net.** Refunds are negative, so a month's total is what you
  actually spent.
