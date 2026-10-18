# OpenID Connect

Group mapping and the sign-in flow are described in [Identity providers](identity-providers.md).


| Config key | Keycloak | Microsoft Entra ID | Okta | Auth0 |
|---|---|---|---|---|
| `issuer` | `https://kc.example/realms/corp` | `https://login.microsoftonline.com/<tenant>/v2.0` | `https://corp.okta.com/oauth2/default` | `https://corp.eu.auth0.com/` |
| `client_id` | client id | application (client) id | client id | client id |
| `groups_claim` | `groups` (add a group mapper) | `groups` (emit group IDs or app roles via `roles`) | `groups` | custom namespaced claim |
| `username_claim` | `preferred_username` | `preferred_username` | `preferred_username` | `email` |

Optional: `scopes` (default `openid profile email`), `token_auth_method` (`client_secret_basic` or
`client_secret_post`), `allowed_email_domains`, `prompt`, `redirect_uri` (defaults to
`{DBM_PUBLIC_BASE_URL}/api/v1/auth/oidc/{provider_id}/callback` — register this URI with the IdP).

Validation: discovery document, PKCE S256, state and nonce in an encrypted cookie, signature against
JWKS, `iss`, `aud`, `nonce`, `exp` (60 s leeway).


## Presets and test

`GET /api/v1/identity-providers/presets` returns the Keycloak, Entra ID, Okta and generic presets used
by the [wizard](../user-guide/onboarding/oidc.md). The staged test checks discovery, issuer match,
JWKS, endpoints and the redirect URI (which warns until the provider is saved).
