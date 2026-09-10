"""Phase 4: the insert BOM, derived from the measured lattice.

PLANNING §7 -- "the insert BOM is already structured in the decks (element,
size, foam density / GSM, qty per kit) -- generatable, not free text". This is
that generator, and `tests/test_dunnage.py` regenerates both shipped BOMs from
their own lattice as a contract at the same status as the 40/48 counts.

Pure arithmetic over (extent, pitch, grid, asset inner). No DB, no mesh, no
CAD -- everything here comes from numbers `nesting` already measured.

Two systems, ONE rule. The Mubea and TRW decks look like two archetypes and
are not: they are the same predicate answered differently.

    parts interleave in plane  ->  layer bars + separators   (Mubea)
    they do not                ->  pocket tray + sheets      (TRW)

What is derived, and what is not, is the whole point of this module. A
plausible wrong number here costs real money per shipment, so every element
carries a `basis` and an `unknown` list, and anything we have no basis for is
emitted as `None` with "needs deck" rather than as a guess. The MS Rod is the
worked example of a number we can never derive: 805mm matches no dimension we
hold (nearest is the 800mm outer breadth, off by 5). Ticket E-DECK filled it
in from `TP_Mubea_1510_R2 (1).pptx` slide 4 (03/04/2026) anyway -- d8x805,
qty 6, mild steel -- as `basis="pattern"`, same status as the bar widths
below: a shipped number, not a guess, and still not one this module can
re-derive from the lattice.

Height budget. The interleave is vertical too, so an element that lies in a
layer boundary is NOT dead height: the parts nest into it. Mubea stacks
190 + 9x66.67 = 790.0mm of parts inside a 790mm inner -- exactly zero slack --
and still carries a 123mm bottom separator assembly and a 30mm top separator,
because both sit inside the 123.3mm of interleave depth at each end. Only
thickness ABOVE that depth pushes the stack up, and `build_height_mm` /
`fits` report it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

EPS = 1e-6

# ---------------------------------------------------------------------------
# PROVISIONAL. PLANNING §6 stock vocabulary -- what the team buys, not
# anything measured. DOMAIN.md (owned by the packaging engineers) does not
# exist yet, so every constant below is a number to re-confirm, and each is a
# keyword argument on `bom` rather than a baked-in fact.
# ---------------------------------------------------------------------------
TRAY_SHEET_MM = 5.0             # PP Bubble Guard tray stock, §6 sheet list
LAYER_SHEET_MM = 3.0            # sheet between pocket-tray layers (TRW deck)
SIDE_SEPARATOR_MM = 35.0        # §6 sheet list; Mubea revised 15 -> 35 at trial 2
TOP_SEPARATOR_MM = 30.0         # NOT in the §6 list -- pattern from one deck
TOP_SEPARATOR_INSET_MM = 50.0   # ditto: one sample, not a rule
# Bar widths and the rod: shipped numbers off `TP_Mubea_1510_R2 (1).pptx`
# slide 4 (03/04/2026), NOT derivable from the lattice. `basis="pattern"`.
CENTRE_BAR_W_MM = 60.0
SIDE_BAR_W_MM = 70.0
MS_ROD_D_MM, MS_ROD_L_MM, MS_ROD_QTY = 8.0, 805.0, 6

NEEDS_DECK = "needs deck"

CAVEAT = (
    "Starting design derived from the measured lattice. This is not a final "
    "BOM: PLANNING §7 -- the tool produces the starting design, physical "
    "trials still decide. One shipped design was revised after its second "
    "trial: side separator 15mm -> 35mm, a slot provision replacing the "
    "earlier hole in the top separator, no window cut in the box, and foam "
    "densities re-specified. Elements marked 'unknown' need the proposal "
    "deck; no number has been guessed for them."
)


def _fmt(v: float) -> str:
    return f"{v:g}"


@dataclass(frozen=True)
class Element:
    """One BOM line. `dims_mm[i]` is None where we hold no basis for it."""

    name: str
    labels: tuple                  # ("L", "B", "H") or ("d", "L") for rod stock
    dims_mm: tuple                 # same length as `labels`; None = unknown
    qty: int | None                # None = unknown
    spec: str | None               # material / GSM / density, or None
    basis: str                     # "derived" | "pattern" | "unknown"
    # Everything about this element we do NOT hold: dimension labels, plus
    # "qty" / "spec" / "spec_density". Never empty for a `basis="unknown"`
    # element, and every None in `dims_mm` appears here.
    unknown: tuple = ()
    matrix: tuple | None = None    # pocket matrix (cols x rows), tray only
    cell_mm: tuple | None = None   # one pocket: L, B, depth
    net_height_mm: float = 0.0     # dead height this adds ABOVE the parts stack
    note: str = ""

    @property
    def size(self) -> str | None:
        """Deck-style size string, or None when we state no dimension at all."""
        if all(d is None for d in self.dims_mm):
            return None
        return "x".join(
            (lbl if lbl not in ("L", "B", "H") else "")
            + (_fmt(d) if d is not None else "?")
            for lbl, d in zip(self.labels, self.dims_mm)
        )

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "labels": list(self.labels),
            "dims_mm": list(self.dims_mm),
            "size": self.size,
            "qty": self.qty,
            "spec": self.spec,
            "basis": self.basis,
            "unknown": list(self.unknown),
            "matrix": list(self.matrix) if self.matrix else None,
            "cell_mm": list(self.cell_mm) if self.cell_mm else None,
            "net_height_mm": round(self.net_height_mm, 2),
            "note": self.note,
        }


@dataclass(frozen=True)
class Bom:
    """The insert BOM for one layout, plus its height budget."""

    archetype: str                 # "bar_and_rod" | "pocket_tray"
    elements: list = field(default_factory=list)
    stack_height_mm: float = 0.0   # what the parts themselves occupy
    build_height_mm: float = 0.0   # stack + dead height from the dunnage
    inner_h_mm: float = 0.0
    nest_depth_mm: float = 0.0     # vertical interleave: extent_H - pitch_H
    caveat: str = CAVEAT

    @property
    def fits(self) -> bool:
        return self.build_height_mm <= self.inner_h_mm + EPS

    def as_dict(self) -> dict:
        return {
            "archetype": self.archetype,
            "elements": [e.as_dict() for e in self.elements],
            "stack_height_mm": round(self.stack_height_mm, 2),
            "build_height_mm": round(self.build_height_mm, 2),
            "inner_h_mm": round(self.inner_h_mm, 2),
            "nest_depth_mm": round(self.nest_depth_mm, 2),
            "fits": self.fits,
            "caveat": self.caveat,
        }


def archetype_of(extent_lbh, pitch_lbh) -> str:
    """Which dunnage system this lattice needs.

    The predicate is the in-plane interleave -- `pitch < extent` on either
    floor axis -- which is exactly what `frontend/src/lib/solve.js::layoutToFit`
    computed as `interleaved`. Per CLAUDE.md hard rule 9 the backend owns it and
    ships it; the frontend must not re-derive it.
    """
    interleaved = (pitch_lbh[0] < extent_lbh[0] - EPS
                   or pitch_lbh[1] < extent_lbh[1] - EPS)
    return "bar_and_rod" if interleaved else "pocket_tray"


def bom(extent_lbh, pitch_lbh, grid, inner_lbh,
        side_separator_mm: float = SIDE_SEPARATOR_MM,
        top_separator_mm: float = TOP_SEPARATOR_MM,
        top_separator_inset_mm: float = TOP_SEPARATOR_INSET_MM,
        tray_sheet_mm: float = TRAY_SHEET_MM,
        layer_sheet_mm: float = LAYER_SHEET_MM,
        centre_bar_w_mm: float = CENTRE_BAR_W_MM,
        side_bar_w_mm: float = SIDE_BAR_W_MM) -> Bom:
    """Insert BOM for one layout. `grid` is (n_along_L, n_along_B, n_layers).

    `extent_lbh` / `pitch_lbh` / `inner_lbh` are all in asset L/B/H order --
    straight off `nesting.Layout` or a `ground_truth.Case`.
    """
    layers = int(grid[2])
    ext_h, pitch_h = float(extent_lbh[2]), float(pitch_lbh[2])
    inner_h = float(inner_lbh[2])
    # The vertical interleave: how deep each part sits into the row below it.
    # Every layer above the first gets that depth free from the parts under it;
    # dunnage lying in a layer boundary is free up to the same depth.
    nest_depth = max(0.0, ext_h - pitch_h)
    stack = ext_h + (layers - 1) * pitch_h

    kind = archetype_of(extent_lbh, pitch_lbh)
    build = (_bar_and_rod if kind == "bar_and_rod" else _pocket_tray)
    elements = build(extent_lbh, pitch_lbh, grid, inner_lbh, nest_depth,
                     side_separator_mm=side_separator_mm,
                     top_separator_mm=top_separator_mm,
                     top_separator_inset_mm=top_separator_inset_mm,
                     tray_sheet_mm=tray_sheet_mm,
                     layer_sheet_mm=layer_sheet_mm,
                     centre_bar_w_mm=centre_bar_w_mm,
                     side_bar_w_mm=side_bar_w_mm)
    dead = sum(e.net_height_mm for e in elements)
    result = Bom(archetype=kind, elements=elements, stack_height_mm=stack,
                 build_height_mm=stack + dead, inner_h_mm=inner_h,
                 nest_depth_mm=nest_depth)
    if not result.fits:
        logger.warning("dunnage BOM overflows: build %.1f > inner H %.1f (%s)",
                       result.build_height_mm, inner_h, kind)
    return result


def _bar_and_rod(extent_lbh, pitch_lbh, grid, inner_lbh, nest_depth, *,
                 side_separator_mm, top_separator_mm, top_separator_inset_mm,
                 centre_bar_w_mm=CENTRE_BAR_W_MM, side_bar_w_mm=SIDE_BAR_W_MM,
                 **_ignored) -> list:
    """Mubea archetype: layer bars carry the interleave, separators box it in.

    The bars ARE the mechanism, so their height is the vertical pitch, floored
    -- a bar taller than the pitch jams the stack, so never round up.

    ponytail: bar length is the asset inner breadth and there is one bar
    position per layer boundary, which is the shipped Mubea layout (grid
    (1, 4, 10) -- one column along L, so one bar run spans the breadth). A
    layout with more than one column along L needs per-column bars, and how
    those are split is a deck question, so the note says so instead of the
    generator inventing it.
    """
    inner_l, inner_b, inner_h = (float(v) for v in inner_lbh)
    layers = int(grid[2])
    bar_h = float(math.floor(pitch_lbh[2]))
    sep_h = float(math.floor(nest_depth))
    multi_col = int(grid[0]) > 1
    col_note = ("Layout has more than one column along L; how the bar run is "
                "split per column is a deck question." if multi_col else "")

    out: list = []

    # Bottom separator assembly: the bottom row has nothing to nest into, so
    # the assembly provides the nest depth. Net height zero -- the parts sit
    # down into it, which is why Mubea's zero-slack stack still carries 123mm
    # of it. Omitted entirely when the pose has no vertical interleave: there
    # is no depth to provide and inventing a thickness would push the stack up.
    if sep_h > 0:
        out.append(Element(
            name="Bottom Separator Sheet Assy",
            labels=("L", "B", "H"), dims_mm=(inner_l, inner_b, sep_h),
            qty=1, spec="EVA 180 kg/cu m + PP Bubble 1500 GSM",
            basis="derived", unknown=(),
            net_height_mm=max(0.0, sep_h - nest_depth),
            note="Thickness is the vertical interleave depth (extent_H - "
                 "pitch_H): the depth the bottom row nests into. Adds no net "
                 "stack height. Density/GSM from the Mubea deck slide 4.",
        ))

    out.append(Element(
        name="Top Center Bar",
        labels=("L", "B", "H"),
        dims_mm=(inner_b, float(centre_bar_w_mm), bar_h),
        qty=layers + 1, spec="EVA 150 kg/cu m", basis="pattern", unknown=(),
        net_height_mm=max(0.0, bar_h - nest_depth),
        note=("Height is the vertical pitch, floored -- the bars are the "
              "interleave, and a bar taller than the pitch jams the stack. "
              "Length is the asset inner breadth. Width is PATTERN, not "
              f"derived: {_fmt(centre_bar_w_mm)}mm off the Mubea deck, which "
              "nothing in the lattice implies. " + col_note).strip(),
    ))
    out.append(Element(
        name="Top Side Bar",
        labels=("L", "B", "H"),
        dims_mm=(inner_b, float(side_bar_w_mm), bar_h),
        qty=2 * layers, spec="EVA 150 kg/cu m", basis="pattern", unknown=(),
        # COPLANAR with the centre bar: same layer boundaries, same height,
        # side by side across the breadth. `bom()` SUMS this field over every
        # element, so charging each bar its own `max(0, bar_h - nest_depth)`
        # billed two bars' worth of height for one bar's worth of geometry.
        # Invisible on Mubea (bar_h 68 < nest_depth 80, both terms are 0) and
        # it bit exactly the poses with little or no vertical interleave --
        # where it reported DOES NOT FIT for a stack that fits.
        #
        # The whole bar contribution sits on the centre bar above, and it is
        # one bar, the topmost: interior bars lie inside the pitch by
        # construction (bar_h = floor(pitch_H)), so the parts stack already
        # pays for them. The algebra agrees --
        #     layers*pitch_H + bar_h - stack == bar_h - nest_depth
        # with stack = ext_H + (layers-1)*pitch_H.
        net_height_mm=0.0,
        note=("Two per layer, one each side. Height and length as the centre "
              f"bar; width is PATTERN, {_fmt(side_bar_w_mm)}mm off the Mubea "
              "deck. Adds no height of its own: coplanar with the centre bar, "
              "which carries the whole bar contribution. " + col_note).strip(),
    ))
    out.append(Element(
        name="Side Separator",
        labels=("L", "B", "H"),
        dims_mm=(inner_b, inner_h, float(side_separator_mm)),
        qty=2, spec="EVA 100/150 kg/cu m + PP Flute 1200 GSM",
        basis="derived", unknown=(),
        net_height_mm=0.0,       # vertical panel: no height budget
        note=f"Inner breadth x inner height, {_fmt(side_separator_mm)}mm from "
             "the PLANNING §6 sheet list. The Mubea deck revised this "
             "15mm -> 35mm after trial 2, so treat the thickness as a "
             "starting point.",
    ))
    out.append(Element(
        name="Top Separator",
        labels=("L", "B", "H"),
        dims_mm=(inner_l - top_separator_inset_mm,
                 inner_b - top_separator_inset_mm, float(top_separator_mm)),
        qty=1, spec="EVA 150 kg/cu m + PP Flute 1200 GSM",
        basis="pattern", unknown=(),
        net_height_mm=max(0.0, float(top_separator_mm) - nest_depth),
        note=f"PATTERN-MATCHED, not derived: a {_fmt(top_separator_inset_mm)}mm "
             f"inset and {_fmt(top_separator_mm)}mm thickness from one sample "
             "(and 30mm is not in the §6 sheet list). Sits in the "
             "interleave depth above the top row.",
    ))
    out.append(Element(
        name="MS Rod",
        labels=("d", "L"), dims_mm=(MS_ROD_D_MM, MS_ROD_L_MM),
        qty=MS_ROD_QTY, spec="Mild steel", basis="pattern", unknown=(),
        note="PATTERN, never derived: d8x805 qty 6 straight off the Mubea "
             "deck. 805mm matches no dimension we hold (nearest is the 800mm "
             "outer breadth, off by 5) and the quantity follows from nothing "
             "we measure, so a DIFFERENT part in the same box does not get "
             "this rod by implication -- it needs its own deck.",
    ))
    return out


def _pocket_tray(extent_lbh, pitch_lbh, grid, inner_lbh, nest_depth, *,
                 centre_bar_w_mm=None, side_bar_w_mm=None,   # bar_and_rod only
                 tray_sheet_mm, layer_sheet_mm, **_ignored) -> list:
    """TRW archetype: a pocket matrix per layer, sheets between layers.

    The pocket is the lattice pitch minus the separator sheet that shares the
    vertical pitch with it: 376.6 x 367.5 x 117 under a 135mm wheel is why the
    wheels sit INTO the tray and 8 layers fit in 1000mm (PLANNING §4).
    """
    inner_l, inner_b, _inner_h = (float(v) for v in inner_lbh)
    nx, ny, layers = int(grid[0]), int(grid[1]), int(grid[2])
    pocket_depth = float(pitch_lbh[2]) - float(layer_sheet_mm)

    return [
        Element(
            name=f"PP Bubble Guard insert (1200 GSM, {_fmt(tray_sheet_mm)}mm)",
            labels=("L", "B", "H"), dims_mm=(inner_l, inner_b, pocket_depth),
            qty=layers, spec=f"PP Bubble Guard, 1200 GSM, {_fmt(tray_sheet_mm)}mm",
            basis="derived",
            matrix=(nx, ny),
            cell_mm=(round(float(pitch_lbh[0]), 1), round(float(pitch_lbh[1]), 1),
                     round(pocket_depth, 1)),
            net_height_mm=0.0,   # pocket depth + layer sheet == the pitch
            note=f"{nx}x{ny} pockets, one insert per layer. Pocket = pitch_L x "
                 f"pitch_B x (pitch_H - {_fmt(layer_sheet_mm)}mm sheet), so the "
                 "part sits INTO the tray. Material/GSM from the PLANNING "
                 "§6 stock list, not measured.",
        ),
        Element(
            name=f"Separator sheet (PP Bubble Guard + EVA, {_fmt(layer_sheet_mm)}mm)",
            labels=("L", "B", "H"),
            dims_mm=(inner_l, inner_b, float(layer_sheet_mm)),
            qty=layers + 1, spec="PP Bubble Guard 1200 GSM + EVA",
            basis="derived", unknown=("spec_density",),
            # `layers` of these share the vertical pitch with a pocket; the one
            # extra sheet under the bottom tray is the only dead height.
            net_height_mm=float(layer_sheet_mm),
            note="One per layer plus one under the bottom tray. EVA density "
                 "(100/150/180 kg/m3) needs the deck.",
        ),
    ]
