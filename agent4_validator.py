"""
AGENT 4 — Validator
--------------------------------
Takes a fix from Agent 3, applies it to a TEMPORARY COPY of the repo (the
real codebase is never touched here), runs the one pytest test that covers
this bug, and scores it based on a real pass/fail — not an LLM's opinion of
its own work.

If the test fails, the failure output is fed back into Agent 3 for another
attempt, up to MAX_ATTEMPTS times.

Input:  parsed ticket (Agent 1) + candidate (Agent 2)
Output: {success, attempts, fix, test_output}
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

import db
from agent1_parser import parse_ticket
from agent2_scanner import scan_codebase
from agent3_fix_generator import generate_fix, show_diff

load_dotenv()

MAX_ATTEMPTS = 3

# Which pytest test covers each function. In a bigger real codebase this
# would come from coverage data or test tags instead of a hand-written map,
# but for this project's known 1-bug-per-function layout, a direct map is
# simple and exact.
FUNCTION_TEST_MAP = {
    "reset_password": "tests/test_auth.py::test_login_after_reset",
    "issue_token": "tests/test_auth.py::test_token_expiry_duration",
    "calculate_total": "tests/test_checkout.py::test_checkout_with_discount",
    "process_payment": "tests/test_payments.py::test_payment_gateway_timeout",
    "update_quantity": "tests/test_cart.py::test_cart_negative_quantity",
}


def get_test_for_function(function_name: str) -> str | None:
    return FUNCTION_TEST_MAP.get(function_name)


def _apply_fix_to_copy(temp_repo: Path, fix: dict) -> None:
    target_file = temp_repo / fix["file_path"]
    content = target_file.read_text(encoding="utf-8")
    if fix["original_code"] not in content:
        raise ValueError(
            f"Could not locate the original function text in {fix['file_path']} "
            "to apply the patch — the file may have changed since Agent 2 scanned it."
        )
    patched = content.replace(fix["original_code"], fix["fixed_code"], 1)
    target_file.write_text(patched, encoding="utf-8")


def run_test(repo_path: str, fix: dict, test_node_id: str) -> tuple[bool, str]:
    """Copies repo_path to a temp dir, applies the fix there, runs the one
    relevant pytest test, and returns (passed, captured_output). The real
    repo on disk is never modified."""
    temp_dir = tempfile.mkdtemp(prefix="defect_agent_validation_")
    try:
        temp_repo = Path(temp_dir) / "repo"
        shutil.copytree(
            repo_path, temp_repo,
            ignore=shutil.ignore_patterns("venv", "__pycache__", ".pytest_cache", ".git"),
        )

        try:
            _apply_fix_to_copy(temp_repo, fix)
        except ValueError as e:
            return False, str(e)

        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_node_id, "-v"],
            cwd=temp_repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        passed = result.returncode == 0
        output = result.stdout + result.stderr
        return passed, output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def validate_with_retries(parsed_ticket: dict, candidate: dict, repo_path: str,
                           max_attempts: int = MAX_ATTEMPTS) -> dict:
    test_node_id = get_test_for_function(candidate["function_name"])
    if not test_node_id:
        return {
            "success": False,
            "attempts": 0,
            "fix": None,
            "test_output": f"No known test mapped to function '{candidate['function_name']}'",
        }

    feedback = None
    fix = None

    for attempt in range(1, max_attempts + 1):
        fix = generate_fix(parsed_ticket, candidate, test_feedback=feedback)
        passed, output = run_test(repo_path, fix, test_node_id)

        db.insert_fix_attempt(
            ticket_key=parsed_ticket["key"],
            attempt_number=attempt,
            candidate_file=fix["file_path"],
            candidate_function=fix["function_name"],
            generated_code=fix["fixed_code"],
            test_passed=passed,
            score=100 if passed else 0,
            feedback=output[-2000:],  # keep the stored feedback bounded
        )

        status = "PASSED" if passed else "FAILED"
        print(f"Attempt {attempt}/{max_attempts}: {status}")

        if passed:
            return {"success": True, "attempts": attempt, "fix": fix, "test_output": output}

        feedback = output  # feed this failure into Agent 3's next attempt

    return {"success": False, "attempts": max_attempts, "fix": fix, "test_output": feedback}


if __name__ == "__main__":
    ticket_key = os.getenv("TICKET_KEY", "SCRUM-6")
    repo_path = os.getenv("MINISHOP_REPO_PATH")

    parsed = parse_ticket(ticket_key)
    candidates = scan_codebase(parsed)

    if not candidates:
        print("No candidate functions found by Agent 2.")
    else:
        top = candidates[0]
        print(f"Validating fixes for {top['file_path']} -> {top['function_name']}()\n")

        result = validate_with_retries(parsed, top, repo_path)

        if result["success"]:
            print(f"\n✅ Fix validated on attempt {result['attempts']} — test passed.")
            print("\n=== Final fix (diff) ===")
            print(show_diff(result["fix"]["original_code"], result["fix"]["fixed_code"]))
        else:
            print(f"\n❌ No passing fix found after {result['attempts']} attempts.")
            print("\nLast test output:")
            print(result["test_output"])
