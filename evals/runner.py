"""Eval runner — load YAML test cases and run against live endpoints.

Usage:
    python -m evals.runner --suite intent_classification --base-url http://localhost:8000
    python -m evals.runner --suite entity_extraction --base-url http://localhost:8002
    python -m evals.runner --all --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

CASES_DIR = Path(__file__).parent / "cases"

# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------


def load_cases(suite: str) -> list[dict[str, Any]]:
    """Load eval cases from YAML file."""
    path = CASES_DIR / f"{suite}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Eval suite not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("cases", [])


# ---------------------------------------------------------------------------
# Runners per suite
# ---------------------------------------------------------------------------


async def run_intent_classification(
    cases: list[dict], base_url: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        message = case["input"]["message"]
        expected_intent = case["expected"]["intent"]
        try:
            resp = await client.post(
                f"{base_url}/chat",
                json={"message": message},
                timeout=10.0,
            )
            resp.raise_for_status()
            body = resp.json()
            actual_intent = body.get("intent", "")
            passed = actual_intent == expected_intent
        except Exception as exc:
            actual_intent = f"ERROR: {exc}"
            passed = False
        results.append(
            {
                "id": case.get("id", "?"),
                "input": message,
                "expected": expected_intent,
                "actual": actual_intent,
                "passed": passed,
            }
        )
    return results


async def run_entity_extraction(
    cases: list[dict], base_url: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        message = case["input"]["message"]
        expected = case["expected"]
        try:
            resp = await client.post(
                f"{base_url}/a2a/tasks",
                json={"skill": "create_order", "params": {"message": message}},
                timeout=10.0,
            )
            resp.raise_for_status()
            task_id = resp.json()["task_id"]

            # Poll for result
            for _ in range(20):
                await asyncio.sleep(0.5)
                poll = await client.get(f"{base_url}/a2a/tasks/{task_id}", timeout=5.0)
                body = poll.json()
                if body["status"] not in ("submitted", "working"):
                    break

            actual = body.get("result", {}).get("output", {})
            passed = _check_entity_match(actual, expected)
        except Exception as exc:
            actual = f"ERROR: {exc}"
            passed = False
        results.append(
            {
                "id": case.get("id", "?"),
                "input": message,
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    return results


async def run_nl2sql(
    cases: list[dict], base_url: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        message = case["input"]["message"]
        expected_sql_contains = case["expected"].get("sql_contains", [])
        try:
            resp = await client.post(
                f"{base_url}/a2a/tasks",
                json={"skill": "bi_query", "params": {"message": message}},
                timeout=10.0,
            )
            resp.raise_for_status()
            task_id = resp.json()["task_id"]

            for _ in range(20):
                await asyncio.sleep(0.5)
                poll = await client.get(f"{base_url}/a2a/tasks/{task_id}", timeout=5.0)
                body = poll.json()
                if body["status"] not in ("submitted", "working"):
                    break

            output = body.get("result", {}).get("output", {})
            actual_sql = output.get("generated_sql", "").upper()
            passed = all(kw.upper() in actual_sql for kw in expected_sql_contains)
            actual = actual_sql
        except Exception as exc:
            actual = f"ERROR: {exc}"
            passed = False
        results.append(
            {
                "id": case.get("id", "?"),
                "input": message,
                "expected": expected_sql_contains,
                "actual": actual,
                "passed": passed,
            }
        )
    return results


async def run_e2e_order(
    cases: list[dict], base_url: str, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        turns = case["input"]["turns"]
        expected = case["expected"]
        passed = False
        actual = {}
        try:
            session_id = None
            task_id = None

            for turn in turns:
                payload: dict[str, Any] = {"message": turn["message"]}
                if session_id:
                    payload["session_id"] = session_id
                if turn.get("continuation") and task_id:
                    payload["continuation"] = {
                        "task_id": task_id,
                        "user_input": turn["message"],
                    }
                    resp = await client.post(
                        f"{base_url}/a2a/tasks",
                        json={"skill": "create_order", "params": payload},
                        timeout=10.0,
                    )
                else:
                    resp = await client.post(
                        f"{base_url}/a2a/tasks",
                        json={"skill": "create_order", "params": payload},
                        timeout=10.0,
                    )
                resp.raise_for_status()
                task_id = resp.json()["task_id"]

                for _ in range(30):
                    await asyncio.sleep(0.5)
                    poll = await client.get(f"{base_url}/a2a/tasks/{task_id}", timeout=5.0)
                    body = poll.json()
                    if body["status"] not in ("submitted", "working"):
                        break

            actual = body.get("result", {}).get("output", {})
            if expected.get("order_code"):
                passed = bool(actual.get("order_code") or actual.get("order_result", {}).get("order_code"))
            else:
                passed = body["status"] == expected.get("final_status", "completed")
        except Exception as exc:
            actual = f"ERROR: {exc}"
            passed = False
        results.append(
            {
                "id": case.get("id", "?"),
                "input": [t["message"] for t in turns],
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_entity_match(actual: dict, expected: dict) -> bool:
    for key, val in expected.items():
        if actual.get(key) != val:
            return False
    return True


def _print_summary(suite: str, results: list[dict]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    rate = passed / total * 100 if total else 0

    print(f"\n{'='*60}")
    print(f"Suite: {suite}")
    print(f"{'='*60}")
    print(f"{'ID':<12} {'PASS':<6} {'INPUT':<40} {'ACTUAL':<30}")
    print(f"{'-'*100}")
    for r in results:
        status = "✓" if r["passed"] else "✗"
        input_str = str(r["input"])[:38]
        actual_str = str(r["actual"])[:28]
        print(f"{str(r['id']):<12} {status:<6} {input_str:<40} {actual_str:<30}")
    print(f"\n{'-'*60}")
    print(f"Result: {passed}/{total} passed ({rate:.1f}%)")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SUITE_RUNNERS = {
    "intent_classification": (run_intent_classification, "http://localhost:8000"),
    "entity_extraction": (run_entity_extraction, "http://localhost:8002"),
    "nl2sql": (run_nl2sql, "http://localhost:8003"),
    "e2e_order": (run_e2e_order, "http://localhost:8002"),
}


async def main(suites: list[str], base_url_override: str | None = None) -> None:
    async with httpx.AsyncClient() as client:
        for suite in suites:
            if suite not in SUITE_RUNNERS:
                print(f"Unknown suite: {suite}. Available: {list(SUITE_RUNNERS)}")
                continue

            runner, default_url = SUITE_RUNNERS[suite]
            url = base_url_override or default_url

            try:
                cases = load_cases(suite)
            except FileNotFoundError as exc:
                print(f"Warning: {exc}")
                continue

            print(f"Running {suite} ({len(cases)} cases) against {url} ...")
            start = time.monotonic()
            results = await runner(cases, url, client)
            elapsed = time.monotonic() - start

            _print_summary(suite, results)
            print(f"Completed in {elapsed:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eval runner for agentic-saas")
    parser.add_argument("--suite", help="Suite name (e.g. intent_classification)")
    parser.add_argument("--all", action="store_true", help="Run all suites")
    parser.add_argument("--base-url", help="Override base URL for all suites")
    args = parser.parse_args()

    if args.all:
        suites_to_run = list(SUITE_RUNNERS)
    elif args.suite:
        suites_to_run = [args.suite]
    else:
        parser.print_help()
        raise SystemExit(1)

    asyncio.run(main(suites_to_run, args.base_url))
