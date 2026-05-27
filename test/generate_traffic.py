#!/usr/bin/env python3
"""Send HTTP requests to local nginx endpoints defined in a JSON config."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Target:
    ip: str
    host: str
    port: int
    path: str
    calls: int
    user_agent: str = "NginxMonitorTest/1.0"

    @property
    def url(self) -> str:
        path = self.path if self.path.startswith("/") else f"/{self.path}"
        return f"http://{self.ip}:{self.port}{path}"


@dataclass
class TargetResult:
    target: Target
    attempted: int = 0
    success: int = 0
    reached: int = 0
    failed: int = 0
    status_counts: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)


def load_targets(path: Path) -> tuple[list[Target], float]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    timeout = float(data.get("timeout_seconds", 5))
    targets: list[Target] = []
    for entry in data["targets"]:
        targets.append(
            Target(
                ip=entry["ip"],
                host=entry["host"],
                port=int(entry.get("port", 80)),
                path=entry.get("path", "/"),
                calls=int(entry["calls"]),
                user_agent=entry.get("user_agent", "NginxMonitorTest/1.0"),
            )
        )
    return targets, timeout


def send_request(target: Target, timeout: float) -> tuple[bool, bool, int | None, str | None]:
    """Return (is_2xx, reached_server, status_code, error)."""
    req = urllib.request.Request(
        target.url,
        headers={
            "Host": target.host,
            "User-Agent": target.user_agent,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            return 200 <= status < 300, True, status, None
    except urllib.error.HTTPError as e:
        return False, True, e.code, None
    except urllib.error.URLError as e:
        return False, False, None, str(e.reason)
    except TimeoutError:
        return False, False, None, "timeout"
    except OSError as e:
        return False, False, None, str(e)


def run_target(target: Target, timeout: float, dry_run: bool) -> TargetResult:
    result = TargetResult(target=target)

    if dry_run:
        result.attempted = target.calls
        result.success = target.calls
        result.reached = target.calls
        result.status_counts[200] = target.calls
        return result

    for _ in range(target.calls):
        result.attempted += 1
        ok, reached, status, err = send_request(target, timeout)
        if err:
            result.failed += 1
            result.errors[err] += 1
        elif reached and status is not None:
            result.reached += 1
            result.status_counts[status] += 1
            if ok:
                result.success += 1
        else:
            result.failed += 1
            result.errors["unknown"] += 1

    return result


def print_result(result: TargetResult) -> None:
    t = result.target
    print(f"\n{t.host} {t.path} → {t.url}")
    print(f"  User-Agent: {t.user_agent}")
    print(
        f"  attempted: {result.attempted}  reached: {result.reached}"
        f"  success (2xx): {result.success}  failed: {result.failed}"
    )
    if result.status_counts:
        print(f"  status: {dict(result.status_counts)}")
    if result.errors:
        print(f"  errors: {dict(result.errors)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate HTTP traffic to nginx endpoints.")
    parser.add_argument(
        "-c",
        "--config",
        default="test/targets.json",
        help="Path to targets JSON (default: test/targets.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned requests without sending HTTP traffic",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        print("Copy test/targets.example.json to test/targets.json and edit it.", file=sys.stderr)
        return 1

    targets, timeout = load_targets(config_path)

    if args.dry_run:
        print("DRY RUN — no HTTP requests will be sent\n")

    print(f"Loaded {len(targets)} endpoint(s), timeout={timeout}s")

    results: list[TargetResult] = []
    for target in targets:
        print(f"\n--- {target.calls} GET {target.url}  Host: {target.host}")
        results.append(run_target(target, timeout, args.dry_run))

    total_attempted = 0
    total_reached = 0
    total_success = 0
    total_failed = 0

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for result in results:
        total_attempted += result.attempted
        total_reached += result.reached
        total_success += result.success
        total_failed += result.failed
        print_result(result)

    print("\nTOTAL")
    print(f"  attempted: {total_attempted}")
    print(f"  reached: {total_reached}")
    print(f"  success (2xx): {total_success}")
    print(f"  failed: {total_failed}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
