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

These are useful when a specific tracked effort is active:
- `project.md`
- `commit.md`

They are not general-purpose repo references.

## Historical Or Audit Docs

These files should be read as point-in-time analysis, plans, or audit notes:
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
5. `project.md` or `commit.md` only if the task is part of a tracked larger effort

## Maintenance Rule

When the codebase changes materially:
- update the current docs above
- do not silently treat historical audit docs as authoritative
- prefer short, stable, high-level docs over long generated inventories that drift quickly
