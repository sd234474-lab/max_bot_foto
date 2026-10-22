"""Q1-01 isolated migration smoke: 0031 -> 0032 -> 0031 on a throwaway DB."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="Requires isolated CI Postgres")
def test_contract_migration_upgrade_and_downgrade_on_disposable_database():
    root = Path(__file__).resolve().parents[2]
    source_url = os.environ["TEST_DATABASE_URL"]
    database_name = "ctr007_q101_" + uuid4().hex[:12]
    # CI PostgreSQL administrative credentials; never connect to a configured production URL.
    if os.environ.get("APP_ENV") != "test" or "127.0.0.1" not in source_url:
        pytest.skip("Run only against isolated localhost test service")
    from sqlalchemy.engine import make_url

    parsed = make_url(source_url)
    database_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    admin_url = parsed.set(database="postgres").render_as_string(hide_password=False)

    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url
        env["APP_ENV"] = "test"

        def alembic(*args):
            subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *args],
                cwd=root, env=env, check=True, timeout=150,
            )

        alembic("upgrade", "0031")
        alembic("upgrade", "0032")
        with psycopg.connect(database_url) as connection:
            rows = connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='contracts'"
            ).fetchall()
            assert rows == [("contracts",)]
        alembic("downgrade", "0031")
        with psycopg.connect(database_url) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='contracts'"
            ).fetchone()[0]
            assert count == 0
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (database_name,)
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))
