"""Phase 2 custom box synthesis (PLANNING §6): solve for the box, don't pick one.

Every run returns top-2 catalogue + 1 custom side by side, so this is on the hot
path, not a fallback. The catalogue side is `nesting.nest`; this is the other half.

The design space is small enough to enumerate outright:

    2 pallet footprints  x  2 part-footprint orientations  ->  4 candidates
    height is the only free variable: solve for the layer count, derive inner H

The in-plane orientations come from `nesting.Pose.footprint_orders`, so a custom
box tries exactly the rotations the catalogue side tries -- one angle ladder.

Counting is `nesting.lattice_count` and nothing else. There is exactly one
counting formula in this codebase; a second one is how the TRW deck ended up
stating 46 kits and shipping 48 (PLANNING §7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import dunnage
from .nesting import EPS, Pose, lattice_count

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PROVISIONAL. Owned by the packaging engineers, not by this module. DOMAIN.md
# does not exist yet; these were called by the PM in ticket B2-2 and every one
# of them is a number to re-confirm, not a fact.
# ---------------------------------------------------------------------------

# Standard pallet footprints, outer LxB mm. Nothing else ships.
PALLET_OUTERS_LB = ((1200.0, 800.0), (1200.0, 1000.0))

# Wall each side, from the real catalogue (1200x800 outer -> 1150x750 inner).
WALL_MM = 25.0

# Tallest inner height in the stocked catalogue (PLS12103, PLS12803). Taller is
# not a returnable box the team keeps.
MAX_INNER_HEIGHT_MM = 1000.0

# PLS family weight limit.
DEFAULT_MAX_WEIGHT_KG = 600.0

# Outer height over inner height: pallet feet, base and lid. Taken from the
# catalogue the custom box competes against, not invented -- every PLS asset
# in `seed_data.PACKAGING` reads outer H = inner H + 196 (986/790, 686/490,
# 1196/1000), the FLC family + 215, PLS1280 + 200. This used to be 40 (a
# 35mm sheet plus 5mm lid clearance), which made every synthesised box
# ~156mm shorter than a real one of the same inner height and let it stack
# one more tier in the truck: a custom box beat the catalogue per truck on
# height it would not have had. The Mubea deck's 123mm bottom separator sits
# INSIDE the inner height (see `synthesise`), so it is not part of this.
OUTER_HEIGHT_OVERHEAD_MM = 196.0

# (name, inner_L, inner_B). Derived, so the 25mm wall stays honest.
ALLOWED_INNER_FOOTPRINTS = tuple(
    (f"{int(l)}x{int(b)}", l - 2 * WALL_MM, b - 2 * WALL_MM)
    for l, b in PALLET_OUTERS_LB
)


@dataclass(frozen=True)
class BoxDesign:
    """One synthesised box, plus the lattice problem it was solved for.

    `count`/`grid` are `lattice_count(extent_lbh, pitch_lbh, inner)` for this
    box's own `inner` less its insert's dead height (`dunnage.dead_height_mm`)
    -- recomputable, never a second opinion.
    """

    footprint: str                 # pallet outer, e.g. "1200x1000"
    inner: tuple[float, float, float]
    outer: tuple[float, float, float]
    count: int
    grid: tuple[int, int, int]
    extent_lbh: tuple[float, float, float]
    pitch_lbh: tuple[float, float, float]
    # Plan silhouette of one part in this footprint order (`nesting.
    # silhouette_runs` wire format), and the insert BOM. Same reason the two
    # above are here: without them the custom box reaches the UI as a count
    # with no geometry to draw, and the only drawing left is the cuboid one.
    silhouette: dict | None = None
    dunnage: dict | None = None
    # Same as `nesting.Layout.turned` -- which of `Pose.footprint_orders`' two
    # in-plane assignments this design won in. The worker composes
    # `nesting.IN_PLANE_TURN` into the rotation it poses the custom box's
    # drawing and 3D animation with (F7).
    turned: bool = False
    # Which resting pose this design is for. `synthesise` is called per pose
    # and does not know the label, so `engine.solve` stamps it on the winner.
    # Defaulted, not required: without it a custom box reaches the UI as a
    # count with no pose, and the pose is what decides whether the insert can
    # actually be moulded -- the catalogue half reports pose_label already.
    pose_label: str = ""
    # Same quantisation ceiling as `nesting.Layout.count_upper`, stamped by
    # `engine.solve` (which knows the pose's voxel size) on the winner. Equal
    # to `count` until then, and for bare-number poses.
    count_upper: int = 0
    # Same baseline as `nesting.Layout.cuboid_count`, stamped by `engine.solve`
    # against this box's own `inner` (F3). 0 until then.
    cuboid_count: int = 0
    # Same as `nesting.Layout.reasons`, composed in the worker (F4). Empty
    # until then.
    reasons: list[str] = field(default_factory=list)

    @property
    def layers(self) -> int:
        return self.grid[2]


def synthesise(extent_lbh, pitch_lbh, part_kg: float = 0.0,
               max_weight_kg: float = DEFAULT_MAX_WEIGHT_KG,
               max_inner_height_mm: float = MAX_INNER_HEIGHT_MM,
               silhouettes: tuple = (None, None),
               clearance_lbh=dunnage.NO_CLEARANCE_LBH) -> BoxDesign | None:
    """Best custom box for one resting pose. -> most parts, or None if none fits.

    `extent_lbh` / `pitch_lbh` are a pose's bounding extent and lattice spacing
    in asset L/B/H order -- straight off `nesting.Layout`, or off a ground-truth
    case's `pose_lbh` / `pitch_lbh`.

    Height is solved, not chosen: take the most layers that fit
    `max_inner_height_mm` and the weight cap, then make the box exactly that
    tall. Two consequences worth knowing:

      * The weight cap SHRINKS the box rather than capping the count of a taller
        one. So `count` is always what this box's own geometry gives, and
        `lattice_count` on `inner` agrees with it (PLANNING §7).
      * The box is tall enough for its OWN insert BOM. `dunnage.bom` says how
        much dead height the dunnage adds above the parts stack (a pocket
        tray's bottom sheet, a bar taller than the nest depth); the inner
        height carries it, and a layer is dropped when that pushes past
        `max_inner_height_mm`. Layers step by `dunnage.layer_step_mm` for the
        same reason: a layer separator the parts do not nest into is paid once
        per layer, not once per box (F11). Without this the synthesised box shipped with
        `fits: False` on its own BOM -- 3mm short on the wheel -- and the
        catalogue side never can, because a stock box's height is a given.
        `clearance_lbh` is what the pitch already carries, for the archetype
        predicate (`engine.solve` passes it).
      * `inner` is the height the lattice gets, and `outer` is inner plus the
        catalogue's own overhead (`OUTER_HEIGHT_OVERHEAD_MM`). Ground truth
        measures inner height as the lattice's: Mubea stacks 190 + 9x66.67 =
        790.0 in a 790mm inner WITH a 123mm bottom separator inside it, and
        TRW stacks 8x120 = 960 in a 1000mm inner. Charging dunnage to the
        inner height costs TRW a whole layer.
    """
    best: BoxDesign | None = None
    # `silhouettes` is the measured pose's own (engine.solve passes it through),
    # so the custom design carries the same footprint the catalogue side does,
    # paired to the footprint order by the same code -- not a second copy of
    # the permutation rule.
    pose = Pose("custom", tuple(extent_lbh), tuple(pitch_lbh),
                silhouettes=silhouettes)

    for name, inner_l, inner_b in ALLOWED_INNER_FOOTPRINTS:
        # `footprint_orders` yields (0,1,2) then (1,0,2); index 1 IS the turn.
        for turned, (extent, pitch, silhouette, _clr) in enumerate(
                pose.footprint_orders()):
            if pitch[2] <= 0 or extent[2] > max_inner_height_mm + EPS:
                continue
            # What the insert adds above the stack; the box carries it and the
            # lattice does not get it. Same expression `nesting.layouts_for`
            # charges the catalogue with.
            # One layer in this footprint first: it fixes parts-per-layer,
            # which both the weight cap and the F11 archetype (does the pose
            # interleave in plan?) need before they can talk about layers.
            per_layer, in_plane, _ = lattice_count(
                extent, pitch, (inner_l, inner_b, extent[2]))
            if not per_layer:
                continue
            dead = dunnage.dead_height_mm(extent, pitch, clearance_lbh,
                                          grid=in_plane)
            step = dunnage.layer_step_mm(extent, pitch, clearance_lbh,
                                         grid=in_plane)
            usable_h = max_inner_height_mm - dead
            if extent[2] > usable_h + EPS:
                continue

            layers = int((usable_h - extent[2]) / step + EPS) + 1
            if part_kg > 0 and max_weight_kg > 0:
                layers = min(layers, int(max_weight_kg / (part_kg * per_layer) + EPS))
            if layers < 1:
                continue

            # The box is exactly stack + dead tall. `count` is the lattice in
            # the stack, and `lattice_count(extent, pitch, inner minus dead)`
            # reproduces it (test_self_consistent) -- the height the insert
            # occupies is never counted as room for parts.
            stack_h = extent[2] + (layers - 1) * pitch[2]
            # Layer-separator height scales with the layer count (F11), so the
            # box carries the dead height of the grid it actually got.
            inner = (inner_l, inner_b, stack_h + dunnage.dead_height_mm(
                extent, pitch, clearance_lbh, grid=in_plane)
                + (layers - 1) * (step - pitch[2]))
            count, grid, _ = lattice_count(extent, pitch, (inner_l, inner_b, stack_h))
            if not count:
                continue

            design = BoxDesign(
                footprint=name,
                count_upper=count,
                inner=inner,
                outer=(inner_l + 2 * WALL_MM, inner_b + 2 * WALL_MM,
                       inner[2] + OUTER_HEIGHT_OVERHEAD_MM),
                count=count,
                grid=grid,
                extent_lbh=extent,
                pitch_lbh=pitch,
                silhouette=silhouette,
                turned=bool(turned),
            )
            # Most parts wins; a tie goes to the smaller box.
            if best is None or (design.count, -design.outer[1], -design.outer[2]) > \
                    (best.count, -best.outer[1], -best.outer[2]):
                best = design

    if best is None:
        logger.info("no standard footprint fits extent %s", tuple(extent_lbh))
    return best
