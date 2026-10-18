# Simulation

A simulated fleet lets you explore every page without real databases. It is generated on request by
a Platform Administrator (**Platform → Simulation**), never at startup.

* Simulated servers are marked *Simulated* everywhere (badge, hatched tiles, banner on the server
  page) and never open a network connection; SQL against them is never executed.
* Their metrics, queries, findings and topology are generated from a profile and seed, including
  scenarios such as outages, replication lag and blocking.
* Fleet pages show a source filter (*Real / Simulated / All*).

Generating, regenerating and removing: [Simulation administration](../administration/simulation.md).
