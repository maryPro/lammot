# AGENTS.md — AI Agent Workflow & Rules for Lammot

This file is mandatory reading before making any changes or plans. For detailed bugfix procedures, see [docs/bugfix-workflow.md](docs/bugfix-workflow.md).

## Project Overview

Lammot is a Telegram Mini App music player.

- **Backend:** Python 3.12, FastAPI, SQLite (`aiosqlite`), `ytmusicapi`, `yt-dlp`.
- **Frontend:** `frontend/index.html`, `frontend/style.css`, `frontend/app.js` (vanilla JS, no frameworks).
- **Entry Point:** `app/main.py`. Architecture: `ARCHITECTURE.md`. UI Spec: `UI_SPEC.md`.
- **Project status:** `docs/PROJECT_STATUS.md` — read this first for current tasks, known issues, and recent decisions.
- **Deployment:** Docker on Railway auto-deployed from `main`.

## Commands

- **Unit/non-network tests:**
  `python -m pytest -m "not integration" -v`
  *(If the isolated worktree lacks its own `venv`, run via the main environment):*
  `/Users/mariia/lammot-main/venv/bin/python -m pytest -m "not integration" -v`
- **Integration tests (real YouTube requests — run ONLY when explicitly requested):**
  `python -m pytest -m integration -v`
- **Frontend JS tests (built-in Node test runner, no npm):**
  `node --test tests/js/player-time.test.js`
- **Local server run (requires activated `venv`):**
  `uvicorn app.main:app --env-file .env --port 8000 --reload`

## Multi-Agent Roles & Workflow

### 1. Project Manager & Architect (`Plan` / `Ask` Mode — in `local main`)
- **Read & Plan Only:** Never modify application code (`app/`, `frontend/`, `tests/`). Only read files, check Git status/history, update documentation (`ARCHITECTURE.md`, `UI_SPEC.md`), and save step-by-step feature plans in `.kilo/plans/<feature-name>.md`.
- **GitHub Sync:** Whenever the user says "Sync", "What's new?", or starts planning a new feature, run `git fetch`, `git pull origin main`, and `git log -n 5 --oneline` in the terminal to stay aligned with GitHub.
- **Communication Style:** Explain everything in plain, clear language without unnecessary jargon. When explaining how the project or a feature works, always structure the response in two parts:
  1. **User Perspective:** How it behaves and impacts the end user.
  2. **Technical Perspective:** Which modules, files, functions, and database tables are involved.

### 2. Bug Detective (`Debug` Mode — in an isolated Git worktree)
- Work strictly inside an isolated Git worktree created for a single bug.
- Do not guess fixes blindly. Inspect logs, trace execution paths, test hypotheses, and pinpoint the exact root cause (file, function, and line number) before switching to `Code` mode.

### 3. Developer (`Code` Mode — in an isolated Git worktree)
- Work strictly inside an isolated Git worktree. Never edit code directly on `main`.
- Follow the mandatory TDD cycle below before finishing any task.

## Strict Guardrails

1. `main` is read-only for implementation. No commits, pushes, merges, rebases, or force-pushes directly into `main`.
2. Work strictly in your assigned branch and worktree. Do not switch branches.
3. Stay strictly within the approved file scope. If additional files must be touched, stop and ask for permission first.
4. Forbidden: unrelated refactoring, renaming, mass formatting, "drive-by improvements", or altering code style in untouched files.
5. Do not modify dependencies (`requirements*.txt`), database schema (`app/database.py`), or public API contracts without explicit approval.
6. Never commit `.env`, `*.db`, `venv/`, `cookies*.txt`, secrets, or tokens.
7. One bug or one feature = one isolated chat/worktree. Do not mix unrelated tasks in the same branch.

## Mandatory TDD Cycle (`Code` Mode)

1. **Red:** Write a regression/feature test first (reproducing the bug or testing the new requirement). Run it and confirm it fails.
2. **Green:** Implement the minimal clean fix/feature to make the test pass.
3. **Regression:** Run the full non-integration suite (`/Users/mariia/lammot-main/venv/bin/python -m pytest -m "not integration" -v` and `node --test tests/js/player-time.test.js` if frontend was touched) and confirm 100% green.
4. **Report:** Provide a clear summary listing modified files, `git diff --stat main...HEAD`, test results, and anything left undone (with reasons).

## Definition of Done

- Regression/feature test failed before the fix and passes after.
- All non-integration Python and JS tests are green.
- Diff is strictly limited to approved files.
- Manual verification of the user scenario is completed by the user.
- Pull Request is created, CI checks pass, and changes are merged via **Squash and merge**.

## Security & Environment

- Local `.env` must contain the **test** bot token only. The production token lives exclusively in Railway Variables.
- The bot uses a single webhook: never run the production bot locally at the same time as Railway.
- `PUBLIC_URL` changes whenever `cloudflared`/`ngrok` restarts — update `.env` and restart `uvicorn` after each tunnel restart.