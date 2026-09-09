# Graph Report - /Users/rahulsharma/PycharmProjects/part-intake  (2026-06-11)

## Corpus Check
- Corpus is ~6,840 words - fits in a single context window. You may not need a graph.

## Summary
- 131 nodes · 170 edges · 12 communities (11 shown, 1 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 15 edges (avg confidence: 0.92)
- Token cost: 67,243 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Geometry & Orientation Flow|Geometry & Orientation Flow]]
- [[_COMMUNITY_Backend API & Config|Backend API & Config]]
- [[_COMMUNITY_STEP Geometry Module|STEP Geometry Module]]
- [[_COMMUNITY_Frontend Components|Frontend Components]]
- [[_COMMUNITY_API Endpoints & Models|API Endpoints & Models]]
- [[_COMMUNITY_Pydantic Schemas|Pydantic Schemas]]
- [[_COMMUNITY_Docker & Celery Infrastructure|Docker & Celery Infrastructure]]
- [[_COMMUNITY_Planning & Progress Docs|Planning & Progress Docs]]
- [[_COMMUNITY_Job Status Schema|Job Status Schema]]

## God Nodes (most connected - your core abstractions)
1. `extract_part()` - 10 edges
2. `extract_part (full pipeline)` - 8 edges
3. `generate_orientation_candidates()` - 5 edges
4. `App (React root component)` - 5 edges
5. `OrientationViewer` - 5 edges
6. `extract_step (Celery task)` - 5 edges
7. `generate_orientation_candidates` - 5 edges
8. `POST /api/parts` - 5 edges
9. `Base` - 4 edges
10. `JobStatusOut` - 4 edges

## Surprising Connections (you probably didn't know these)
- `Candidate Orientations + User Confirmation` --rationale_for--> `App (React root component)`  [INFERRED]
  PLANNING.md → frontend/src/App.jsx
- `Candidate Orientations + User Confirmation` --rationale_for--> `generate_orientation_candidates`  [EXTRACTED]
  PLANNING.md → backend/app/geometry.py
- `Celery + Redis Async Extraction` --rationale_for--> `extract_step (Celery task)`  [EXTRACTED]
  PLANNING.md → backend/app/worker.py
- `docker-compose api service` --references--> `FastAPI app (Part Intake API)`  [INFERRED]
  docker-compose.yml → backend/app/main.py
- `docker-compose api service` --references--> `Settings (INTAKE_ env config)`  [INFERRED]
  docker-compose.yml → backend/app/config.py

## Hyperedges (group relationships)
- **STEP -> GLB -> min OBB -> Orientation Candidates Pipeline** — geometry_extract_part, geometry_detect_step_length_unit, geometry_convert_step_to_glb, geometry_load_unified_mesh, geometry_minimum_obb, geometry_generate_orientation_candidates [EXTRACTED 1.00]
- **Phase 1 Upload -> Poll -> Confirm -> Save Flow** — app_app, main_upload_step, worker_extract_step, main_job_status, main_create_part, models_partprofile [EXTRACTED 1.00]
- **Metre/mm x1000 Scaling Sync Contract** — planning_glb_metre_mm_scaling, geometry_load_unified_mesh, glbmodel_loadorientedmodel, orientationviewer_orientationviewer [EXTRACTED 1.00]

## Communities (12 total, 1 thin omitted)

### Community 0 - "Geometry & Orientation Flow"
Cohesion: 0.09
Nodes (27): App (React root component), drawDimensions, Drawing2D, svgEl, _rotation_axis_to_z, convert_step_to_glb, detect_step_length_unit, extract_part (full pipeline) (+19 more)

### Community 1 - "Backend API & Config"
Cohesion: 0.12
Nodes (17): Config, Settings — env-driven so the same image runs locally and on AWS., Settings, create_part(), list_parts(), FastAPI app — Phase 1: Part Intake.  Endpoints:   POST /api/parts/upload-step, _to_out(), upload_step() (+9 more)

### Community 2 - "STEP Geometry Module"
Cohesion: 0.13
Nodes (20): convert_step_to_glb(), detect_step_length_unit(), extract_part(), ExtractionResult, generate_orientation_candidates(), load_unified_mesh(), minimum_obb(), OrientationCandidate (+12 more)

### Community 3 - "Frontend Components"
Cohesion: 0.18
Nodes (7): drawDimensions(), svgEl(), candidateMatrix(), computeTightBounds(), loadOrientedModel(), zUpToYUp, api

### Community 4 - "API Endpoints & Models"
Cohesion: 0.21
Nodes (14): api (frontend fetch client), _to_out (PartProfile -> PartProfileOut), POST /api/parts, GET /api/jobs/{job_id}, GET /api/parts, POST /api/parts/upload-step, ExtractionJob (ORM model), PartProfile (ORM model) (+6 more)

### Community 5 - "Pydantic Schemas"
Cohesion: 0.19
Nodes (10): job_status(), Config, ExtractionResultOut, JobStatusOut, OrientationCandidateOut, PartProfileIn, Pydantic schemas — the data contracts for Phase 1 (Part Intake)., Manual entry, or STP-extracted values confirmed by the user. (+2 more)

### Community 6 - "Docker & Celery Infrastructure"
Cohesion: 0.25
Nodes (8): Settings (INTAKE_ env config), docker-compose api service, docker-compose Postgres service, docker-compose Redis service, docker-compose worker service, FastAPI app (Part Intake API), Vite Dev Server /api Proxy, celery_app (Celery instance)

### Community 7 - "Planning & Progress Docs"
Cohesion: 0.6
Nodes (5): CLAUDE.md Dev Rules, Part Intake & Insert Designer (PLANNING.md), Backend Progress Notes, Frontend Progress Notes, README: Part Intake

## Knowledge Gaps
- **35 isolated node(s):** `api`, `zUpToYUp`, `Celery worker: runs the CPU-heavy STEP extraction off the request cycle.  Run: c`, `Config`, `Settings — env-driven so the same image runs locally and on AWS.` (+30 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `extract_part (full pipeline)` connect `Geometry & Orientation Flow` to `API Endpoints & Models`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Why does `extract_part()` connect `STEP Geometry Module` to `Backend API & Config`?**
  _High betweenness centrality (0.030) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `App (React root component)` (e.g. with `PartProfileIn` and `Candidate Orientations + User Confirmation`) actually correct?**
  _`App (React root component)` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `api`, `zUpToYUp`, `Celery worker: runs the CPU-heavy STEP extraction off the request cycle.  Run: c` to the rest of the system?**
  _35 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Geometry & Orientation Flow` be split into smaller, more focused modules?**
  _Cohesion score 0.09 - nodes in this community are weakly interconnected._
- **Should `Backend API & Config` be split into smaller, more focused modules?**
  _Cohesion score 0.12 - nodes in this community are weakly interconnected._
- **Should `STEP Geometry Module` be split into smaller, more focused modules?**
  _Cohesion score 0.13 - nodes in this community are weakly interconnected._