# Engineering Rules

Last updated: 2026-04-19
Status: Active

## Purpose

This file is the default workflow for making changes in this repo.

Use it for:
- new features
- behavior or settings changes
- feature deletions
- schema or data changes
- test-harness changes
- infra or deployment changes

This file is repo-wide.
- `project.md` is for a specific refactor or project plan.
- `test.md` is for test coverage strategy.
- `commit.md` is for one-off commit-splitting plans when needed.

## Core Rules

- Match the amount of process to the risk of the change.
- Define expected behavior before changing code.
- Prefer the canonical implementation point over quick fixes in multiple places.
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

1. Understand the current behavior.
2. Define what is changing and who is affected.
3. Decide whether the work belongs on `main` or a task branch.
4. Implement the change in small, coherent commits.
5. Run the narrowest tests that prove the change.
6. Run broader smoke when the affected surface justifies it.
7. Clean up created state and update docs if behavior or process changed.
8. Merge only when the worktree is clean, the change is verified, and rollback is clear.

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
  update `project.md` and/or `commit.md` when relevant

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
  check `project.md` and/or `commit.md`

Default reading order before non-trivial work:
1. `rules.md`
2. `README.md`
3. the most relevant technical doc in `docs/`
4. `test.md` if behavior, flows, personas, or cleanup are affected
5. `project.md` or `commit.md` only when the task belongs to a tracked larger effort

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

## Testing Expectations

Run the smallest useful test set first, then widen only if the change affects broader behavior.

- logic-only change: targeted unit or service tests
- route, auth, response-shape, or validation change: API integration tests
- template, static, JS, browser flow, or mounted UI change: browser and/or live smoke
- stateful, destructive, or long-running flow change: nightly or deeper E2E coverage
- test-harness change: prove both correctness and cleanup behavior

If a change touches user-facing behavior, permissions, plans, uploads, DB-backed flows, or cleanup logic, do not stop at `py_compile`.

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

## Quick Decision Rule

Before making a change, ask:

1. What exactly is changing?
2. Who can be affected?
3. What is the safest place to implement it?
4. What is the smallest test set that proves it?
5. If it fails, how do we undo it cleanly?
