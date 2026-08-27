---
name: rollback
description: Reverse the last publish for this app, one version/release back. Use when the user asks to "roll back", "revert the deploy", or reports something broken right after a deploy. Thin wrapper around the shared logic in the infrastructure repo — see WEBAPP_PROJECT_STANDARD.md §9.
---

# Rollback

Thin wrapper — the actual logic lives once in
`infrastructure/scripts/rollback.sh`. Reverses the *last publish*, not the
last commit: for a container app it redeploys the previous image tag; for a
static app it flips the `current` symlink back one release. It does not
create a new git tag or touch CHANGELOG.md — a rollback is an operational
action, not a release.

## 1. Confirm before running

Same rule as deploy: **ask first, this reaches whoever uses the app** —
unless the user already told you something is actively broken right now, in
which case treat that as the go-ahead and move fast.

## 2. Roll back

```bash
bash "${INFRA_DIR:-/samba/projects/infrastructure}"/scripts/rollback.sh [local|public]
```

> **Path note:** `$INFRA_DIR` defaults to the infrastructure repo's checkout and is set for you by
> `deploy-common.sh`. Prefer it over a hardcoded absolute path — this file is committed to the app's
> own repo, and some of those repos are public (WEBAPP_PROJECT_STANDARD.md §1), so a literal server
> path would be published along with it. Set `INFRA_DIR` explicitly if the checkout ever moves.


Omit the target for Profile A (container) apps.

## 3. Report the result, and don't stop there

Tell the user what version/release it's on now. **A rollback undoes the
symptom, not the cause** — the broken version is still tagged and pushed, so
the next `/deploy` run would just reintroduce it. Say so explicitly, and
ask whether to look at what broke before anyone deploys again.
