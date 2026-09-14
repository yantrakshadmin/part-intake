# Brief for the external audit

You are auditing **part-intake** ("Packit"), an internal web tool for a
returnable-packaging company's projects team. A packaging engineer uploads a
customer's CAD part (STEP/IGES), confirms its dimensions and resting pose, and
the tool ranks the company's catalogue of returnable boxes by how many parts
fit per box (2-D nesting of the part's silhouette, not cuboid packing), then
draws the insert layout, computes truck loads and trips/year, and renders a
proposal PDF. One customer team uses it today on a single VM behind one shared
basic-auth login. We want it fit for a second team.

We have done our own audit. **Your job is to be the second, independent
opinion: challenge our findings, find what we missed, and rank everything.**
Do not restate our documents back to us.

## What you are given

| File | What it is |
|---|---|
| `CLAUDE.md` | Working rules, the ground-truth numbers, environment landmines |
| `PLANNING.md` | Plan of record (features F1–F11, what shipped) |
| `PROGRESS.md` | Session-by-session log — the honest history, including defects found and how they were fixed |
| `docs/USER_JOURNEY.md` | Every step a user performs, start to end, cited to the code that implements it |
| `docs/WORKFLOW_AUDIT.md` | Our own audit: ~45 findings, P0/P1/P2, with a top-5 and a verdict |
| `deploy/README.md` | How it is hosted and deployed (GCP VM, docker compose, push-to-main deploys) |
| (optional) the repository | `backend/app` FastAPI + Celery + geometry engine; `frontend/src` React + Three.js |

You do **not** get customer CAD files or the customer proposal decks (NDA).
Two facts stand in for them: a Mubea stabiliser bar fits **40** per PLS12801
and a TRW steering wheel fits **48** per PLS1280; the engine reproduces both,
and a box-shaped (cuboid) baseline would say 8–10 for the bar.

## What we want back

Four sections, in this order. Every finding is **one line**:
`severity — finding — where (file or step number) — your recommended fix`.
Severity: **P0** loses data, blocks a user with no way forward, or gives a
wrong number; **P1** costs a user real time or trust; **P2** polish.

1. **Challenge our audit.** For each of our five "Top 5" items and three P0s in
   `docs/WORKFLOW_AUDIT.md`: agree, downgrade, or upgrade — and say why in one
   line. If our ranking is wrong, give yours.

2. **Usability defects we missed.** Walk `docs/USER_JOURNEY.md` step by step as
   a first-time packaging engineer who has never seen the tool. For every step
   ask: can I tell what to do next? can I tell if it worked? can I undo it? can
   I get stuck here? We already know about: automatic first solve with default
   parameters; dims pre-filled from the first pose candidate; draft custom boxes
   that cannot be activated; no way to replace a wrong CAD inside a project;
   dead project fields (route km, cost, emissions); polls that never time out.
   Do not list those again unless you disagree with our severity.

3. **Trust in the number.** The whole product is one number — parts per box —
   and the drawing that illustrates it. Where in the flow could a user be shown
   a number that is wrong, stale, or from a different run than the drawing
   next to it, without any signal? Where does the user have to *believe* the
   tool rather than *check* it? What would you put on screen so an engineer
   can verify a count in under a minute?

4. **Readiness for a second team.** A ranked list of what must be true before
   a second customer team logs in (auth, isolation, deletion, cleanup, backup,
   support). Then a one-paragraph verdict: ship to team two now / after items
   1–N / not in this shape — and the single biggest risk if we did it today.

## Rules

- Cite what you are reacting to (step number, file, or finding) so we can
  reconcile your list with ours line by line.
- Terse. No executive summary, no restating what the product does, no praise.
- If a finding needs the code and you were not given it, mark it
  **"needs code"** rather than guessing.
- If something in our documents looks wrong or contradictory, say so — that
  is the most valuable line you can write.
- Target 100–200 lines total.
