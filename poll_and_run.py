"""
AUTO PIPELINE — poll Jira, run the full agent chain on every new ticket
--------------------------------
This is the "partial production" entry point you asked for: instead of
manually editing TICKET_KEY and running agent5_approval.py by hand, this
polls Jira on a loop and automatically runs

    Agent1 (parse) -> Agent2 (scan) -> Agent4's retry loop (which calls
    Agent3 as needed) -> Agent5 (diff + YOUR terminal approval + commit/PR
    + Jira write-back)

for every new "To Do" ticket it finds, tagging each one with an
'ai-processed' label afterward so it's never picked up twice.

Agent 5 still pauses and waits for your y/N approval in the terminal —
that's intentional, the human-approval gate doesn't go away just because
detection is now automatic. In a real production system this blocking
prompt would instead be a queued notification (Slack message, review
dashboard, etc.) that someone responds to later; here, keeping it a direct
terminal prompt is the simpler "partial production" version of that same
gate.

Run with:  python poll_and_run.py
Stop with: Ctrl+C
"""

import os
import time

from dotenv import load_dotenv
from jira import JIRA

from agent1_parser import parse_ticket
from agent2_scanner import scan_codebase
from agent3_fix_generator import show_diff
from agent4_validator import validate_with_retries
from agent5_approval import run_agent5

load_dotenv()

JIRA_URL = os.getenv("JIRA_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
PROJECT_KEY = os.getenv("PROJECT_KEY", "SCRUM")
REPO_PATH = os.getenv("MINISHOP_REPO_PATH")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

PROCESSED_LABEL = "ai-processed"


def get_jira_client() -> JIRA:
    return JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))


def find_new_tickets(jira: JIRA):
    jql = (
        f'project = {PROJECT_KEY} AND status = "To Do" '
        f'AND (labels is EMPTY OR labels not in ({PROCESSED_LABEL}))'
    )
    return jira.search_issues(jql)


def mark_as_processed(jira: JIRA, issue_key: str) -> None:
    issue = jira.issue(issue_key)
    current_labels = list(issue.fields.labels or [])
    if PROCESSED_LABEL not in current_labels:
        current_labels.append(PROCESSED_LABEL)
        issue.update(fields={"labels": current_labels})


def process_ticket(ticket_key: str) -> None:
    print(f"\n{'=' * 60}\nProcessing {ticket_key}\n{'=' * 60}")

    parsed = parse_ticket(ticket_key)
    candidates = scan_codebase(parsed)

    if not candidates:
        print(f"No candidate functions found for {ticket_key} — skipping.")
        return

    top = candidates[0]
    result = validate_with_retries(parsed, top, REPO_PATH)

    if not result["success"]:
        print(f"No passing fix found for {ticket_key} after {result['attempts']} attempts.")
        if result.get("fix"):
            print("\n=== Last attempted fix (diff) ===")
            print(show_diff(result["fix"]["original_code"], result["fix"]["fixed_code"]))
        print("\n=== Last test output ===")
        print(result["test_output"])
        return

    # run_agent5 prints the diff, blocks on your terminal approval, and
    # (on approval) commits/PRs + writes back to the Jira ticket.
    run_agent5(ticket_key, parsed, result["fix"])


def run_once(jira: JIRA) -> None:
    new_issues = find_new_tickets(jira)
    if not new_issues:
        print("No new tickets found.")
        return

    for issue in new_issues:
        process_ticket(issue.key)
        mark_as_processed(jira, issue.key)


def run_forever() -> None:
    jira = get_jira_client()
    print(f"Polling project '{PROJECT_KEY}' every {POLL_INTERVAL_SECONDS}s. "
          f"Press Ctrl+C to stop.")
    try:
        while True:
            run_once(jira)
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped polling.")


if __name__ == "__main__":
    run_forever()