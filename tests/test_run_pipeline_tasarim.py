"""Unit tests for ``scripts/run_pipeline_tasarim`` orchestrator.

Subprocess invocation is mocked out (we don't want to actually shell out
to the real pipeline scripts in unit tests). We verify:
  - issue-folder resolution (single / none / multiple)
  - per-stage command-building (correct flags, --force propagation)
  - stage filtering (--skip-stage / --only-stage)
  - parse_argv validation
  - --dry-run prints commands without executing
  - missing --issue folder gracefully skips folder-scoped stages
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
_SPEC = importlib.util.spec_from_file_location(
    "run_pipeline_tasarim",
    SCRIPTS_DIR / "run_pipeline_tasarim.py",
)
runner = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
sys.modules["run_pipeline_tasarim"] = runner
_SPEC.loader.exec_module(runner)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# _resolve_issue_folder
# ---------------------------------------------------------------------------

def test_resolve_issue_folder_single_match(tmp_path):
    (tmp_path / "TS_246_2016-06-09").mkdir()
    (tmp_path / "TS_483_2026-04-24").mkdir()
    assert runner._resolve_issue_folder(tmp_path, "246") == "TS_246_2016-06-09"


def test_resolve_issue_folder_no_match_returns_none(tmp_path):
    """No matching folder yet (stage 1 hasn't downloaded it). Caller
    should skip folder-scoped stages gracefully."""
    (tmp_path / "TS_246_2016-06-09").mkdir()
    assert runner._resolve_issue_folder(tmp_path, "999") is None


def test_resolve_issue_folder_multiple_matches_raises(tmp_path):
    """Two folders sharing the same bulletin number indicate a naming
    drift the user must resolve before re-running (we won't guess)."""
    (tmp_path / "TS_246_2016-06-09").mkdir()
    (tmp_path / "TS_246_2026-04-24").mkdir()  # wrong-dated dup
    with pytest.raises(ValueError, match="Multiple TS_246_"):
        runner._resolve_issue_folder(tmp_path, "246")


# ---------------------------------------------------------------------------
# _build_stage_command
# ---------------------------------------------------------------------------

def _cmd(stage_name, **overrides):
    """Convenience: build a command with sensible defaults."""
    defaults = dict(
        bulletins_root=Path("/fake/bulletins"),
        issue_no=None,
        issue_folder_name=None,
        force=False,
    )
    defaults.update(overrides)
    return runner._build_stage_command(stage_name, **defaults)


def test_build_collect_command_with_issue_and_force():
    cmd = _cmd("collect", issue_no="246", force=True)
    joined = " ".join(cmd)
    assert "data_collection_tasarim.py" in joined
    assert "--issue 246" in joined
    assert "--force" in joined
    assert "--bulletins-root /fake/bulletins" in joined.replace("\\", "/")


def test_build_collect_command_no_issue_no_force():
    cmd = _cmd("collect")
    joined = " ".join(cmd)
    assert "--issue" not in joined
    assert "--force" not in joined


def test_build_cd_extract_always_uses_all_regardless_of_issue():
    """cd_extract operates on .rar files, not folders. It always runs in
    --all mode and trusts its own per-file skip logic."""
    cmd = _cmd("cd_extract", issue_no="246", issue_folder_name="TS_246_2016-06-09")
    joined = " ".join(cmd)
    assert "cd_extract_tasarim.py" in joined
    assert "--all" in joined
    assert "--bulletins-dir /fake/bulletins" in joined.replace("\\", "/")
    # cd_extract doesn't take --issue
    assert "--issue" not in joined


def test_build_cd_extract_with_force():
    cmd = _cmd("cd_extract", force=True)
    assert "--force" in cmd


def test_build_pdf_extract_uses_folder_name_for_issue():
    """pdf_extract takes --issue TS_NNN_YYYY-MM-DD, not the bare number."""
    cmd = _cmd("pdf_extract", issue_folder_name="TS_246_2016-06-09")
    joined = " ".join(cmd)
    assert "pdf_extract_tasarim.py" in joined
    assert "--issue TS_246_2016-06-09" in joined


def test_build_merge_command_all_when_no_issue():
    """No --issue → merge runs --all so every folder is processed."""
    cmd = _cmd("merge")
    joined = " ".join(cmd)
    assert "pipeline.merge_into_metadata" in joined
    assert "--all" in joined
    assert "--issue" not in joined


def test_build_merge_command_with_issue_omits_all():
    cmd = _cmd("merge", issue_folder_name="TS_246_2016-06-09")
    joined = " ".join(cmd)
    assert "--issue TS_246_2016-06-09" in joined
    assert "--all" not in joined


def test_build_embed_command():
    cmd = _cmd("embed", issue_folder_name="TS_246_2016-06-09", force=True)
    joined = " ".join(cmd)
    assert "embeddings_tasarim.py" in joined
    assert "--issue TS_246_2016-06-09" in joined
    assert "--force" in joined


def test_build_ingest_command():
    cmd = _cmd("ingest", issue_folder_name="TS_246_2016-06-09", force=True)
    joined = " ".join(cmd)
    assert "pipeline.ingest_designs" in joined
    assert "--issue TS_246_2016-06-09" in joined
    assert "--force" in joined


def test_build_unknown_stage_raises():
    with pytest.raises(ValueError, match="unknown stage"):
        _cmd("bogus_stage_name")


# ---------------------------------------------------------------------------
# _selected_stages — --skip-stage / --only-stage
# ---------------------------------------------------------------------------

def _ns(**overrides):
    """Build a fake argparse.Namespace."""
    import argparse
    return argparse.Namespace(
        only_stage=overrides.get("only_stage"),
        skip_stage=overrides.get("skip_stage"),
    )


def test_selected_stages_default_returns_all():
    sel = runner._selected_stages(_ns())
    assert [s.index for s in sel] == [1, 2, 3, 4, 5, 6]


def test_selected_stages_skip_filters_out_specified():
    sel = runner._selected_stages(_ns(skip_stage=[1, 2]))
    assert [s.index for s in sel] == [3, 4, 5, 6]


def test_selected_stages_only_restricts_to_specified():
    sel = runner._selected_stages(_ns(only_stage=[3, 6]))
    assert [s.index for s in sel] == [3, 6]


# ---------------------------------------------------------------------------
# parse_argv
# ---------------------------------------------------------------------------

def test_parse_argv_defaults():
    ns = runner.parse_argv([])
    assert ns.issue is None
    assert ns.force is False
    assert ns.skip_stage is None
    assert ns.only_stage is None
    assert ns.continue_on_error is False
    assert ns.dry_run is False


def test_parse_argv_force_and_issue():
    ns = runner.parse_argv(["--issue", "246", "--force"])
    assert ns.issue == "246"
    assert ns.force is True


def test_parse_argv_skip_stage_repeatable():
    ns = runner.parse_argv(["--skip-stage", "1", "--skip-stage", "2"])
    assert ns.skip_stage == [1, 2]


def test_parse_argv_only_stage_repeatable():
    ns = runner.parse_argv(["--only-stage", "6"])
    assert ns.only_stage == [6]


def test_parse_argv_only_and_skip_mutually_exclusive(capsys):
    with pytest.raises(SystemExit):
        runner.parse_argv(["--only-stage", "1", "--skip-stage", "2"])
    assert "mutually exclusive" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# main — --dry-run + folder-missing skip
# ---------------------------------------------------------------------------

def test_main_dry_run_does_not_invoke_subprocess(tmp_path, monkeypatch):
    """--dry-run should log every stage's command but never shell out."""
    (tmp_path / "TS_246_2016-06-09").mkdir()

    calls = []
    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return type("P", (), {"returncode": 0})()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    rc = runner.main([
        "--bulletins-root", str(tmp_path),
        "--issue", "246",
        "--dry-run",
    ])
    assert rc == 0
    assert calls == []  # no subprocess.run invocations


def test_main_missing_issue_folder_skips_folder_stages(tmp_path, monkeypatch):
    """--issue NNN with no TS_NNN_* folder on disk: stage 1 runs (to
    download); stages 2b–6 skip cleanly with rc=None in the summary."""
    # bulletins_root exists but no TS_246_* folder yet
    monkeypatch.setattr(runner.subprocess, "run",
                         lambda *a, **k: type("P", (), {"returncode": 0})())

    rc = runner.main([
        "--bulletins-root", str(tmp_path),
        "--issue", "246",
        "--only-stage", "3",   # pdf_extract — folder-scoped
        "--only-stage", "6",   # ingest — folder-scoped
    ])
    # Both stages skipped because folder absent. Summary returns 0
    # (skipped is not failure).
    assert rc == 0


def test_main_unknown_bulletins_root_returns_1(tmp_path):
    rc = runner.main(["--bulletins-root", str(tmp_path / "nope")])
    assert rc == 1
