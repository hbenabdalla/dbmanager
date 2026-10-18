# Documentation

The documentation is a [MkDocs](https://www.mkdocs.org/) site with the Material theme, configured
in `mkdocs.yml` at the repository root and published by Read the Docs (`.readthedocs.yaml`).

```bash
python -m venv .venv-docs && . .venv-docs/bin/activate
pip install -r docs/requirements.txt
mkdocs serve            # http://127.0.0.1:8000, live reload
mkdocs build --strict   # what CI and Read the Docs run
```

`--strict` turns every warning into an error: broken links, missing anchors, pages not in `nav`.
The `ci-docs.yml` workflow runs it on every pull request that touches the docs, and
`.readthedocs.yaml` sets `fail_on_warning: true`.

## Conventions

* One audience per folder (`getting-started/`, `user-guide/`, `administration/`, …); add every new
  page to `nav` in `mkdocs.yml`.
* Use relative links to `.md` files (`../monitoring/health.md#…`), never to the published URL.
* Diagrams are Mermaid fenced blocks (` ```mermaid `); notes use admonitions (`!!! note`);
  alternatives use content tabs (`=== "Docker Compose"`).
* Screenshots live in `docs/assets/screenshots/`, are real captures of the application (simulated
  fleet allowed, never production data or secrets) and are kept small (quantized PNG).
* Describe only what the code does. Planned work belongs in the [roadmap](../product/roadmap.md),
  and capabilities that are not validated against a real engine are not described as validated.
* `docs/openapi.json` is a static export of the API schema served alongside the site.
