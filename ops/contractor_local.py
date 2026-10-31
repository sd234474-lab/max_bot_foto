"""Small Windows wrapper around stock PostgreSQL, uvicorn and Vite commands.

Only .local is writable. No existing service, launcher, database or .env is used.
Run init only after owner approval. start/stop never creates or resets databases.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
PG = LOCAL / "runtime" / "pgsql" / "bin"
DATA = LOCAL / "postgres-data"
PYTHON = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
CONFIG = LOCAL / "config.json"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(args, **kwargs):
    return subprocess.run(
        [str(a) for a in args], check=True, creationflags=FLAGS, **kwargs
    )


def config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if cfg.get("database") != "contractor_max_test" or cfg.get("port") != 55432:
        raise RuntimeError(
            "Only the isolated contractor_max_test database on port 55432 is allowed"
        )
    return cfg


def environment():
    cfg = config()
    # Do not inherit production endpoints, tokens, tenant mode or .env settings.
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {
            "PATH",
            "Path",
            "SystemRoot",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "COMSPEC",
            "PATHEXT",
        }
    }
    env.update(
        APP_ENV="test",
        COOKIE_SECURE="false",
        APP_ORIGIN="http://127.0.0.1:5174",
        ORGANIZATIONS_ENABLED="true",
        DATABASE_URL=f"postgresql+psycopg://contractor:{cfg['db_password']}@127.0.0.1:55432/contractor_max_test",
        DATA_DIR=str(LOCAL / "data"),
        MAX_BOT_TOKEN="",
        YANDEX_DISK_TOKEN="",
        ML_AUTO_TRAIN="false",
        ML_DOWNLOAD_BACKBONE="false",
        VITE_API_TARGET="http://127.0.0.1:8014",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
    )
    return env


def database_start():
    import psycopg

    cfg = config()
    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=55432,
            user="contractor",
            password=cfg["db_password"],
            dbname="postgres",
            connect_timeout=3,
        ) as db:
            actual = Path(db.execute("SHOW data_directory").fetchone()[0]).resolve()
            if actual != DATA.resolve():
                raise RuntimeError(
                    "Port 55432 belongs to a different PostgreSQL cluster"
                )
            return
    except psycopg.OperationalError:
        pass
    result = subprocess.run(
        [str(PG / "pg_ctl.exe"), "-D", str(DATA), "status"],
        capture_output=True,
        creationflags=FLAGS,
    )
    if result.returncode:
        run(
            [
                PG / "pg_ctl.exe",
                "-D",
                DATA,
                "-l",
                LOCAL / "logs" / "postgres.log",
                "-w",
                "start",
            ]
        )


def init():
    if DATA.exists() or CONFIG.exists():
        if DATA.exists() and CONFIG.exists() and not config().get("organization_id"):
            database_start()
            complete_init()
            return
        raise RuntimeError(
            "Initialization already completed or inconsistent; files will not be overwritten"
        )
    for folder in (LOCAL / "logs", LOCAL / "data"):
        folder.mkdir(parents=True, exist_ok=True)
    cfg = {
        "database": "contractor_max_test",
        "port": 55432,
        "db_password": secrets.token_urlsafe(32),
        "admin_password": secrets.token_urlsafe(20),
    }
    CONFIG.write_text(json.dumps(cfg), encoding="utf-8")
    password_file = LOCAL / "init-password"
    password_file.write_text(cfg["db_password"], encoding="utf-8")
    try:
        run(
            [
                PG / "initdb.exe",
                "-D",
                DATA,
                "-U",
                "contractor",
                "--encoding=UTF8",
                "--locale=C",
                "-A",
                "scram-sha-256",
                "--pwfile",
                password_file,
            ]
        )
    finally:
        password_file.unlink(missing_ok=True)
    with (DATA / "postgresql.conf").open("a", encoding="utf-8") as stream:
        stream.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\n")
    database_start()
    complete_init()


def complete_init():
    cfg = config()
    env = environment()
    env["PGPASSWORD"] = cfg["db_password"]
    import psycopg

    with psycopg.connect(
        host="127.0.0.1",
        port=55432,
        user="contractor",
        password=cfg["db_password"],
        dbname="postgres",
        autocommit=True,
    ) as db:
        if not db.execute(
            "SELECT 1 FROM pg_database WHERE datname='contractor_max_test'"
        ).fetchone():
            db.execute("CREATE DATABASE contractor_max_test")
    run(
        [PYTHON, "-m", "app.cli", "migrate"],
        cwd=ROOT / "backend",
        env=env | {"ORGANIZATIONS_ENABLED": "false"},
    )
    run(
        [PYTHON, ROOT / "ops" / "contractor_bootstrap.py"],
        cwd=ROOT / "backend",
        env=env,
    )
    print("Initialized. Private local credentials: .local/config.json")


def available(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def start(open_browser=False):
    cfg = config()
    if not DATA.exists():
        raise RuntimeError("Database has not been initialized")
    state_path = LOCAL / "processes.json"
    if state_path.exists():
        raise RuntimeError(
            "A previous start is recorded. Use stop before starting again"
        )
    if not available(8014) or not available(5174):
        raise RuntimeError("Port 8014 or 5174 is occupied; no process was replaced")
    database_start()
    node = (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "nodejs"
        / "node.exe"
    )
    if not node.exists():
        raise RuntimeError("Node.js not found")
    commands = [
        (
            "backend",
            [
                PYTHON,
                "-m",
                "uvicorn",
                "app.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8014",
            ],
            ROOT / "backend",
        ),
        (
            "frontend",
            [
                node,
                ROOT / "frontend/node_modules/vite/bin/vite.js",
                "--host",
                "127.0.0.1",
                "--port",
                "5174",
                "--strictPort",
            ],
            ROOT / "frontend",
        ),
    ]
    processes = []
    try:
        for name, args, cwd in commands:
            with (LOCAL / "logs" / (name + ".log")).open("ab") as log:
                child = subprocess.Popen(
                    [str(a) for a in args],
                    cwd=cwd,
                    env=environment(),
                    stdout=log,
                    stderr=log,
                    creationflags=FLAGS,
                )
            processes.append(
                {
                    "name": name,
                    "pid": child.pid,
                    "created": psutil.Process(child.pid).create_time(),
                }
            )
            state_path.write_text(json.dumps(processes), encoding="utf-8")
        for _ in range(45):
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:8014/api/health", timeout=1
                ).close()
                urllib.request.urlopen("http://127.0.0.1:5174", timeout=1).close()
                url = (
                    "http://127.0.0.1:5174/org/"
                    + cfg["organization_id"]
                    + "/contractor/vehicles"
                )
                print(url)
                if open_browser:
                    webbrowser.open(url)
                return
            except (OSError, TimeoutError):
                time.sleep(1)
        raise RuntimeError("Startup failed. See .local/logs; use stop before retrying")
    except Exception:
        stop()
        raise


def stop():
    state_path = LOCAL / "processes.json"
    if state_path.exists():
        # Verify command line before terminating a recorded PID: Windows may
        # reuse a PID after a crash. Never kill a process just by its port/name.
        for item in json.loads(state_path.read_text(encoding="utf-8")):
            try:
                process = psutil.Process(item["pid"])
                if (
                    "created" in item
                    and abs(process.create_time() - item["created"]) > 0.01
                ):
                    raise RuntimeError(
                        "Recorded PID has been reused; refusing to stop it"
                    )
                if str(ROOT).casefold() not in " ".join(process.cmdline()).casefold():
                    raise RuntimeError(
                        "Recorded process does not belong to this workspace"
                    )
                children = process.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                process.terminate()
                psutil.wait_procs([process, *children], timeout=10)
            except psutil.NoSuchProcess:
                pass
        state_path.unlink()
    if DATA.exists():
        result = subprocess.run(
            [str(PG / "pg_ctl.exe"), "-D", str(DATA), "status"],
            capture_output=True,
            creationflags=FLAGS,
        )
        if result.returncode == 0:
            run([PG / "pg_ctl.exe", "-D", DATA, "-m", "fast", "-w", "stop"])
    print("Local contractor services stopped; data retained")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "stop", "import"])
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if args.action == "init":
        init()
    elif args.action == "start":
        start(args.open_browser)
    elif args.action == "stop":
        stop()
    else:
        database_start()
        run(
            [
                PYTHON,
                "-m",
                "app.contractor_import",
                "--sources",
                LOCAL / "sources",
                "--questionnaires",
                ROOT.parent / "questionnaires",
            ],
            cwd=ROOT / "backend",
            env=environment(),
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        raise SystemExit(1) from None
