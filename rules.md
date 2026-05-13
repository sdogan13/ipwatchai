# Engineering Rules

Last updated: 2026-05-08
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

## Section 2: Non-Negotiable Rules

### Implementation Rules

- Match the amount of process to the risk of the change.
- Define expected behavior before changing code.
- Prefer the canonical implementation point over quick fixes in multiple places.
- Optimize for correct behavior, not just a green test run.
- Fix root causes instead of masking failures with shortcuts.
- Keep `main` stable.

#### Localization / Internationalization Rule

All new or changed user-facing text in the app must be fully localizable.

Apply this rule:
- do not hardcode user-facing strings directly in components, templates, pages, or client logic
- every new or updated visible text string must be added through the app’s translation/i18n system
- every new or updated text string must include translations for all supported app languages in the same task:
  - English
  - Turkish
  - Arabic
- Arabic translations must be verified for correct right-to-left display and layout impact where relevant
- placeholder text, validation messages, error messages, empty states, tooltips, labels, button text, banners, dialogs, and notifications are all included
- if text is added but translations are missing, the task is not complete

Do not:
- merge UI text changes with only one language added
- leave temporary English-only copy in the codebase
- bypass the translation system for speed or convenience

Verification:
- confirm the new text appears correctly in English, Turkish, and Arabic
- confirm there are no missing translation keys or fallback-only strings for the new text
- verify layout and readability for Arabic where the changed UI is affected


### Branch Rule

**All work happens on `main`. Do not create new branches or worktrees.**

This rule supersedes the older split-by-risk policy. Multiple parallel agents creating their own branches led to deep divergence and hours of reconciliation work. From now on:

- Always work on `main`. Never run `git checkout -b`, `git switch -c`, `git branch <new-name>`, or `git worktree add`.
- Never switch to a non-main branch via `git checkout <branch>` or `git switch <branch>`.
- Allowed: `git checkout main`, file-level `git checkout -- <file>` or `git checkout HEAD -- <file>`, `git branch -d` / `-D` (delete merged branches), all read-only inspection (`git branch`, `git branch -v`, `git worktree list`).
- Commit small, focused changes directly to `main`. If a change is too risky for a direct commit, surface that concern in chat and let the human decide — do not branch unilaterally.
- Two enforcement layers are wired up in the repo, both currently **dormant** until the parallel MiniLM/color refactor finishes:
  - **Model-side** — `.claude/hooks/block-branch-mess.sh` (referenced from `.claude/settings.json` PreToolUse hook). Blocks Claude Code's Bash tool calls that match branch-creation or non-main checkout patterns.
  - **Git-side** — `.githooks/` (reference-transaction + pre-commit + post-checkout). Once activated, blocks branch creation and non-main commits for every git client (humans, other AI tools, IDE integrations).
- Activate after the MiniLM refactor lands its final commit. See [`.githooks/README.md`](.githooks/README.md) for the exact activation command. Activating now would block the in-flight `feat/remove-trademark-minilm-color` commits.
- Don't try to bypass either layer once they're active. If the policy is genuinely wrong for a specific task, raise it in chat first.

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

### Document Verification Rule

When work involves parsing or extracting data from a document (PDF, scan, image, or any visual artifact), do not rely on text extraction alone. Visually open and inspect the source before designing or finalizing extraction logic.

Apply this rule:
- before writing or modifying a parser for a document, render representative pages and inspect them visually: at minimum the legend or table of contents, the first record of each detected section, and at least one edge-case page
- when the document ships a code legend, INID table, field index, or similar spec page, read that page first; do not infer field semantics from sample data alone
- when a parser branch decides a section is "skip" or "out of scope", render and inspect those pages before agreeing — what looks like a header in extracted text may be substantive content visually
- when text extraction returns mojibake, replacement characters, or empty fields, render the page visually before attributing the issue to the source
- before signing off on extraction quality, visually spot-check at least one parsed record per detected section against its source page

Do not:
- claim "all codes or fields extracted correctly" based on pattern-matching frequency or sample agreement alone
- declare data quality meets expectations without visual verification on a real artifact
- treat regex pattern coverage as a substitute for spec coverage

Verification:
- include rendered page references in the parser plan and in the verification report
- when reporting extraction completeness for a new document type or schema, attach or cite visual confirmation of representative pages

### Verification Reporting Rule

Report verification honestly.

- Only report commands that were actually run.
- Distinguish verified behavior from inferred behavior.
- If a needed test could not be run, say so and explain why.
- Do not imply broader coverage than was actually executed.

## Section 3: Reference Checklists

### Documentation Sync Matrix

Check the changed paths against this map:

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
- all new or changed user-facing text is available in English, Turkish, and Arabic

Do not mark a task complete if:
- the changed behavior is not covered by the right level of tests
- the relevant docs were not checked
- setup, API, schema, or workflow changes were made without checking the matching reference docs
- created state or temp artifacts were left behind unintentionally
- the pass was achieved by weaker assertions, broader mocks, skips, bypass logic, or other temporary tricks

# Section 4 Coding Rules

1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

5. YOUR CODE WILL BE REVIEWED BY "CLAUDE CODE" ONCE YOU ARE DONE!