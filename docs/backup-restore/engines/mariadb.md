# MariaDB

Same as [MySQL](mysql.md), with MariaDB's own tools: `mariadb-dump --single-transaction --routines
--triggers --events --hex-blob --no-tablespaces --default-character-set=utf8mb4` and the `mariadb`
client for restores (MariaDB TLS options `ssl` / `ssl-verify-server-cert` / `skip-ssl`). The
qualified-definition safety check applies too.

MariaDB enforces the same binary-logging rule for triggers and routines as MySQL (error 1419; see
[MySQL](mysql.md)); the official MariaDB image runs without binary logging.

Validated against MariaDB 11.4 (Docker) and 10.11 (with and without binary logging) (round trip including a trigger and UTF-8 data, refusal of
qualified definitions under another name). Not managed: `mariabackup`, binlog point-in-time
recovery, users and grants.
