/**
 * Sends Capital One alert emails to the expense tracker.
 *
 * Paste this into script.google.com, set the two constants below, run it once
 * to authorise, then add a time-based trigger for every 5 minutes.
 * The README in the repo has the clicks, step by step.
 *
 * Nothing is deleted. Each thread gets a "Processed" sub-label once it has been
 * sent, which is how the script knows not to send it twice.
 */

const APP_URL = "https://PUT-YOUR-APP-URL-HERE.up.railway.app";   // no trailing slash
const SECRET  = "PUT-YOUR-INGEST-SECRET-HERE";

const LABEL_NAME = "Expenses CapitalOne";
const DONE_NAME  = "Expenses CapitalOne/Processed";
const SENDER     = "capitalone@notification.capitalone.com";
const LOOK_BACK  = "7d";   // how far back a run will look for anything missed

function forwardExpenses() {
  const done = GmailApp.getUserLabelByName(DONE_NAME) || GmailApp.createLabel(DONE_NAME);

  // The label is the intended source. The sender is included as a safety net so
  // that if the Gmail filter is missing, renamed, or stops matching, the alerts
  // still come through from the inbox instead of being silently missed.
  const query = '(label:"' + LABEL_NAME + '" OR from:' + SENDER + ')' +
                ' -label:"' + DONE_NAME + '" newer_than:' + LOOK_BACK;
  const threads = GmailApp.search(query, 0, 50);
  let sent = 0;

  threads.forEach(function (t) {
    // Already sent: the Processed label is on the thread.
    if (t.getLabels().some(function (l) { return l.getName() === DONE_NAME; })) return;

    let ok = true;
    t.getMessages().forEach(function (m) {
      try {
        const res = UrlFetchApp.fetch(APP_URL + "/ingest", {
          method: "post",
          contentType: "application/json",
          headers: { "X-Ingest-Secret": SECRET },
          payload: JSON.stringify({
            gmail_id: m.getId(),
            subject: m.getSubject(),
            from: m.getFrom(),
            date: m.getDate().toISOString(),
            body: m.getPlainBody()
          }),
          muteHttpExceptions: true
        });
        const code = res.getResponseCode();
        if (code >= 200 && code < 300) {
          sent++;
        } else {
          // Leave the thread unlabelled so the next run tries it again.
          ok = false;
          Logger.log("Ingest said " + code + ": " + res.getContentText());
        }
      } catch (err) {
        ok = false;
        Logger.log("Could not reach the app: " + err);
      }
    });

    if (ok) t.addLabel(done);
  });

  Logger.log("Sent " + sent + " message(s).");
}

/**
 * Run this once, by hand, to create the 5-minute trigger.
 * Running it twice would make two triggers, so it clears its own first.
 */
function installTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === "forwardExpenses") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("forwardExpenses").timeBased().everyMinutes(5).create();
  Logger.log("Trigger installed: forwardExpenses every 5 minutes.");
}
