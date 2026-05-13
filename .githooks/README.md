# Git hooks — enforce single-main-branch policy

These hooks are tracked in-repo and enforce the project's "all work happens on `main`" rule for **everyone** using git on this repo — direct human use, other AI tools (Codex CLI, etc.), and IDE git integrations alike. The Claude-specific hook in `.claude/hooks/block-branch-mess.sh` covers the model-side; these cover everyone else.

## Status

**Dormant by default.** The hook scripts ship in the repo but `core.hooksPath` is intentionally NOT set, so they don't fire until you activate them. This avoids surprising in-flight work the moment the policy lands.

## Activation (once per machine / per clone)

**Standard single-clone repo:**
```sh
git config core.hooksPath .githooks
```

**This repo currently has `extensions.worktreeConfig = true`** — `git config` writes to per-worktree config by default. To make the policy apply to ALL worktrees in this clone, write to the shared config directly:
```sh
git config --file "$(git rev-parse --git-common-dir)/config" core.hooksPath .githooks
```

For worktrees on branches that don't have `.githooks/` checked out, set an absolute path instead:
```sh
git config core.hooksPath "<absolute-path-to-main-checkout>/.githooks"
```

Verify with:
```sh
git config core.hooksPath
# should print: .githooks (or an absolute path)
```

## Deactivation

```sh
git config --unset core.hooksPath
# and from any worktree that has a per-worktree override:
git config --worktree --unset core.hooksPath
```

## What each hook does

| Hook | Trigger | Behaviour |
|---|---|---|
| [`reference-transaction`](reference-transaction) | Any ref update | **Blocks** creation of `refs/heads/*` (any branch other than `main`). Catches `git branch foo`, `git checkout -b foo`, `git switch -c foo`. |
| [`pre-commit`](pre-commit) | Before each commit | **Refuses** the commit if HEAD is not on `main`. |
| [`post-checkout`](post-checkout) | After branch checkout | **Loud warning** when HEAD lands on a non-main branch. (Git has no `pre-checkout` hook for branch switches, so this is a deterrent rather than a hard block.) |

Together: branch creation is blocked at the ref level, accidental switches get a screaming warning, and any commit on a non-main branch is refused. The only remaining bypass is `git -c core.hooksPath= ...` per-command override — don't do that.

## Why not just `.git/hooks/`?

`.git/hooks/` is per-clone and not tracked, so a new clone or worktree wouldn't have these. `core.hooksPath` pointing at a tracked directory means every clone picks them up after the one-time `git config` line above.

## See also

- [`CLAUDE.md > Branch Rule`](../CLAUDE.md) — human-facing policy
- [`.claude/hooks/block-branch-mess.sh`](../.claude/hooks/block-branch-mess.sh) — Claude Code model-side hook (catches before the command runs)
