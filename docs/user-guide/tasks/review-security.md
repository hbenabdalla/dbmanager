# Review security findings and compliance

**Goal:** a periodic review: see which servers score worst, which checks fail across the fleet,
triage the findings, and keep a record for auditors.

## Before you start

* `security.view` on the servers to review; `security.manage` to acknowledge, dismiss or resolve
  findings and to run a scan; `user.view` for privileged accounts. The Security Auditor role holds
  all of them (plus `audit.view` and `audit.export` when assigned at platform scope).
* Findings exist once a security scan has run (every 6 hours after registration).

## Steps

1. Open **Security → Security posture**. Each server has a score (100 minus 25 per critical, 10 per
   high, 4 per medium and 1 per low open finding). Select a band — *Good*, *Fair* or *Poor* — to
   list those servers, and compare the averages **By environment** and **By engine**.

    ![Security posture](../../assets/screenshots/security-posture.png)

2. Under **Open findings**, select a severity or a category to open the matching findings.
3. Open **Security → Compliance** for the rule view. With **Rule set** on *Security rules*, each
   check shows its engines, category, default severity and **Failing servers**: how many of the
   servers you can see have an open or acknowledged finding of that rule (hover the number for the
   number of findings), or *pass*. Start with the checks that fail on many servers.

    ![Compliance](../../assets/screenshots/security-compliance.png)

4. Open **Security → Findings** and narrow the list with the search box (title, rule id or server),
   the severity and the status filter (*Open and acknowledged* by default).
5. Select a finding. Read the **Finding**, the **Evidence** and the **Recommendation**; the
   **Implementation guidance** shows the statement or setting to change.
6. Triage it, with a *Note for the audit log*:
    * **Acknowledge** — accepted, a fix is planned; it stays in the open list and in the score.
    * **Dismiss** — not applicable (for example an accepted risk); it leaves the open list.
    * **Mark resolved** — fixed. The next scan reopens it if the condition is still there.
7. Open **Security → Privileged accounts** and choose *Administrative, no login in 90 days* to find
   dormant administrative accounts.
8. After fixing something, re-scan the server: its **Settings** tab → **Operations** →
   **Scan security now**.
9. For the record: **Overview → Reports** → *Security findings* (filter by statuses and minimum
   severity) or *Privileged users*, as CSV or PDF; or [schedule it](schedule-a-report.md).

## What you should see

* The server's score rises once its findings are resolved or dismissed and the next scan
  confirms it.
* Every status change, with your note, is in **Security → Audit logs**.

## If it does not work

* A server has no score: no security scan has completed yet, or the monitoring account lacks the
  privileges the rules read — see the server's **Overview** collection status and the
  [engine pages](../../database-engines/index.md).
* **Acknowledge** / **Dismiss** are missing: your role lacks `security.manage` for that server.

Reference: [Security](../security.md), [Platform security model](../../security/index.md).
