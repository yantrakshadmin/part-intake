"""S1 — rerunnable sweep: run the real engine + drawings over every CAD file
we have and flag anomalies, so a bug like F7 (PLANNING.md §10) shows up on
every file, every time, not just the one Rahul happened to pick.

Reuses the engine exactly the way `ground_truth.py` / `test_tata.py --cad` /
`test_clearance.py` do -- extract -> measure poses -> rank against the seeded
catalogue -> render -- via the real functions in `app.geometry`, `app.engine`,
`app.nesting`, `app.dunnage` and `app.insert_drawing`. Nothing here
reimplements nesting math or the drawing painter; it only assembles calls the
way `app.engine.solve` already does (minus the custom-box synthesis branch,
which no ticket needs here -- add it back if one does).

Catalogue is the seed list (`app.catalogue.containers(None)`), same trick as
`ground_truth.py`'s own `Asset` instances: no DB, dev.db untouched.

CAD is customer NDA: read locally, never copied into the repo. Only file
names, dims and counts are printed -- never volume/mass (CLAUDE.md hard rule
4 -- these are open surface models) and never vertex data.

Run:
    cd backend
    python tests/sweep.py [dir-or-file ...] [--out sweep.md] [--catalogue-top 3]

Defaults to ../Rahul and tests/fixtures/customer (both gitignored, NDA, and
each is fine to be absent -- a missing default path is skipped, not a
failure). `.stp/.step/.igs/.iges` only; `.SLDPRT/.SLDASM` are listed as
"unreadable by design" (`app.geometry.extract_part`'s own gate, hard rule 5),
never handed to the extractor.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dunnage                                        # noqa: E402
from app import engine as engine_mod                            # noqa: E402
from app.catalogue import containers                            # noqa: E402
from app.engine import measure_distinct_poses                   # noqa: E402
from app.geometry import extract_part, load_unified_mesh        # noqa: E402
from app.insert_drawing import _as_4x4, build_gif, explode_png, pose_voxels  # noqa: E402
from app.nesting import IN_PLANE_TURN, layouts_for, rank_catalogue  # noqa: E402
# Same fixture identities `test_clearance.py` itself pins 40/PLS12801 and
# 48/PLS1280 against -- reused here, not retyped, so the sweep's own
# ground-truth check can never disagree with that file's.
from tests.test_clearance import BAR as GT_BAR, WHEEL as GT_WHEEL          # noqa: E402
from tests.ground_truth import PLS1280 as GT_PLS1280, PLS12801 as GT_PLS12801  # noqa: E402

logger = logging.getLogger("sweep")

# filename -> (asset, part_kg, expected count, expected grid). The full
# catalogue ranking below (step 2) legitimately picks a BETTER box than
# either of these for both parts (PLANNING §7's own worked example: PLS12103
# beats PLS12801 per-box) -- that is the engine working, not a bug. This is
# the separate, narrower check ground_truth.py/test_clearance.py make: the
# lattice math for THIS ONE asset must still reproduce what shipped.
GT_PINS = {
    GT_BAR.name: (GT_PLS12801, 5.0, 40, (1, 4, 10)),
    GT_WHEEL.name: (GT_PLS1280, 2.5, 48, (3, 2, 8)),
}

CAD_SUFFIXES = {".stp", ".step", ".igs", ".iges"}
UNREADABLE_SUFFIXES = {".sldprt", ".sldasm"}
DEFAULT_PATHS = ["../Rahul", "tests/fixtures/customer"]

def pocket_lt_part(layout) -> list:
    """Dunnage cells too small to hold the part in plan (F11). -> [(name, axis,
    cell, extent)]

    Reads the SAME `dunnage.bom` dict the sweep already stamped on the layout
    (hard rule 9: one expression, not a second opinion). `dunnage.bom` raises
    rather than return such a BOM, so on a real file the flag arrives the
    other way round -- `process` catches that ValueError and flags the row --
    and this scan is what catches a BOM that got past the invariant.
    """
    bad = []
    for el in (layout.dunnage or {}).get("elements", []):
        cell = el.get("cell_mm")
        if not cell:
            continue
        for ax in (0, 1):
            if round(cell[ax], 1) < round(float(layout.extent_lbh[ax]), 1) - 1e-6:
                bad.append((el["name"], "LB"[ax], cell[ax],
                            float(layout.extent_lbh[ax])))
    return bad


EXTRACT_SLOW_S = 60.0
RENDER_SLOW_S = 20.0


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def discover(raw_paths: list[str]) -> tuple[list[Path], list[Path]]:
    """(readable, unreadable) files found under the given dirs/files.

    A default path that does not exist (e.g. no ../Rahul on this machine) is
    skipped quietly -- it is a convenience default, not a required input.
    """
    readable: list[Path] = []
    unreadable: list[Path] = []
    for raw in raw_paths:
        p = Path(raw)
        if not p.exists():
            logger.info("path %s does not exist, skipping", p)
            continue
        candidates = [p] if p.is_file() else sorted(p.rglob("*"))
        for f in candidates:
            if not f.is_file():
                continue
            suf = f.suffix.lower()
            if suf in CAD_SUFFIXES:
                readable.append(f)
            elif suf in UNREADABLE_SUFFIXES:
                unreadable.append(f)
    return readable, unreadable


class _ClipCapture(logging.Handler):
    """Grabs `insert_drawing`'s "parts clipped by the volume" WARNING off the
    real logger instead of re-deriving the clip from the placement -- the log
    line and the picture come from the same `_place` call (hard rule 9), so
    reading the log is the honest way to know the drawing disagreed with the
    lattice count.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.hit: tuple[int, int] | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if "parts clipped by the volume" not in record.getMessage():
            return
        try:
            landed, expected = int(record.args[0]), int(record.args[1])
        except Exception:  # noqa: BLE001 -- format drifted; still flag CLIPPED
            landed = expected = -1
        self.hit = (landed, expected)


@dataclass
class Row:
    path: Path
    extract_s: float = 0.0
    solid_count: int = 0
    watertight: bool = False
    canonical_dims: tuple = ()
    units_assumed: str = ""
    warnings: list = field(default_factory=list)
    n_poses: int = 0
    extract_error: str | None = None

    engine_s: float = 0.0
    top: list = field(default_factory=list)   # ranked Layouts, best first
    engine_error: str | None = None
    # (asset_name, got_count, got_grid, want_count, want_grid, match) for the
    # two pinned ground-truth files only; None for every other file.
    gt_check: tuple | None = None

    explode_s: float | None = None
    gif_s: float | None = None
    clipped: tuple[int, int] | None = None
    render_error: str | None = None
    explode_png: Path | None = None
    packed_png: Path | None = None

    # F11: [(element, axis, cell_mm, extent_mm)] over every ranked layout.
    pockets_lt_part: list = field(default_factory=list)

    flags: list = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return (self.extract_s + self.engine_s + (self.explode_s or 0.0)
                + (self.gif_s or 0.0))


def rotation_for(candidates, pose_label: str, turned: bool):
    """Same expression as `worker._render_drawings.rotation_for`: the resting
    rotation, turned by `IN_PLANE_TURN` when the winning layout is (F7)."""
    rotation = next((c.rotation_matrix for c in candidates
                     if c.label == pose_label), None)
    if rotation is None or not turned:
        return rotation
    return IN_PLANE_TURN @ _as_4x4(rotation)


def process(path: Path, assets, out_dir: Path, top_n: int) -> Row:
    row = Row(path=path)

    # --- 1. extract -------------------------------------------------------
    t0 = time.monotonic()
    try:
        r = extract_part(str(path))
        mesh, _ = load_unified_mesh(r.glb_path)
    except Exception as exc:  # noqa: BLE001 -- one bad file must not stop the sweep
        row.extract_error = f"{type(exc).__name__}: {exc}"
        row.flags.append("EXTRACT_FAIL")
        return row
    row.extract_s = time.monotonic() - t0
    row.solid_count = r.solid_count
    row.watertight = r.watertight
    row.canonical_dims = r.canonical_dims_lbh
    row.units_assumed = r.units_assumed
    row.warnings = list(r.warnings)
    row.n_poses = len(r.candidates)
    if row.extract_s > EXTRACT_SLOW_S:
        row.flags.append("EXTRACT_SLOW")
    if row.warnings:
        row.flags.append("WARNINGS")

    # --- 2. engine: rank the seeded catalogue, top-N ----------------------
    t0 = time.monotonic()
    try:
        poses = measure_distinct_poses(mesh, r.candidates)
        ranked = rank_catalogue(mesh, r.candidates, assets, part_kg=0.0,
                                top_n=top_n, poses=poses)
        # Stamp cuboid_count/dunnage the same way `engine.solve` does --
        # reusing its own helpers, not a second opinion on the lattice.
        inner_by_name = {a.name: a.inner for a in assets}
        clearance_lbh = engine_mod.DEFAULT_CLEARANCE_LBH
        part_lbh = poses[0].extent if poses else (0.0, 0.0, 0.0)
        stamped = []
        for lay in ranked:
            inner = inner_by_name.get(lay.asset_name)
            if inner is None:
                stamped.append(lay)
                continue
            cc = engine_mod.cuboid_count(part_lbh, inner)
            try:
                bom = dunnage.bom(lay.extent_lbh, lay.pitch_lbh, lay.grid,
                                  inner, clearance_lbh)
            except ValueError as exc:
                # `dunnage.bom`'s own F11 invariant. Caught HERE: the outer
                # except sets ENGINE_FAIL, which is not fatal, so a BOM with a
                # pocket narrower than the part would have exited 0.
                row.pockets_lt_part.append((lay.asset_name, "-", 0.0, 0.0))
                row.flags.append("POCKET_LT_PART")
                logger.error("dunnage.bom refused %s/%s: %s",
                             path.name, lay.asset_name, exc)
                stamped.append(replace(lay, cuboid_count=cc))
                continue
            stamped.append(replace(lay, dunnage=bom.as_dict(), cuboid_count=cc))
        row.top = stamped
        row.pockets_lt_part += [b for lay in stamped for b in pocket_lt_part(lay)]
        if row.pockets_lt_part and "POCKET_LT_PART" not in row.flags:
            row.flags.append("POCKET_LT_PART")

        no_nest = all(
            p.pitch[0] >= p.extent[0] - 1e-6 and p.pitch[1] >= p.extent[1] - 1e-6
            for p in poses
        ) if poses else False
        if no_nest:
            row.flags.append("NO_NEST")

        if row.top:
            winner = row.top[0]
            if winner.count < winner.cuboid_count:
                row.flags.append("BELOW_CUBOID")
            if winner.count_upper < winner.count:
                row.flags.append("UPPER_LT_COUNT")
            if winner.turned:
                row.flags.append("TURNED")

        pin = GT_PINS.get(path.name)
        if pin is not None:
            asset, kg, want_count, want_grid = pin
            pinned = layouts_for(poses, asset, kg)
            got = pinned[0] if pinned else None
            match = (got is not None and got.count == want_count
                     and got.grid == want_grid)
            row.gt_check = (asset.name, got.count if got else 0,
                           got.grid if got else None, want_count, want_grid,
                           match)
            if not match:
                row.flags.append("GT_MISMATCH")
    except Exception as exc:  # noqa: BLE001
        row.engine_error = f"{type(exc).__name__}: {exc}"
        row.flags.append("ENGINE_FAIL")
        # `dunnage.bom`'s F11 invariant, raised from wherever the engine asked
        # for a BOM (`layouts_for` charges one per pose for the height budget,
        # long before this file stamps its own). ENGINE_FAIL is not fatal, so
        # without this line a pocket narrower than the part still exits 0.
        if "cannot hold it" in str(exc):
            row.flags.append("POCKET_LT_PART")
        return row
    row.engine_s = time.monotonic() - t0

    if not row.top:
        return row

    # --- 3. drawing: winner only, exactly like worker._render_drawings ----
    winner = row.top[0]
    inner = inner_by_name.get(winner.asset_name)
    rotation = rotation_for(r.candidates, winner.pose_label, winner.turned)
    stem = out_dir / _slug(path.stem)

    clip = _ClipCapture()
    ins_logger = logging.getLogger("app.insert_drawing")
    ins_logger.addHandler(clip)
    try:
        try:
            voxels = pose_voxels(mesh, rotation) if rotation is not None else None
            if voxels is None:
                raise ValueError(f"no rotation for pose {winner.pose_label!r}")
            bom = dunnage.bom(winner.extent_lbh, winner.pitch_lbh, winner.grid,
                              inner, clearance_lbh)
        except Exception as exc:  # noqa: BLE001
            row.render_error = f"setup: {type(exc).__name__}: {exc}"
            row.flags.append("RENDER_FAIL")
            return row

        try:
            t0 = time.monotonic()
            png = explode_png(voxels=voxels, extent_lbh=winner.extent_lbh,
                              pitch_lbh=winner.pitch_lbh, grid=winner.grid,
                              inner_lbh=inner, bom=bom,
                              asset_name=winner.asset_name, count=winner.count)
            row.explode_s = time.monotonic() - t0
            out_dir.mkdir(parents=True, exist_ok=True)
            path_png = stem.with_name(stem.name + "_explode.png")
            path_png.write_bytes(png)
            row.explode_png = path_png
        except Exception as exc:  # noqa: BLE001
            row.render_error = f"explode_png: {type(exc).__name__}: {exc}"
            row.flags.append("RENDER_FAIL")

        try:
            t0 = time.monotonic()
            _gif, packed, _seq = build_gif(
                voxels=voxels, extent_lbh=winner.extent_lbh,
                pitch_lbh=winner.pitch_lbh, grid=winner.grid, inner_lbh=inner,
                bom=bom, asset_name=winner.asset_name, count=winner.count,
                pose_matrix=rotation, frames=False)
            row.gif_s = time.monotonic() - t0
            out_dir.mkdir(parents=True, exist_ok=True)
            path_packed = stem.with_name(stem.name + "_packed.png")
            path_packed.write_bytes(packed)
            row.packed_png = path_packed
        except Exception as exc:  # noqa: BLE001
            note = f"build_gif: {type(exc).__name__}: {exc}"
            row.render_error = f"{row.render_error}; {note}" if row.render_error else note
            row.flags.append("RENDER_FAIL")

        if (row.explode_s or 0) + (row.gif_s or 0) > RENDER_SLOW_S:
            row.flags.append("RENDER_SLOW")
        if clip.hit is not None:
            row.clipped = clip.hit
            row.flags.append("CLIPPED")
    finally:
        ins_logger.removeHandler(clip)

    return row


def _fmt(v, nd=1):
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def render_markdown(rows: list[Row], unreadable: list[Path], top_n: int) -> str:
    lines = ["# CAD sweep", ""]
    lines.append(f"{len(rows)} readable file(s), {len(unreadable)} "
                 "unreadable-by-design (.SLDPRT/.SLDASM).")
    lines.append("")

    if unreadable:
        lines.append("## Unreadable by design")
        for p in unreadable:
            lines.append(f"- `{p.name}` -- SolidWorks native, no open-source "
                         "reader (hard rule 5). Export STEP/IGES.")
        lines.append("")

    lines.append("## Summary")
    lines.append("| file | extract_s | solids | watertight | poses | engine_s | "
                 "winner | explode_s | gif_s | total_s | flags |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        if row.top:
            w = row.top[0]
            winner = (f"{w.asset_name} {w.pose_label} count={w.count} "
                     f"grid={w.grid}")
        else:
            winner = "-"
        lines.append(
            f"| {row.path.name} | {_fmt(row.extract_s)} | {row.solid_count} | "
            f"{row.watertight} | {row.n_poses} | {_fmt(row.engine_s)} | "
            f"{winner} | {_fmt(row.explode_s) if row.explode_s is not None else '-'} | "
            f"{_fmt(row.gif_s) if row.gif_s is not None else '-'} | "
            f"{_fmt(row.total_s)} | {', '.join(row.flags) or '-'} |"
        )
    lines.append("")

    lines.append(f"## Per-file detail (top {top_n} catalogue layouts)")
    for row in rows:
        lines.append(f"### {row.path.name}")
        if row.extract_error:
            lines.append(f"- EXTRACT_FAIL: {row.extract_error}")
            lines.append("")
            continue
        lines.append(
            f"- extract: {_fmt(row.extract_s)}s, solids={row.solid_count}, "
            f"watertight={row.watertight}, dims={row.canonical_dims}mm, "
            f"units={row.units_assumed}, poses={row.n_poses}"
        )
        if row.warnings:
            lines.append(f"- warnings: {'; '.join(row.warnings)}")
        if row.engine_error:
            lines.append(f"- ENGINE_FAIL: {row.engine_error}")
            lines.append("")
            continue
        lines.append("")
        lines.append("| rank | asset | pose | count | grid | extent_lbh | "
                     "pitch_lbh | count_upper | cuboid_count | limited_by | "
                     "turned | archetype |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for i, lay in enumerate(row.top, start=1):
            lines.append(
                f"| {i} | {lay.asset_name} | {lay.pose_label} | {lay.count} | "
                f"{lay.grid} | {lay.extent_lbh} | {lay.pitch_lbh} | "
                f"{lay.count_upper} | {lay.cuboid_count} | {lay.limited_by} | "
                f"{lay.turned} | {(lay.dunnage or {}).get('archetype', '-')} |"
            )
        lines.append("")
        if row.gt_check:
            name, got_c, got_g, want_c, want_g, match = row.gt_check
            lines.append(
                f"- ground-truth contract: {name} got {got_c}/{got_g}, "
                f"shipped {want_c}/{want_g} -> {'MATCH' if match else 'MISMATCH'}"
            )
        for name, ax, cell, ext in row.pockets_lt_part:
            lines.append(f"- POCKET_LT_PART: {name} cell {cell:g}mm on {ax} "
                         f"under a {ext:g}mm part")
        if row.render_error:
            lines.append(f"- RENDER_FAIL: {row.render_error}")
        if row.explode_s is not None or row.gif_s is not None:
            lines.append(
                f"- drawing: explode {_fmt(row.explode_s) if row.explode_s is not None else '-'}s, "
                f"gif(frames=False) {_fmt(row.gif_s) if row.gif_s is not None else '-'}s"
                + (f", CLIPPED {row.clipped[0]}/{row.clipped[1]} part cells landed"
                   if row.clipped else "")
            )
        if row.explode_png:
            lines.append(f"- {row.explode_png}")
        if row.packed_png:
            lines.append(f"- {row.packed_png}")
        lines.append(f"- FLAGS: {', '.join(row.flags) or '-'}")
        lines.append("")

    lines.append("## Flag summary")
    all_flags = ["EXTRACT_FAIL", "EXTRACT_SLOW", "ENGINE_FAIL", "CLIPPED",
                "NO_NEST", "BELOW_CUBOID", "UPPER_LT_COUNT", "TURNED",
                "RENDER_FAIL", "RENDER_SLOW", "WARNINGS", "GT_MISMATCH",
                "POCKET_LT_PART"]
    for flag in all_flags:
        hit = [r.path.name for r in rows if flag in r.flags]
        lines.append(f"- {flag}: {len(hit)} -- {', '.join(hit) if hit else '(none)'}")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", default=DEFAULT_PATHS)
    ap.add_argument("--out", default="sweep.md")
    ap.add_argument("--catalogue-top", type=int, default=3, dest="top_n")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    out_path = Path(args.out)
    out_dir = out_path.parent if out_path.parent != Path("") else Path(".")

    readable, unreadable = discover(args.paths)
    if not readable and not unreadable:
        print("No CAD files found under:", args.paths)
        return 0

    assets = containers(None)   # seed list, no DB (ticket: never touch dev.db)

    rows = []
    for f in readable:
        print(f"--- {f} ---", flush=True)
        t0 = time.monotonic()
        row = process(f, assets, out_dir, args.top_n)
        print(f"    {time.monotonic() - t0:.1f}s total, flags={row.flags}",
             flush=True)
        rows.append(row)

    md = render_markdown(rows, unreadable, args.top_n)
    print(md)
    out_path.write_text(md)
    print(f"\nwritten to {out_path}")

    # BELOW_CUBOID/UPPER_LT_COUNT per the ticket; GT_MISMATCH too -- the
    # ground-truth regression is CLAUDE.md's own non-negotiable, and this is
    # the one check in this file that can actually see it break.
    fatal = any(f in row.flags for row in rows
               for f in ("BELOW_CUBOID", "UPPER_LT_COUNT", "GT_MISMATCH",
                         "POCKET_LT_PART"))
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
