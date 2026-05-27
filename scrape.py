#!/usr/bin/env python3
"""Count nginx access log lines per site/day (minus ignored User-Agents), split by HTTP status."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import DefaultDict, Iterator, Literal, TextIO

import pymysql

# nginx combined: "METHOD path HTTP/x.y" STATUS ...
STATUS_RE = re.compile(r'"[A-Z]+\s+[^"]*\s+HTTP/[^"]+"\s+(\d{3})\b')
TIME_LOCAL_RE = re.compile(r"\[(\d{2}/\w{3}/\d{4}):")
USER_AGENT_RE = re.compile(r'"([^"]*)"\s*$')

Outcome = Literal["success", "fail"]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nginx_daily_traffic (
    site VARCHAR(64) NOT NULL,
    day DATE NOT NULL,
    success_count INT NOT NULL DEFAULT 0,
    fail_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (site, day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

UPSERT_SQL = """
INSERT INTO nginx_daily_traffic (site, day, success_count, fail_count)
VALUES (%s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    success_count = VALUES(success_count),
    fail_count = VALUES(fail_count),
    updated_at = CURRENT_TIMESTAMP;
"""


@dataclass
class DayCounts:
    success: int = 0
    fail: int = 0
    skipped_3xx: int = 0


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_ignore_patterns(path: Path) -> list[str]:
    if not path.is_file():
        return []
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line.lower())
    return patterns


def should_ignore(user_agent: str, patterns: list[str]) -> bool:
    ua = user_agent.lower()
    return any(p in ua for p in patterns)


def parse_day(line: str) -> date | None:
    m = TIME_LOCAL_RE.search(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d/%b/%Y").date()
    except ValueError:
        return None


def parse_status(line: str) -> int | None:
    m = STATUS_RE.search(line)
    if not m:
        return None
    return int(m.group(1))


def parse_user_agent(line: str) -> str:
    m = USER_AGENT_RE.search(line)
    return m.group(1) if m else ""


def classify_status(status: int) -> Outcome | None:
    if 200 <= status < 300:
        return "success"
    if 300 <= status < 400:
        return None
    return "fail"


def open_log(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def iter_log_lines(log_dir: Path, log_file: str) -> Iterator[str]:
    """Read main log file; also read log_file.1 if present (common after logrotate)."""
    main = log_dir / log_file
    paths = [main]
    rotated = log_dir / f"{log_file}.1"
    if rotated.is_file():
        paths.append(rotated)
    for p in paths:
        if not p.is_file():
            continue
        with open_log(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line


def count_site(
    site_name: str,
    log_dir: Path,
    log_file: str,
    ignore_patterns: list[str],
) -> dict[date, DayCounts]:
    counts: DefaultDict[date, DayCounts] = defaultdict(DayCounts)
    if not log_dir.is_dir():
        raise FileNotFoundError(f"log_dir not found: {log_dir}")

    for line in iter_log_lines(log_dir, log_file):
        day = parse_day(line)
        if day is None:
            continue
        ua = parse_user_agent(line)
        if should_ignore(ua, ignore_patterns):
            continue
        status = parse_status(line)
        if status is None:
            continue
        outcome = classify_status(status)
        bucket = counts[day]
        if outcome is None:
            bucket.skipped_3xx += 1
        elif outcome == "success":
            bucket.success += 1
        else:
            bucket.fail += 1

    return dict(counts)


def mysql_connect(cfg: dict) -> pymysql.connections.Connection:
    mysql = cfg["mysql"]
    env = os.environ
    return pymysql.connect(
        host=env.get("MYSQL_HOST", mysql.get("host", "127.0.0.1")),
        port=int(env.get("MYSQL_PORT", mysql.get("port", 3306))),
        user=env.get("MYSQL_USER", mysql["user"]),
        password=env.get("MYSQL_PASSWORD", mysql["password"]),
        database=env.get("MYSQL_DATABASE", mysql["database"]),
        charset="utf8mb4",
        autocommit=False,
    )


def _table_columns(conn: pymysql.connections.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COLUMN_NAME FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nginx_daily_traffic'
            """
        )
        return {row[0] for row in cur.fetchall()}


def ensure_table(conn: pymysql.connections.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()

    cols = _table_columns(conn)
    if not cols:
        return

    with conn.cursor() as cur:
        if "success_count" not in cols:
            cur.execute(
                "ALTER TABLE nginx_daily_traffic "
                "ADD COLUMN success_count INT NOT NULL DEFAULT 0"
            )
        if "fail_count" not in cols:
            cur.execute(
                "ALTER TABLE nginx_daily_traffic "
                "ADD COLUMN fail_count INT NOT NULL DEFAULT 0"
            )
        if "count" in cols:
            cur.execute("ALTER TABLE nginx_daily_traffic DROP COLUMN count")
    conn.commit()


def write_counts(
    conn: pymysql.connections.Connection,
    site: str,
    counts: dict[date, DayCounts],
) -> int:
    rows = 0
    with conn.cursor() as cur:
        for day, bucket in sorted(counts.items()):
            cur.execute(
                UPSERT_SQL,
                (site, day.isoformat(), bucket.success, bucket.fail),
            )
            rows += 1
    conn.commit()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape nginx access logs into MySQL.")
    parser.add_argument(
        "-c",
        "--config",
        default="config.json",
        help="Path to config JSON (default: config.json)",
    )
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    cfg = load_config(config_path)
    ignore_path = Path(cfg.get("ignore_agents_file", "ignore_agents.txt"))
    ignore_patterns = load_ignore_patterns(ignore_path)

    all_counts: dict[str, dict[date, DayCounts]] = {}

    for site in cfg["sites"]:
        name = site["name"]
        log_dir = Path(site["log_dir"])
        log_file = site["log_file"]
        print(f"Scraping {name}: {log_dir / log_file}")
        all_counts[name] = count_site(name, log_dir, log_file, ignore_patterns)
        for d, bucket in sorted(all_counts[name].items()):
            print(
                f"  {d.isoformat()}: success={bucket.success} fail={bucket.fail}"
                f" (3xx skipped={bucket.skipped_3xx})"
            )

    conn = mysql_connect(cfg)
    try:
        ensure_table(conn)
        total_rows = 0
        for site_name, counts in all_counts.items():
            total_rows += write_counts(conn, site_name, counts)
        print(f"Upserted {total_rows} row(s) into MySQL.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
