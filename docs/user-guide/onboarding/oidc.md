# OpenID Connect wizard

Open it with **OIDC provider** on **Platform → Configuration → OIDC** (or on **Identity providers**);
the page lists the OpenID Connect providers already configured, with **Test**, **Edit**,
**All fields** (the flat form) and **Delete**. The wizard is also reachable at
`/platform/identity/wizard?kind=oidc`.

Presets: **Keycloak, Microsoft Entra ID, Okta, generic.** A preset fills the issuer template, scopes
and the username, email and groups claims; adjust them for your tenant.

Steps: **Provider** (the preset) → **Client** (issuer, client id, client secret — stored encrypted)
→ **Claims** (username, email and groups claims, allowed email domains) → **Test** → **Save**
(**Save provider**) → **Done**.

The test fetches the discovery document and checks: discovery → issuer match → JWKS → endpoints
→ redirect URI. The redirect-URI stage warns until the provider is saved, because the callback URL
contains the provider id: `{DBM_PUBLIC_BASE_URL}/api/v1/auth/oidc/{provider_id}/callback`. Register
that URL with your identity provider after saving.

Reference: [OpenID Connect](../../administration/oidc.md).
