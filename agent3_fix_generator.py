"""
AGENT 3 — Fix Generator
--------------------------------
Takes the ticket (Agent 1) + the candidate function (Agent 2) and asks a
coding-focused local model to generate a corrected version of that function.

If Agent 4 (next step) finds the fix doesn't pass its test, it calls this
again with test_feedback set, so Agent 3 can correct its previous attempt
instead of repeating the same mistake — this is what makes the retry loop
actually improve each time instead of just rerolling randomly.

Input:  parsed ticket dict (Agent 1) + candidate dict (Agent 2)
Output: {file_path, function_name, start_line, end_line, original_code, fixed_code}
"""

import difflib
import os

from dotenv import load_dotenv
from langchain_ollama import ChatOllama

import db
from agent1_parser import parse_ticket
from agent2_scanner import scan_codebase

load_dotenv()

CODE_MODEL = os.getenv("CODE_MODEL", "qwen2.5-coder:7b")

llm = ChatOllama(model=CODE_MODEL, temperature=0)


def _clean_llm_code_output(raw: str) -> str:
    """Models often wrap code in markdown fences even when told not to —
    strip ```python / ``` fences if present, and trim whitespace."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence (``` or ```python)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop trailing fence
        text = "\n".join(lines)
    return text.strip()


def generate_fix(parsed_ticket: dict, candidate: dict, test_feedback: str | None = None) -> dict:
    feedback_block = ""
    if test_feedback:
        feedback_block = f"""
Your previous attempt did NOT pass the test. Here is the failure output —
fix the code so this specific failure no longer happens:

{test_feedback}
"""

    prompt = f"""You are a precise Python bug-fixing assistant. You will be given a Jira
bug ticket and the exact function believed to contain the bug. Return a
corrected version of ONLY that function — same name, same signature, same
indentation style — with the bug fixed.

Rules:
- Return ONLY the corrected Python function code, nothing else.
- Do NOT include markdown code fences, explanations, or comments about what you changed.
- Do NOT change the function's name or its parameters.
- Keep the fix minimal and targeted at the described bug.
- If the fix requires catching an exception, catch the MOST SPECIFIC
  exception type available (e.g. a custom exception class already defined
  elsewhere in the file or module) — never a bare `except:` or
  `except Exception`, which would silently swallow unrelated bugs too.
- Do NOT invent new business rules, thresholds, special cases, or
  validation that the ticket never asked for. Fix only the specific defect
  described — nothing more.
- If a comment in the current code explains why a value only happens to
  work "by coincidence" or "in this specific case", that comment is
  describing the BUG itself, not a rule to preserve or build a condition
  around.

Ticket: {parsed_ticket['key']} — {parsed_ticket['summary']}
Description:
{parsed_ticket['description']}

File: {candidate['file_path']}
Full file, for context — sibling functions, custom exception classes, etc.
(do NOT modify anything here; this is reference only):
{candidate.get('file_source', candidate['code'])}

Target function to fix:
{candidate['code']}
{feedback_block}
Return ONLY the corrected version of the target function above:"""

    response = llm.invoke(prompt)
    fixed_code = _clean_llm_code_output(response.content)

    return {
        "file_path": candidate["file_path"],
        "function_name": candidate["function_name"],
        "start_line": candidate["start_line"],
        "end_line": candidate["end_line"],
        "original_code": candidate["code"],
        "fixed_code": fixed_code,
    }


def show_diff(original_code: str, fixed_code: str) -> str:
    diff = difflib.unified_diff(
        original_code.splitlines(keepends=True),
        fixed_code.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
    )
    return "".join(diff)


if __name__ == "__main__":
    ticket_key = os.getenv("TICKET_KEY", "SCRUM-6")
    parsed = parse_ticket(ticket_key)
    candidates = scan_codebase(parsed)

    if not candidates:
        print("No candidate functions found by Agent 2.")
    else:
        top = candidates[0]
        print(f"Generating fix for {top['file_path']} -> {top['function_name']}()...\n")

        fix = generate_fix(parsed, top)

        print("=== Proposed fix (diff) ===")
        print(show_diff(fix["original_code"], fix["fixed_code"]))

        db.insert_fix_attempt(
            ticket_key=parsed["key"],
            attempt_number=1,
            candidate_file=fix["file_path"],
            candidate_function=fix["function_name"],
            generated_code=fix["fixed_code"],
            test_passed=False,  # not yet validated — that's Agent 4's job
            score=0,
            feedback="Awaiting Agent 4 validation",
        )
        print("\nSaved as fix_attempts #1 (not yet validated).")