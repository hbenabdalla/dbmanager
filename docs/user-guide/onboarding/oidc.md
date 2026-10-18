# OpenID Connect wizard

Presets: **Keycloak, Microsoft Entra ID, Okta, generic.** A preset fills the issuer template, scopes
and the username, email and groups claims; adjust them for your tenant.

Steps: choose the preset → issuer and client (client id, client secret — stored encrypted) → claims
and allowed email domains → **Test** → save.

The test fetches the discovery document and checks: discovery → issuer match → JWKS → endpoints
→ redirect URI. The redirect-URI stage warns until the provider is saved, because the callback URL
contains the provider id: `{DBM_PUBLIC_BASE_URL}/api/v1/auth/oidc/{provider_id}/callback`. Register
that URL with your identity provider after saving.

Reference: [OpenID Connect](../../administration/oidc.md).
