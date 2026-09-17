"""Catalogue adapter acceptance check (ticket B2-1, extended by C-TARE).

Run:  python tests/test_catalogue.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Check 9 imports app.main, which seeds its DB at import time against the
# compose default (postgres host "db") unless told otherwise. Point it at a
# throwaway sqlite BEFORE anything under app/ is imported, as test_solve_api
# does, so this file still runs bare as the docstring promises.
import os  # noqa: E402
import tempfile  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="test_catalogue_")
os.environ.setdefault("INTAKE_DATABASE_URL", f"sqlite:///{_TMP}/seed.db")
os.environ.setdefault("INTAKE_LOCAL_STORAGE_DIR", _TMP)
os.environ.setdefault("INTAKE_REDIS_URL", "redis://127.0.0.1:6379/9")

from sqlalchemy import (Column, Integer, MetaData, String, Table,  # noqa: E402
                        create_engine, inspect, select, text)
from sqlalchemy.orm import Session  # noqa: E402

from app.catalogue import Container, containers  # noqa: E402
from app.engine import parts_per_truck  # noqa: E402
from app.models import Base, Packaging, PartProfile  # noqa: E402
from app.nesting import lattice_count  # noqa: E402
from app.seed_data import PACKAGING, VEHICLES, seed_master_data  # noqa: E402
from tests.ground_truth import CASES  # noqa: E402


def main() -> int:
    boxes = containers()

    # 1. Exactly the seeded PACKAGING rows, all containers. 17: PLS1280 (TRW
    # deck) and PLS12804 (Tata deck) are both deck-only, in no other source.
    assert len(boxes) == len(PACKAGING) == 17, len(boxes)
    assert all(c.kind == "container" for c in boxes)

    # 2. Duck-compatible with ground_truth.Asset: has .name/.inner/.max_weight_kg,
    # and app.nesting.lattice_count accepts it unchanged.
    pls12801 = next(c for c in boxes if c.name == "PLS12801")
    assert isinstance(pls12801, Container)
    count, grid, limited_by = lattice_count(
        extent_lbh=(1085.0, 285.0, 190.0),
        pitch_lbh=(1085.0, 155.0, 200.0 / 3.0),
        inner_lbh=pls12801.inner,
        part_kg=5.0,
        max_weight_kg=pls12801.max_weight_kg,
    )
    assert count == 40, count

    # 3. Ground-truth assets must not drift.
    assert pls12801.inner == (1150, 750, 790), pls12801.inner
    pls12803 = next(c for c in boxes if c.name == "PLS12803")
    assert pls12803.inner == (1150, 750, 1000), pls12803.inner

    # 4. A row with kind != "container" is excluded from containers() — exercise
    # the real DB path, not just the no-DB fallback.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Packaging(
            item_code="PLS12801", inner_l_mm=1150, inner_b_mm=750, inner_h_mm=790,
            outer_l_mm=1200, outer_b_mm=800, outer_h_mm=986,
            max_weight_kg=600, status="checked", kind="container",
        ))
        db.add(Packaging(
            item_code="RACK-01", inner_l_mm=1000, inner_b_mm=1000, inner_h_mm=1000,
            outer_l_mm=1000, outer_b_mm=1000, outer_h_mm=1000,
            max_weight_kg=100, status="checked", kind="rack",
        ))
        db.commit()

        db_boxes = containers(db)
        assert isinstance(db_boxes[0], Container)
        db_names = {c.name for c in db_boxes}
        assert db_names == {"PLS12801"}, db_names

    # 5. C-TARE: per-asset tare, read from SCSSolutions_Report_company17.xlsx
    # (seed_data.PACKAGING_TARE), not a request-level guess.
    pls12103 = next(c for c in boxes if c.name == "PLS12103")
    crt6412 = next(c for c in boxes if c.name == "CRT6412")
    assert pls12801.tare_kg == 30, pls12801.tare_kg
    assert pls12103.tare_kg == 39, pls12103.tare_kg
    assert crt6412.tare_kg == 1.9, crt6412.tare_kg

    # Every one of these 15 has a REAL tare, never a 0 standing in for
    # "unknown" (the source report reads 0 for 13 of its 49 rows, but none of
    # those 13 are among these 15 — see seed_data.PACKAGING_TARE's docstring).
    unknown_or_zero = [c.name for c in boxes if not c.tare_kg]
    assert not unknown_or_zero, unknown_or_zero

    # 6. The truck tier ranks with PER-ASSET tare now, not a flat guess
    # applied to every asset. This is engine.py:41's worked example redone
    # with each box's real tare (PLS12801 30kg, PLS12103's real 39kg, not
    # the 30kg the module header used to assume for both) — PLS12103's own
    # tare eats back its box-level lead and the two now TIE at the truck,
    # they do not swap: PLS12801 must never rank BELOW PLS12103 per truck.
    c0 = CASES[0]
    sxl32 = next(v for v in VEHICLES if v[0] == "32_ft_sxl")
    n801, _, _ = lattice_count(c0.pose_lbh, c0.pitch_lbh, pls12801.inner,
                               c0.part_kg, pls12801.max_weight_kg)
    n103, _, _ = lattice_count(c0.pose_lbh, c0.pitch_lbh, pls12103.inner,
                               c0.part_kg, pls12103.max_weight_kg)
    fit801 = parts_per_truck(pls12801.outer, n801, c0.part_kg,
                             pls12801.tare_kg, sxl32)
    fit103 = parts_per_truck(pls12103.outer, n103, c0.part_kg,
                             pls12103.tare_kg, sxl32)
    assert fit801.parts >= fit103.parts, (fit801.parts, fit103.parts)
    assert (fit801.parts, fit103.parts) == (1560, 1560), \
        (fit801.parts, fit103.parts)

    # 7. Seeding onto a NON-EMPTY table. Every other check here builds a fresh
    # in-memory DB, so the empty-table path is the only one they ever run --
    # which is exactly how PLS1280 and PLS12804 got appended to PACKAGING and
    # then never appeared on the dev.db anyone actually uses. Assert both
    # halves: a missing code gets inserted, and an existing row is not touched.
    engine2 = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine2)
    with Session(engine2) as db:
        db.add(Packaging(
            item_code="PLS12801", inner_l_mm=1, inner_b_mm=1, inner_h_mm=1,
            outer_l_mm=1, outer_b_mm=1, outer_h_mm=1, max_weight_kg=1,
            status="checked", kind="container", tare_kg=99.0,
        ))
        db.commit()
        seed_master_data(db)
        seeded = {c.item_code: c for c in db.scalars(select(Packaging))}
        assert len(seeded) == len(PACKAGING), sorted(seeded)
        missing = [code for code, *_ in PACKAGING if code not in seeded]
        assert not missing, missing
        # untouched: dims and an explicit tare both survive
        assert seeded["PLS12801"].inner_l_mm == 1, seeded["PLS12801"].inner_l_mm
        assert seeded["PLS12801"].tare_kg == 99.0, seeded["PLS12801"].tare_kg
        # and the newcomers arrived complete
        assert seeded["PLS12804"].inner_h_mm == 580, seeded["PLS12804"].inner_h_mm
        assert seeded["PLS12804"].tare_kg == 25.0, seeded["PLS12804"].tare_kg

    # 8. Both findings from the API review existed because nothing tested
    # them. Serialisation, not the ORM: pydantic drops undeclared fields
    # SILENTLY, so reading `row.tare_kg` off the model proves nothing about
    # what leaves over HTTP.
    from app.schemas import PackagingOut, SolveIn        # noqa: PLC0415
    import pydantic                                      # noqa: PLC0415
    row = Packaging(
        id=1, item_code="PLS12801", inner_l_mm=1150, inner_b_mm=750,
        inner_h_mm=790, outer_l_mm=1200, outer_b_mm=800, outer_h_mm=986,
        max_weight_kg=600, status="checked", kind="container",
        tare_kg=30.0, material="HDPE", fold_type="unknown",
    )
    dumped = PackagingOut.model_validate(row).model_dump()
    assert dumped.get("tare_kg") == 30.0, dumped
    assert dumped.get("material") == "HDPE", dumped

    # An EMPTY `assets` list used to validate, then fall through the worker's
    # `if requested:` to the unrestricted catalogue -- "rank none of them"
    # became "rank all of them", with no warning. None must still mean
    # unrestricted; [] must not.
    assert SolveIn(assets=None).assets is None
    try:
        SolveIn(assets=[])
        raise AssertionError("assets=[] accepted -- it silently ranks everything")
    except pydantic.ValidationError:
        pass

    # 9. T6 migration check (landmine 5): a db built before fold_type /
    # folded_h_mm / lid_void_mm / surface_class existed must gain all four
    # when main._ensure_added_columns runs, and a packaging row written
    # before the column existed must read back the column's own SQL
    # DEFAULT, not a Python-side value nobody wrote for it.
    from app import main as app_main                     # noqa: PLC0415
    old_engine = create_engine("sqlite:///:memory:")
    old_meta = MetaData()
    Table(
        "packaging", old_meta,
        Column("id", Integer, primary_key=True),
        Column("item_code", String(32)),
        Column("kind", String(16)),
    )
    Table(
        "part_profiles", old_meta,
        Column("id", Integer, primary_key=True),
        Column("part_number", String(64)),
    )
    old_meta.create_all(old_engine)
    with old_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO packaging (id, item_code, kind) "
            "VALUES (1, 'OLD001', 'container')"
        ))

    insp_before = inspect(old_engine)
    pkg_before = {c["name"] for c in insp_before.get_columns("packaging")}
    pp_before = {c["name"] for c in insp_before.get_columns("part_profiles")}
    assert not {"fold_type", "folded_h_mm", "lid_void_mm"} & pkg_before, pkg_before
    assert "surface_class" not in pp_before, pp_before

    app_main._ensure_added_columns(old_engine)

    insp_after = inspect(old_engine)
    pkg_after = {c["name"] for c in insp_after.get_columns("packaging")}
    pp_after = {c["name"] for c in insp_after.get_columns("part_profiles")}
    assert {"fold_type", "folded_h_mm", "lid_void_mm"} <= pkg_after, pkg_after
    assert "surface_class" in pp_after, pp_after

    with old_engine.begin() as conn:
        old_fold_type = conn.execute(
            text("SELECT fold_type FROM packaging WHERE id = 1")
        ).scalar()
    assert old_fold_type == "unknown", old_fold_type

    # 10. Serialisation: all four T6 fields actually leave over HTTP (a
    # PackagingOut/PartProfileOut missing a field drops it SILENTLY -- hard
    # rule 9), and the catalogue adapter carries them through the DB path too.
    ser_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(ser_engine)
    with Session(ser_engine) as db:
        db.add(Packaging(
            item_code="T6-SER", inner_l_mm=500, inner_b_mm=400, inner_h_mm=300,
            outer_l_mm=520, outer_b_mm=420, outer_h_mm=320, max_weight_kg=50,
            status="checked", kind="container",
            fold_type="collapsible", folded_h_mm=150.0, lid_void_mm=40.0,
        ))
        db.add(PartProfile(
            part_number="T6-PART", part_name="T6 test part",
            length_mm=100, breadth_mm=80, height_mm=50, weight_kg=2.5,
            source="manual", surface_class="raw",
        ))
        db.commit()
        pkg_row = db.scalars(
            select(Packaging).where(Packaging.item_code == "T6-SER")
        ).one()
        part_row = db.scalars(
            select(PartProfile).where(PartProfile.part_number == "T6-PART")
        ).one()

        pkg_dumped = PackagingOut.model_validate(pkg_row).model_dump()
        assert pkg_dumped.get("fold_type") == "collapsible", pkg_dumped
        assert pkg_dumped.get("folded_h_mm") == 150.0, pkg_dumped
        assert pkg_dumped.get("lid_void_mm") == 40.0, pkg_dumped

        # main._to_out is the actual /api/parts serialisation path (PartProfile
        # has glb_path, not glb_url -- _to_out derives it, so this is not a
        # plain PartProfileOut.model_validate(row)).
        part_dumped = app_main._to_out(part_row).model_dump()
        assert part_dumped.get("surface_class") == "raw", part_dumped

        db_boxes = containers(db)
        ser_box = next(c for c in db_boxes if c.name == "T6-SER")
        assert ser_box.fold_type == "collapsible", ser_box.fold_type
        assert ser_box.folded_h_mm == 150.0, ser_box.folded_h_mm
        assert ser_box.lid_void_mm == 40.0, ser_box.lid_void_mm

    print(f"PASS  containers(): {len(boxes)} rows, all kind=container")
    print(f"PASS  duck-typed for nesting.lattice_count: PLS12801 -> {count} parts")
    print("PASS  PLS12801 inner (1150, 750, 790), PLS12803 inner (1150, 750, 1000)")
    print("PASS  non-container rows excluded")
    print(f"PASS  per-asset tare: PLS12801={pls12801.tare_kg} "
          f"PLS12103={pls12103.tare_kg} CRT6412={crt6412.tare_kg}")
    print("PASS  no zero-for-unknown tare among the 15 ingested assets")
    print(f"PASS  truck tier per-asset tare: PLS12801 {n801}/box -> "
          f"{fit801.parts} parts/truck; PLS12103 {n103}/box -> "
          f"{fit103.parts} parts/truck (PLS12801 not outranked)")
    print("PASS  seeding a non-empty table inserts missing codes "
          "(PLS12804 in) and overwrites nothing (PLS12801 tare 99.0 kept)")
    print("PASS  PackagingOut ships tare_kg/material (pydantic drops "
          "undeclared fields silently); assets=[] rejected, None unrestricted")
    print("PASS  _ensure_added_columns adds packaging.fold_type/folded_h_mm/"
          "lid_void_mm and part_profiles.surface_class to a pre-T6 db; "
          f"old row reads back fold_type={old_fold_type!r}")
    print("PASS  PackagingOut/PartProfileOut/catalogue.containers() all ship "
          "fold_type/folded_h_mm/lid_void_mm/surface_class")
    print()
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
