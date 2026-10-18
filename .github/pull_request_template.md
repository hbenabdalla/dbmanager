## Summary

<!-- What does this change and why? Link the issue / roadmap sprint it implements. -->

## Checklist

- [ ] **Tests** — added or updated; `pytest tests/unit tests/api` and `npm test` pass; no new unexplained skips
- [ ] **Documentation** — user/admin/developer docs updated; `mkdocs build --strict` passes
- [ ] **Security** — no secrets in code, logs, API responses or audit events; input validated; RBAC enforced on new endpoints
- [ ] **Migrations** — Alembic migration included and tested on an existing database (or: no schema change)
- [ ] **API compatibility** — no breaking change to `/api/v1`, or the break is documented in CHANGELOG.md
- [ ] **Database compatibility** — engine versions affected listed; real-engine suite run for changed adapter SQL (paste `real-engine-matrix.md`)
- [ ] **Screenshots** — before/after for UI changes, light and dark mode
- [ ] **CHANGELOG.md** — entry under *Unreleased*

## Notes for reviewers

<!-- Anything that needs special attention, follow-ups, known limitations. -->
