"""Local PostgreSQL backups and isolated restore drills (host Python, stdlib only)."""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime, timezone

DB_CONTAINER = "poopbot-db-1"
TABLES = ("alembic_version", "chats", "users", "chat_members", "sessions",
          "session_messages", "user_streaks", "user_global_streaks",
          "session_user_state", "poop_events", "rate_limits", "command_messages", "app_settings", "mini_app_requests")


class BackupError(RuntimeError):
    pass


def metrics_sql(tables=None):
    tables = TABLES if tables is None else tuple(tables)
    if set(tables) not in (set(TABLES), set(TABLES) - {"mini_app_requests"}):
        raise BackupError("unsupported manifest table set")
    # Hash multisets of complete rows, including disputed historical records.
    # Both sides use UTC and the same PostgreSQL image. No personal data is logged.
    parts = []
    for table in tables:
        parts.append(f"'{table}', (SELECT json_build_object('rows', count(*), 'digest', "
                     "md5(coalesce(string_agg(md5(row_to_json(t)::text), '' "
                     f"ORDER BY md5(row_to_json(t)::text)), ''))) FROM {table} t)")
    return "SELECT json_build_object(" + ",".join(parts) + ");"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def run(args, *, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, timeout=300):
    result = subprocess.run(args, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        # Do not include SQL rows, connection strings or command arguments in alerts/logs.
        raise BackupError(f"{Path(args[0]).name}: command failed, exit={result.returncode}")
    return result.stdout


def inspect(container):
    return json.loads(run(["docker", "inspect", container]))[0]


def psql_args(container, user, database):
    return ["docker", "exec", "-i", container, "psql", "-X", "-q", "-A", "-t",
            "-v", "ON_ERROR_STOP=1", "-U", user, "-d", database]


def db_identity():
    data = inspect(DB_CONTAINER)
    env = dict(item.split("=", 1) for item in data["Config"]["Env"])
    return data["Image"], env["POSTGRES_USER"], env["POSTGRES_DB"]


class Snapshot:
    """Keep a read-only transaction alive while pg_dump imports its snapshot."""
    def __init__(self, user, database):
        self.args = psql_args(DB_CONTAINER, user, database)

    def __enter__(self):
        self.errors = tempfile.TemporaryFile()
        self.process = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.errors)
        return self

    def query(self, sql):
        self.process.stdin.write((sql + "\n").encode())
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 60)[0]:
            raise BackupError("source snapshot query timed out")
        line = self.process.stdout.readline().decode().strip()
        if not line:
            raise BackupError("source snapshot query failed")
        return line

    def __exit__(self, *exc):
        try:
            self.process.communicate(b"ROLLBACK;\n", timeout=10)
        except (subprocess.TimeoutExpired, BrokenPipeError):
            self.process.kill()
            self.process.wait(timeout=10)
        finally:
            self.errors.close()


class Operations:
    def __init__(self, root):
        self.root = root.resolve()
        self.backups = self.root / "backups"
        self.logs = self.backups / "operations"
        self.logs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stage = "initialization"

    def log(self, event, **fields):
        now = datetime.now(timezone.utc)
        record = dict(time=now.isoformat(), event=event, **fields)
        with (self.logs / f"{now:%Y-%m}.jsonl").open("a") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())

    def notify(self, text):
        # Read only the two necessary values; never source .env as shell code.
        values = {}
        for line in (self.root / ".env").read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key in {"BOT_TOKEN", "BOT_OWNER_ID"}:
                values[key] = value.strip().strip("\"'")
        token, owner = values.get("BOT_TOKEN", ""), values.get("BOT_OWNER_ID", "")
        if not re.fullmatch(r"\d+:[\w-]+", token) or not owner.isdigit():
            raise BackupError("owner notification configuration is missing")
        body = json.dumps({"chat_id": int(owner), "text": text}).encode()
        for attempt in range(3):
            try:
                request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                                 data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    if json.load(response).get("ok"):
                        self.log("notification_sent")
                        return
            except Exception:
                # Exceptions can contain the token-bearing URL. Never record them.
                pass
            if attempt < 2:
                time.sleep(2)
        raise BackupError("Telegram notification delivery failed")

    def create(self):
        self.stage = "source_snapshot"
        image, user, database = db_identity()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S_%f")
        archive = self.backups / f"poopbot_daily_{stamp}.dump"
        temporary = archive.with_suffix(".partial")
        try:
            with Snapshot(user, database) as snapshot:
                snapshot_id = snapshot.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; "
                    "SET LOCAL TIME ZONE 'UTC'; SET LOCAL idle_in_transaction_session_timeout='10min'; "
                    "SELECT pg_export_snapshot();")
                if not re.fullmatch(r"[0-9A-Fa-f-]+", snapshot_id):
                    raise BackupError("unexpected snapshot identifier")
                metrics = json.loads(snapshot.query(metrics_sql()))
                self.stage = "pg_dump"
                with temporary.open("wb") as output:
                    run(["docker", "exec", DB_CONTAINER, "pg_dump", "-U", user, "-d", database,
                         "-Fc", "--snapshot", snapshot_id], stdout=output)
                    output.flush()
                    os.fsync(output.fileno())
            with temporary.open("rb") as file:
                if file.read(5) != b"PGDMP":
                    raise BackupError("pg_dump did not produce a custom-format archive")
            manifest = dict(version=1, created_at=datetime.now(timezone.utc).isoformat(),
                            archive=archive.name, sha256=sha256(temporary), metrics=metrics,
                            postgres_image=image, database=database, user=user)
            self.stage = "publish_backup"
            os.replace(temporary, archive)
            atomic_json(archive.with_suffix(".json"), manifest)
            self.prune()
            return dict(archive=archive.name, bytes=archive.stat().st_size, sha256=manifest["sha256"])
        finally:
            temporary.unlink(missing_ok=True)

    def prune(self):
        protected = None
        verified = self.logs / "last_verified.json"
        if verified.exists():
            protected = json.loads(verified.read_text()).get("archive")
        files = sorted(self.backups.glob("poopbot_daily_*.dump"), reverse=True)
        for archive in files[7:]:
            if archive.name != protected and archive.with_suffix(".json").exists():
                archive.unlink()
                archive.with_suffix(".json").unlink()

    def choose_archive(self, supplied=None):
        if supplied:
            archive = (self.backups / supplied).resolve()
        else:
            files = list(self.backups.glob("poopbot_daily_*.dump")) + list(self.backups.glob("poopbot_*.sql.gz"))
            if not files:
                raise BackupError("no local backup found")
            archive = max(files, key=lambda p: p.stat().st_mtime).resolve()
        if not archive.is_relative_to(self.backups.resolve()) or not archive.is_file():
            raise BackupError("archive must be an existing file inside backups")
        if not supplied and time.time() - archive.stat().st_mtime > 36 * 3600:
            raise BackupError("latest backup is older than 36 hours")
        return archive

    def verify(self, supplied=None):
        self.stage = "archive_validation"
        archive = self.choose_archive(supplied)
        custom = archive.suffix == ".dump"
        if not custom and not archive.name.endswith(".sql.gz"):
            raise BackupError("unsupported backup format")
        manifest = None
        if custom:
            manifest = json.loads(archive.with_suffix(".json").read_text())
            if manifest.get("version") != 1 or manifest.get("sha256") != sha256(archive):
                raise BackupError("backup manifest or checksum mismatch")
            image, user, database = manifest["postgres_image"], manifest["user"], manifest["database"]
        else:
            image, user, database = db_identity()
        # Only a local, content-addressed PostgreSQL image is accepted, never a
        # remote image name taken from a manifest. Verify its advertised major.
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise BackupError("invalid PostgreSQL image identifier")
        image_info = json.loads(run(["docker", "image", "inspect", image]))[0]
        image_env = dict(v.split("=", 1) for v in image_info["Config"]["Env"])
        if image_env.get("PG_MAJOR") != "16":
            raise BackupError("restore drill requires local PostgreSQL 16 image")
        name = "poopbot-restore-check-" + uuid.uuid4().hex
        self.stage = "sandbox_start"
        created = False
        try:
            # Also attempt cleanup if Docker starts the container but the client
            # times out before receiving its acknowledgement.
            created = True
            run(["docker", "run", "-d", "--rm", "--name", name,
                 "--label", "poopbot.restore-check=true", "--network", "none",
                 "--memory", "256m", "--memory-swap", "256m", "--cpus", "0.25",
                 "--pids-limit", "128", "--tmpfs", "/var/lib/postgresql/data:rw,size=192m",
                 "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", f"POSTGRES_USER={user}",
                 "-e", f"POSTGRES_DB={database}", image,
                 "-c", "shared_buffers=16MB", "-c", "max_connections=10"], timeout=60)
            for _ in range(60):
                # initdb's temporary server only listens on a Unix socket. Wait
                # for the final server before starting the restore.
                result = subprocess.run(["docker", "exec", name, "pg_isready", "-q", "-h", "127.0.0.1", "-U", user,
                                         "-d", database], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=10)
                if result.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise BackupError("isolated PostgreSQL did not become ready")
            self.stage = "restore"
            with tempfile.TemporaryFile() as plain:
                if custom:
                    source = archive.open("rb")
                else:
                    with gzip.open(archive, "rb") as compressed:
                        total = 0
                        for chunk in iter(lambda: compressed.read(1024 * 1024), b""):
                            total += len(chunk)
                            if total > 256 * 1024 * 1024:
                                raise BackupError("legacy SQL archive exceeds drill size limit")
                            plain.write(chunk)
                    plain.seek(0)
                    source = plain
                try:
                    if custom:
                        run(["docker", "exec", "-i", name, "pg_restore", "--single-transaction",
                             "--exit-on-error", "--no-owner", "--no-privileges",
                             "-U", user, "-d", database], stdin=source, timeout=420)
                    else:
                        run(psql_args(name, user, database) + ["--single-transaction"], stdin=source, timeout=420)
                finally:
                    if custom:
                        source.close()
            self.stage = "data_validation"
            metrics = json.loads(run(psql_args(name, user, database) +
                                     ["-c", "SET TIME ZONE 'UTC'; " + metrics_sql(manifest["metrics"] if manifest else [t for t in TABLES if t != "mini_app_requests"])]))
            if manifest is not None and metrics != manifest["metrics"]:
                raise BackupError("restored rows differ from the original snapshot")
            if metrics["alembic_version"]["rows"] != 1:
                raise BackupError("restored database has no unique migration revision")
            result = dict(archive=archive.name, sha256=sha256(archive), metrics=metrics,
                          verified_at=datetime.now(timezone.utc).isoformat(),
                          validation="snapshot_match" if manifest else "legacy_restore_without_manifest")
        finally:
            if created:
                run(["docker", "rm", "-f", name], timeout=30)
        atomic_json(self.logs / "last_verified.json", result)
        return result

    def execute(self, action, archive=None):
        started = time.monotonic()
        try:
            with (self.logs / "operations.lock").open("a") as lock:
                self.stage = "lock"
                for _ in range(120):
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        time.sleep(5)
                else:
                    raise BackupError("another backup operation is still running")
                self.log("started", action=action)
                if action == "create":
                    result = self.create()
                elif action == "verify":
                    result = self.verify(archive)
                else:
                    self.notify("🧪 Poopbot: проверка уведомлений о бэкапах. Доставка работает; это не сообщение об аварии.")
                    result = {}
                report = dict(action=action, status="ok", duration_sec=round(time.monotonic()-started, 2), **result)
                atomic_json(self.logs / f"last_{action}.json", report)
                self.log("finished", **report)
                return report
        except Exception as error:
            # Avoid arbitrary exception text: urllib URLs and SQL errors can expose secrets.
            message = str(error) if isinstance(error, BackupError) else type(error).__name__
            report = dict(action=action, status="failed", stage=self.stage, error=message)
            atomic_json(self.logs / f"last_{action}.json", report)
            self.log("failed", **report)
            if action != "test-alert":
                try:
                    self.notify(f"❌ Poopbot: ошибка {'создания бэкапа' if action == 'create' else 'проверки восстановления'}.\n"
                                f"Этап: {self.stage}. Причина: {message}.\n"
                                "Журнал: /opt/poopbot/backups/operations/. Рабочая БД не изменялась этой операцией.")
                except Exception:
                    self.log("notification_failed", action=action)
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "verify", "test-alert"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", help="Backup filename; omitted means latest daily backup, maximum age 36h")
    args = parser.parse_args()
    os.umask(0o077)
    def stopped(*_):
        raise BackupError("operation interrupted")
    signal.signal(signal.SIGTERM, stopped)
    result = Operations(args.root).execute(args.action, args.archive)
    print(json.dumps({k: v for k, v in result.items() if k != "metrics"}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
