# Respond to an alert

**Goal:** an alert has fired (in the top-bar bell, on the dashboard or as a notification). You
confirm that you are handling it, find the cause, and close it — or tune the rule if it fired
for no good reason.

## Before you start

* `alerts.view` to see alerts of the servers in your scope; `alerts.acknowledge` to acknowledge,
  resolve and comment (DBA Administrator, Database Operator, Performance Engineer and Platform
  Administrator hold it).
* Changing rules, maintenance windows and **Evaluate now** need `alerts.manage` at platform scope.

## Steps

1. Open **Operations → Alerts → Alerts** (or select the bell in the top bar, the *Active alerts*
   tile on the dashboard, the link in the notification, or *Show active alerts* in the command
   palette). The **Active** tab lists firing and acknowledged alerts; **Pending** lists alerts whose
   condition holds but whose *For (seconds)* duration has not elapsed.

    ![Alerts](../../assets/screenshots/alerts.png)

2. Select **Acknowledge** on the alert's row. In the *Acknowledge alert* dialog, enter a
   **Comment (optional)** and select **Acknowledge**. The alert stays active, shows *by* your name,
   and no further notifications (or re-notifications) are sent for it.
3. Select the alert's title to open its detail: the **Timeline**, **Comments** and
   **Notifications** (which channels were notified, with the result). Use **Add a comment** to
   record what you find.
4. Select the server name to open the server page. Depending on the rule:
    * a metric rule (connections, CPU, latency, lag …): **Performance** tab for the history and
      **Sessions** / **Locks** for blocking;
    * *Server health state*: the health reasons in the header and the last error;
    * *Collector failing*: the collection status on the **Overview** tab and
      **Settings → Operations → Test connection**.
5. Fix the cause. Cancelling a blocking statement or terminating a session needs `session.manage`
   and an *Administration enabled* server ([Sessions and locks](../sessions-and-locks.md)).
6. Let it resolve: when the condition no longer holds at an evaluation, the alert is resolved
   automatically (`resolved_by = auto`) and, if the rule has *Notify on resolve*, a resolved
   notification is sent. Or select **Resolve** to close it yourself; it fires again at the next
   evaluation if the condition still holds.
7. To check at once instead of waiting for the next run (every minute by default), a user with
   `alerts.manage` selects **Evaluate now** on **Operations → Alerts → Alert rules**.

## If the alert is noise

* Planned work: schedule a window on **Operations → Alerts → Maintenance windows**. Alerts in an
  active window are marked *Suppressed (maintenance)* and not notified.
* Wrong threshold or too sensitive: edit the rule on **Alert rules** (pencil icon). Raise
  **For (seconds)** so a short spike does not fire, use *Average over a window* instead of
  *Latest value*, or use *Deviation from baseline (z-score)* so each server is compared with its
  own history.

## What you should see

* The alert moves from **Active** to **Resolved**; its detail keeps the timeline, comments and
  notification history.
* Acknowledgements, resolutions, comments and manual evaluations are in the audit log.

## If it does not work

* No notification arrived: check the channel's delivery log (history icon) on **Platform →
  Configuration → Notification channels**, its minimum severity and routing filters
  ([Notifications](../../administration/notifications.md)).
* **Acknowledge** and **Resolve** are missing: your role lacks `alerts.acknowledge` for that server.
* The alert re-fires right after **Resolve**: the condition still holds; acknowledge it instead.

Reference: [Alerting](../alerting.md).
