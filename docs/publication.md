# Publication checks

Public release name: **housing-decision-agent**. Do not change visibility on a repository whose working files or history contain private data. Produce a fresh export with no history, review it, then obtain the owner's separate approval before creating/pushing a public repository.

`PUBLISH_MANIFEST.txt` defines the allowed publication inputs. The export checker enforces that boundary, rejects unsafe paths/symlinks and unexpected payload files, scans the exact result, and records hashes for verification. Follow `python3 scripts/export_public.py --help` for the current export interface.

The local private denylist is supplied by `HOUSING_PRIVACY_DENYLIST` and must stay outside the repository. Require the dedicated secret-scanner gate before publication; do not treat an unavailable scanner as a pass. CI provides a later regression check, **not prevention of the first public disclosure**.

Manual review must also establish that:

- Names, locations, addresses, budgets, weights, constraints and other preferences in examples/tests are independently fictional—not copied from a private user profile.
- Neither the exported files nor packaged wheel/sdist include a private profile, secrets, old logs, backups, old Git history, or local deployment configuration.
- Every exception to automated scanning has a narrow reviewed scope and a reason. Never exempt secrets or a private denylist finding by an inline comment.
- The exact scanned artifact is the artifact eventually published. Re-export and rescan after edits.
- Optional region guidance is about the region, not about one resident's personal circumstances.

Scanners cannot classify every ordinary numeric JSON value or person name as public/private. A zero-finding scan is evidence of the checks performed, not a guarantee of privacy.
