"""F4 — "why this design", composed server-side from fields that already
exist on a solved `nesting.Layout` / `synthesis.BoxDesign`.

One function, called by `worker.run_solve` once clearance, poses_searched,
confirmed_label and warnings are all in scope. Wording is the backend's; the
frontend only prints `reasons` (CLAUDE.md hard rule 9 -- a sentence and the
number it describes come from the same expression, never a client re-derive).
"""

from __future__ import annotations

from .nesting import DEFAULT_STACK_CLEARANCE_MM

# Axis labels for the two in-plane axes nesting.Layout/synthesis.BoxDesign
# both carry L/B/H in -- height (index 2) is a layer count, not an in-plane
# nest, so it never appears here.
_AXES = ("L", "B", "H")


def _interleave(extent_lbh, pitch_lbh) -> tuple:
    return tuple(round(p / e, 3) if e else 1.0
                for p, e in zip(pitch_lbh, extent_lbh))


def reasons_for(layout_or_design, *, part_lbh, part_kg: float,
                clearance_mm: float, poses_searched: list,
                confirmed_label: str | None, inner_lbh, max_weight_kg) -> list[str]:
    """>= 3 sentences explaining one layout/design, every one from a field
    that is already on `layout_or_design` or passed in by the worker.

    `part_lbh` is accepted for parity with the fields the worker already has
    in scope at the call site; none of the sentences below need it yet.
    """
    obj = layout_or_design
    out: list[str] = []

    # --- pose -----------------------------------------------------------
    n = len(poses_searched)
    if confirmed_label is not None and n == 1:
        out.append(f"Rests {obj.pose_label!r}; confirmed pose only.")
    else:
        out.append(f"Rests {obj.pose_label!r}; {n} poses searched, "
                   "this one packs most.")

    # --- interleave -------------------------------------------------------
    interleave = _interleave(obj.extent_lbh, obj.pitch_lbh)
    nested_axes = [i for i in (0, 1) if interleave[i] < 1]
    if nested_axes:
        # Report the axis nesting the hardest (smallest ratio = biggest gain).
        ax = min(nested_axes, key=lambda i: interleave[i])
        pct = round((1 - interleave[ax]) * 100)
        out.append(
            f"Parts nest {pct}% into each other along {_AXES[ax]}: "
            f"pitch {obj.pitch_lbh[ax]} mm against a {obj.extent_lbh[ax]} mm extent."
        )
    else:
        out.append("No in-plane nesting: pitch equals extent.")

    # --- limit ------------------------------------------------------------
    # `Layout.limited_by` is authoritative where it exists (nesting.py). A
    # `BoxDesign` does not carry it -- its height is solved to fit, not
    # measured against a fixed inner -- so infer it: geometry, unless the
    # weight cap is what actually stopped another layer going on.
    limited_by = getattr(obj, "limited_by", None)
    if limited_by is None:
        per_layer = obj.grid[0] * obj.grid[1]
        # ponytail: heuristic, not a re-derivation of synthesise's own search
        # -- "one more layer would break the cap" is all a reasons sentence
        # needs, and synthesise doesn't store which bound it hit.
        limited_by = ("weight" if (part_kg > 0 and max_weight_kg and per_layer
                                   and (obj.count + per_layer) * part_kg
                                   > max_weight_kg)
                     else "geometry")
    if limited_by == "weight":
        total = round(obj.count * part_kg, 1)
        out.append(
            f"Weight-capped: {obj.count} x {part_kg} kg = {total} kg of a "
            f"{max_weight_kg} kg limit."
        )
    else:
        out.append(
            f"Geometry-capped: {obj.grid[0]} across x {obj.grid[1]} along x "
            f"{obj.grid[2]} layers fills {obj.extent_lbh} in {inner_lbh}."
        )

    # --- band ---------------------------------------------------------
    if obj.count_upper > obj.count:
        out.append(
            f"Raster band {obj.count}-{obj.count_upper}: the ceiling needs a "
            "finer voxel to confirm."
        )

    # --- clearance ------------------------------------------------------
    out.append(
        f"Solved at {clearance_mm} mm in-plane clearance, "
        f"{DEFAULT_STACK_CLEARANCE_MM:g} mm stacking."
    )

    # --- baseline -----------------------------------------------------
    out.append(f"Cuboid packing of the same box gives {obj.cuboid_count}.")

    # --- dunnage --------------------------------------------------------
    dunnage = getattr(obj, "dunnage", None)
    if dunnage and dunnage.get("fits") is False:
        out.append(
            f"Insert build height exceeds the box: {dunnage.get('build_height_mm')} "
            f"> {dunnage.get('inner_h_mm')} mm."
        )

    return out


def _demo():
    """python -m app.reasons -- assert-based self check, no fixtures."""
    from .nesting import Layout

    # Interleaved on B (pitch 155 < extent 285), packs 40, cuboid baseline 8.
    nested = Layout(
        asset_name="PLS12801", pose_label="largest face down", count=40,
        grid=(4, 10, 1), extent_lbh=(1085.0, 285.0, 190.0),
        pitch_lbh=(1085.0, 155.0, 66.67), limited_by="geometry",
        count_upper=44, cuboid_count=8,
    )
    r = reasons_for(nested, part_lbh=(1085, 190, 285), part_kg=5.0,
                    clearance_mm=5.0, poses_searched=["a", "b", "c"],
                    confirmed_label=None, inner_lbh=(1150, 750, 790),
                    max_weight_kg=600.0)
    assert len(r) >= 3, r
    assert any("nest" in s and "%" in s for s in r), r

    # A pure cuboid: pitch == extent on both floor axes, no interleave.
    cuboid = Layout(
        asset_name="PLS12801", pose_label="flat", count=8,
        grid=(2, 2, 2), extent_lbh=(500.0, 375.0, 395.0),
        pitch_lbh=(500.0, 375.0, 395.0), limited_by="geometry",
        count_upper=8, cuboid_count=8,
    )
    r2 = reasons_for(cuboid, part_lbh=(500, 375, 395), part_kg=5.0,
                     clearance_mm=5.0, poses_searched=["flat"],
                     confirmed_label="flat", inner_lbh=(1150, 750, 790),
                     max_weight_kg=600.0)
    assert len(r2) >= 3, r2
    assert not any("nest" in s and "%" in s for s in r2), r2
    assert any("No in-plane nesting" in s for s in r2), r2
    assert "confirmed pose only" in r2[0], r2

    print("app.reasons self-check ok")
    print("nested:", *r, sep="\n  ")
    print("cuboid:", *r2, sep="\n  ")


if __name__ == "__main__":
    _demo()
