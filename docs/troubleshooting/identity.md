# Identity providers

## LDAP / Active Directory

Run **Test** in the wizard or on the saved provider: it checks DNS → TCP → TLS → service bind →
user search → group lookup → user bind and reports the first failing stage.

| Failing stage | Check |
|---|---|
| DNS / TCP | host name and port (`636` for `ldaps://`, `389` for `ldap://` + StartTLS); firewall from the backend container |
| TLS | `ldaps://` or `start_tls: true`; upload the internal CA in `ca_pem`; plain `ldap://` without StartTLS fails here unless `allow_insecure` is set (labs only) |
| Service bind | `bind_dn` and its password |
| User search | `user_base_dn` and `user_filter` (Active Directory: `sAMAccountName={username}`, OpenLDAP: `uid={username}`) |
| Group lookup | `group_attribute` (`memberOf`) or `group_search_base` + `group_filter` for directories without `memberOf` |
| User bind | the user's password; unknown users and wrong passwords get the same generic error by design |

Users sign in but have no access: their directory groups are not mapped to platform groups/roles
([Identity providers](../administration/identity-providers.md)).

## OpenID Connect

| Symptom | Check |
|---|---|
| IdP reports *invalid redirect URI* | register exactly `{DBM_PUBLIC_BASE_URL}/api/v1/auth/oidc/{provider_id}/callback`; `DBM_PUBLIC_BASE_URL` must be the external HTTPS URL |
| Staged test: issuer mismatch | `issuer` must equal the `issuer` in the discovery document (Entra ID: `…/<tenant>/v2.0`) |
| Sign-in fails after returning from the IdP | clock skew (tokens are checked with 60 s leeway), cookies dropped on plain HTTP (`DBM_COOKIE_SECURE`), wrong client secret / `token_auth_method` |
| Signed in without roles | the `groups_claim` is missing from the token (Keycloak: add a group mapper; Entra ID: emit groups or app roles) |
| Rejected e-mail domain | `allowed_email_domains` |
