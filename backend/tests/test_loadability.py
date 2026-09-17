"""T3: is there a way to PUT the part into the lattice the engine counted?

`app/loadability.py` answers that in three rungs -- straight descent, oblique
translation over 40 directions on the upper hemisphere, and a headroom-gated
tilt -- and REFUSES the pose when none of them clears.

Everything here is a synthetic occupancy grid at 1mm voxels, hand-checked
against `nesting.lattice_check`'s own enumeration first, so each fixture is a
lattice that FITS (no mixed-vector collision) and differs only in whether it
can be loaded. That separation is the point: T1 already says the parts do not
interpenetrate at rest, and this file is about the dock.

The two real files are not here -- they need the NDA CAD. `tests/sweep.py` and
`tests/ground_truth.py` cover those; the numbers this file's design was
verified against are in the module docstring of `app/loadability.py`.

Run:  python tests/test_loadability.py
"""
import logging
import math
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                              # noqa: E402

from app import loadability as L                                # noqa: E402
from app.nesting import (DIAG_FATAL_RATIO, Raster, _lattice_offsets,  # noqa: E402
                         _overlap_cells, _shift_voxels, min_pitch)

VOXEL = 1.0


class _FineRaster(Raster):
    """A `Raster` whose 2mm re-check is SCRIPTED rather than measured.

    `Raster.fine_cells` re-voxelises the real mesh, and these fixtures are
    hand-built grids with no mesh behind them. What pass 2 is being tested on
    is the DECISION -- does `fine_blocks` separate a raster artefact from a
    crash -- not trimesh's voxeliser, so the fixture states what the 2mm
    answer is. Both policies are what the real files actually do:

      `graze_max = 0`   every contact is real and scales like an
                        interpenetration (T1 measured 2.7-3.4x on five
                        customer files). Pass 2 must confirm pass 1.
      `graze_max = n`   a contact of n cells or fewer is 4mm rounding and is
                        ZERO at 2mm -- ZB 3000 M2 exactly: 2-7 cells at 4mm,
                        0 at 2mm on the path that loads, against cells4=45 ->
                        cells2=111 on the one that does not.

    `fine_calls` counts the re-checks, which is how a test proves pass 2 ran
    at all (or, for the ground-truth files, that it never did).
    """

    def __init__(self, grid, voxel_mm, graze_max: int = 0):
        super().__init__(grid, (0.0, 0.0, 0.0), voxel_mm, None, None)
        self.graze_max = graze_max
        self.fine_calls = 0

    def fine_cells(self, off) -> int:
        self.fine_calls += 1
        cells4 = _overlap_cells(self.grid, off)
        return 0 if cells4 <= self.graze_max else 4 * cells4


def _grid(cells, shape) -> np.ndarray:
    g = np.zeros(shape, dtype=bool)
    for c in cells:
        g[c] = True
    return g


def _fixture(cells, shape, bounds=(3, 3, 3), t1_clean=True, graze_max=0):
    """-> (raster, pitch, bounds, inner). Pitch is MEASURED, never asserted in.

    Also asserts the lattice is clean on T1's mixed vectors, so a failure in
    this file can only be about loadability.
    """
    g = _grid(cells, shape)
    r = _FineRaster(g, VOXEL, graze_max)
    pitch = tuple(min_pitch(g, ax, VOXEL, 0.0) for ax in range(3))
    shifts = _shift_voxels(pitch, (0.0, 0.0, 0.0), VOXEL)
    for v, ijk in _lattice_offsets(g.shape, shifts, bounds):
        assert not t1_clean or not r.cells((0, 1, 2), v), \
            f"fixture is not even a valid lattice: collides at {ijk}"
    inner = tuple(b * p for b, p in zip(bounds, pitch))
    return r, pitch, bounds, inner


def _path(cells, shape, bounds=(3, 3, 3), graze_max=0):
    r, pitch, bounds, inner = _fixture(cells, shape, bounds,
                                       graze_max=graze_max)
    return L.load_path(r, (0, 1, 2), pitch, bounds, inner)


# A solid 3x3x3 block: no interleave anywhere, so the shadows are disjoint at
# the pitch and a vertical descent is provable without searching.
CUBOID = [(x, y, z) for x in range(3) for y in range(3) for z in range(3)]

# Found by random search over small interlocking shapes (scratch driver, not
# in the repo) and then pinned: 8 cells in a 3x3x3 box, pitch (2, 2, 3). The
# plan shadows overlap 1mm on both floor axes, so a straight descent fouls the
# part already placed beside it, and the way in is an almost-horizontal slide
# along L.
OBLIQUE = [(0, 1, 2), (0, 2, 1), (1, 0, 0), (1, 0, 2),
           (1, 1, 2), (1, 2, 1), (2, 0, 0), (2, 1, 1)]

# Same search, same pinning: 21 cells in a 4x4x4 box, pitch (4, 2, 3). The B
# pitch is half the part's width and the vertical nest is deep, so every one
# of the 40 directions runs into either the neighbour beside it or the layer
# beneath it, and there is no headroom for the tilt rung.
REFUSED = [(0, 0, 3), (0, 1, 3), (0, 2, 1), (0, 3, 1), (0, 3, 2), (1, 1, 2),
           (1, 2, 0), (1, 2, 1), (1, 2, 2), (1, 3, 3), (2, 0, 1), (2, 0, 2),
           (2, 1, 1), (2, 1, 3), (2, 2, 0), (2, 3, 2), (3, 0, 2), (3, 2, 1),
           (3, 3, 0), (3, 3, 1), (3, 3, 2)]

# Grazes a neighbour at REST on a mixed lattice vector -- T1's "grazing"
# verdict, the YXA bar's own 1.04 tangency in miniature -- and still lifts
# straight out. 10 cells in a 4x4x4 box, pitch (2, 3, 2): 2 cells shared at
# the seat, and delta_proj 2mm on L so it is the SWEEP that answers it, not
# the prune.
GRAZING = [(0, 1, 0), (0, 1, 1), (0, 2, 0), (0, 3, 0), (1, 2, 0),
           (2, 1, 2), (2, 2, 2), (2, 3, 3), (3, 0, 0), (3, 1, 1)]

# Item 1 (code review): two lattices on the same part whose neighbour sets
# differ -- 6 wide along B against 2 wide. Their verdicts differ, so they must
# not share a memo key.
MEMO = [(0, 1, 2), (0, 2, 1), (0, 5, 1), (1, 0, 0), (1, 1, 0),
        (1, 1, 2), (1, 6, 1), (2, 1, 1), (2, 1, 2), (2, 4, 2)]

# A "hook": nests vertically at pitch 2 and locks under the part below it one
# voxel up. Its plan shadows are disjoint (delta_proj 0), which is exactly the
# case the projection argument does NOT cover.
HOOK = [(0, 1, 0), (1, 1, 1), (2, 1, 2), (0, 1, 3)]


def test_the_direction_set_is_40_unit_vectors_above_the_floor():
    """Was `loadability._selfcheck`; the module keeps no second suite."""
    d = L.directions()
    assert len(d) == L.SWEEP_DIRECTIONS and d[0] == (0.0, 0.0, 1.0)
    assert all(abs(math.dist(v, (0, 0, 0)) - 1.0) < 1e-9 for v in d), "not unit"
    assert all(v[2] > 0 for v in d), "a direction below the floor"
    assert len(set(d)) == len(d), "a direction is searched twice"
    print(f"  {len(d)} directions, straight up first, all unit, all z > 0")


def test_delta_proj_is_floored_and_ignores_a_single_part_axis():
    """Was `loadability._selfcheck`."""
    assert L.delta_proj((157.0, 145.0), (145.0, 145.0)) == (12.0, 0.0)
    assert L.delta_proj((100.0, 100.0), (110.0, 100.0)) == (0.0, 0.0), "not floored"
    assert L.delta_proj((157.0, 145.0), (145.0, 145.0), (1, 2)) == (0.0, 0.0), \
        "one part on L cannot overhang a neighbour that is not there"
    print("  12mm on B, floored below zero, zeroed on a one-part axis")


def test_the_placed_set_is_row_major_and_bounded_by_the_part():
    """Was `loadability._selfcheck`."""
    assert L.placed_neighbours((10, 10, 10), (1, 1, 1)) == []
    n = L.placed_neighbours((10, 10, 10), (5, 5, 5), (1, 1), (15, 15, 15))
    assert (10, 0, 0) not in n and (-10, 0, 0) in n, n
    assert (10, -10, 0) in n, "the previous row is fully placed"
    assert len(n) == 13, n         # a 15-wide bbox only reaches +-1 at pitch 10
    wide = L.placed_neighbours((10, 10, 10), (5, 5, 5), (1, 1), (35, 35, 35))
    assert (-30, 0, 0) in wide and (-40, 0, 0) not in wide, wide
    assert L.limits((10, 10, 10), (5, 5, 5), (35, 35, 35)) == (3, 3, 3)
    assert L.limits((10, 10, 10), (5, 5, 5), (15, 15, 15)) == (1, 1, 1)
    assert len(L._load_orders((10, 10, 10), (5, 5, 5), (35, 35, 35))) == 4
    assert len(L._load_orders((10, 10, 10), (1, 5, 5), (35, 35, 35))) == 2, \
        "the L sign is moot when the lattice is one part wide"
    print(f"  {len(n)} neighbours at a 15-voxel bbox, {len(wide)} at 35")


def test_tilt_is_gated_by_headroom():
    """Was `loadability._selfcheck`."""
    assert L.max_tilt_deg(300.0, 50.0, 0.0) == 0.0, "tilt with no headroom"
    assert L.max_tilt_deg(300.0, 50.0, 1000.0) == L.MAX_TILT_DEG
    assert 0 < L.max_tilt_deg(300.0, 50.0, 10.0) < L.MAX_TILT_DEG
    print(f"  0deg at no headroom, {L.MAX_TILT_DEG}deg capped, "
          f"{L.max_tilt_deg(300.0, 50.0, 10.0)}deg at 10mm")


def test_straight_when_the_shadows_are_disjoint():
    """delta_proj 0 -> straight, and no direction search is paid for."""
    out = _path(CUBOID, (3, 3, 3))
    assert out["kind"] == "straight", out
    assert out["pass"] == 1, "a clear pose must never buy the fine re-check"
    assert out["delta_proj"] == [0.0, 0.0], out
    assert out["pitch_sil"] == [3.0, 3.0], out
    assert out["dir"]["u"] == [0.0, 0.0, 1.0], out
    assert out["probes"] == 1, \
        f"the prune bought nothing: {out['probes']} probes"
    print(f"  cuboid -> {out['kind']}, delta_proj {out['delta_proj']}, "
          f"{out['probes']} probe")


def test_oblique_when_a_straight_descent_fouls_the_neighbour():
    """Overlapping shadows, no start corner lets it down, one slide gets in."""
    out = _path(OBLIQUE, (3, 3, 3))
    assert out["delta_proj"] == [1.0, 1.0], out
    assert out["kind"] == "oblique", out
    assert out["pass"] == 1, "pass 1 found it; pass 2 must not have run"
    u = out["dir"]["u"]
    assert abs(sum(c * c for c in u) - 1.0) < 1e-3, u
    assert u != [0.0, 0.0, 1.0], "an oblique verdict on the vertical direction"
    assert u[2] > 0, f"the load path goes down through the floor: {u}"
    assert out["probes"] > 1, out
    print(f"  interlocked pair -> {out['kind']} on u={u} after "
          f"{out['probes']} probes")


def test_refused_carries_no_load_path():
    """No rung clears -> kind 'none' and the reason string the ticket names."""
    out = _path(REFUSED, (4, 4, 4))
    assert out["kind"] == "none", out
    assert out["dir"] is None, out
    assert out["reason"] == "no load path", out
    assert any(out["delta_proj"]), \
        "a refusal with disjoint shadows would mean the prune is wrong"
    assert out["probes"] >= L.SWEEP_DIRECTIONS, out
    print(f"  interlocked lattice -> {out['kind']!r}: {out['reason']!r} after "
          f"{out['probes']} probes and the tilt rung "
          f"(headroom {out['headroom_mm']}mm)")


def test_pass_two_cannot_un_refuse_a_real_crash():
    """The tolerant pass re-measures a contact; it does not forgive one.

    Same lattice as `test_refused_carries_no_load_path`, every contact real at
    2mm. Pass 1 refuses, pass 2 re-runs the whole sweep with `fine_blocks`
    judging each contact, and the answer is the same refusal -- now paid for
    with the fine re-check rather than asserted from the coarse raster.
    """
    out = _path(REFUSED, (4, 4, 4), graze_max=0)
    assert out["kind"] == "none", out
    assert out["pass"] == 2, out
    assert out["reason"] == "no load path", out
    assert out["probes"] > 2 * L.SWEEP_DIRECTIONS, \
        f"pass 2 did not re-walk the sweep: {out['probes']} probes"
    print(f"  every contact real at 2mm -> still {out['kind']!r} after pass "
          f"{out['pass']}, {out['probes']} probes")


def test_pass_two_clears_a_contact_that_vanishes_at_two_mm():
    """ZB 3000 M2 in miniature: refused on rounding, loadable in fact.

    The SAME lattice as the test above -- the only thing that changes is what
    the 2mm re-check reports. Its obliques are blocked in pass 1 by a single
    ONE-cell contact and its straight descent by a 2- and a 4-cell one, so
    when the one-cell contacts turn out to be nothing at 2mm the verdict is
    `oblique`, not `straight`: the part slides in, it cannot be dropped in.

    On the real file this is 2-7 cells out of 32573 at 4mm reading as zero at
    2mm, against cells4=45 -> cells2=111 (ratio 2.47) on the vertical path.
    """
    r, pitch, bounds, inner = _fixture(REFUSED, (4, 4, 4), graze_max=1)
    out = L.load_path(r, (0, 1, 2), pitch, bounds, inner)
    assert out["kind"] == "oblique", out
    assert out["pass"] == 2, out
    assert out["dir"]["u"] != [0.0, 0.0, 1.0], \
        "a straight descent cannot be the answer -- it is blocked by 2 and 4 cells"
    assert r.fine_calls > 0, "pass 2 never called the fine re-check"
    strict = _path(REFUSED, (4, 4, 4), graze_max=0)
    assert strict["kind"] == "none", \
        "the fixture must be refused when the same contacts are real"
    print(f"  1-cell contacts are 0 at 2mm -> {out['kind']} on "
          f"u={out['dir']['u']} at pass {out['pass']}, "
          f"{r.fine_calls} fine re-checks")


def test_refused_layout_is_dropped_and_says_why():
    """`nesting.layouts_for` must not RANK a pose it cannot load.

    Same discipline as T1's fatal lattice: an unbuildable design is not a
    design. `Layout` is only ever built for a layout that ships, so there is
    no `reasons` list to append to and the string goes to the log -- and to
    `load_path["reason"]`, which is where a caller can read it.
    """
    from app.nesting import Pose, layouts_for
    from tests.ground_truth import Asset

    r, pitch, bounds, _ = _fixture(REFUSED, (4, 4, 4))
    pose = Pose(label="refused fixture", extent=(4.0, 4.0, 4.0), pitch=pitch,
                voxel_mm=VOXEL, clearance=(0.0, 0.0, 0.0), raster=r)
    # 12 x 8 x 10 is 3 x 3 x 3 of this part at its measured pitch: both
    # footprint orders are refused, so the asset ranks nothing at all.
    asset = Asset("SYNTH", (12, 8, 10), (13, 9, 11), 1.0, 1000.0)

    log = logging.getLogger("app.nesting")
    seen = []

    class _Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    h = _Grab()
    log.addHandler(h)
    try:
        out = layouts_for([pose], asset)
    finally:
        log.removeHandler(h)

    assert out == [], f"a layout with no load path was ranked: {out}"
    assert any("no load path" in m for m in seen), seen
    print(f"  layouts_for dropped it: {[m for m in seen if 'no load path' in m][0][:96]}...")


def test_a_resting_contact_is_not_a_blocked_path():
    """Cells shared at the SEAT are T1's business, not T3's.

    The engine's contact tolerance (`nesting.DIAG_FATAL_RATIO`) already judged
    the resting overlap; on the deck-proven YXA bar it is 28 shared cells with
    the (0,-1,-1) neighbour that fade to 19, 8, 1 as the bar lifts. A step is
    blocked only when it makes the contact WORSE than it already is. Score
    every shared cell as a collision instead and this fixture -- and the
    Mubea 40 -- stop having a load path at all.
    """
    from app.nesting import _shift_voxels as _sv
    r, pitch, bounds, _ = _fixture(GRAZING, (4, 4, 4), t1_clean=False)
    shifts = _sv(pitch, (0.0, 0.0, 0.0), VOXEL)
    shape = r.grid.shape
    seats = [max(L._seat(r, (0, 1, 2), n, shape).values(), default=0)
             for _, n in L._load_orders(shifts, bounds, shape)]
    assert max(seats) > 0, "the fixture does not graze anything at rest"
    assert any(L.delta_proj(L.pitch_sil(r, (0, 1, 2)), pitch, bounds)), \
        "the prune would answer this without ever consulting the seat"

    out = L.load_path(r, (0, 1, 2), pitch, bounds,
                      tuple(b * p for b, p in zip(bounds, pitch)))
    assert out["kind"] == "straight", out

    # Same fixture, intolerant: every shared cell counts. It stops lifting.
    up = L.directions()[0]
    strict = [L._clear_along(r, (0, 1, 2), up, n, dict.fromkeys(n, 0), shape)
              for _, n in L._load_orders(shifts, bounds, shape)]
    assert not any(strict), \
        "the fixture passes even with no tolerance, so it proves nothing"
    print(f"  grazes {max(seats)} cell(s) at rest -> {out['kind']}; "
          f"with no tolerance every start corner reads as blocked")


def test_the_prune_does_not_skip_the_part_directly_below():
    """delta_proj 0 proves nothing about the column under the part.

    `min_pitch` proves the MULTIPLES of the stacking pitch clear. It never
    proves `pitch + one voxel` clear, and an undercut fouls exactly there. If
    the prune ever returns "straight" without testing that neighbour, this
    hook ships as loadable and is not.
    """
    out = _path(HOOK, (3, 3, 4), bounds=(1, 1, 4))
    assert out["delta_proj"] == [0.0, 0.0], out
    assert out["kind"] != "straight", \
        f"the prune skipped the column below: {out}"
    print(f"  undercut, disjoint shadows -> {out['kind']}, not straight")


def test_load_order_is_a_free_variable():
    """Which end the packer starts from flips the answer, so it is searched.

    The overlap count is symmetric in the WHOLE offset vector -- |A n A+d| ==
    |A n A-d| -- and NOT in one component of it. So lifting a part away from
    its -B neighbour and away from its +B one are different questions, and
    fixing the order at (+1, +1) refuses the deck-proven Mubea 40 (measured:
    the bar fouls its -B neighbour from the first voxel, 2, 5, 11, 19 shared
    cells, and is clear of the +B one all the way out).
    """
    g = _grid([(0, 0, 0), (0, 1, 1)], (1, 2, 2))
    r = _FineRaster(g, VOXEL)
    up = r.cells((0, 1, 2), (0, 1, 1))
    down = r.cells((0, 1, 2), (0, -1, 1))
    assert up != down, \
        f"one component flipped and the overlap did not move: {up} vs {down}"
    assert r.cells((0, 1, 2), (0, -1, -1)) == up, \
        "the whole vector flipped and the overlap DID move"

    shifts = _shift_voxels((2.0, 2.0, 2.0), (0.0, 0.0, 0.0), VOXEL)
    a = L.placed_neighbours(shifts, (3, 3, 3), (1, 1), (5, 5, 5))
    b = L.placed_neighbours(shifts, (3, 3, 3), (-1, -1), (5, 5, 5))
    assert a != b and len(a) == len(b), (a, b)
    assert {(-x, -y, z) for x, y, z in a} == set(b), \
        "the two start corners are not mirror images of each other"
    assert len(L._load_orders(shifts, (3, 3, 3), (5, 5, 5))) == 4
    print(f"  |A n A+(0,1,1)| = {up}, |A n A-(0,1,1) flipped on B| = {down}; "
          f"4 start corners searched")


def test_the_memo_is_keyed_on_the_neighbour_set_it_answered_for():
    """Two lattices of the SAME part in the SAME pose are two questions.

    The neighbour set comes from `limits(shifts, bounds, shape)`, so the cache
    has to be keyed on that and not on `bounds` -- an earlier key capped bounds
    at 2 and a 6-wide lattice read a 2-wide one's answer: oblique reported as
    straight, on a raster the solve reuses across every asset in the
    catalogue.
    """
    narrow, wide = (1, 2, 2), (1, 6, 2)

    def ask(r, pitch, bounds):
        return L.load_path(r, (0, 1, 2), pitch, bounds,
                           tuple(b * p for b, p in zip(bounds, pitch)))

    r0, pitch, _, _ = _fixture(MEMO, (3, 7, 3), bounds=wide, t1_clean=False)
    fresh = {b: ask(_fixture(MEMO, (3, 7, 3), bounds=b, t1_clean=False)[0],
                    pitch, b)["kind"] for b in (narrow, wide)}
    assert fresh[narrow] != fresh[wide], \
        f"the fixture is pointless unless the two lattices differ: {fresh}"

    # One raster, narrow first -- exactly the order `rank_catalogue` hits when
    # a small box is scored before a big one.
    ask(r0, pitch, narrow)
    again = ask(r0, pitch, wide)
    assert again["kind"] == fresh[wide], \
        f"stale verdict from the memo: {again['kind']} vs {fresh[wide]}"
    assert again["neighbours"] == ask(
        _fixture(MEMO, (3, 7, 3), bounds=wide, t1_clean=False)[0],
        pitch, wide)["neighbours"]
    print(f"  {narrow} -> {fresh[narrow]}, {wide} -> {fresh[wide]}; "
          f"cached in that order the second still reads {again['kind']}")


def test_the_tilted_copy_is_never_thinner_than_the_part():
    """The tilt rung must not lift a part through a hole it made itself.

    Nearest-neighbour rotation drops cells out of a one-voxel shell (the YXA
    bar loses 94 of 16503 at 5deg), and a puncture is a path `_lift_clear`
    would happily pass through -- the rung would INVENT a load path. One
    dilation closes them; the cost is a voxel of slop, which can only refuse.
    """
    g = _grid(OBLIQUE, (3, 3, 3))
    base = int(g.sum())
    for axis in (0, 1):
        for phi in (3.0, 5.0, 10.0, -7.0):
            got = int(L.tilted_copy(g, phi, axis).sum())
            assert got >= base, \
                f"tilt thinned the part at {phi}deg about axis {axis}: " \
                f"{got} < {base}"
    raw = np.asarray(__import__("scipy.ndimage", fromlist=["rotate"]).rotate(
        g, 5.0, axes=(1, 2), order=0, reshape=True, prefilter=False), dtype=bool)
    print(f"  {base} cells -> {int(raw.sum())} rotated raw -> "
          f"{int(L.tilted_copy(g, 5.0, 0).sum())} dilated")


def test_the_clearance_cancels_out_of_delta_proj():
    """`pitch_sil` and `pitch_lbh` carry the same clearance, so it cancels.

    `min_pitch` returns `shift * voxel + clearance`, so at the shipped 5mm
    default both sides move by exactly 5 and delta_proj is the same pure
    geometry it is at zero. Without this the 5mm is subtracted from one side
    only and delta_proj reads one voxel LOW -- a false "straight", which is
    the dangerous direction.
    """
    r, pitch0, bounds, _ = _fixture(OBLIQUE, (3, 3, 3))
    c = 5.0
    at0 = L.delta_proj(L.pitch_sil(r, (0, 1, 2), (0.0, 0.0, 0.0)),
                       pitch0, bounds)
    pitch5 = (pitch0[0] + c, pitch0[1] + c, pitch0[2])
    at5 = L.delta_proj(L.pitch_sil(r, (0, 1, 2), (c, c, 0.0)), pitch5, bounds)
    assert at0 == at5, f"clearance did not cancel: {at0} at 0mm vs {at5} at {c}mm"
    assert any(at0), "a zero delta would pass this test for the wrong reason"
    print(f"  delta_proj {at0} at clearance 0 == {at5} at clearance {c}")


def test_layout_out_round_trips_the_three_fields():
    """Hard rule 9: a field on the dataclass that pydantic drops is a bug.

    `asdict(layout)` -> `LayoutOut.model_validate` -> `.model_dump()` has to
    still carry `pitch_sil`, `delta_proj` and `load_path`.
    """
    from app.nesting import Layout
    from app.schemas import LayoutOut

    out = _path(OBLIQUE, (3, 3, 3))
    lay = Layout("SYNTH", "pose", 27, (3, 3, 3), (3.0, 3.0, 3.0),
                 (2.0, 2.0, 3.0), "geometry",
                 pitch_sil=tuple(out["pitch_sil"]),
                 delta_proj=tuple(out["delta_proj"]),
                 load_path=out)
    d = asdict(lay)
    d["interleave"] = lay.interleave
    dumped = LayoutOut.model_validate(d).model_dump()
    assert dumped["pitch_sil"] == (3.0, 3.0), dumped["pitch_sil"]
    assert dumped["delta_proj"] == (1.0, 1.0), dumped["delta_proj"]
    assert dumped["load_path"]["kind"] == "oblique", dumped["load_path"]
    assert dumped["load_path"]["dir"]["u"] == out["dir"]["u"]
    print(f"  LayoutOut keeps pitch_sil={dumped['pitch_sil']} "
          f"delta_proj={dumped['delta_proj']} "
          f"load_path.kind={dumped['load_path']['kind']!r} "
          f"dir={dumped['load_path']['dir']}")


if __name__ == "__main__":
    for fn in (test_the_direction_set_is_40_unit_vectors_above_the_floor,
               test_delta_proj_is_floored_and_ignores_a_single_part_axis,
               test_the_placed_set_is_row_major_and_bounded_by_the_part,
               test_tilt_is_gated_by_headroom,
               test_straight_when_the_shadows_are_disjoint,
               test_oblique_when_a_straight_descent_fouls_the_neighbour,
               test_refused_carries_no_load_path,
               test_pass_two_cannot_un_refuse_a_real_crash,
               test_pass_two_clears_a_contact_that_vanishes_at_two_mm,
               test_refused_layout_is_dropped_and_says_why,
               test_a_resting_contact_is_not_a_blocked_path,
               test_the_prune_does_not_skip_the_part_directly_below,
               test_load_order_is_a_free_variable,
               test_the_memo_is_keyed_on_the_neighbour_set_it_answered_for,
               test_the_tilted_copy_is_never_thinner_than_the_part,
               test_the_clearance_cancels_out_of_delta_proj,
               test_layout_out_round_trips_the_three_fields):
        print(f"{fn.__name__}:")
        fn()
    print("\nall checks passed")
