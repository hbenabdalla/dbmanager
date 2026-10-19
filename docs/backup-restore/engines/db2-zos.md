# Db2 for z/OS

**External tool required — not implemented in DB Manager.** Db2 for z/OS is backed up with z/OS
utilities (`COPY` / `RECOVER`, DFSMSdss, `BACKUP SYSTEM`) under your site's job scheduler. DB
Manager neither runs them nor records their results. The server's *registration* is still protected
by [platform backups](../platform-backup.md) (inventory component).
