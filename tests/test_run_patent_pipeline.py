"""Tests for the patent pipeline orchestrator's argv parser and
per-stage command builder.

The orchestrator is a thin shell over the real stage CLIs. We don't
execute anything end-to-end here — that's covered by each stage's own
tests. We only verify:

  * default flags are conservative (idempotent, all 7 stages selected)
  * --force forwards to every stage's command
  * --stages selects a subset, preserves order, dedupes, and rejects
    out-of-range values
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_patent_pipeline import (  # noqa: E402
    STAGE_NAMES,
    STAGE_ORDER,
    _build_stage_command,
    _parse_stages,
    parse_argv,
)


def test_defaults():
    args = parse_argv([])
    assert args.stages == list(STAGE_ORDER)
    assert args.force is False
    assert args.dry_run is False
    assert args.stop_on_error is False
    assert args.bulletins_dir.name == "Patent__Faydali_Model"


def test_force_flag_is_off_by_default():
    """--force must be explicit; default runs are idempotent."""
    assert parse_argv([]).force is False
    assert parse_argv(["--force"]).force is True


def test_parse_stages_subset():
    args = parse_argv(["--stages", "2,3,4,5,6,7"])
    assert args.stages == [2, 3, 4, 5, 6, 7]


def test_parse_stages_single():
    args = parse_argv(["--stages", "6"])
    assert args.stages == [6]


def test_parse_stages_preserves_user_order():
    """If the user passes 7,5,3 we trust their ordering."""
    args = parse_argv(["--stages", "7,5,3"])
    assert args.stages == [7, 5, 3]


def test_parse_stages_dedupes():
    args = parse_argv(["--stages", "2,2,3,3,4"])
    assert args.stages == [2, 3, 4]


def test_parse_stages_rejects_out_of_range():
    with pytest.raises(SystemExit):
        parse_argv(["--stages", "8"])


def test_parse_stages_rejects_non_integer():
    with pytest.raises(SystemExit):
        parse_argv(["--stages", "abc"])


def test_parse_stages_rejects_empty():
    with pytest.raises(SystemExit):
        parse_argv(["--stages", ""])


def test_stage_names_cover_every_stage():
    assert set(STAGE_NAMES.keys()) == set(STAGE_ORDER)


# ---------------------------------------------------------------------------
# _build_stage_command — verify --force forwarding for every stage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_command_no_force(stage, tmp_path):
    cmd = _build_stage_command(stage, bulletins_dir=tmp_path, force=False)
    assert "--force" not in cmd, f"stage {stage} should not include --force when force=False"
    assert str(tmp_path) in cmd


@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_command_with_force(stage, tmp_path):
    cmd = _build_stage_command(stage, bulletins_dir=tmp_path, force=True)
    assert "--force" in cmd, f"stage {stage} must forward --force when force=True"


def test_stage1_uses_bulletins_root_flag(tmp_path):
    """Collector uses --bulletins-root, not --bulletins-dir."""
    cmd = _build_stage_command(1, bulletins_dir=tmp_path, force=False)
    assert "--bulletins-root" in cmd
    assert "--bulletins-dir" not in cmd


def test_stages_2_through_7_use_bulletins_dir_flag(tmp_path):
    for stage in (2, 3, 4, 5, 6, 7):
        cmd = _build_stage_command(stage, bulletins_dir=tmp_path, force=False)
        assert "--bulletins-dir" in cmd, f"stage {stage} should use --bulletins-dir"


def test_stages_2_through_7_pass_all_flag(tmp_path):
    """Stages 2-7 should run over every bulletin in the dir by default."""
    for stage in (2, 3, 4, 5, 6, 7):
        cmd = _build_stage_command(stage, bulletins_dir=tmp_path, force=False)
        assert "--all" in cmd, f"stage {stage} should default to --all"


def test_stage_5_invokes_reconcile_module(tmp_path):
    cmd = _build_stage_command(5, bulletins_dir=tmp_path, force=False)
    assert cmd[1:4] == ["-m", "pipeline.reconcile_patent", "--all"]


def test_stage_7_invokes_ingest_module(tmp_path):
    cmd = _build_stage_command(7, bulletins_dir=tmp_path, force=False)
    assert cmd[1:4] == ["-m", "pipeline.ingest_patents", "--all"]


def test_unknown_stage_raises():
    with pytest.raises(ValueError):
        _build_stage_command(99, bulletins_dir=Path("."), force=False)


def test_parse_stages_helper_independent_of_argparse():
    assert _parse_stages("1,2,3") == [1, 2, 3]
    assert _parse_stages("3,1,2") == [3, 1, 2]
    with pytest.raises(Exception):
        _parse_stages("")
