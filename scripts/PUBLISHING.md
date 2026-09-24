# Publication gate (local, before any push)

**Never publish this working tree or its Git history.** It may contain private legacy
files outside the public manifest. Export only the selected files into a NEW directory
outside the checkout. The export command does not initialize Git, create a remote,
commit, push, or upload anything.

## Local workflow

1. Review `PUBLISH_MANIFEST.txt`: one exact relative regular file per line, including
   the manifest itself. No directory entries, globbing, traversal, symlinks, submodules,
   or untracked files. Add newly reviewed files explicitly to the manifest and Git index.
   Never automatically append every tracked file from a mixed private source checkout.
2. Review provenance of every public fixture and example. Category IDs can stay stable
   for compatibility; values and schedules must be independently fictional, not lightly
   renamed personal data. Numeric preferences, budgets, coordinates, identifiers, and
   generic names are **manual provenance review**, not something regex can certify.
3. Set `HOUSING_PRIVACY_DENYLIST` to a locally maintained policy file outside the repo
   (one regex per non-comment line). Do not commit its contents or its machine-specific
   path. A missing/unreadable configured file or invalid regex fails the gate. When the
   variable is absent, the receipt explicitly records `local_denylist: false`; that is
   not evidence that personal data was checked against local policy.
4. Install gitleaks. Linux x64 has a pinned, SHA256-verified installer:

   ```sh
   python scripts/install_gitleaks.py /tmp/housing-tools/gitleaks
   export PATH="/tmp/housing-tools:$PATH"
   gitleaks version
   ```

   The output file must be new. The installer pins upstream **8.24.3**, verifies the
   archive against a checksum stored in source, and reads only the regular executable
   member; it never extracts archive paths. Other platforms need a separately verified
   gitleaks install on PATH. Pin/check any upgrade deliberately.
5. Build the export, with the dedicated secret scanner required:

   ```sh
   python scripts/prepublish.py /tmp/housing-public-export --require-secrets
   ```

   Both that directory and `/tmp/housing-public-export.sha256` must be new. The export
   must be outside the source checkout. Every selected file is copied and hashed,
   including `PUBLISH_MANIFEST.txt`. Privacy scanning runs on those **exported bytes**;
   gitleaks then scans the same directory with explicit built-in rules, an empty ignore
   file, and inline allow comments disabled. Any failure removes this run's export and
   checksum sidecar. Existing destinations are never merged, overwritten, or removed.
   Without `--require-secrets`, an unavailable gitleaks is explicitly reported as not
   run; that mode is for development, **not publication approval**.
6. Independently verify the checksum sidecar before using the export:

   ```sh
   python scripts/prepublish.py /tmp/housing-public-export --verify /tmp/housing-public-export.sha256
   (cd /tmp/housing-public-export && sha256sum --check ../housing-public-export.sha256)
   ```

   The Python verifier also rejects extra files/directories and symlinks. The sidecar
   sits outside the export so the export is exactly the manifest inventory; a digest
   file cannot hash itself. Keep the sidecar as local audit evidence, not an extra file
   inside the public tree. Hashes detect changes against that receipt, not authenticity
   if an attacker can replace both data and receipt. Stop writes to the source while
   exporting, and do not modify the export after verification.

No automated success replaces human review or authorizes publication. Any later public
repository must start independently from the reviewed export, **not from this history**.

## Scanner behavior and review exceptions

`python scripts/privacy_scan.py` selects files exclusively from the manifest and requires
tracked regular files. Explicit paths scan every filename recursively with no extension
filter or cache-directory exclusion. `--all` scans all tracked files, including private
legacy files; do not use it to decide which legacy files to publish. On a clean public
checkout, `python scripts/privacy_scan.py --public-check` additionally rejects any tracked
or nonignored untracked path outside the manifest, even if the content looks harmless.
Run the strict check before tests/build tools generate non-source files.

Unreadable, missing, oversized (over 5 MB), and unsupported binary files fail closed.
Common street suffixes, numbered streets, and apartment/unit markers are covered, but
international addresses and generic names are not comprehensively detectable. No address
or name detection guarantee is made. Fictional addresses on Example Ave/Street are an
explicit built-in convention; this never exempts the line from other detectors.

Inline `privacy-scan: allow` has **no effect** anywhere. `EXCEPTIONS` in the scanner permits
only reviewed fictional `money_figure` matches by exact relative file, pattern, and SHA256
of the UTF-8 line (excluding its newline), with a reason. It cannot suppress a secret,
address, email, or local denylist match. Changing a line invalidates its exception.

For vetted vendor fonts/images only, `TRUSTED_BINARY_ASSETS` may contain entries keyed by
exact `app/static/vendor/...` path, each with `sha256` and a meaningful `reason` naming
upstream version/source/license. It starts empty. Check upstream provenance and bytes
before adding an entry. Wrong hashes and paths fail; printable strings still undergo
privacy/denylist scanning and gitleaks still runs. This is not a general binary bypass.
A newly vetted file also needs its own exact manifest entry.

## CI is defense in depth, not pre-leak protection

GitHub Actions runs **after a push**. By then exposed data may already be copied or cached.
CI cannot protect the first push and cannot access a private workstation policy by default.
The public-only CI first enforces the strict inventory and privacy scan, installs pinned
verified gitleaks (no token-requiring community action), and builds/scans/verifies an export
before tests or any downstream artifacts. The mixed private source intentionally fails
the public-only inventory check; never weaken it to make the private history pass.

## Optional local pre-push check

On a separately created, clean-history public checkout, wire these checks into an existing
local `.git/hooks/pre-push` hook if desired. Review your existing hook first; do not overwrite
another hook or change global Git configuration. Example hook body:

```sh
#!/bin/sh
set -eu
python scripts/privacy_scan.py --public-check
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
python scripts/prepublish.py "$scratch/export" --require-secrets
```

Make the hook executable locally. This is a current-tree guard, **not a history scanner**;
it does not approve older commits or replace the clean-export boundary. Hooks can be
bypassed and are not installed automatically. Do not use this hook as justification for
pushing the mixed private source branch or any unreviewed history.
