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

Start here before non-trivial work:
- read `rules.md`
- read `README.md`
- read the most relevant current technical docs in `docs/`
- read `test.md` if behavior, flows, personas, or cleanup are affected
- read a task-specific plan doc only when the current task actually has one

This file is the repo-wide workflow reference.
- `README.md` is the setup and runtime guide
- `docs/API_REFERENCE.md`, `docs/DEPLOYMENT.md`, `docs/DATABASE_SCHEMA.md`, and `docs/FILE_INDEX.md` are the current technical references
- `test.md` is the test strategy and verification guide
- `docs/DOCUMENTATION.md` is the documentation map
- `docs/archive/` holds historical project-specific trackers

## Canonical Execution Flow

Use this sequence for every non-trivial task. Do not skip stages.

1. DEFINE `/define`
   Clarify the current behavior, the intended behavior, the affected users or systems, and the current source of truth in docs and code.
   Do not move on until the scope is clear.

2. PLAN `/plan`
   Choose `main` or a task branch, find the canonical implementation point, list the code, docs, and tests that must move together, and decide how the change will be verified and rolled back.
   Do not move on until the change strategy is clear.

3. BUILD `/build`
   Implement the change in the canonical place, keep the scope coherent, and avoid shortcuts that only make the diff or tests look clean.
   Do not move on until the code matches the intended behavior.

4. VERIFY `/verify`
   Run the smallest meaningful verification first, then widen when the changed surface requires more proof.
   Do not move on until the right level of behavior is actually proved or a real blocker is reported.

5. REVIEW `/review`
   Review the staged diff, related docs, cleanup behavior, and rollback path. Check that no temporary tricks were used to get green.
   Do not move on until the staged change is coherent, documented, and reversible.

6. SHIP `/ship`
   Commit and merge only intended files from a clean worktree, then report exactly what changed and what was verified.

If a stage fails, go back to the previous stage instead of forcing progress.

## Startup Gate

Before entering `BUILD` on a non-trivial task, be able to answer:

1. What exactly is changing?
2. Who or what can be affected?
3. Which docs and code define the current behavior?
4. Where is the canonical place to implement the fix or feature?
5. Which tests and docs must move with the change?
6. Does this belong on `main` or a task branch?

## Core Rules

- Match the amount of process to the risk of the change.
- Define expected behavior before changing code.
- Prefer the canonical implementation point over quick fixes in multiple places.
- Optimize for correct behavior, not just a green test run.
- Fix root causes instead of masking failures with shortcuts.
- Keep one concern per commit.
- Test the affected behavior before merge.
- Clean up created data, temp files, and runtime artifacts in the same task.
- Keep `main` stable.

## Branch Rule

Use `main` only when all of these are true:
- the change is small
- the change is low risk
- the change is easy to undo
- the change does not touch data, infra, billing, permissions, or test harness behavior

Use a task branch for everything else.

Default:
- small docs-only tweak: `main` is acceptable
- anything non-trivial: use a task branch

## Default Workflow

Follow `DEFINE -> PLAN -> BUILD -> VERIFY -> REVIEW -> SHIP` in order.

Practical rule:
- do not start `BUILD` before the problem and implementation point are clear
- do not start `SHIP` before verification, cleanup, docs, and staged-diff review are complete
- if a stage exposes unclear requirements, conflicting docs, or incomplete proof, stop and resolve that before moving forward

For AI agents, this means:
- do not jump straight to editing without reading the relevant code and docs first
- do not assume tests or docs can be deferred to a later cleanup task
- do not treat archived docs as the current source of truth
- do not weaken tests, widen mocks, or silently reduce scope just to get green
- if the honest fix is blocked, surface the blocker instead of papering over it

## Blocker Rule

If the correct fix is unclear or blocked:

- identify the exact blocker, affected files, and impact
- separate known facts from guesses
- prefer asking for clarification over making high-risk assumptions
- do not edit unrelated code just to get past the blocker
- do not claim success when only part of the real path is fixed

## Documentation Sync Rule

Do not update every `.md` file after every tiny internal edit.
Do update the relevant docs whenever the codebase behavior, setup, structure, or workflow changes.

Before commit, run:

```powershell
git diff --name-only --cached
```

Then check the changed paths against this map:

- app behavior, routes, auth, search, UI flow:
  update `README.md`, `docs/API_REFERENCE.md`, and `test.md` if tests or flows changed
- env vars, compose files, ports, runtime setup, deployment path:
  update `README.md` and `docs/DEPLOYMENT.md`
- schema, migrations, data lifecycle, cleanup rules:
  update `docs/DATABASE_SCHEMA.md`
- entrypoints, directory layout, repo structure:
  update `README.md` and `docs/FILE_INDEX.md`
- workflow or process changes:
  update `rules.md` and, if needed, `docs/DOCUMENTATION.md`
- active tracked project or multi-batch cleanup:
  update the task-specific tracker if one exists

## Sync Matrix

Use this as the on-the-go checklist for files that commonly need to move together.

- user-visible behavior or runtime flow change:
  check `README.md`, `docs/API_REFERENCE.md`, and the relevant tests
- route, auth, permission, billing, search, report, or watchlist change:
  check `docs/API_REFERENCE.md`, `test.md`, `tests/test_api_endpoints.py`, and the relevant live or browser suites
- env var or settings change:
  check `config/settings.py`, `.env.production.example`, `README.md`, and `docs/DEPLOYMENT.md`
- Docker, ports, service topology, or deployment path change:
  check `docker-compose.yml`, `deploy/docker-compose.prod.yml`, `README.md`, and `docs/DEPLOYMENT.md`
- schema, migration, cleanup, or data lifecycle change:
  check `deploy/schema.sql`, `migrations/`, `docs/DATABASE_SCHEMA.md`, and the relevant tests
- repo structure, entrypoint, or module-boundary change:
  check `README.md` and `docs/FILE_INDEX.md`
- workflow or engineering process change:
  check `rules.md` and `docs/DOCUMENTATION.md`
- test strategy, test personas, cleanup behavior, or verification lane change:
  check `test.md` and the affected test files
- active tracked refactor or multi-batch cleanup change:
  check the task-specific tracker if one exists

Default reading order before non-trivial work:
1. `rules.md`
2. `README.md`
3. the most relevant technical doc in `docs/`
4. `test.md` if behavior, flows, personas, or cleanup are affected
5. a task-specific plan doc only when the current task actually has one

If no doc update is needed, explicitly verify that the relevant docs still match the new behavior.

## Change Checklists

### New Feature

- Define the user, entrypoint, permissions, and expected outcome first.
- Add the smallest complete version before adding edge improvements.
- Cover the allowed path, denied path, and invalid-input path.
- Add docs for any new config, env vars, or operational steps.

### Behavior Or Settings Change

- Write down the old behavior and the new behavior.
- Search the repo for every place that reads or writes the setting.
- Keep defaults safe.
- Validate config early and fail loudly on invalid values.

### Feature Deletion

- Prove the feature is actually unused or intentionally retired.
- Search code, templates, JS, tests, docs, scripts, env vars, and deploy files before deleting.
- Remove references in one coherent pass so the repo does not keep dead routes, broken buttons, or orphan config.
- Have a cleanup or migration plan if data is involved.

### Schema Or Data Change

- Prefer forward-safe migrations over clever migrations.
- Think through foreign keys, backfills, idempotency, quotas, and rollback.
- Audit destructive operations before running them.
- Never treat cleanup as an afterthought.

### Test-Harness Change

- Keep tests idempotent.
- Prefer seeded or managed test accounts over fresh disposable accounts.
- Delete test data created during the run unless persistence is intentional.
- Verify that the new test flow does not leave junk state behind.

## Test Sync Rule

Behavior changes must update the relevant tests in the same task.

Apply this rule:
- new feature: add or extend the relevant tests in the same task
- bug fix: add or extend a regression test in the same task
- refactor with no behavior change: existing tests should still pass without unnecessary rewrites
- harness or persona change: update the affected live, browser, nightly, or cleanup tests in the same task

Do not:
- change code and leave test updates for later
- rewrite unrelated tests just to force green
- treat repeated flaky-test patching as a substitute for proper stabilization

## Robustness Rule

AI agents must not trade correctness for a passing test run.

Apply this rule:
- fix the root cause in production code, the harness, test data, fixtures, or cleanup
- keep the changed behavior covered by the strongest practical test layer
- prefer proving the real path over proving only a mocked path

Do not:
- weaken assertions, delete coverage, add skips, or add `xfail` just to get green
- replace integration coverage with lighter mocked coverage unless equal or better proof exists elsewhere
- add test-only branches, hardcoded values, or bypass logic that hides real failures
- silently change acceptance criteria because the original path is harder to fix
- mark a flaky test as acceptable without addressing the source of nondeterminism

When stabilizing tests, look for:
- shared-state leakage
- cleanup gaps
- timing or retry races
- environment drift
- hidden ordering dependencies
- stale fixtures or unrealistic mocks

If the honest fix is not complete, report the blocker and leave the task incomplete.

## Testing Expectations

Run the smallest useful test set first, then widen only if the change affects broader behavior.

- logic-only change: targeted unit or service tests
- route, auth, response-shape, or validation change: API integration tests
- template, static, JS, browser flow, or mounted UI change: browser and/or live smoke
- stateful, destructive, or long-running flow change: nightly or deeper E2E coverage
- test-harness change: prove both correctness and cleanup behavior

If a change touches user-facing behavior, permissions, plans, uploads, DB-backed flows, or cleanup logic, do not stop at `py_compile`.
If a changed test now passes for a different reason, explain whether the product behavior changed or the test was previously wrong.

## Verification Reporting Rule

Report verification honestly.

- Only report commands that were actually run.
- Distinguish verified behavior from inferred behavior.
- If a needed test could not be run, say so and explain why.
- Do not imply broader coverage than was actually executed.

## Git And Commit Rules

- Do not use blanket `git add .` on a mixed worktree.
- Stage by path or use `git add -p` for mixed files.
- Do not mix refactor, feature work, infra cleanup, and unrelated docs in one commit unless they are inseparable.
- Keep commits reviewable and reversible.
- Avoid `--no-verify` unless the hook is blocking an intentional, already-verified change; if used, document why.
- Do not merge with a dirty worktree.

## Definition Of Done

A change is done when:
- the expected behavior is clear
- the code was changed in the right place
- the affected tests passed at the right depth
- created data and temp artifacts were cleaned up
- relevant docs were updated
- the relevant docs were checked even if no update was needed
- the branch or worktree is clean
- the rollback path is understood

For AI agents, do not mark a task complete if any of the following is still true:
- the changed behavior is not covered by the right level of tests
- the relevant docs were not checked
- setup, API, schema, or workflow changes were made without checking the matching reference docs
- created state or temp artifacts were left behind unintentionally
- the pass was achieved by weaker assertions, broader mocks, skips, bypass logic, or other temporary tricks

## Quick Decision Rule

Before making a change, ask:

1. What exactly is changing?
2. Who can be affected?
3. What is the safest place to implement it?
4. What is the smallest test set that proves it?
5. If it fails, how do we undo it cleanly?
