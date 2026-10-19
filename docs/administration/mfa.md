# Multi-factor authentication

DBManager supports a second factor for **local accounts**: an authenticator app (TOTP, RFC 6238)
plus one-time recovery codes. Directory (LDAP) users authenticate against the directory; single
sign-on (OIDC) users get MFA from their identity provider, which DBManager can require
(see [OIDC](#single-sign-on-oidc) below).

## Policy

| Setting | Values | Effect |
|---|---|---|
| `DBM_MFA_REQUIRED_FOR` | `none` (default), `admins`, `all` | Whose local accounts must use MFA. `admins` means every user holding a platform-only permission (for example `users.manage`, `roles.manage`, `settings.manage`); `all` means every local user. |
| `DBM_MFA_ISSUER` | text, default `DBManager` (`DB Manager` before 0.14.0) | Name shown in the authenticator app next to the username. It is stored in the app at enrollment: existing entries keep the old name and keep working. Set `DBM_MFA_ISSUER="DB Manager"` to keep the previous name for new enrollments. |

When the policy applies to a user who has not enrolled yet, that user can still sign in with the
password, but the session is limited to enrollment: the web interface opens **Account → Security**
and every other API call answers `403` with the code `mfa_enrollment_required` until an
authenticator is set up. `GET /api/v1/auth/me` reports this as `mfa_enrollment_required: true`.

!!! warning "Before switching to `admins`"
    Platform administrators who have not enrolled are limited as described above from the moment
    the setting takes effect, including the bootstrap administrator. Enroll at least one
    administrator first.

## Enrollment

Each user enrolls in **user menu → Security** (`/account/security`):

1. **Set up authenticator** and confirm the current password.
2. Scan the QR code with an authenticator app (Microsoft Authenticator, Google Authenticator,
   1Password, …) or type the key shown below it.
3. Enter the 6-digit code the app shows and select **Turn on**.
4. Store the **ten recovery codes**: copy them or download them as a text file. They are shown
   only once.

The secret is stored encrypted with the platform master key and is never shown again. Codes are
accepted within ±30 seconds of clock skew, and each code works only once.

## Signing in

With MFA enabled the password alone no longer creates a session. After the password, the sign-in
page asks for the **authentication code**: the 6-digit code from the app or one recovery code. The
second step must happen within five minutes, otherwise the sign-in starts again with the password.
Failed codes count towards the normal lockout (`DBM_LOGIN_MAX_FAILURES`) and are audited as a
failed `auth.login`.

## Recovery codes

Each recovery code signs in once, in place of the app code. **Security** shows how many are left;
**New recovery codes** (password plus a current code) replaces the whole set
(audited as `auth.mfa_recovery_codes`).

## Turning MFA off

**Turn off** on the Security page needs the password and a current code (audited as
`auth.mfa_disable`). If the policy requires
MFA for the user, a new authenticator must be set up at the next sign-in.

## Administrator reset (lost device)

A user who lost the authenticator and the recovery codes asks an administrator. In
**Platform → Users**, open the user and select **Reset MFA** (`users.manage`). The authenticator
and recovery codes are removed; the user signs in with the password and, where the policy applies,
enrolls again. The reset is audited (`user.mfa_reset`). Verify the request through a second
channel before resetting.

API: `POST /api/v1/users/{id}/mfa/reset`.

## Single sign-on (OIDC)

For OpenID Connect providers, the provider performs MFA. DBManager can **require** it per
provider (Identity providers → provider → *Multi-factor authentication*):

| Field | Configuration key | Meaning |
|---|---|---|
| Require multi-factor authentication | `require_mfa` | Sign-in is refused unless the ID token proves a second factor. |
| Accepted amr values | `mfa_amr_values` | `amr` claim values that count as MFA. Empty uses the RFC 8176 multi-factor methods (`mfa`, `otp`, `hwk`, `swk`, `sc`, `fpt`, `face`, `iris`, `retina`, `vbm`, `pin`). |
| Accepted acr values | `required_acr` | `acr` claim values that also satisfy the requirement (for example the provider's MFA level of assurance). |
| Request acr_values | `acr_values` | Sent with the authorization request to ask the provider for a specific policy. |

A token is accepted when its `amr` contains an accepted value **or** its `acr` is one of the
accepted acr values.
Check which `amr` / `acr` values your provider puts in the ID token before turning this on.

## API tokens and directory users

* The MFA endpoints (`/api/v1/me/mfa/…`) are interactive-only: personal API tokens are refused
  (`interactive_only`).
* API tokens themselves are not subject to a second factor. Create them only after signing in with
  MFA, keep their lifetime short and scope them through the owner's roles (see
  [API tokens](api-tokens.md)).
* LDAP users are not enrolled in DBManager; enforce MFA in front of the directory or use OIDC.

## API reference

| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | Returns the user, or `{mfa_required: true, mfa_token, methods}` |
| `POST /auth/login/mfa` | `{mfa_token, code}` → session |
| `GET /me/mfa` | Status: available, enabled, recovery codes left, required, enrollment pending |
| `POST /me/mfa/totp/setup` | `{password}` → secret, `otpauth://` URI, QR code (SVG) |
| `POST /me/mfa/totp/enable` | `{code}` → recovery codes |
| `POST /me/mfa/totp/disable` | `{password, code}` |
| `POST /me/mfa/recovery-codes` | `{password, code}` → new recovery codes |
| `POST /users/{id}/mfa/reset` | Administrator reset (`users.manage`) |
