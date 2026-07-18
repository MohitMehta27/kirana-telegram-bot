"""Apply sql/001_schema.sql + sql/002_seed.sql using .env DB_* credentials."""

from __future__ import annotations

import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def run_sql_file(cur, path: Path) -> tuple[int, int]:
    sql = path.read_text(encoding="utf-8")
    statements: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        s = line.strip()
        if s.startswith("--") or s == "":
            continue
        buf.append(line)
        if s.endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    ok = err = 0
    for stmt in statements:
        try:
            cur.execute(stmt)
            ok += 1
        except Exception as e:
            err += 1
            print(f"ERR in {path.name}: {e}")
    return ok, err


def main() -> None:
    load_dotenv(ROOT / ".env")
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    dbname = os.getenv("DB_NAME", "t_bot")

    conn = pymysql.connect(
        host=host, port=port, user=user, password=password, charset="utf8mb4", autocommit=True
    )
    cur = conn.cursor()
    cur.execute(
        f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cur.close()
    conn.close()

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=dbname,
        charset="utf8mb4",
        autocommit=True,
    )
    cur = conn.cursor()
    for name in ("001_schema.sql", "002_seed.sql"):
        ok, err = run_sql_file(cur, ROOT / "sql" / name)
        print(f"{name}: {ok} ok, {err} errors")
    cur.execute("SHOW TABLES")
    print("tables:", [r[0] for r in cur.fetchall()])
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
