# IP Watch AI Documentation Index

Last updated: 2026-04-19
Status: Current

## Purpose

This file explains which documentation is current, which files are project-specific, and which documents are historical snapshots or audits.

## Primary Current Docs

Start here first:
- `README.md`: setup, runtime, and test entrypoints
- `rules.md`: repo-wide engineering workflow
- `test.md`: current test strategy and verification lanes

Current technical references:
- `docs/API_REFERENCE.md`
- `docs/DATABASE_SCHEMA.md`
- `docs/DEPLOYMENT.md`
- `docs/FILE_INDEX.md`

## Project-Specific Docs

If a future task needs a project-specific plan or a one-off commit tracker, keep it task-specific and do not treat it as a repo-wide default doc.

## Historical Or Audit Docs

These files should be read as point-in-time analysis, plans, or audit notes:
- `docs/archive/reorg-project-plan.md`
- `docs/archive/reorg-commit-plan.md`
- `docs/CODE_AUDIT_REPORT.md`
- `docs/EVENTS_SYSTEM_PLAN.md`
- `docs/PHASE12_CLEANUP_INVENTORY.md`
- `docs/RESULTS_DISPLAY_AUDIT.md`

They may still be useful, but they are not guaranteed to reflect the latest codebase state.

## Practical Reading Order

Before making a change:
1. `rules.md`
2. `README.md`
3. the most relevant technical reference in `docs/`
4. `test.md` if the change affects behavior, flows, personas, or cleanup
5. a task-specific plan doc only if the current task actually has one

## Maintenance Rule

When the codebase changes materially:
- update the current docs above
- do not silently treat historical audit docs as authoritative
- prefer short, stable, high-level docs over long generated inventories that drift quickly
