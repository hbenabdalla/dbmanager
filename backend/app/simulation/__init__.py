"""Simulated demo fleet: generation, status and removal.

Layers (outer to inner):

* ``app.api.v1.simulation``  — HTTP endpoints, permission checks, audit of requests
* :mod:`app.simulation.service`    — lifecycle (generate / regenerate / remove), progress, audit
* :mod:`app.simulation.generators` — planning the estate and producing history
* :mod:`app.simulation.repository` — counting and deleting simulated rows

Simulated rows are identified by ``data_source = 'simulated'`` on servers, sites, clusters and
applications; everything else a simulated server owns (databases, metrics, query statistics,
findings, recommendations, tags, jobs) hangs off the server row and is removed with it.
"""
