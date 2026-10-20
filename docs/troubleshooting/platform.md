# Platform problems

| Symptom | Check |
|---|---|
| Backend exits with "CHANGE_ME placeholders" or "master key is not valid base64" | `.env` still has template values: run `scripts/init-env.sh` / `scripts/init-env.ps1`; on a fresh install `docker compose down -v`, then `docker compose up --build` |
| `password authentication failed` after editing `POSTGRES_PASSWORD` | the database volume keeps its first password: on a fresh install `docker compose down -v`; otherwise change it with `ALTER ROLE` |
| Frontend never starts | it waits for the backend's `/readyz`; check `docker compose logs backend` (migrations, database connection) |
| `CryptoError: unable to decrypt` | wrong `DBM_MASTER_KEY`, or the previous key is missing from `DBM_PREVIOUS_MASTER_KEYS` during rotation ([Upgrade](../operations/upgrade.md#master-key-rotation)) |
| Login works but cookies are dropped | serve over HTTPS, or set `DBM_COOKIE_SECURE=false` for plain-HTTP testing on hosts other than localhost |
| Locked out after failed logins | wait `DBM_LOGIN_LOCKOUT_MINUTES` (15) or **Platform → Users → Unlock** |
| Lost admin password | `docker compose exec backend python -m app.cli reset-password admin` |
| `429 rate_limited` | a [rate limit](../administration/rate-limiting.md) was reached: `details.policy` says which (`api`, `target`, `expensive`, `login`, `token_failures`) and `Retry-After` how long to wait. Raise the matching `DBM_*_PER_MINUTE` setting if legitimate use hits it; behind a proxy check `DBM_TRUSTED_PROXIES` (otherwise all users share one login budget) |
| Client IP in the audit log, rate limits or token allow-lists is the proxy's address | Two settings are involved ([how the client address is determined](../deployment/reverse-proxy-tls.md#how-the-client-address-is-determined)). **Frontend nginx**: set `DBM_OUTER_PROXY` to the address (one CIDR) of your reverse proxy *as nginx sees it* — on the Docker host usually the gateway of the `database-manager_app` network (`docker network inspect database-manager_app`); the default `127.0.0.1/32` trusts no outer proxy, so every request shows the proxy's address. Restart the frontend afterwards (`docker compose up -d frontend`); the value is rendered into the nginx configuration at start. **Backend**: `DBM_TRUSTED_PROXIES` must contain the frontend nginx (Compose default `172.16.0.0/12,127.0.0.1`), or the ingress controller when traffic reaches the backend directly. Never add client networks to either setting |
| `422 safety_backup_required` on a platform restore | The restore request turned off the pre-restore safety backup. A platform restore always takes a safety backup of the current configuration first; skipping it is allowed only when a Platform Administrator enables the setting `backup.allow_restore_without_safety_backup` ([Restore](../backup-restore/restore.md#platform-restore)). Leave the safety backup on unless the storage target cannot hold it |
| Webhook URL is not shown after saving a channel | Intended: a webhook URL (Slack, Teams and many receivers put the credential in the URL) and extra headers are stored encrypted and are write-only. The channel shows only scheme and host (`https://hooks.slack.com/…`) and the header names. To change the URL, enter the full new URL; leaving the field empty keeps the stored one (entering headers replaces all of them). Use **Test** to check delivery ([Notifications](../administration/notifications.md)) |

## Documentation build fails

`mkdocs build --strict` fails on any warning. Typical causes: a link to a page or anchor that does
not exist, a page missing from `nav`, or an unpinned plugin. Install the pinned versions with
`pip install -r docs/requirements.txt` and run the build locally ([Documentation](../development/documentation.md)).
