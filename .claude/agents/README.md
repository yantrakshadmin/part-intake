# Agent team

Four roles, split by the rule sets that actually differ in this repo — not by org chart.

| Agent | Model | Owns |
|---|---|---|
| `geometry` | opus | CAD parsing, OBB, poses, silhouettes, nesting engine |
| `backend`  | sonnet | FastAPI, Celery, SQLAlchemy, catalogue, API contracts |
| `frontend` | sonnet | React, Vite, Three.js, the workspace UI |
| `tester`   | sonnet | Verification. Runs the real thing. Never fixes. |

Agent *types* are few on purpose. Parallelism comes from spawning multiple
**instances** of one type, not from inventing more types — every extra type is
another handoff where the spec gets misread.

Deliberately NOT agents:
- **Reviewer** — `/code-review` and `/simplify` already exist. Don't rebuild them.
- **Planner** — that's the PMs (Rahul + Claude).
- **Deployer** — it was a checklist, not a role. Landmines live in CLAUDE.md.
- **Domain expert** — cannot be roleplayed. Packaging knowledge belongs in
  DOMAIN.md, written by the engineers who have it.
- **Integrator** — the cross-boundary invariant (metre↔mm) is enforced by a
  contract test, not by a fifth agent. Code beats process.
