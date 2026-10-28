# Retention

Retention removes backups **created by the policy** that its rules no longer keep. It runs after
every successful policy run. Backups made manually, by another policy, or before a policy existed
are never touched, and KBM DBManager never deletes anything outside a backup's own folder in the
storage target.

A backup is kept when **any** rule keeps it:

| Rule | Keeps |
|---|---|
| Keep last N | the N newest successful backups |
| Keep N days | successful backups younger than N days |
| Daily / weekly / monthly generations | the newest successful backup of each of the last N days / ISO weeks / months (in the policy's time zone) |

Always kept: the newest successful backup of the policy and **pinned** backups. All rules at zero
means "keep everything". Failed and corrupted backups of the policy are cleaned up once they are
older than the grace period (keep-days, at least 7 days).

Deleted backups stay in the catalog with status `DELETED` (who, when) so the audit trail is
complete. **Backup policies → Retention** shows which backups the rules keep and which the next run
removes.
