# Initial bootstrap and first login

On its first start the backend:

1. applies the Alembic migrations to the control-plane database;
2. synchronises the built-in roles;
3. creates the first administrator **only if no user exists**, using
   `DBM_BOOTSTRAP_ADMIN_USERNAME` (default `admin`) and `DBM_BOOTSTRAP_ADMIN_PASSWORD`.

## Sign in

Open <http://localhost:8080>, sign in as `admin` with the password from `.env`. If the password was
generated or reset, you are asked to change it immediately (12+ characters, three character classes).

## Run the setup wizard

Administrators see a banner *DB Manager is not fully set up yet* until the
[Platform setup wizard](../user-guide/onboarding/platform-setup.md) is completed. It is resumable:
progress is saved after each step.

![Platform setup wizard](../assets/screenshots/setup-wizard.png)

The wizard covers the administrator password, organization name and time zone, sites,
environments, sign-in method (local, LDAP, OIDC), monitoring profile, metric retention and the
first database. Afterwards the **Setup checklist** on the dashboard shows what is still open.

## Lost the admin password?

```bash
docker compose exec backend python -m app.cli reset-password admin
```
