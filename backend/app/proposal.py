"""F7: proposal PDF. One `build_pdf` call, matplotlib + PIL only (already
installed dependencies — PRD §5 F7 says no new ones).

Every number that reaches a page comes off `run_out` (the same `RunOut` the
API returns — see `app.runs._run_out`) or the raw `run.result_json` (for the
per-layout/per-design detail `RunOut` does not carry, e.g. the BOM or the
drawing/gif file names) or a `project`/`part` field. Nothing here re-derives
a count, a pitch or a gain (CLAUDE.md hard rule 9) — the one exception is the
truck load drawing's box outer footprint and weight cap, which are not
engine outputs at all but catalogue reference data. `build_pdf` takes an
OPTIONAL `db` for exactly that lookup (`_asset_meta`/`_vehicle_meta`): a box
or vehicle that exists only in the database (a draft, or added after the
seed constants were last touched) must draw to its real size, not a 1x1x1
placeholder -- `worker.run_proposal` and the CLI both have a session and
pass it; `containers()`/`VEHICLES` are the fallback for callers that don't
(review tests that build a PDF with no db at all).
"""

from __future__ import annotations

import datetime as dt
import logging
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")                              # worker/CLI, no display
import matplotlib.font_manager as fm                # noqa: E402
import matplotlib.pyplot as plt                     # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from matplotlib.patches import Rectangle            # noqa: E402
from PIL import Image                               # noqa: E402
from sqlalchemy import select                       # noqa: E402
from sqlalchemy.orm import Session                   # noqa: E402

from .catalogue import containers_named
from .config import settings
from .models import PartProfile, Project, SolveJob, Vehicle
from .nesting import DEFAULT_STACK_CLEARANCE_MM
from .schemas import RunOut
from .seed_data import VEHICLES

logger = logging.getLogger(__name__)

# Dashboard palette (CLAUDE.md "UI direction").
C_PRIMARY = "#1E40AF"
C_ACCENT = "#D97706"
C_GROUND = "#F8FAFC"
C_INK = "#0F172A"
C_MUTE = "#64748B"

PAGE_SIZE = (11.69, 8.27)   # A4 landscape, inches

# Table styling (tester finding: default `ax.table(bbox=...)` stretches every
# row to fill whatever height the bbox is given, independent of row count --
# 3 rows in a 0.65-axes-fraction bbox is how a row ends up ~200px tall with
# clipped text. `TABLE_ROW_H` sizes the bbox FROM the row count instead, so
# adding rows grows the table, not the rows.
TABLE_ROW_H = 0.06     # axes-fraction per row (header + body), before .scale
TABLE_FONTSIZE = 9

_FONT = "Fira Sans" if any(f.name == "Fira Sans" for f in fm.fontManager.ttflist) \
    else "DejaVu Sans"
plt.rcParams["font.family"] = _FONT


def _wrap(text, width: int = 28) -> str:
    """Wrap into lines <= `width` chars, never truncate (a clipped "PP Bubble
    Guard insert (1200 GSM, 5m" reads as missing data, not long data)."""
    text = str(text)
    return "\n".join(textwrap.wrap(text, width=width)) or text


def _styled_table(ax, rows: list[tuple], col_labels: list[str],
                  weights: list[float], x0: float, y_top: float, width: float,
                  best_row: int | None = None):
    """One table, positioned with its TOP at `y_top` and height driven by
    `len(rows)` (see `TABLE_ROW_H`), column widths proportional to `weights`,
    text left-aligned / header centered, the `best_row` (0-indexed into
    `rows`) highlighted. Returns `(table, bottom_y)` so the caller can place
    a caption below whatever height the table actually used.
    """
    total_w = sum(weights)
    col_widths = [w / total_w for w in weights]
    height = TABLE_ROW_H * (len(rows) + 1)
    bottom_y = y_top - height
    table = ax.table(cellText=rows, colLabels=col_labels, colWidths=col_widths,
                     cellLoc="left", colLoc="center",
                     bbox=(x0, bottom_y, width, height))
    table.auto_set_font_size(False)
    table.set_fontsize(TABLE_FONTSIZE)
    table.scale(1, 1.5)
    for (row_i, _), cell in table.get_celld().items():
        cell.set_edgecolor("#CBD5E1")
        if row_i == 0:
            cell.set_facecolor(C_PRIMARY)
            cell.set_text_props(color="white", weight="bold")
        elif best_row is not None and row_i == best_row + 1:
            cell.set_facecolor("#FDE9CB")
        else:
            cell.set_facecolor("white")
    return table, bottom_y


def _page():
    fig = plt.figure(figsize=PAGE_SIZE, facecolor=C_GROUND)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor(C_GROUND)
    return fig, ax


def _header(ax, title: str, subtitle: str = "") -> None:
    ax.text(0.06, 0.93, title, fontsize=22, weight="bold", color=C_PRIMARY,
            va="top")
    if subtitle:
        ax.text(0.06, 0.87, subtitle, fontsize=12, color=C_MUTE, va="top")
    ax.plot([0.06, 0.94], [0.845, 0.845], color=C_ACCENT, linewidth=1.5)


def _footer(ax, text: str) -> None:
    ax.text(0.06, 0.03, text, fontsize=8, color=C_MUTE, va="bottom")


def _drawing_of(best_obj: dict | None) -> str | None:
    """The packed-box picture for the PDF: F15's orthographic Front/Side/Top,
    falling back to the exploded isometric for results rendered before it
    existed. One expression, so every page embeds the same drawing.
    """
    o = best_obj or {}
    return o.get("ortho_url") or o.get("drawing_url")


def _local_path(url: str | None) -> Path | None:
    """`/api/files/<name>` -> the file on disk, or None (missing/absent)."""
    if not url:
        return None
    p = Path(settings.local_storage_dir) / Path(url).name
    return p if p.is_file() else None


def _asset_meta(name: str, best_obj: dict | None, db: Session | None = None):
    """(outer_lbh, max_weight_kg) for the option named `name`.

    "custom" carries its own `outer` in `best_obj` (from `synthesis.BoxDesign`
    via `dataclasses.asdict`) — real per-run data. A stocked asset's outer
    footprint and weight cap are not engine outputs at all (the count only
    ever needed the INNER), so `catalogue.containers_named` is the source --
    NOT `containers()`, which only sees status="checked" rows and would draw
    a draft or later-added box as a 1x1x1 unit placeholder even with a real
    db. `containers_named` ignores status (the same reason `engine.solve`
    itself uses it for a caller-named box) and falls back to the seed
    constants when `db` is None. None, None when the name matches nothing at
    all.
    """
    if name == "custom" and best_obj is not None:
        return tuple(best_obj.get("outer") or ()), None
    found, _ = containers_named(db, [name])
    if found:
        return found[0].outer, found[0].max_weight_kg
    return None, None


def _vehicle_meta(name: str, db: Session | None = None):
    """(cargo_l, cargo_b, cargo_h, payload_kg) mm/kg for a vehicle name.

    Reads the `Vehicle` row when `db` is given, else `seed_data.VEHICLES` --
    same reason `_asset_meta` prefers the db: a vehicle added after the seed
    constants were last touched must draw to its real cargo size. None when
    the name matches nothing on file either way."""
    if db is not None:
        row = db.scalars(select(Vehicle).where(Vehicle.name == name)).first()
        if row is not None:
            return row.cargo_l_mm, row.cargo_b_mm, row.cargo_h_mm, row.payload_kg
    for n, cl, cb, ch, payload in VEHICLES:
        if n == name:
            return cl, cb, ch, payload
    return None


def _cover_page(project: Project, run_out: RunOut):
    fig, ax = _page()
    ax.text(0.5, 0.62, "Packaging proposal", fontsize=30, weight="bold",
            color=C_PRIMARY, ha="center")
    ax.text(0.5, 0.52, project.customer, fontsize=20, color=C_INK, ha="center")
    ax.text(0.5, 0.46, f"{project.part_number} — {project.part_name}",
            fontsize=14, color=C_INK, ha="center")
    # Audit #1: short run id + date so this page can be matched back to the
    # run on screen -- same "Run #<8 chars>" the frontend already prints
    # (PackingRecommendation.jsx), not the full uuid.
    ax.text(0.5, 0.36,
            f"Run #{run_out.solve_job_id[:8]} · {dt.date.today().isoformat()}",
            fontsize=11, color=C_MUTE, ha="center")
    ax.text(0.5, 0.31, f"Owner: {project.owner or '—'}   "
                       f"Status: {project.status}", fontsize=11, color=C_MUTE,
            ha="center")
    ax.text(0.5, 0.10, "Yantra Packs", fontsize=16, weight="bold",
            color=C_ACCENT, ha="center")
    _footer(ax, f"Run {run_out.solve_job_id} — starting design, trials decide "
                "(PLANNING §7)")
    return fig


def _part_pose_page(part: PartProfile, run_out: RunOut,
                    best_obj: dict | None):
    fig, ax = _page()
    _header(ax, "Part and pose", part.part_name)
    lines = [
        f"L x B x H:  {part.length_mm:g} x {part.breadth_mm:g} x "
        f"{part.height_mm:g} mm",
        f"Weight:  {part.weight_kg:g} kg",
        f"Poses searched:  {len(run_out.poses_searched)}"
        f" ({', '.join(run_out.poses_searched)})" if run_out.poses_searched else
        "Poses searched:  0",
        f"In-plane clearance:  {run_out.clearance_mm} mm",
    ]
    # The backend's own sentence for the pose actually used (app.reasons) —
    # printed verbatim, never re-derived from the confirmed-orientation matrix.
    if run_out.reasons:
        lines.append(f"Pose:  {run_out.reasons[0]}")
    y = 0.74
    for line in lines:
        ax.text(0.06, y, line, fontsize=12.5, color=C_INK, va="top")
        y -= 0.07

    png = _local_path(_drawing_of(best_obj))
    if png is not None:
        img = Image.open(png)
        # Full width under the text block: the F15 drawing is three panels
        # side by side (~13:5), and the old portrait slot beside the text
        # shrank it to a thumbnail and ran the pose sentence under it.
        iax = fig.add_axes((0.06, 0.08, 0.88, 0.34))
        iax.imshow(img)
        iax.axis("off")
    else:
        ax.text(0.5, 0.4, "No packed-box drawing available for this part.",
                fontsize=11, color=C_MUTE, ha="center")
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _comparison_page(run: SolveJob, run_out: RunOut):
    fig, ax = _page()
    _header(ax, "Ranked comparison", "Every catalogue option plus the "
                                     "synthesised custom design")
    r = run.result_json or {}
    rows = []
    for l in r.get("catalogue") or []:
        rows.append((_wrap(l.get("asset_name", "")), _wrap(l.get("pose_label", "")),
                    l.get("count", 0), l.get("cuboid_count", 0),
                    "x".join(str(g) for g in l.get("grid", ())),
                    l.get("limited_by", "-"), l.get("count_upper", 0)))
    custom = r.get("custom")
    if custom is not None:
        rows.append((_wrap("custom"), _wrap(custom.get("pose_label", "")),
                    custom.get("count", 0), custom.get("cuboid_count", 0),
                    "x".join(str(g) for g in custom.get("grid", ())),
                    # BoxDesign carries no `limited_by` (F4 note) -- "custom"
                    # says why the dash is missing rather than reading as an
                    # unfilled field.
                    "custom",
                    custom.get("count_upper", 0)))

    if not rows:
        # worker's own "No layout found" case: still a `status="done"` run,
        # just an empty answer. `ax.table(cellText=[])` raises IndexError --
        # `main.create_proposal` already refuses to write a proposal from a
        # run like this (422), but `build_pdf` must not crash if it is ever
        # called on one anyway (an older proposal row, a direct call).
        ax.text(0.5, 0.5, "No option was found in this run.", fontsize=12,
                color=C_MUTE, ha="center")
        _footer(ax, f"Run {run_out.solve_job_id}")
        return fig

    col_labels = ["Asset", "Pose", "Count", "Cuboid", "Grid", "Limited by",
                 "Ceiling"]
    # Asset/Pose carry the longest strings (a pose label, an asset code) --
    # about 2x a numeric column's width.
    weights = [2, 2, 1, 1, 1.2, 1.3, 1]

    # Highlight the recommended/best row -- the SAME option best_count
    # describes (custom iff custom_beats_catalogue, else catalogue[0]).
    best_row = (len(rows) - 1) if (r.get("custom_beats_catalogue")
                                    and custom is not None) else 0

    _, bottom_y = _styled_table(ax, rows, col_labels, weights, x0=0.06,
                               y_top=0.80, width=0.88,
                               best_row=best_row if rows else None)

    ax.text(0.06, max(bottom_y - 0.05, 0.08),
           f"Best: {run_out.best_asset} at {run_out.best_count} "
           "parts/box (highlighted).", fontsize=10.5,
            color=C_ACCENT, weight="bold")
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _bom_page(best_obj: dict | None, run_out: RunOut):
    fig, ax = _page()
    _header(ax, "Insert BOM", f"{run_out.best_asset} — starting design")
    dunnage = (best_obj or {}).get("dunnage")
    if not dunnage:
        ax.text(0.5, 0.5, "No insert BOM for this option.", fontsize=12,
                color=C_MUTE, ha="center")
        return fig

    rows = [(_wrap(e.get("name", "")), e.get("size", ""), e.get("qty", ""),
            e.get("basis", "")) for e in dunnage.get("elements") or []]
    # Element names are the long strings here ("PP Bubble Guard insert
    # (1200 GSM, 5mm)"); the other three columns are short.
    weights = [2, 1, 1, 1]
    _, bottom_y = _styled_table(ax, rows,
                               ["Element", "Dims (mm)", "Qty", "Basis"],
                               weights, x0=0.06, y_top=0.78, width=0.88)

    build = dunnage.get("build_height_mm")
    inner_h = dunnage.get("inner_h_mm")
    fits = dunnage.get("fits")
    y = max(bottom_y - 0.05, 0.20)
    ax.text(0.06, y,
           f"Build height {build} mm vs inner {inner_h} mm — "
           f"{'fits' if fits else 'DOES NOT FIT'}.",
           fontsize=12, weight="bold",
           color=(C_PRIMARY if fits else "#B91C1C"))
    caveat = dunnage.get("caveat", "")
    ax.text(0.06, y - 0.08, caveat, fontsize=9, color=C_MUTE, wrap=True)
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _exploded_page(best_obj: dict | None, run_out: RunOut):
    fig, ax = _page()
    _header(ax, "Packed box - Front / Side / Top", run_out.best_asset or "")
    png = _local_path(_drawing_of(best_obj))
    if png is not None:
        img_rect = (0.06, 0.06, 0.88, 0.76)
        img = Image.open(png)
        iax = fig.add_axes(img_rect)
        iax.imshow(img)
        iax.axis("off")
    else:
        ax.text(0.5, 0.5, "No packed-box drawing available for this option.",
                fontsize=12, color=C_MUTE, ha="center")
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _sequence_page(best_obj: dict | None, run_out: RunOut):
    fig, ax = _page()
    _header(ax, "Packing sequence", run_out.best_asset or "")
    gif = _local_path((best_obj or {}).get("gif_url"))
    # F5 S1: a part with a GLB has no GIF (the live animation replaces it),
    # only `packed_url` -- the same hold frame the GIF would have ended on
    # (hard rule 9) -- so the still stands in for the First/Middle/Last strip.
    packed = None if gif is not None else _local_path(
        (best_obj or {}).get("packed_url"))
    if gif is None and packed is None:
        ax.text(0.5, 0.5, "No packing-sequence GIF available for this option.",
                fontsize=12, color=C_MUTE, ha="center")
    elif gif is None:
        img = Image.open(packed).convert("RGB")
        iax = fig.add_axes((0.06, 0.06, 0.88, 0.76))
        iax.imshow(img)
        iax.axis("off")
        iax.set_title("Packed", fontsize=11, color=C_INK)
    else:
        im = Image.open(gif)
        n = getattr(im, "n_frames", 1)
        idxs = sorted({0, n // 2, n - 1})
        labels = ["First", "Middle", "Last"][:len(idxs)]
        width = 0.9 / len(idxs)
        for i, (idx, label) in enumerate(zip(idxs, labels)):
            im.seek(idx)
            frame = im.convert("RGB")
            iax = fig.add_axes((0.05 + i * width, 0.12, width - 0.02, 0.66))
            iax.imshow(frame)
            iax.axis("off")
            iax.set_title(label, fontsize=11, color=C_INK)
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _dim_labels(sub_ax, w: float, h: float, w_mm: float, h_mm: float) -> None:
    """Length/height captions (mm) along the bottom and left edge of a
    to-scale sub-axes spanning (0, 0)-(w, h) in its own data coordinates."""
    sub_ax.text(w / 2, -h * 0.10, f"{w_mm:.0f} mm", fontsize=8.5,
               color=C_MUTE, ha="center", va="top")
    sub_ax.text(-w * 0.10, h / 2, f"{h_mm:.0f} mm", fontsize=8.5,
               color=C_MUTE, ha="center", va="center", rotation=90)


def _truck_page(run: SolveJob, run_out: RunOut, outer, db: Session | None = None):
    fig, ax = _page()
    _header(ax, "Truck load", run_out.truck_vehicle or "")
    truck = (run.result_json or {}).get("truck")
    if truck is None:
        ax.text(0.5, 0.5, "No truck fit for this run.", fontsize=12,
                color=C_MUTE, ha="center")
        return fig

    floor_l, floor_b = truck.get("floor_grid", (0, 0))
    stack = truck.get("stack", 0)
    # `outer` is `_asset_meta(truck.get("asset_name"), ...)`'s result,
    # computed ONCE in `build_pdf` (also what `numbers["truck_outer"]`
    # reports) -- not re-looked-up here, so the drawing and the number can
    # never disagree.
    if outer and truck.get("floor_rotated"):
        outer = (outer[1], outer[0], outer[2])
    ow, ob, oh = outer if outer else (1.0, 1.0, 1.0)

    # Cargo bay outline, from the vehicle's own row (or the seed constants
    # when there is no db -- see `_vehicle_meta`).
    # No vehicle on file for this name: draw a bay that exactly encloses the
    # boxes rather than inventing a real cargo size.
    cargo = _vehicle_meta(run_out.truck_vehicle or "", db)
    cl, cb, ch = (cargo[0], cargo[1], cargo[2]) if cargo else (
        max(floor_l, 1) * ow, max(floor_b, 1) * ob, max(stack, 1) * oh)

    # Plan view: the cargo bay (cargo_l x cargo_b), boxes to scale inside it.
    pax = fig.add_axes((0.06, 0.24, 0.38, 0.52))
    pax.set_title("Plan (floor fit)", fontsize=11, color=C_INK)
    pax.add_patch(Rectangle((0, 0), cl, cb, facecolor="none",
                            edgecolor=C_INK, linewidth=1.4))
    for i in range(int(floor_l)):
        for j in range(int(floor_b)):
            pax.add_patch(Rectangle((i * ow, j * ob), ow * 0.94, ob * 0.94,
                                    facecolor="#DBEAFE", edgecolor=C_PRIMARY))
    pax.set_xlim(-cl * 0.16, cl * 1.06)
    pax.set_ylim(-cb * 0.16, cb * 1.06)
    pax.set_aspect("equal")
    pax.axis("off")
    _dim_labels(pax, cl, cb, cl, cb)

    # Side view: the cargo bay (cargo_l x cargo_h), floor_l boxes across x
    # stack boxes high, both to scale.
    sax = fig.add_axes((0.48, 0.24, 0.28, 0.52))
    sax.set_title("Side (stack)", fontsize=11, color=C_INK)
    sax.add_patch(Rectangle((0, 0), cl, ch, facecolor="none",
                            edgecolor=C_INK, linewidth=1.4))
    for i in range(int(floor_l)):
        for k in range(int(stack)):
            sax.add_patch(Rectangle((i * ow, k * oh), ow * 0.94, oh * 0.94,
                                    facecolor="#FDE9CB", edgecolor=C_ACCENT))
    sax.set_xlim(-cl * 0.16, cl * 1.06)
    sax.set_ylim(-ch * 0.16, ch * 1.06)
    sax.set_aspect("equal")
    sax.axis("off")
    _dim_labels(sax, cl, ch, cl, ch)

    ax.text(0.06, 0.19, f"{int(floor_l)} x {int(floor_b)} per floor, "
                       f"{int(stack)} high", fontsize=10, color=C_INK,
           weight="bold")

    lines = [
        f"Boxes: {truck.get('boxes')}   Parts/truck: {run_out.parts_per_truck}",
        f"Limited by: {truck.get('limited_by')}",
        f"By volume: {truck.get('by_volume')}   By weight: {truck.get('by_weight')}",
        f"Tare: {truck.get('tare_kg')} kg",
        "Trips/year: " + (str(run_out.trips_per_year)
                          if run_out.trips_per_year is not None
                          else "not provided"),
    ]
    y = 0.74
    for line in lines:
        ax.text(0.78, y, line, fontsize=11, color=C_INK, va="top")
        y -= 0.07
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _gain_page(run_out: RunOut):
    fig, ax = _page()
    _header(ax, "Baseline and gain")
    lines = [
        f"Best: {run_out.best_count} parts ({run_out.best_asset})",
        f"Cuboid baseline (same box, best of 6 orientations): "
        f"{run_out.cuboid_count}"
        + (f"  ->  {run_out.gain_vs_cuboid}x" if run_out.gain_vs_cuboid
           else ""),
    ]
    if run_out.customer_count is not None:
        pct = run_out.gain_vs_customer_pct
        sign = "+" if (pct or 0) >= 0 else ""
        lines.append(f"Customer's current pack: {run_out.customer_count}"
                     f"  ->  {sign}{pct}%" if pct is not None else
                     f"Customer's current pack: {run_out.customer_count}")
    else:
        lines.append("Customer's current pack: not provided")
    y = 0.72
    for line in lines:
        ax.text(0.06, y, line, fontsize=14, color=C_INK, va="top")
        y -= 0.08

    ax.text(0.06, y - 0.03, "Why this design:", fontsize=13, weight="bold",
           color=C_PRIMARY, va="top")
    y -= 0.10
    for reason in run_out.reasons:
        ax.text(0.08, y, f"- {reason}", fontsize=10.5, color=C_INK, va="top",
               wrap=True)
        y -= 0.06
    _footer(ax, f"Run {run_out.solve_job_id}")
    return fig


def _assumptions_page(best_obj: dict | None, run_out: RunOut, max_weight_kg):
    fig, ax = _page()
    _header(ax, "Assumptions")
    # `max_weight_kg` is `_asset_meta(...)`'s result, computed once in
    # `build_pdf` -- the same value `numbers["max_weight_kg"]` reports.
    caveat = ((best_obj or {}).get("dunnage") or {}).get("caveat", "")
    lines = [
        f"In-plane clearance: {run_out.clearance_mm} mm; "
        f"{DEFAULT_STACK_CLEARANCE_MM:g} mm stacking clearance (a layer rests "
        "on the layer, or the dunnage, below it).",
        f"Weight cap for this option: "
        f"{max_weight_kg if max_weight_kg is not None else 'not on file'} kg.",
        "Tare for the truck fit is the asset's own catalogue tare where one "
        "is on file, else the request's override.",
        "This is a starting design; physical trials still decide "
        "(PLANNING §7) — a proposal is not a final BOM.",
        "No volume or mass is reported from customer CAD: these are open "
        "surface models and those numbers would be meaningless.",
        f"Figures on this proposal come from run {run_out.solve_job_id}, "
        f"solved {run_out.created_at.date().isoformat()}.",
    ]
    if caveat:
        lines.append(caveat)
    y = 0.74
    for line in lines:
        ax.text(0.06, y, line, fontsize=11, color=C_INK, va="top", wrap=True)
        y -= 0.09
    return fig


def build_pdf(project: Project, part: PartProfile, run: SolveJob,
             run_out: RunOut, out_path: Path, png_dir: Path | None = None,
             db: Session | None = None) -> dict:
    """Render the F7 proposal PDF for one run. Returns `{"pages": n,
    "numbers": {...}}` -- `numbers` is every headline value the pages print,
    for the test to cross-check against `run_out`/`run.result_json`. Stored
    nowhere; it exists only so the test can assert without re-parsing the PDF.

    `png_dir`: dev-only eyeballing aid (`python -m app.proposal --png`), never
    passed by `worker.run_proposal`. When given, each page figure is ALSO
    saved as `<out_path stem>_p<N>.png` at 110 dpi -- this machine has neither
    pdftoppm nor pypdf to turn the PDF itself into images to look at.

    `db`: optional session for `_asset_meta`/`_vehicle_meta` (the truck load
    drawing's box outer footprint, weight cap and cargo bay) -- passed by
    `worker.run_proposal` and the CLI, both of which have one open already;
    falls back to the static seed constants when None (review/unit tests
    that build a PDF with no db at all).
    """
    from .runs import _best_object

    r = run.result_json or {}
    best_obj = _best_object(r)
    truck = r.get("truck")

    # Computed ONCE, fed to both the truck page's drawing and `numbers` --
    # never a second lookup that could disagree with what got drawn.
    outer, max_weight_kg = _asset_meta((truck or {}).get("asset_name", ""),
                                       best_obj, db)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if png_dir is not None:
        Path(png_dir).mkdir(parents=True, exist_ok=True)

    page_figs = [
        _cover_page(project, run_out),
        _part_pose_page(part, run_out, best_obj),
        _comparison_page(run, run_out),
        _bom_page(best_obj, run_out),
        _exploded_page(best_obj, run_out),
        _sequence_page(best_obj, run_out),
        _truck_page(run, run_out, outer, db),
        _gain_page(run_out),
        _assumptions_page(best_obj, run_out, max_weight_kg),
    ]
    pages = 0
    with PdfPages(out_path) as pdf:
        for i, fig in enumerate(page_figs, start=1):
            pdf.savefig(fig)
            if png_dir is not None:
                fig.savefig(Path(png_dir) / f"{out_path.stem}_p{i}.png", dpi=110)
            plt.close(fig)
            pages += 1

    # Every row page 3 actually prints, catalogue AND custom, in the same
    # (asset, pose, count, cuboid_count, grid, limited_by, count_upper) shape
    # -- raw result_json values, not the wrapped/joined strings the table
    # displays (those are a rendering choice, not a second number).
    comparison_rows = [
        (l.get("asset_name"), l.get("pose_label"), l.get("count"),
         l.get("cuboid_count"), tuple(l.get("grid") or ()),
         l.get("limited_by"), l.get("count_upper"))
        for l in r.get("catalogue") or []
    ]
    custom = r.get("custom")
    if custom is not None:
        comparison_rows.append(
            (custom.get("asset_name", "custom"), custom.get("pose_label"),
             custom.get("count"), custom.get("cuboid_count"),
             tuple(custom.get("grid") or ()), "custom",
             custom.get("count_upper"))
        )
    dunnage = (best_obj or {}).get("dunnage") or {}

    numbers = {
        "best_count": run_out.best_count,
        "best_asset": run_out.best_asset,
        "cuboid_count": run_out.cuboid_count,
        "customer_count": run_out.customer_count,
        "gain_vs_cuboid": run_out.gain_vs_cuboid,
        "gain_vs_customer_pct": run_out.gain_vs_customer_pct,
        "clearance_mm": run_out.clearance_mm,
        "poses_searched": len(run_out.poses_searched),
        "comparison_rows": comparison_rows,
        "truck_boxes": run_out.truck_boxes,
        "parts_per_truck": run_out.parts_per_truck,
        "trips_per_year": run_out.trips_per_year,
        "truck_by_volume": (truck or {}).get("by_volume"),
        "truck_by_weight": (truck or {}).get("by_weight"),
        "truck_limited_by": (truck or {}).get("limited_by"),
        "truck_tare_kg": (truck or {}).get("tare_kg"),
        "truck_floor_grid": tuple((truck or {}).get("floor_grid") or ()) or None,
        "truck_stack": (truck or {}).get("stack"),
        "truck_outer": outer,
        "max_weight_kg": max_weight_kg,
        "bom_build_height_mm": dunnage.get("build_height_mm"),
        "bom_inner_h_mm": dunnage.get("inner_h_mm"),
        "bom_fits": dunnage.get("fits"),
        "bom_elements": [(e.get("name"), e.get("qty"))
                        for e in dunnage.get("elements") or []],
    }
    logger.info("proposal PDF: %s pages -> %s", pages, out_path)
    return {"pages": pages, "numbers": numbers}


if __name__ == "__main__":
    import argparse
    import tempfile

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from .runs import _run_out, proposal_run_for

    parser = argparse.ArgumentParser(
        prog="python -m app.proposal",
        description="Render one project's proposal PDF for eyeballing "
                    "(INTAKE_DATABASE_URL picks the sqlite db).",
    )
    parser.add_argument("project_id", type=int)
    parser.add_argument(
        "--png", action="store_true",
        help="dev-only: also dump each page as proposal_<id>_p<N>.png at "
             "110 dpi next to the PDF -- this machine has neither pdftoppm "
             "nor pypdf to rasterise the PDF itself.",
    )
    args = parser.parse_args()

    eng = create_engine(settings.database_url)
    Session = sessionmaker(bind=eng)
    with Session() as db:
        proj = db.get(Project, args.project_id)
        if proj is None:
            raise SystemExit(f"Project {args.project_id} not found")
        # Same helper the route uses (app.runs.proposal_run_for): recommended
        # run if set and done, else newest done -- so this CLI and
        # POST /api/projects/{id}/proposal can never print a different run's
        # catalogue for the same project.
        solve_job = proposal_run_for(proj, db)
        if solve_job is None:
            raise SystemExit("No finished solve for this project")
        part_row = db.scalars(
            select(PartProfile).where(PartProfile.project_id == args.project_id)
        ).first()
        if part_row is None:
            raise SystemExit("Project has no part")

        out_dir = Path(tempfile.gettempdir())
        result = build_pdf(
            proj, part_row, solve_job, _run_out(solve_job, proj),
            out_dir / f"proposal_{args.project_id}.pdf",
            png_dir=out_dir if args.png else None, db=db,
        )
        print(f"wrote {result['pages']} pages")
        print(result["numbers"])
        if args.png:
            for p in sorted(out_dir.glob(f"proposal_{args.project_id}_p*.png")):
                print(p)
