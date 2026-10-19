# LDAP / Active Directory

Group mapping and the sign-in flow are described in [Identity providers](identity-providers.md).


| Config key | Example |
|---|---|
| `server_uri` | `ldaps://dc01.corp.example:636` (or `ldap://…` with `start_tls: true`) |
| `verify_cert`, `ca_pem` | `true`, PEM of your internal CA |
| `bind_dn` | `CN=svc-dbmanager,OU=Service Accounts,DC=corp,DC=example` |
| `user_base_dn` | `OU=Users,DC=corp,DC=example` |
| `user_filter` | `(&(objectClass=user)(sAMAccountName={username}))` |
| `attr_username`, `attr_email`, `attr_display_name` | `sAMAccountName`, `mail`, `displayName` |
| `group_attribute` | `memberOf` |
| `group_search_base`, `group_filter` | optional, for directories without `memberOf` (`(member={user_dn})`) |
| `auto_provision` | `true` |
| `default_group_id` | optional platform group every directory user joins |

Flow: service bind → search one user entry (escaped filter) → bind as that DN with the supplied
password → read groups → provision. Unknown users get the same generic error as wrong passwords.
For OpenLDAP use `uid` and `(&(objectClass=inetOrgPerson)(uid={username}))`.

**The service account must be allowed to read users and groups.** With access rules that let an
account read only its own entry (the default of several OpenLDAP images), the service bind still
succeeds but every user search comes back empty, so sign-ins fail with the generic error. The
staged test reports this at the *user search* stage. In OpenLDAP grant read access, for example:

```ldif
olcAccess: {1}to * by self read by dn="cn=admin,dc=example,dc=org" write
  by dn="uid=svc-dbm,ou=people,dc=example,dc=org" read by * none
```

In Active Directory, authenticated users can read users and groups by default.

A group found both through `memberOf` and through the group search is counted once.


## Staged test

**Test** in the wizard (or on a saved provider) runs DNS → TCP → TLS → service bind → user search →
group lookup → user bind and reports the first failing stage. Plain `ldap://` without StartTLS fails
at the TLS stage unless `allow_insecure` is set (labs only).
