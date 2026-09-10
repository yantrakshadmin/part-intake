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
from dataclasses import dataclass

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

# Base separator sheet under the bottom layer, and clearance under the lid.
# These sit OUTSIDE the inner height, i.e. they add to the outer height -- see
# the note in `synthesise`. 35mm is the thickest sheet in use; the Mubea deck's
# bottom separator assembly is 1150x750x123.
BASE_SEPARATOR_MM = 35.0
TOP_CLEARANCE_MM = 5.0

# (name, inner_L, inner_B). Derived, so the 25mm wall stays honest.
ALLOWED_INNER_FOOTPRINTS = tuple(
    (f"{int(l)}x{int(b)}", l - 2 * WALL_MM, b - 2 * WALL_MM)
    for l, b in PALLET_OUTERS_LB
)


@dataclass(frozen=True)
class BoxDesign:
    """One synthesised box, plus the lattice problem it was solved for.

    `count`/`grid` are `lattice_count(extent_lbh, pitch_lbh, inner)` for this
    box's own `inner` -- recomputable, never a second opinion.
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
    # Which resting pose this design is for. `synthesise` is called per pose
    # and does not know the label, so `engine.solve` stamps it on the winner.
    # Defaulted, not required: without it a custom box reaches the UI as a
    # count with no pose, and the pose is what decides whether the insert can
    # actually be moulded -- the catalogue half reports pose_label already.
    pose_label: str = ""

    @property
    def layers(self) -> int:
        return self.grid[2]


def synthesise(extent_lbh, pitch_lbh, part_kg: float = 0.0,
               max_weight_kg: float = DEFAULT_MAX_WEIGHT_KG,
               max_inner_height_mm: float = MAX_INNER_HEIGHT_MM,
               silhouettes: tuple = (None, None)) -> BoxDesign | None:
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
      * `inner` is the height the lattice gets. The base separator and top
        clearance are added to `outer`, because ground truth measures inner
        height that way: Mubea stacks 190 + 9x66.67 = 790.0 in a 790mm inner
        with a 123mm bottom separator, and TRW stacks 8x120 = 960 in a 1000mm
        inner. Charging those 40mm to the inner height costs TRW a whole layer.
    """
    best: BoxDesign | None = None
    # `silhouettes` is the measured pose's own (engine.solve passes it through),
    # so the custom design carries the same footprint the catalogue side does,
    # paired to the footprint order by the same code -- not a second copy of
    # the permutation rule.
    pose = Pose("custom", tuple(extent_lbh), tuple(pitch_lbh),
                silhouettes=silhouettes)

    for name, inner_l, inner_b in ALLOWED_INNER_FOOTPRINTS:
        for extent, pitch, silhouette in pose.footprint_orders():
            if pitch[2] <= 0 or extent[2] > max_inner_height_mm + EPS:
                continue

            # One layer first: it fixes parts-per-layer, which the weight cap
            # needs before it can talk about layers.
            per_layer, _, _ = lattice_count(extent, pitch, (inner_l, inner_b, extent[2]))
            if not per_layer:
                continue

            layers = int((max_inner_height_mm - extent[2]) / pitch[2] + EPS) + 1
            if part_kg > 0 and max_weight_kg > 0:
                layers = min(layers, int(max_weight_kg / (part_kg * per_layer) + EPS))
            if layers < 1:
                continue

            inner_h = extent[2] + (layers - 1) * pitch[2]
            inner = (inner_l, inner_b, inner_h)
            count, grid, _ = lattice_count(extent, pitch, inner)
            if not count:
                continue

            design = BoxDesign(
                footprint=name,
                inner=inner,
                outer=(inner_l + 2 * WALL_MM, inner_b + 2 * WALL_MM,
                       inner_h + BASE_SEPARATOR_MM + TOP_CLEARANCE_MM),
                count=count,
                grid=grid,
                extent_lbh=extent,
                pitch_lbh=pitch,
                silhouette=silhouette,
            )
            # Most parts wins; a tie goes to the smaller box.
            if best is None or (design.count, -design.outer[1], -design.outer[2]) > \
                    (best.count, -best.outer[1], -best.outer[2]):
                best = design

    if best is None:
        logger.info("no standard footprint fits extent %s", tuple(extent_lbh))
    return best
