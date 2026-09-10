---
description: Review uncommitted work before it lands, hunting this project's known failure patterns
---

Step 4 of the development cycle in CLAUDE.md: **before anything lands.**

Scope: everything uncommitted, unless the user names a narrower target.
`git status --porcelain` then `git diff --stat` first, and say the size out
loud — 19 files is a different job from one.

## Split the work

Three scoped reviewers in parallel, matching the project's own team model
(`.claude/agents/README.md` explains why the team is four roles, not twelve):

| Agent | Area |
|---|---|
| `geometry` | `nesting.py`, `geometry.py`, `dunnage.py`, `synthesis.py`, `insert_drawing.py` + their tests |
| `backend` | `engine.py`, `catalogue.py`, `worker.py`, `main.py`, `models.py`, `schemas.py`, `seed_data.py` + their tests |
| `frontend` | `src/lib/*`, `App.jsx`, `components/*`, `index.css` |

Each one reviews only. Findings come back to the PM, who fixes or tickets
them — a reviewer that edits is no longer a reviewer.

## The hunting list

Do not hand a reviewer a generic checklist. Hand it this project's actual
history, because the same four defects keep recurring:

1. **Tests that cannot fail.** `ground_truth.evaluate()` was fed a stub engine,
   so the real engine was never on the hook for 40. `test_nesting.test_real_bar`
   reached 40 by hand-passing `clearance_mm=0.0` while shipped defaults gave 36.
   A drawing check passed for any `range(element.qty)` implementation. Every
   `test_catalogue` check built a fresh in-memory DB, so the empty-table seeding
   path was the only one ever exercised. **For every load-bearing assertion,
   ask: what implementation would fail this?** If the answer is "none", it is a
   finding. Check especially for tests passing parameters that differ from
   shipped defaults.
2. **Hard rule 9 — pydantic drops undeclared fields silently.** Four instances
   so far. Go field by field: every dataclass the API returns against every
   response model. Then check nothing is re-derived client-side that the
   backend already sent.
3. **Silent failure paths.** Broad `except`, `continue` on missing data,
   defaults that mask absence. `_render_drawings` swallows render errors on
   purpose, which is how a missing `mkdir` made *every* drawing vanish with
   nothing in `warnings`. For each, ask what the user would see and whether
   they could tell anything failed.
4. **Landmine 5 — no Alembic.** A new column needs `main._ensure_added_columns`
   *and* a data backfill in the same commit, because seeding only ever ran on
   an empty table.

Also: hard rule 1 (one scaling site — `scale.check.mjs` enforces the frontend
half), hard rule 4 (never report volume or mass from customer CAD), and
whether comments still tell the truth. One told readers to drop dev.db for a
column that had been auto-ALTERed for two sessions.

## Standard of proof

Verify before reporting. A finding needs concrete inputs that produce a wrong
number, a crash, or a wrong thing on screen — otherwise mark it PLAUSIBLE, not
CONFIRMED. Say plainly when an area is clean; padding with style notes buries
the real defects.

## Before finishing

- Ground-truth regression must pass: `python backend/tests/ground_truth.py`
  (runs the whole sweep including `test_clearance` and `test_tata`). Non-negotiable.
- Every defect fixed should leave a check behind that fails without the fix.
  Prove it bites — reintroduce the bug once and watch it go red.
- Customer CAD and the proposal decks are NDA and "strictly confidential":
  read locally, never copy into the repo, never upload, never paste contents
  into any external service.
