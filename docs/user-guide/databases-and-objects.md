# Databases, schemas and objects

* **Databases** (server tab, or Operations → Administration → Databases) — name, size, owner,
  encoding/collation and engine-specific attributes (for example Query Store state on SQL Server).
  Selecting a database opens its schemas.
* **Schemas** — object counts per type; for Oracle a schema is a user, for Db2 a qualifier.
* **Objects** — tables, views, materialized views, indexes, sequences, functions, procedures,
  packages, triggers and synonyms where the engine has them. The detail view shows columns,
  constraints, indexes, triggers and the definition (DDL) when the monitoring account may read it.

Engine differences matter: PostgreSQL DDL is reconstructed from the catalog (PARTIAL), Oracle uses
`DBMS_METADATA`, SQL Server module definitions need `VIEW ANY DEFINITION`. The exact status per
engine is in the [support matrix](../database-engines/index.md).

Creating databases, schemas or indexes is an administration action — see
[Users and privileges](users-and-privileges.md#guarded-administration).
