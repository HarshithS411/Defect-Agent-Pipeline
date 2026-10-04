# Defect Agent Pipeline — DB + Agent1 (Parser) + Agent2 (Scanner)

This is a **separate project folder from MiniShop**. MiniShop is the target
codebase being scanned and fixed; this pipeline is the automation that acts
on it. Keep them as two separate folders (two separate git repos, ideally).

```
defect_agent_pipeline/
├── db.py               # SQLite schema + helper functions
├── agent1_parser.py     # fetches a Jira ticket, stores it in the DB
├── agent2_scanner.py    # read-only: finds the matching function in MiniShop
├── requirements.txt
└── .env.example
```

## Setup

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in:
- Your Jira credentials (same as before)
- `MINISHOP_REPO_PATH` — the **local folder path** where you cloned MiniShop
  (e.g. your `Defect-Resolution-Agent_Demo` folder)

Make sure Ollama is running (`mxbai-embed-large` is used for Agent 2's
similarity search).

## The database

Run once to create it:
```bash
python db.py
```

This creates `defect_agent.db` (SQLite) with four tables:

| Table | Purpose |
|---|---|
| `tickets` | every ticket Agent 1 has parsed |
| `fix_attempts` | every Agent3→Agent4 round (added in the next step) — code generated, test pass/fail, score |
| `resolutions` | the final accepted fix per ticket — diff, PR url, approved y/n |
| `solutions` | the knowledge base: past ticket + its solution + embedding, so future tickets can match against real history instead of hardcoded examples |

You can inspect it anytime with `sqlite3 defect_agent.db` or any SQLite
viewer/VS Code extension.

## Agent 1 — Parser

```bash
python agent1_parser.py
```
Fetches the ticket in `TICKET_KEY` (from `.env`), guesses a component from
keywords, and saves it to the `tickets` table. Prints what it found.

## Agent 2 — Scanner (read-only)

```bash
python agent2_scanner.py
```
Runs Agent 1 first (to get the parsed ticket), then:
1. Walks `MINISHOP_REPO_PATH`'s `auth/`, `checkout/`, `payments/`, `cart/`
   folders and extracts every function using Python's `ast` module.
2. Embeds the ticket text and every function's code with
   `mxbai-embed-large`.
3. Ranks functions by cosine similarity to the ticket and prints the top 3.

For ticket SCRUM-5 ("Login fails with 500 error after password reset"), you
should see `auth/service.py -> reset_password()` at or near the top.

Agent 2 **never modifies** anything — it only reads files and returns
candidate locations. That's intentional: finding the bug and fixing the bug
are separate responsibilities, which keeps Agent 3 (next step) focused and
makes Agent 2 safe to run as often as you like.

## Agent 3 — Fix Generator

```bash
python agent3_fix_generator.py
```
Runs Agent 1 + Agent 2 first, takes Agent 2's top candidate function, and
asks `CODE_MODEL` (default `qwen2.5-coder:7b` — pull it with
`ollama pull qwen2.5-coder:7b` if you haven't) to return a corrected
version of just that function. Prints the proposed change as a unified
diff, and saves it to the `fix_attempts` table (not yet validated — that's
Agent 4, next).

Agent 3 does **not** touch any files on disk — it only generates a
candidate fix in memory/DB. Writing to the actual codebase only happens
after Agent 4 confirms the fix is correct and a human approves it (Agent 5).

If the diff looks wrong or unrelated to the bug, that's useful signal too —
it usually means either the candidate function from Agent 2 wasn't the
right one, or the coding model needs a stronger prompt/model for that
particular bug.

## Agent 4 — Validator

```bash
python agent4_validator.py
```
This is the agent that makes "score it, redo if low" real instead of just
an LLM grading itself:

1. Copies MiniShop to a temporary folder (the real repo on disk is never
   touched).
2. Applies Agent 3's fix to that temp copy.
3. Runs the ONE pytest test that covers this specific bug (mapped by
   function name in `FUNCTION_TEST_MAP`).
4. If it passes → done, score 100, stop.
5. If it fails → the test's failure output is fed back into Agent 3 as
   `test_feedback`, which generates a new attempt. Repeats up to
   `MAX_ATTEMPTS` (default 3).

Every attempt (pass or fail) is logged to `fix_attempts` in the DB —
useful if you want to see later how many tries a given bug actually took.

Make sure `pytest` is installed in **this project's** venv too (not just
MiniShop's) — Agent 4 runs MiniShop's tests using this environment's
Python:
```bash
pip install pytest
```
(it's already in `requirements.txt`, so a normal `pip install -r requirements.txt` covers it)

## Agent 5 — Approval + Commit/PR

```bash
python agent5_approval.py
```

Runs the full Agent1 → Agent2 → Agent3/4 (with retries) chain, then:

1. Prints the validated fix as a diff.
2. Asks you directly in the terminal: `Approve and commit this fix? [y/N]`
3. **If you type `y`:**
   - With `GITHUB_TOKEN` + `GITHUB_REPO` set: creates a branch
     (`fix/scrum-8`), applies the fix, commits, pushes, opens a PR on
     GitHub, and merges it immediately (squash merge) — since you already
     approved in the terminal, there's no second approval needed on
     GitHub's side. You still get a real PR as a visible record.
   - Without those set: commits and pushes the fix directly to whatever
     branch is currently checked out — the fallback path, no PR created.
4. **If you type anything else:** nothing is written to the real repo at
   all. The rejection is logged in `resolutions` (`approved = 0`).
5. On success either way, writes the ticket + diff + an embedding of the
   problem into the `solutions` table — this is the "knowledge source"
   step. Future tickets don't use this yet (that'd mean teaching Agent 2/3
   to check `solutions` before falling back to a fresh fix — a natural
   follow-up once you've resolved a few more tickets).

### GitHub PAT setup (optional but recommended)

1. Go to https://github.com/settings/personal-access-tokens → **Generate
   new token (fine-grained)**.
2. Scope it to **only your MiniShop repo**.
3. Under Repository permissions, grant: **Contents: Read and write**,
   **Pull requests: Read and write**.
4. Copy the token into `.env` as `GITHUB_TOKEN`, and set `GITHUB_REPO` to
   `yourusername/your-repo-name`.

### What I verified before handing this over

Using a local throwaway git repo seeded with MiniShop's real code (no
actual GitHub push, since that needs your repo/token):
- The fix is correctly applied to the real file content (not a copy).
- `git add` + `git commit` produce a clean commit with the correct diff.
- The working tree is clean afterward — no stray changes left behind.

The push/PR/merge calls themselves need your real GitHub repo + token to
test — that's on you to confirm the first time you run this for real.

## Auto-pipeline — poll Jira and run everything automatically

```bash
python poll_and_run.py
```

This is the "partial production" version: instead of manually setting
`TICKET_KEY` and running `agent5_approval.py` by hand, this runs
continuously and checks Jira every `POLL_INTERVAL_SECONDS` (default 60s)
for new "To Do" tickets. For each one it finds, it automatically runs the
full chain — Agent1 → Agent2 → Agent4's retry loop (calling Agent3 as
needed) → Agent5 — then tags the ticket `ai-processed` so it's never
picked up again.

**Agent5's approval prompt still blocks and waits for you** when a new
ticket is found — the human-approval gate doesn't disappear just because
detection became automatic. In a real production system that blocking
terminal prompt would instead be a queued Slack/dashboard notification
someone responds to later; here it's simpler to just leave it as a direct
prompt. Practically: leave this running in a terminal, and when you see a
diff appear asking for approval, that's your cue to look and respond.

To test it: run `poll_and_run.py`, then — while it's running — create a
new Bug ticket in Jira from another tab (e.g. bug #3 or #5, using the
ticket text from the MiniShop README). Within one poll cycle, you should
see it picked up and run through the whole pipeline automatically, pausing
at the approval prompt exactly like the manual runs did.

Stop it any time with Ctrl+C.

## Writing the resolution back to Jira

As of this update, Agent 5 also closes the loop on the Jira side:

- **If you approve a fix**: posts a comment on the ticket with the PR link
  (or a note that it was committed directly), and tries to transition the
  ticket to a "Done"/"Resolved"/"Closed" status — whichever of those
  exists in your Jira workflow. If none match, the comment still posts and
  the status is just left alone (not an error).
- **If you reject a fix**: posts a comment noting an automated attempt was
  made and rejected, so there's a visible trail even for fixes that didn't
  make it in. Status is left untouched.

### A note on what this agent does NOT catch

Agent 4's test passing does not mean the code is *well-written* — only
that it produces the expected output for that one test case. You are the
real quality gate here: read the diff before typing `y`. (Concretely: on
bug #4 in this project, the validated fix used `except Exception` instead
of the more correct `except PaymentGatewayTimeout` — it passed the test
but was too broad. A human reviewing the diff would catch that in seconds;
Agent 4 alone would not.)
