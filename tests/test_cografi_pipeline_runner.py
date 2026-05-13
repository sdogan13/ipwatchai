"""Unit tests for ``scripts/run_cografi_pipeline.py``.

Subprocess execution is exercised manually via ``--dry-run`` and a
single-stage live run; these tests pin the pure-Python pieces:

  * ``_build_stage_command`` argv shape per stage, with and without
    ``--force``, including the ingest-stage-no-force invariant.
  * ``_parse_stages`` valid + invalid input handling + dedup +
    user-supplied ordering.
  * ``parse_argv`` defaults + flag wiring.
  * ``main`` end-to-end with ``--dry-run`` (no subprocess fired)
    + the ``--force does not apply`` informational log path.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import List

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUNNER_PATH = _PROJECT_ROOT / "scripts" / "run_cografi_pipeline.py"


def _load_runner_module():
    """Load the runner as a module without polluting sys.modules
    permanently across test files."""
    spec = importlib.util.spec_from_file_location(
        "run_cografi_pipeline_under_test", _RUNNER_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner():
    return _load_runner_module()


# ---------------------------------------------------------------------------
# _build_stage_command
# ---------------------------------------------------------------------------

def test_build_stage_collect_without_force(runner):
    cmd = runner._build_stage_command(1, bulletins_dir=Path("/tmp/x"), force=False)
    assert cmd[1].endswith("data_collection_cografi.py")
    assert "--bulletins-root" in cmd
    assert str(Path("/tmp/x")) in cmd
    assert "--force" not in cmd
    assert "--all" not in cmd  # collector has no --all flag


def test_build_stage_collect_with_force(runner):
    cmd = runner._build_stage_command(1, bulletins_dir=Path("/tmp/x"), force=True)
    assert cmd[-1] == "--force"


def test_build_stage_extract_uses_all_and_bulletins_root(runner):
    cmd = runner._build_stage_command(2, bulletins_dir=Path("/tmp/x"), force=False)
    assert cmd[1].endswith("pdf_extract_cografi.py")
    assert "--all" in cmd
    assert "--bulletins-root" in cmd


def test_build_stage_extract_with_force(runner):
    cmd = runner._build_stage_command(2, bulletins_dir=Path("/tmp/x"), force=True)
    assert "--force" in cmd


def test_build_stage_embed_uses_all_and_bulletins_root(runner):
    cmd = runner._build_stage_command(3, bulletins_dir=Path("/tmp/x"), force=False)
    assert cmd[1].endswith("embeddings_cografi.py")
    assert "--all" in cmd


def test_build_stage_embed_with_force(runner):
    cmd = runner._build_stage_command(3, bulletins_dir=Path("/tmp/x"), force=True)
    assert "--force" in cmd


def test_build_stage_ingest_uses_module_path(runner):
    cmd = runner._build_stage_command(4, bulletins_dir=Path("/tmp/x"), force=False)
    # python -m pipeline.ingest_cografi ...
    assert "-m" in cmd
    assert "pipeline.ingest_cografi" in cmd
    assert "--all" in cmd
    assert "--bulletins-root" in cmd


def test_build_stage_ingest_ignores_force(runner):
    """Ingest is naturally idempotent via UPSERT — the runner must
    NOT pass --force to it (the underlying CLI doesn't accept it
    and would error)."""
    cmd_no_force = runner._build_stage_command(4, bulletins_dir=Path("/tmp/x"), force=False)
    cmd_with_force = runner._build_stage_command(4, bulletins_dir=Path("/tmp/x"), force=True)
    assert cmd_no_force == cmd_with_force
    assert "--force" not in cmd_with_force


def test_build_stage_unknown_stage_raises(runner):
    with pytest.raises(ValueError, match="unknown stage"):
        runner._build_stage_command(99, bulletins_dir=Path("/tmp/x"), force=False)


def test_force_capable_stages_constant_excludes_ingest(runner):
    assert 1 in runner._FORCE_CAPABLE_STAGES
    assert 2 in runner._FORCE_CAPABLE_STAGES
    assert 3 in runner._FORCE_CAPABLE_STAGES
    assert 4 not in runner._FORCE_CAPABLE_STAGES


# ---------------------------------------------------------------------------
# _parse_stages
# ---------------------------------------------------------------------------

def test_parse_stages_single(runner):
    assert runner._parse_stages("3") == [3]


def test_parse_stages_multiple_in_order(runner):
    assert runner._parse_stages("2,3,4") == [2, 3, 4]


def test_parse_stages_preserves_user_order(runner):
    """User-supplied order matters — running embed before extract is
    valid for some development workflows (e.g. after a partial extract)."""
    assert runner._parse_stages("4,1") == [4, 1]


def test_parse_stages_dedupes_while_preserving_first_occurrence(runner):
    assert runner._parse_stages("2,3,2,4,3") == [2, 3, 4]


def test_parse_stages_strips_whitespace(runner):
    assert runner._parse_stages(" 1 , 2 , 3 ") == [1, 2, 3]


def test_parse_stages_rejects_non_integer(runner):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="expects integers"):
        runner._parse_stages("abc")


def test_parse_stages_rejects_out_of_range(runner):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="out of range"):
        runner._parse_stages("5")


def test_parse_stages_rejects_empty(runner):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError, match="cannot be empty"):
        runner._parse_stages(",,,")


# ---------------------------------------------------------------------------
# parse_argv
# ---------------------------------------------------------------------------

def test_parse_argv_defaults(runner):
    args = runner.parse_argv([])
    assert args.stages == [1, 2, 3, 4]
    assert args.force is False
    assert args.dry_run is False
    assert args.stop_on_error is False
    assert args.bulletins_dir.name == "Cografi_Isaret_ve_Geleneksel_Urun_Adi"


def test_parse_argv_partial_stages(runner):
    args = runner.parse_argv(["--stages", "2,3"])
    assert args.stages == [2, 3]


def test_parse_argv_force_and_stop_on_error(runner):
    args = runner.parse_argv(["--force", "--stop-on-error"])
    assert args.force is True
    assert args.stop_on_error is True


def test_parse_argv_custom_bulletins_dir(runner):
    args = runner.parse_argv(["--bulletins-dir", "/some/where"])
    assert args.bulletins_dir == Path("/some/where")


# ---------------------------------------------------------------------------
# main (--dry-run path; no subprocess fired)
# ---------------------------------------------------------------------------

def test_main_dry_run_returns_zero_and_does_not_subprocess(runner, monkeypatch, caplog):
    """--dry-run must short-circuit before subprocess.run."""
    called: List[List[str]] = []

    def fail_if_called(cmd, cwd=None):
        called.append(cmd)
        raise AssertionError("subprocess.run must not be invoked under --dry-run")

    monkeypatch.setattr(runner.subprocess, "run", fail_if_called)
    rc = runner.main([
        "--dry-run",
        "--bulletins-dir", str(_PROJECT_ROOT / "bulletins"),
    ])
    assert rc == 0
    assert called == []


def test_main_force_with_ingest_emits_informational_log(runner, monkeypatch, caplog):
    """When --force is set and the stage list includes ingest, the
    runner should log that --force does not apply to ingest."""
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="run_cografi_pipeline"):
        runner.main([
            "--dry-run", "--force",
            "--stages", "3,4",
            "--bulletins-dir", str(_PROJECT_ROOT / "bulletins"),
        ])
    msgs = [r.getMessage() for r in caplog.records]
    assert any("--force does not apply to:" in m and "ingest" in m for m in msgs)


def test_main_force_with_only_force_capable_stages_does_not_emit_note(runner, monkeypatch, caplog):
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="run_cografi_pipeline"):
        runner.main([
            "--dry-run", "--force",
            "--stages", "1,2,3",
            "--bulletins-dir", str(_PROJECT_ROOT / "bulletins"),
        ])
    msgs = [r.getMessage() for r in caplog.records]
    assert not any("--force does not apply" in m for m in msgs)
