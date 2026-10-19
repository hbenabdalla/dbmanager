# Encryption

Backups are encrypted with [age](https://age-encryption.org) (format v1) through the `pyrage`
library. DBManager implements no cryptography of its own. The backup record shows whether a
backup is encrypted, the algorithm, the (public) recipient, the creation time and the backup and
application versions — never a key or passphrase.

## Modes

**age recipient (recommended).** The archive is encrypted to an X25519 public key (`age1…`).
Only the holder of the matching private key (`AGE-SECRET-KEY-1…`) can decrypt it. **Generate key
pair** in the policy or backup wizard creates one; the private key is shown **once** and is not
stored by DBManager. Keep it in a password manager or vault, away from the backup storage. DB
Manager needs it only to verify contents or to restore — paste it for that run, or point
`DBM_BACKUP_IDENTITY_FILE` at a file holding it.

**Passphrase.** The archive is encrypted (streaming) to a fresh X25519 key generated for this
backup; that key is itself encrypted with the passphrase using age's scrypt recipient and stored
next to the archive as `key.age`. Passphrases have at least 12 characters. Scheduled backups read
the passphrase from a reference (`env:NAME` or `file:/path`, same rules as credential references),
so it is never stored in DBManager's database. A passphrase given for a manual backup is kept
sealed only until the job ends.

**None.** Possible, with a warning: anyone who can read the storage can read the backup (password
hashes, configuration, database contents). Credentials remain encrypted with the master key.

## Decrypting without DBManager

Both formats are standard age files:

```bash
# recipient mode
age -d -i identity.txt -o archive.tar archive.tar.age

# passphrase mode
age -d -o identity.txt key.age          # asks for the passphrase
age -d -i identity.txt -o archive.tar archive.tar.age

tar -tf archive.tar                     # manifest.json, tables/*.jsonl, metrics/…
```

## Master key dependency

Platform backups contain DBManager's secrets **as stored**: credentials, identity-provider and
channel secrets, S3 secret keys and TOTP seeds stay envelope-encrypted with `DBM_MASTER_KEY`. The
manifest lists the key ids they need (`key_ids`). **The master key is never written into a
backup.** To restore those secrets, the installation must have the same key as
`DBM_MASTER_KEY` or list it in `DBM_PREVIOUS_MASTER_KEYS`; the restore wizard reports missing key
ids. Back up the master key separately (for example in your vault), never next to the backups.

Keys, identities and passphrases are never logged, never included in audit events and never
returned by the API.
