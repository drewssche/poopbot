import gzip
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.base import Base
from app.db import models  # noqa: F401
from scripts import backup_ops as backups


@pytest.fixture
def ops(tmp_path):
    return backups.Operations(tmp_path)


def archive(ops, name="poopbot_daily_2026-10-19.dump"):
    path = ops.backups / name
    path.write_bytes(b"PGDMP-test-data")
    metrics = {t: {"rows": 1, "digest": "abc"} for t in backups.TABLES}
    manifest = dict(version=1, sha256=backups.sha256(path), metrics=metrics,
                    postgres_image="sha256:" + "a"*64, user="poopbot", database="poopbot")
    path.with_suffix(".json").write_text(json.dumps(manifest))
    return path, metrics


def fake_docker(monkeypatch, metrics, *, fail_restore=False):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Config": {"Env": ["PG_MAJOR=16"]}}]).encode()
        if "pg_restore" in args:
            assert kwargs["stdin"].read(5) == b"PGDMP"
            if fail_restore:
                raise backups.BackupError("restore failed")
        if "psql" in args:
            return json.dumps(metrics).encode()
        return b""
    monkeypatch.setattr(backups, "run", run)
    monkeypatch.setattr(backups.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    return calls


def test_metrics_cover_every_application_table():
    assert set(backups.TABLES) == set(Base.metadata.tables) | {"alembic_version"}
    assert "row_to_json" in backups.metrics_sql()
    assert "UPDATE " not in backups.metrics_sql()


def test_commands_do_not_consume_the_calling_shell_input(monkeypatch):
    seen = []
    def call(args, **kwargs):
        seen.append(kwargs["stdin"])
        return SimpleNamespace(returncode=0, stdout=b"ok")
    monkeypatch.setattr(backups.subprocess, "run", call)
    assert backups.run(["docker", "inspect", "example"]) == b"ok"
    assert seen == [backups.subprocess.DEVNULL]


def test_restore_is_isolated_and_cleans_up(ops, monkeypatch):
    path, metrics = archive(ops)
    calls = fake_docker(monkeypatch, metrics)
    result = ops.verify()
    assert result["validation"] == "snapshot_match"
    assert result["metrics"] == metrics
    start = next(c for c in calls if c[:2] == ["docker", "run"])
    assert start[start.index("--network")+1] == "none"
    assert start[start.index("--memory")+1] == "256m"
    assert "--tmpfs" in start and "-v" not in start and "--mount" not in start and "-p" not in start
    assert not any("BOT_TOKEN" in arg for arg in start)
    restore = next(c for c in calls if "pg_restore" in c)
    assert restore[3].startswith("poopbot-restore-check-")
    assert backups.DB_CONTAINER not in restore
    assert "--single-transaction" in restore and "--exit-on-error" in restore
    assert calls[-1][:3] == ["docker", "rm", "-f"]
    assert json.loads((ops.logs / "last_verified.json").read_text())["archive"] == path.name


@pytest.mark.parametrize("failure", ["restore", "mismatch"])
def test_failure_cleans_sandbox_alerts_and_preserves_last_verified(ops, monkeypatch, failure):
    path, metrics = archive(ops)
    if failure == "mismatch":
        metrics["poop_events"]["digest"] = "changed"
    calls = fake_docker(monkeypatch, metrics, fail_restore=failure == "restore")
    (ops.logs / "last_verified.json").write_text('{"archive":"older.dump"}')
    notifications = []
    monkeypatch.setattr(ops, "notify", notifications.append)
    report = ops.execute("verify")
    assert report["status"] == "failed"
    assert len(notifications) == 1
    assert calls[-1][:3] == ["docker", "rm", "-f"]
    assert json.loads((ops.logs / "last_verified.json").read_text())["archive"] == "older.dump"
    assert any(json.loads(l)["event"] == "failed" for p in ops.logs.glob("*.jsonl") for l in p.read_text().splitlines())


def test_corrupt_archive_fails_before_starting_container(ops, monkeypatch):
    path, _ = archive(ops)
    path.write_bytes(b"corrupted")
    monkeypatch.setattr(backups, "run", lambda *a, **kw: pytest.fail("must not start Docker"))
    with pytest.raises(backups.BackupError, match="checksum"):
        ops.verify()


def test_legacy_gzip_can_be_restored_but_not_claimed_as_snapshot_match(ops, monkeypatch):
    path = ops.backups / "poopbot_2026-10-19_03-15-01.sql.gz"
    with gzip.open(path, "wb") as file:
        file.write(b"SELECT 1;")
    metrics = {t: {"rows": 1, "digest": "abc"} for t in backups.TABLES}
    fake_docker(monkeypatch, metrics)
    monkeypatch.setattr(backups, "db_identity", lambda: ("sha256:"+"a"*64, "poopbot", "poopbot"))
    assert ops.verify()["validation"] == "legacy_restore_without_manifest"


def test_prune_retains_seven_plus_last_verified_and_never_touches_old_archives(ops):
    for i in range(10):
        archive(ops, f"poopbot_daily_2026-10-{i+1:02}.dump")
    protected = "poopbot_daily_2026-10-01.dump"
    (ops.logs / "last_verified.json").write_text(json.dumps({"archive": protected}))
    legacy = ops.backups / "poopbot_old.sql.gz"
    legacy.write_bytes(b"old")
    manual = ops.backups / "manual.dump"
    manual.write_bytes(b"manual")
    ops.prune()
    assert len(list(ops.backups.glob("poopbot_daily_*.dump"))) == 8
    assert (ops.backups / protected).exists() and legacy.exists() and manual.exists()


def test_missing_stale_and_external_archives_rejected(ops):
    with pytest.raises(backups.BackupError, match="no local"):
        ops.choose_archive()
    path, _ = archive(ops)
    os.utime(path, (0, 0))
    with pytest.raises(backups.BackupError, match="older"):
        ops.choose_archive()
    assert ops.choose_archive(path.name) == path
    outside = ops.root / "outside.dump"
    outside.touch()
    with pytest.raises(backups.BackupError, match="inside backups"):
        ops.choose_archive(str(outside))


def test_dump_uses_exported_readonly_snapshot_and_publishes_manifest(ops, monkeypatch):
    queries, commands = [], []
    metrics = {t: {"rows": 1, "digest": "abc"} for t in backups.TABLES}
    class Snapshot:
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def query(self, sql):
            queries.append(sql)
            return "0001-0002-1" if "pg_export_snapshot" in sql else json.dumps(metrics)
    monkeypatch.setattr(backups, "Snapshot", Snapshot)
    monkeypatch.setattr(backups, "db_identity", lambda: ("sha256:"+"a"*64, "poopbot", "poopbot"))
    def dump(args, **kw):
        commands.append(args)
        kw["stdout"].write(b"PGDMP-test")
    monkeypatch.setattr(backups, "run", dump)
    result = ops.create()
    assert "REPEATABLE READ READ ONLY" in queries[0]
    assert commands[0][-2:] == ["--snapshot", "0001-0002-1"]
    path = ops.backups / result["archive"]
    assert json.loads(path.with_suffix(".json").read_text())["metrics"] == metrics
    assert not list(ops.backups.glob("*.partial"))


def test_notification_failure_never_leaks_token(ops, monkeypatch):
    token = "123456:" + "secret-token"
    (ops.root / ".env").write_text(f"BOT_TOKEN={token}\nBOT_OWNER_ID=123\n")
    def unavailable(*a, **kw):
        raise RuntimeError("https://api.telegram.org/bot"+token)
    monkeypatch.setattr(backups.urllib.request, "urlopen", unavailable)
    monkeypatch.setattr(backups.time, "sleep", lambda _: None)
    result = ops.execute("verify")
    assert result["status"] == "failed"
    logs = "".join(p.read_text() for p in ops.logs.glob("*.json*"))
    assert token not in logs
    assert "notification_failed" in logs


def test_restore_manifest_from_before_mini_app_migration(ops, monkeypatch):
    path, metrics = archive(ops)
    metrics.pop('mini_app_requests')
    metrics.pop('weekly_reports')
    manifest = json.loads(path.with_suffix('.json').read_text())
    manifest['metrics'] = metrics
    path.with_suffix('.json').write_text(json.dumps(manifest))
    calls = fake_docker(monkeypatch, metrics)
    assert ops.verify()['validation'] == 'snapshot_match'
    sql = next(c[-1] for c in calls if 'psql' in c)
    assert 'mini_app_requests' not in sql


def test_manifest_before_weekly_reports_remains_supported():
    previous = [t for t in backups.TABLES if t != 'weekly_reports']
    sql = backups.metrics_sql(previous)
    assert 'mini_app_requests' in sql and 'weekly_reports' not in sql
