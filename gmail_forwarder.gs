/**
 * Sends Capital One alert emails to the expense tracker.
 *
 * Paste this into script.google.com, set the two constants below, run it once
 * to authorise, then add a time-based trigger for every 5 minutes.
 * The README in the repo has the clicks, step by step.
 *
 * Nothing is deleted and nothing is labelled. The script remembers the ID of
 * every message it has sent, so a message is sent once and only once.
 *
 * It used to label the whole thread instead, and that quietly lost most of
 * the alerts: Capital One gives every purchase alert the same subject, so
 * Gmail files them all in one thread. The first alert was sent, the thread
 * was marked done, and the next twenty messages added to that same thread
 * were skipped. Threads are not the unit here. Messages are.
 */

const APP_URL = "https://PUT-YOUR-APP-URL-HERE.up.railway.app";   // no trailing slash
const SECRET  = "PUT-YOUR-INGEST-SECRET-HERE";

const LABEL_NAME = "Expenses CapitalOne";
const SENDER     = "capitalone@notification.capitalone.com";
const LOOK_BACK  = "14d";   // how far back a run looks for anything missed
const REMEMBER   = 400;     // how many message IDs to keep (a month or so)
const SEEN_KEY   = "sentMessageIds";

function forwardExpenses() {
  const store = PropertiesService.getScriptProperties();
  const seen = (store.getProperty(SEEN_KEY) || "").split(",").filter(String);
  const already = {};
  seen.forEach(function (id) { already[id] = true; });

  // The label is the intended source. The sender is included as a safety net so
  // that if the Gmail filter is missing, renamed, or stops matching, the alerts
  // still come through from the inbox instead of being silently missed.
  const query = '(label:"' + LABEL_NAME + '" OR from:' + SENDER + ')' +
                ' newer_than:' + LOOK_BACK;

  let sent = 0, skipped = 0, failed = 0;
  const fresh = [];

  GmailApp.search(query, 0, 100).forEach(function (thread) {
    thread.getMessages().forEach(function (m) {
      const id = m.getId();
      if (already[id]) { skipped++; return; }
      try {
        const res = UrlFetchApp.fetch(APP_URL + "/ingest", {
          method: "post",
          contentType: "application/json",
          headers: { "X-Ingest-Secret": SECRET },
          payload: JSON.stringify({
            gmail_id: id,
            subject: m.getSubject(),
            from: m.getFrom(),
            date: m.getDate().toISOString(),
            body: m.getPlainBody()
          }),
          muteHttpExceptions: true
        });
        const code = res.getResponseCode();
        if (code >= 200 && code < 300) {
          // Remembered only once the app has it, so anything that failed is
          // tried again on the next run.
          fresh.push(id);
          sent++;
        } else {
          failed++;
          Logger.log("Ingest said " + code + ": " + res.getContentText());
        }
      } catch (err) {
        failed++;
        Logger.log("Could not reach the app: " + err);
      }
    });
  });

  if (fresh.length) {
    store.setProperty(SEEN_KEY, seen.concat(fresh).slice(-REMEMBER).join(","));
  }
  Logger.log("Sent " + sent + ", already sent " + skipped + ", failed " + failed + ".");
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

/**
 * Only if you ever want the script to forget what it has sent and offer every
 * message in the look-back window again. The app ignores anything it already
 * has, so this is safe — it just makes one busy run.
 */
function forgetWhatWasSent() {
  PropertiesService.getScriptProperties().deleteProperty(SEEN_KEY);
  Logger.log("Forgotten. The next run will offer everything from the last " + LOOK_BACK + ".");
}
