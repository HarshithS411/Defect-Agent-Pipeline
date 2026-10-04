"""
AGENT 2 — Scanner (read-only)
--------------------------------
Given a parsed ticket, scans the target codebase (a local clone of your
MiniShop repo) and finds the function(s) most likely related to the ticket,
using embedding similarity over every function in the codebase.

This agent NEVER writes to the codebase — it only reads files and returns
candidate locations for Agent 3 to act on later.

Input:  parsed ticket dict (from Agent 1)
Output: a list of candidates, best match first:
        {file_path, function_name, code, start_line, end_line, score}
"""

import ast
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from langchain_ollama import OllamaEmbeddings

from agent1_parser import parse_ticket

load_dotenv()

REPO_PATH = os.getenv("MINISHOP_REPO_PATH")
# Only look inside these folders — skip tests/, venv/, .git/, etc.
SCAN_FOLDERS = ("auth", "checkout", "payments", "cart")

embedder = OllamaEmbeddings(model="mxbai-embed-large")


def _extract_functions(file_path: Path) -> list[dict]:
    """Parses one .py file with Python's ast module and returns the source
    of every function/method defined in it (this is the 'chunking' step —
    chunk by function, not by raw line count)."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            code = ast.get_source_segment(source, node)
            if code:
                functions.append({
                    "function_name": node.name,
                    "code": code,
                    "start_line": node.lineno,
                    "end_line": node.end_lineno,
                    # Full file, so Agent 3 can see sibling functions, custom
                    # exception classes, etc. that the isolated function text
                    # alone wouldn't reveal.
                    "file_source": source,
                })
    return functions


def collect_codebase_functions(repo_path: str | None = None) -> list[dict]:
    """Walks the target repo and returns every function found, each tagged
    with the file it came from (relative path)."""
    repo_path = repo_path or REPO_PATH
    if not repo_path:
        raise ValueError("MINISHOP_REPO_PATH is not set in .env")

    repo = Path(repo_path)
    if not repo.exists():
        raise FileNotFoundError(f"Repo path does not exist: {repo}")

    all_functions = []
    for folder in SCAN_FOLDERS:
        folder_path = repo / folder
        if not folder_path.exists():
            continue
        for py_file in sorted(folder_path.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            for fn in _extract_functions(py_file):
                fn["file_path"] = str(py_file.relative_to(repo))
                all_functions.append(fn)

    return all_functions


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def scan_codebase(parsed_ticket: dict, repo_path: str | None = None, top_k: int = 3) -> list[dict]:
    """Read-only: embeds the ticket text and every function in the codebase,
    returns the top_k most similar functions, best match first."""
    functions = collect_codebase_functions(repo_path)
    if not functions:
        return []

    ticket_text = f"{parsed_ticket['summary']}\n{parsed_ticket['description']}"
    ticket_emb = np.array(embedder.embed_query(ticket_text))

    scored = []
    for fn in functions:
        fn_emb = np.array(embedder.embed_query(fn["code"]))
        score = _cosine_sim(ticket_emb, fn_emb)
        scored.append({**fn, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


if __name__ == "__main__":
    ticket_key = os.getenv("TICKET_KEY", "SCRUM-5")
    parsed = parse_ticket(ticket_key)
    print(f"Scanning codebase for ticket {ticket_key}...\n")

    candidates = scan_codebase(parsed)
    for i, c in enumerate(candidates, start=1):
        print(f"#{i}  score={c['score']:.3f}  {c['file_path']} -> "
              f"{c['function_name']}()  (lines {c['start_line']}-{c['end_line']})")