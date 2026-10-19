# MariaDB

Same as [MySQL](mysql.md), with MariaDB's own tools: `mariadb-dump --single-transaction --routines
--triggers --events --hex-blob --no-tablespaces --default-character-set=utf8mb4` and the `mariadb`
client for restores (MariaDB TLS options `ssl` / `ssl-verify-server-cert` / `skip-ssl`). The
qualified-definition safety check applies too.

Validated against MariaDB 10.11 (round trip including a trigger and UTF-8 data, refusal of
qualified definitions under another name). Not managed: `mariabackup`, binlog point-in-time
recovery, users and grants.
