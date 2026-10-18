# Security settings

Administrator-relevant controls (the full model is in [Security](../security/index.md)):

| Control | Where |
|---|---|
| Session lifetime and idle timeout | `DBM_SESSION_TTL_MINUTES` (480), `DBM_SESSION_IDLE_TIMEOUT_MINUTES` (30) |
| Brute-force protection | `DBM_LOGIN_MAX_FAILURES`, `DBM_LOGIN_LOCKOUT_MINUTES`, `DBM_LOGIN_RATE_PER_IP_PER_MINUTE` |
| API rate limit per user | `DBM_API_RATE_PER_USER_PER_MINUTE` (600) |
| Cookies | `DBM_COOKIE_SECURE` (keep `true` behind HTTPS), `DBM_COOKIE_DOMAIN` |
| Client IP behind a proxy | `DBM_TRUSTED_PROXIES` |
| Production safeguards | Platform → Settings: disable writes or EXPLAIN ANALYZE on production servers |
| Secret encryption key | `DBM_MASTER_KEY` / `DBM_MASTER_KEY_ID`, rotation with `app.cli rotate-master-key` |
| File-based secrets | `DBM_SECRET_FILE_DIRS` |
| Who may register servers | `server.register` (it lets the backend open connections to any host — grant narrowly) |

Review regularly: platform users with platform-scope roles, identity-provider group mappings, the
audit log for `access.denied` and administration events.
