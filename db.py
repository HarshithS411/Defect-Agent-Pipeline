"""
DB layer — one SQLite file doing two jobs:
  1. Runtime tables: what happened on each ticket (tickets, fix_attempts,
     resolutions) — your audit trail.
  2. Knowledge table: solved tickets + their fix (solutions) — this is what
     later steps query instead of the hardcoded GraphRAG-lite list.
"""

import json
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "defect_agent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_key TEXT PRIMARY KEY,
    summary TEXT,
    description TEXT,
    status TEXT,
    component TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fix_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_key TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    candidate_file TEXT,
    candidate_function TEXT,
    generated_code TEXT,
    test_passed INTEGER,
    score INTEGER,
    feedback TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_key) REFERENCES tickets(ticket_key)
);

CREATE TABLE IF NOT EXISTS resolutions (
    ticket_key TEXT PRIMARY KEY,
    final_file TEXT,
    final_function TEXT,
    final_diff TEXT,
    pr_url TEXT,
    approved INTEGER,
    resolved_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (ticket_key) REFERENCES tickets(ticket_key)
);

CREATE TABLE IF NOT EXISTS solutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_key TEXT,
    component TEXT,
    problem_summary TEXT,
    solution_summary TEXT,
    file_path TEXT,
    function_name TEXT,
    embedding TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print(f"Database ready at {DB_PATH}")


def upsert_ticket(ticket_key: str, summary: str, description: str,
                   status: str, component: str | None = None) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO tickets (ticket_key, summary, description, status, component)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(ticket_key) DO UPDATE SET
             summary = excluded.summary,
             description = excluded.description,
             status = excluded.status,
             component = excluded.component""",
        (ticket_key, summary, description, status, component),
    )
    conn.commit()
    conn.close()


def insert_fix_attempt(ticket_key: str, attempt_number: int, candidate_file: str,
                        candidate_function: str, generated_code: str,
                        test_passed: bool, score: int, feedback: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fix_attempts
           (ticket_key, attempt_number, candidate_file, candidate_function,
            generated_code, test_passed, score, feedback)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ticket_key, attempt_number, candidate_file, candidate_function,
         generated_code, int(test_passed), score, feedback),
    )
    conn.commit()
    conn.close()


def insert_resolution(ticket_key: str, final_file: str, final_function: str,
                       final_diff: str, pr_url: str | None, approved: bool) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO resolutions
           (ticket_key, final_file, final_function, final_diff, pr_url, approved)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(ticket_key) DO UPDATE SET
             final_file = excluded.final_file,
             final_function = excluded.final_function,
             final_diff = excluded.final_diff,
             pr_url = excluded.pr_url,
             approved = excluded.approved""",
        (ticket_key, final_file, final_function, final_diff, pr_url, int(approved)),
    )
    conn.commit()
    conn.close()


def insert_solution(ticket_key: str, component: str, problem_summary: str,
                     solution_summary: str, file_path: str, function_name: str,
                     embedding: list[float]) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO solutions
           (ticket_key, component, problem_summary, solution_summary,
            file_path, function_name, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ticket_key, component, problem_summary, solution_summary,
         file_path, function_name, json.dumps(embedding)),
    )
    conn.commit()
    conn.close()


def get_all_solutions() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM solutions").fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        d["embedding"] = json.loads(d["embedding"]) if d["embedding"] else None
        results.append(d)
    return results


def get_ticket(ticket_key: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM tickets WHERE ticket_key = ?", (ticket_key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


if __name__ == "__main__":
    init_db()
