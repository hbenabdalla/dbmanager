# Release process

## Branches and pull requests

* `main` is always releasable; CI must be green. Branch protection (required reviews, required
  checks `Backend lint`, `Backend tests`, `Frontend build`, `Documentation build`,
  `Secret scan`, no force pushes) is described in `CONTRIBUTING.md` → *Branch protection*.
* Work happens on short-lived branches from `main` (`feat/…`, `fix/…`, `docs/…`) and is merged
  through pull requests with review; commit messages follow Conventional Commits
  (see `CONTRIBUTING.md`).

## Versions and tags

DBManager uses [Semantic Versioning](https://semver.org/); before 1.0, minor versions may contain
breaking changes, listed in `CHANGELOG.md` with migration notes. A release is an annotated tag
`vX.Y.Z` on `main`:

1. Complete the [release checklist](../product/release-checklist.md).
2. Move the `Unreleased` section of `CHANGELOG.md` to the new version and date.
3. Tag and push:

    ```bash
    git tag -a vX.Y.Z -m "DBManager vX.Y.Z"
    git push origin vX.Y.Z
    ```

4. `ci-docker.yml` builds the images for `v*` tags. Build metadata is passed to the backend image
   as build arguments `DBM_BUILD_VERSION`, `DBM_GIT_COMMIT` and `DBM_BUILD_DATE` and exposed by
   the backend.

No version has been tagged yet.

## Documentation versions on Read the Docs

Read the Docs builds the site from `.readthedocs.yaml`:

| Read the Docs version | Built from |
|---|---|
| `latest` | the default branch (`main`) — documentation of unreleased work |
| `stable` | the highest `vX.Y.Z` tag — documentation of the current release |
| `vX.Y.Z` | each release tag, once activated in the Read the Docs project settings |

The project is `db-manager` on Read the Docs: [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/). Pull-request previews can
be enabled in the project settings. Until the first tag exists, `stable` points to `latest`. The
application links to `latest` by default; installations can point `DBM_DOCUMENTATION_URL` at
another copy or set it to `off`.
