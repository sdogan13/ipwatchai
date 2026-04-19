# Engineering Rules

Last updated: 2026-04-19
Status: Active

## Purpose

This file is the default operating spec for AI coding agents working in this repo.

Use it for:
- new features
- behavior or settings changes
- feature deletions
- schema or data changes
- test-harness changes
- infra or deployment changes

Follow these rules as instructions, not as optional advice.

## Required Reading

Before non-trivial work, read:
1. `rules.md`
2. `README.md`
3. the most relevant current technical doc in `docs/`
4. `test.md` if behavior, flows, personas, cleanup, or verification lanes are affected
5. a task-specific plan doc only when the current task actually has one

Current reference docs:
- `README.md`: setup and runtime guide
- `docs/API_REFERENCE.md`, `docs/DEPLOYMENT.md`, `docs/DATABASE_SCHEMA.md`, `docs/FILE_INDEX.md`: current technical references
- `test.md`: test strategy and verification guide
- `docs/DOCUMENTATION.md`: documentation map
- `docs/archive/`: historical project-specific trackers, not current source of truth

## Section 1: Mandatory Workflow

Use `DEFINE -> PLAN -> BUILD -> VERIFY -> REVIEW -> SHIP` for every non-trivial task. If one stage fails, go back instead of forcing progress.

### DEFINE `/define`

Clarify:
- current behavior
- intended behavior
- affected users, systems, or data
- current source of truth in code and docs

Do not move on until the scope is clear.

### PLAN `/plan`

Decide:
- whether the change belongs on `main` or a task branch
- the canonical implementation point
- which code, docs, and tests must move together
- how the change will be verified
- how the change can be rolled back cleanly

Do not move on until the change strategy is clear.

### BUILD `/build`

Implement the change:
- in the canonical place
- with coherent scope
- without shortcuts that only make the diff or tests look green

Do not move on until the code matches the intended behavior.

### VERIFY `/verify`

Run:
- the smallest meaningful proof first
- broader verification only when the changed surface requires it

Do not move on until the right level of behavior is actually proved or a real blocker is reported.

### REVIEW `/review`

Review:
- the staged diff
- the related docs
- cleanup behavior
- rollback path
- whether any temporary tricks were used to get green

Do not move on until the staged change is coherent, documented, and reversible.

### SHIP `/ship`

Commit and merge only:
- intended files
- from a clean worktree
- with an exact report of what changed and what was verified

## Section 2: Non-Negotiable Rules

### Implementation Rules

- Match the amount of process to the risk of the change.
- Define expected behavior before changing code.
- Prefer the canonical implementation point over quick fixes in multiple places.
- Optimize for correct behavior, not just a green test run.
- Fix root causes instead of masking failures with shortcuts.
- Keep one concern per commit.
- Keep `main` stable.

### Branch Rule

Use `main` only when all of these are true:
- the change is small
- the change is low risk
- the change is easy to undo
- the change does not touch data, infra, billing, permissions, or test-harness behavior

Use a task branch for everything else.

### Test Integrity Rule

Behavior changes must update the relevant tests in the same task.

Apply this rule:
- new feature: add or extend the relevant tests in the same task
- bug fix: add or extend a regression test in the same task
- refactor with no behavior change: existing tests should still pass without unnecessary rewrites
- harness or persona change: update the affected live, browser, nightly, or cleanup tests in the same task

Keep the changed behavior covered by the strongest practical test layer.
Prefer proving the real path over proving only a mocked path.

Do not:
- change code and leave test updates for later
- weaken assertions, delete coverage, add skips, or add `xfail` just to get green
- replace integration coverage with lighter mocked coverage unless equal or better proof exists elsewhere
- add test-only branches, hardcoded values, or bypass logic that hides real failures
- rewrite unrelated tests just to force green
- silently change acceptance criteria because the original path is harder to fix
- treat repeated flaky-test patching as a substitute for proper stabilization

When stabilizing tests, check for:
- shared-state leakage
- cleanup gaps
- timing or retry races
- environment drift
- hidden ordering dependencies
- stale fixtures or unrealistic mocks

If a changed test now passes for a different reason, explain whether the product behavior changed or the test was previously wrong.
If the honest fix is not complete, report the blocker and leave the task incomplete.

### Documentation And State Rule

Update the relevant docs whenever codebase behavior, setup, structure, or workflow changes.
If no doc update is needed, explicitly verify that the relevant docs still match the new behavior.

Clean up created data, temp files, and runtime artifacts in the same task.

For test-harness changes:
- keep tests idempotent
- prefer seeded or managed test accounts over fresh disposable accounts
- delete test data created during the run unless persistence is intentional
- verify that the new test flow does not leave junk state behind

### Blocker Rule

If the correct fix is unclear or blocked:
- identify the exact blocker, affected files, and impact
- separate known facts from guesses
- prefer clarification over high-risk assumptions
- do not edit unrelated code just to get past the blocker
- do not claim success when only part of the real path is fixed

### Verification Reporting Rule

Report verification honestly.

- Only report commands that were actually run.
- Distinguish verified behavior from inferred behavior.
- If a needed test could not be run, say so and explain why.
- Do not imply broader coverage than was actually executed.

### Git And Commit Rule

- Do not use blanket `git add .` on a mixed worktree.
- Stage by path or use `git add -p` for mixed files.
- Do not mix refactor, feature work, infra cleanup, and unrelated docs in one commit unless they are inseparable.
- Keep commits reviewable and reversible.
- Avoid `--no-verify` unless the hook is blocking an intentional, already-verified change; if used, document why.
- Do not merge with a dirty worktree.

## Section 3: Reference Checklists

### Documentation Sync Matrix

Before commit, run:

```powershell
git diff --name-only --cached
```

Then check the changed paths against this map:

- app behavior, routes, auth, search, UI flow:
  check `README.md`, `docs/API_REFERENCE.md`, and `test.md` if tests or flows changed
- env vars, compose files, ports, runtime setup, deployment path:
  check `README.md` and `docs/DEPLOYMENT.md`
- schema, migrations, data lifecycle, cleanup rules:
  check `docs/DATABASE_SCHEMA.md`
- entrypoints, directory layout, repo structure:
  check `README.md` and `docs/FILE_INDEX.md`
- workflow or process changes:
  check `rules.md` and, if needed, `docs/DOCUMENTATION.md`
- active tracked project or multi-batch cleanup:
  check the task-specific tracker if one exists

### Testing Depth Guide

Run the smallest useful test set first, then widen only if the change affects broader behavior.

- logic-only change: targeted unit or service tests
- route, auth, response shape, or validation change: API integration tests
- template, static, JS, browser flow, or mounted UI change: browser and/or live smoke
- stateful, destructive, or long-running flow change: nightly or deeper E2E coverage
- test-harness change: prove both correctness and cleanup behavior

If a change touches user-facing behavior, permissions, plans, uploads, DB-backed flows, or cleanup logic, do not stop at `py_compile`.

### Change-Type Checklist

- new feature:
  define the user, entrypoint, permissions, and expected outcome first; add the smallest complete version first; cover the allowed path, denied path, and invalid-input path
- behavior or settings change:
  write down the old behavior and the new behavior; search the repo for all reads and writes; keep defaults safe; validate config early
- feature deletion:
  prove the feature is intentionally retired; search code, templates, JS, tests, docs, scripts, env vars, and deploy files; remove references in one coherent pass
- schema or data change:
  prefer forward-safe migrations; think through foreign keys, backfills, idempotency, quotas, and rollback; audit destructive operations
- repo structure or module-boundary change:
  keep entrypoints coherent and update structure docs
- test-harness change:
  prove both correctness and cleanup behavior

## Done Gate

A task is done only when:
- the expected behavior is clear
- the code was changed in the right place
- the affected tests passed at the right depth
- created data and temp artifacts were cleaned up
- relevant docs were updated
- the relevant docs were checked even if no update was needed
- the branch or worktree is clean
- the rollback path is understood

Do not mark a task complete if:
- the changed behavior is not covered by the right level of tests
- the relevant docs were not checked
- setup, API, schema, or workflow changes were made without checking the matching reference docs
- created state or temp artifacts were left behind unintentionally
- the pass was achieved by weaker assertions, broader mocks, skips, bypass logic, or other temporary tricks
