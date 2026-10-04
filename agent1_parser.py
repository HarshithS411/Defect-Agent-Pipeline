"""
AGENT 1 — Parser
--------------------------------
Fetches a Jira ticket and extracts what the rest of the pipeline needs,
then stores it in the DB (tickets table).

Input:  a Jira ticket key, e.g. "SCRUM-5"
Output: a structured dict {key, summary, description, status, component}
"""

import os

from dotenv import load_dotenv
from jira import JIRA

import db

load_dotenv()

JIRA_URL = os.getenv("JIRA_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")

# A rough first guess at which component a ticket belongs to, based on
# keywords in its text. Agent 2's codebase scan will find the real location
# regardless — this is just a cheap hint stored alongside the ticket.
COMPONENT_KEYWORDS = {
    "login": "Auth Service",
    "password": "Auth Service",
    "token": "Auth Service",
    "session": "Auth Service",
    "checkout": "Checkout",
    "discount": "Checkout",
    "payment": "Payments",
    "gateway": "Payments",
    "cart": "Cart",
    "quantity": "Cart",
}


def _guess_component(text: str) -> str | None:
    text_lower = text.lower()
    for keyword, component in COMPONENT_KEYWORDS.items():
        if keyword in text_lower:
            return component
    return None


def parse_ticket(ticket_key: str) -> dict:
    jira = JIRA(server=JIRA_URL, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))
    issue = jira.issue(ticket_key)

    summary = issue.fields.summary
    description = issue.fields.description or ""
    status = str(issue.fields.status)
    component = _guess_component(f"{summary} {description}")

    parsed = {
        "key": issue.key,
        "summary": summary,
        "description": description,
        "status": status,
        "component": component,
    }

    db.upsert_ticket(
        ticket_key=parsed["key"],
        summary=parsed["summary"],
        description=parsed["description"],
        status=parsed["status"],
        component=parsed["component"],
    )

    return parsed


if __name__ == "__main__":
    db.init_db()
    ticket_key = os.getenv("TICKET_KEY", "SCRUM-5")
    result = parse_ticket(ticket_key)
    print("Parsed ticket:")
    for k, v in result.items():
        print(f"  {k}: {v}")
