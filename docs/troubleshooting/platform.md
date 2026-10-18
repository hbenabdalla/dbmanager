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
| Client IPs in the audit log are the proxy's | add the proxy network to `DBM_TRUSTED_PROXIES` ([Reverse proxy and TLS](../deployment/reverse-proxy-tls.md)) |

## Documentation build fails

`mkdocs build --strict` fails on any warning. Typical causes: a link to a page or anchor that does
not exist, a page missing from `nav`, or an unpinned plugin. Install the pinned versions with
`pip install -r docs/requirements.txt` and run the build locally ([Documentation](../development/documentation.md)).
