from pathlib import Path


class _Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)

    def fetchone(self):
        return self.rows.pop(0)


class _Db:
    def __init__(self, rows=None):
        self.cursor_obj = _Cursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def _use_dbs(monkeypatch, module, dbs):
    pending = list(dbs)

    def _database():
        return pending.pop(0)

    monkeypatch.setattr(module, "Database", _database)
    return pending


def test_logo_visual_migration_guard_applies_missing_columns(monkeypatch):
    import migrations.run_enhance_logo_visual as migration

    check_db = _Db([{"present": 2}])
    apply_db = _Db()
    pending = _use_dbs(monkeypatch, migration, [check_db, apply_db])

    assert migration.ensure_logo_visual_columns() is True
    assert pending == []
    assert apply_db.committed is True
    assert "ADD COLUMN IF NOT EXISTS dino_embedding" in apply_db.cursor_obj.executed[0]


def test_logo_project_migration_guard_applies_missing_columns(monkeypatch):
    import migrations.run_logo_studio_projects_migration as migration

    check_db = _Db([{"has_projects": True}, {"present": 7}])
    apply_db = _Db()
    pending = _use_dbs(monkeypatch, migration, [check_db, apply_db])

    assert migration.ensure_logo_studio_projects_schema() is True
    assert pending == []
    assert apply_db.committed is True
    assert "CREATE TABLE IF NOT EXISTS logo_projects" in apply_db.cursor_obj.executed[0]


def test_startup_wires_logo_studio_schema_guards():
    source = Path("app_lifecycle.py").read_text(encoding="utf-8")

    assert "ensure_logo_studio_projects_schema" in source
    assert "ensure_logo_visual_columns" in source
