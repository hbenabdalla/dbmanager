# Generate a demo environment

To explore DBManager without connecting real databases, a Platform Administrator can generate a
**simulated fleet** under **Platform → Configuration → Simulation** (permission `simulation.generate`;
removal needs `simulation.delete`):

1. choose a profile (`small`, `standard`, `large`) and optionally a seed (same seed ⇒ same estate);
2. **Generate simulated data**. Progress is shown; the fleet appears on the dashboard when finished.

Simulated servers carry a *Simulated* badge and a hatched pattern, never open a network
connection, and their SQL is never executed. When real servers exist, fleet views default to the
real ones; the source filter (*Real / Simulated / All*) switches.

Remove the simulation with **Platform → Configuration → Simulation → Remove simulated data** (a preview lists what will be
deleted; real servers are never touched) or from the CLI:

```bash
docker compose exec backend python -m app.cli simulation status
docker compose exec backend python -m app.cli simulation cleanup --dry-run
```

Details: [Simulation](../administration/simulation.md).
