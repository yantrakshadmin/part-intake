"""T4 — retention computed, not assumed. Unit checks on synthetic grids.

Run:  python tests/test_retention.py

Hand-built rasters, not CAD: what is under test is the DECISION each test
makes (stands / falls, frozen / sliding, compacts / jams), and a real part
would only prove that trimesh voxelises. The real files are the acceptance
run, reported on the ticket, not a unit test -- they are NDA and this file has
to run on a machine with no CAD in it.

Each check is written so that breaking the rule it guards flips it: the
free-stand pair differ only in stance, the friction pair only in `mu`, the
compact pair only in whether the part's faces are square to the push.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.nesting import Raster                                   # noqa: E402
from app.retention import (compact, drift, free_stand,           # noqa: E402
                           frozen_layers, retention)

CELL = 4.0


def _raster(grid: np.ndarray, cell: float = CELL) -> Raster:
    """A `Raster` over a hand-built grid. No mesh: retention never needs one.

    `Raster.cells` is all of the class T4 touches -- the 2mm re-voxelisation
    (the only thing that reads `mesh`) belongs to `lattice_check`.
    """
    return Raster(np.asarray(grid, dtype=bool), (0.0, 0.0, 0.0), cell,
                  None, None)


def _com(grid: np.ndarray, cell: float = CELL) -> tuple:
    return tuple((np.argwhere(np.asarray(grid)).mean(axis=0) + 0.5) * cell)


def test_free_stand_passes_on_a_stance_it_should():
    """A flat pressing: 80 x 80mm of foot under a 20mm-high centroid."""
    part = np.zeros((20, 20, 10), dtype=bool)
    part[:, :, :2] = True                       # a flat plate, 80 x 80 x 8mm
    fs = free_stand(part[:, :, :3].any(axis=2), CELL, _com(part))
    assert fs["ok"], fs
    assert fs["r_min_mm"] >= 20.0 and fs["tip_deg"] >= 15.0, fs
    print(f"  flat plate: r_min {fs['r_min_mm']}mm, tip {fs['tip_deg']} deg -> ok")


def test_free_stand_fails_on_an_upright_wall():
    """The rear-shroud shape: tall, standing on 8mm of wall. C14."""
    part = np.zeros((20, 2, 60), dtype=bool)    # 80 x 8 x 240mm upright
    part[:, :, :] = True
    fs = free_stand(part[:, :, :3].any(axis=2), CELL, _com(part))
    assert not fs["ok"], fs
    assert fs["r_min_mm"] < 20.0, fs
    print(f"  upright wall: r_min {fs['r_min_mm']}mm, tip {fs['tip_deg']} deg "
          f"-> FAIL")


def test_frozen_layers_are_judged_at_the_weakest_layer():
    """Friction acts on BOTH faces of a part, and the top layer is exempt.

    The decision this guards: a layer is held when mu times the load on its
    bottom face plus the load on its top face carries its own weight at 1 g,
    and the stack is only as good as the weakest layer BELOW the top one --
    the top layer has nothing above it and is the lid pad's job, not
    friction's. Six layers at mu = 0.25 leaves a layer short of that, so the
    stack slides and `weakest_layer` says which one to put the pad on.
    """
    fz = frozen_layers((2, 2, 6), mu=0.25, mass_kg=0.3)
    assert fz["frozen"][0] and not fz["frozen"][-1], fz
    assert fz["verdict"] == "slides", fz
    assert 0 < fz["weakest_layer"] < 5, fz
    assert not fz["frozen"][fz["weakest_layer"]], fz
    assert all(fz["frozen"][:fz["weakest_layer"]]), fz
    print(f"  mu 0.25, 6 layers: frozen {fz['frozen']} -> {fz['verdict']}, "
          f"weakest {fz['weakest_layer']}")


def test_more_friction_holds_the_stack():
    """Same stack at mu 0.5: every layer under the top one freezes.

    The decision: more friction must be able to turn the verdict, or the
    verdict is a constant and says nothing. A preload does the same job at the
    low mu -- it adds a normal force the stack does not have to earn by
    weight.
    """
    fz = frozen_layers((2, 2, 6), mu=0.5, mass_kg=0.3)
    assert fz["verdict"] == "held" and fz["weakest_layer"] == -1, fz
    assert not fz["frozen"][-1], fz          # still exempt, still reported
    low = frozen_layers((2, 2, 6), mu=0.25, mass_kg=0.3)
    held = frozen_layers((2, 2, 6), mu=0.25, mass_kg=0.3, preload_n=100.0)
    assert low["verdict"] == "slides" and held["verdict"] == "held", (low, held)
    print(f"  mu 0.5: frozen {fz['frozen']} -> {fz['verdict']}; "
          f"mu 0.25 + 100N preload -> {held['verdict']}")


def test_drift_probes_every_neighbour_not_just_the_one_ahead():
    """A part sliding on the diagonal hits the part BESIDE it first.

    80 x 24mm block on an 88 x 56 pitch: 8mm of air along L, 32mm along B. The
    diagonal answer is 11.3mm -- two voxels of travel until the L-neighbour is
    touched -- and NOT 45.3mm, which is what probing only the diagonal
    neighbour returns.
    """
    part = np.ones((20, 6, 4), dtype=bool)          # 80 x 24 x 16mm
    d = drift(_raster(part), (0, 1, 2), (88.0, 56.0, 16.0), (3, 3, 2))
    assert d == {"L": 8.0, "B": 32.0, "L+B": 11.3, "L-B": 11.3}, d
    lone = drift(_raster(part), (0, 1, 2), (88.0, 56.0, 16.0), (1, 3, 2))
    assert lone["L"] is None and lone["B"] == 32.0, lone
    print(f"  80x24 block, pitch 88x56: {d}")


def test_compact_is_ok_when_the_face_is_square_to_the_push():
    """A cuboid chain compacts: the contact normal is along the push.

    Pushing one voxel deeper doubles the shared cells; sliding one voxel
    sideways barely changes them. That ratio IS the contact slope, and a flat
    face square to the push sits well inside any friction cone.
    """
    part = np.ones((20, 20, 20), dtype=bool)
    got = compact(_raster(part), (0, 1, 2), (88.0, 88.0, 88.0), (5, 2, 2),
                  mu=0.25)
    assert got["verdict"] == "ok", got
    assert got["tan_theta"] <= 0.25, got
    print(f"  cuboid chain: {got['verdict']}, tan_theta {got['tan_theta']} "
          f"vs mu {got['mu']}, {got['contact_cells']} cells at wall "
          f"{got['wall_mm']}mm")


def test_compact_jams_on_a_45_degree_ramp_and_the_friction_cone_decides():
    """A sheared prism: the mating faces are inclined 45 deg to the push.

    tan(45) = 1, so the pushed part squirts sideways at any mu below 1 and
    stops at any mu above it. Both verdicts come from the same geometry --
    that is the point: the jam test is a computation against mu, not a rule
    about interleaving (Opus R5 §2 against Gemini R4 §1).
    """
    n_l, n_b, n_h = 20, 10, 8
    part = np.zeros((n_l + n_b, n_b, n_h), dtype=bool)
    for j in range(n_b):
        part[j:j + n_l, j, :] = True        # each row steps one cell along L
    pitch = ((n_l + n_b) * CELL + 8.0, n_b * CELL + 8.0, n_h * CELL)
    jam = compact(_raster(part), (0, 1, 2), pitch, (5, 2, 2), mu=0.25)
    ok = compact(_raster(part), (0, 1, 2), pitch, (5, 2, 2), mu=1.5)
    assert jam["verdict"] == "jam" and ok["verdict"] == "ok", (jam, ok)
    assert jam["tan_theta"] == ok["tan_theta"], (jam, ok)
    assert 0.25 < jam["tan_theta"] <= 1.5, jam
    print(f"  45 deg ramp: tan_theta {jam['tan_theta']} -> jam at mu "
          f"{jam['mu']}, ok at mu {ok['mu']}")


def _plates(indices, n_l: int, n_b: int = 6, n_h: int = 6) -> np.ndarray:
    """A shell pair fixture: full B x H plates at the given L indices.

    Two copies of this, offset along L, share cells only where a plate lands on
    a plate -- which is how two open surfaces meet (a curve, not a volume) and
    is why the shared-cell count does not have to grow as they are pushed
    together.
    """
    part = np.zeros((n_l, n_b, n_h), dtype=bool)
    for i in indices:
        part[i, :, :] = True
    return part


def test_compact_walks_past_a_contact_that_does_not_resist():
    """Plates at L 0, 2, 5, 6, 7: the first touch does not resist, the next does.

    The decision: a wall position where pushing deeper does NOT increase the
    contact has no slope to measure, so the test walks on instead of dividing
    by a guard. One step on, the contact grows and the slope is real -- so the
    verdict is a measured one and `wall_mm` says where it came from.
    """
    part = _plates((0, 2, 5, 6, 7), 8)
    got = compact(_raster(part), (0, 1, 2), (36.0, 40.0, 24.0), (5, 2, 2),
                  mu=0.25)
    assert got["verdict"] in ("ok", "jam") and got["tan_theta"] is not None, got
    assert got["wall_mm"] > 4.0, got      # not the first contacting step
    print(f"  shell pair that resists one step later: {got['verdict']}, "
          f"tan_theta {got['tan_theta']} measured at wall {got['wall_mm']}mm "
          f"on {got['contact_cells']} cells")


def test_compact_is_unknown_when_the_shells_only_cross():
    """Plates at the two ends only: they touch once and then pass through.

    No wall position in the window resists the push, so there is no contact
    normal. The answer is `unknown` with `tan_theta` null and the reason -- not
    a magnitude out of a divide-by-zero guard (the TRW wheel read 19.0 that
    way).
    """
    part = _plates((0, 15), 16)
    got = compact(_raster(part), (0, 1, 2), (68.0, 40.0, 24.0), (5, 2, 2),
                  mu=0.25)
    assert got["verdict"] == "unknown" and got["tan_theta"] is None, got
    assert got["contact_cells"] > 0 and got["note"], got
    print(f"  crossing shells: {got['verdict']}, tan_theta {got['tan_theta']}, "
          f"{got['contact_cells']} cells at wall {got['wall_mm']}mm "
          f"-- {got['note']}")


def test_payload_carries_every_key_the_ticket_names():
    """And says so when it had to assume the mass (hard rule 4: never CAD)."""
    part = np.ones((10, 10, 4), dtype=bool)
    pay = retention(_raster(part), (0, 1, 2), (48.0, 48.0, 16.0), (5, 2, 3),
                    family="layer_sheets")
    assert set(pay) >= {"free_stand", "frozen_layers", "drift_mm", "compact",
                        "mu", "mass_assumed"}, pay
    assert pay["mu"] == 0.25 and pay["mass_assumed"] is True, pay
    # The jam verdict ships with the numbers behind it, never as one word.
    assert set(pay["compact"]) == {"verdict", "axis", "wall_mm", "note",
                                   "contact_cells", "tan_theta", "mu"}, pay
    assert pay["compact"]["mu"] == pay["mu"], pay
    heavy = retention(_raster(part), (0, 1, 2), (48.0, 48.0, 16.0), (5, 2, 3),
                      family="layer_sheets", weight_kg=2.0)
    assert heavy["mass_assumed"] is False, heavy
    assert retention(None, (0, 1, 2), (48.0,) * 3, (1, 1, 1)) is None
    print(f"  payload keys {sorted(pay)}; mu {pay['mu']} ({pay['mu_basis']})")


def test_layout_out_round_trips_the_retention_payload():
    """Hard rule 9: a field on the dataclass that the response model drops is
    a silent data loss, not an error. Prove it over the real pydantic model."""
    from dataclasses import asdict
    from app.nesting import Layout
    from app.schemas import LayoutOut
    part = np.ones((10, 10, 4), dtype=bool)
    pay = retention(_raster(part), (0, 1, 2), (48.0, 48.0, 16.0), (5, 2, 3),
                    family="pocket_tray", weight_kg=0.3)
    layout = Layout("PLS1280", "Largest face down", 30, (5, 2, 3),
                    (40.0, 40.0, 16.0), (48.0, 48.0, 16.0), "geometry",
                    retention=pay)
    # The shape `worker.run_solve` stores and the route validates: the whole
    # dataclass plus the one derived field, straight through the model.
    out = LayoutOut.model_validate(
        {**asdict(layout), "interleave": layout.interleave}).model_dump()
    assert out["retention"] == pay, out.get("retention")
    print(f"  LayoutOut kept retention: compact="
          f"{out['retention']['compact']['verdict']} at tan_theta "
          f"{out['retention']['compact']['tan_theta']}, "
          f"mu={out['retention']['mu']}")


if __name__ == "__main__":
    for fn in (test_free_stand_passes_on_a_stance_it_should,
               test_free_stand_fails_on_an_upright_wall,
               test_frozen_layers_are_judged_at_the_weakest_layer,
               test_more_friction_holds_the_stack,
               test_drift_probes_every_neighbour_not_just_the_one_ahead,
               test_compact_is_ok_when_the_face_is_square_to_the_push,
               test_compact_jams_on_a_45_degree_ramp_and_the_friction_cone_decides,
               test_compact_walks_past_a_contact_that_does_not_resist,
               test_compact_is_unknown_when_the_shells_only_cross,
               test_payload_carries_every_key_the_ticket_names,
               test_layout_out_round_trips_the_retention_payload):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
