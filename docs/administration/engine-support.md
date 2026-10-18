# Engine support

![Engine support](../assets/screenshots/engine-support.png)

**Platform → Configuration → Engine support** (`GET /api/v1/platform/engine-support`) is generated
from the adapters' capability declarations — nothing in the frontend hard-codes a status. Per engine
it shows the support level (supported / partial), the driver status, the real-engine **validation**
status and, per capability, the declared level.

* **Validated** — the real-engine suite (`scripts/integration-test`) passed against the version
  shown in *Validated against*. Results ship in `backend/app/data/real_engine_validation.json`.
* **FULL\*** — the adapter implements the capability fully, but it has not been proven by the
  real-engine suite for this engine.
* **External validation** — Db2 for z/OS, which cannot run in a container.

A registered server's own capabilities (after runtime discovery of privileges, configuration and
licences) are on its **Overview**. Meaning of every level: [Database engines](../database-engines/index.md).
To refresh validation, run the [real-engine integration tests](../operations/integration-tests.md).
