"""Master data seed — imported from packaging_and_vehicles (1).xlsx
(2026-06-11). Inserted on startup only when the tables are empty, so user
edits and custom boxes are never overwritten.

Rows: (item_code, inner L/B/H, outer L/B/H, max weight kg)

PACKAGING_TARE (tare_kg, material) is a second, later import — read locally
from the customer's own SCS Solutions report,
`SCSSolutions_Report_company17.xlsx` (49 rows, columns J and I; read
2026-09-10). It disagrees with the sheet above on some inner/outer dims and
max_weight_kg for these same 15 assets (see ticket C-TARE's conflict table);
this seed intentionally does NOT touch those fields, only tare_kg/material,
because which source is authoritative on dims/weight is a PM call. Only the
15 assets already in PACKAGING are keyed here — the report's other 34 rows
are Phase 3 catalogue cleanup (PLANNING §5), not ingested by this ticket.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Packaging, Vehicle

PACKAGING = [
    ("PLS12801", 1150, 750, 790, 1200, 800, 986, 600),
    ("PLS12802", 1150, 750, 490, 1200, 800, 686, 600),
    ("PLS12803", 1150, 750, 1000, 1200, 800, 1196, 600),
    ("PLS12101", 1150, 950, 790, 1200, 1000, 986, 600),
    ("PLS12102", 1150, 950, 490, 1200, 1000, 686, 600),
    ("PLS12103", 1150, 950, 1000, 1200, 1000, 1196, 600),
    # From TP_TRW Sun Steering Wheels_1704 slide 3 (the TRW ground-truth
    # asset). NOT a typo for PLS12803 -- different inner H -- and absent
    # from the SCS solutions report too, so this deck is its only source.
    ("PLS1280", 1150, 750, 1000, 1200, 800, 1200, 600),
    # From TP_Tata Autocomp _ 1574 & 1578 slides 3-4, which ship two of their
    # four cases in it. Inner H 580 puts it between PLS12802 (490) and
    # PLS12801 (790); also absent from the SCS report, so the deck is the
    # only source. Outer H 790 == PLS12801's INNER H; that is a coincidence
    # of the moulds, not a typo.
    ("PLS12804", 1150, 750, 580, 1200, 800, 790, 600),
    ("FLC12101", 1110, 910, 760, 1200, 1000, 975, 900),
    ("FSC", 1110, 910, 580, 1200, 1000, 795, 900),
    ("FLC12102", 1110, 910, 985, 1200, 1000, 1200, 900),
    ("CRT6412", 550, 360, 105, 600, 400, 120, 20),
    ("CRT6418", 550, 360, 165, 600, 400, 180, 20),
    ("CRT6423", 550, 360, 220, 600, 400, 235, 20),
    ("CRT6435", 550, 360, 335, 600, 400, 350, 20),
    ("CRT4312", 350, 260, 105, 400, 300, 120, 20),
    ("CRT4323", 350, 260, 220, 400, 300, 235, 20),
]

# (item_code) -> (tare_kg, material), from SCSSolutions_Report_company17.xlsx
# columns J/I. None of these 15 read 0 in the source (0 = "no tare on file"
# elsewhere in that report, never seeded as a value here).
PACKAGING_TARE: dict[str, tuple[float, str]] = {
    "PLS12801": (30.0, "HDPE"),
    "PLS12802": (26.0, "HDPE"),
    "PLS12803": (33.0, "HDPE"),
    "PLS12101": (36.0, "HDPE"),
    "PLS12102": (32.0, "HDPE"),
    "PLS12103": (39.0, "HDPE"),
    "PLS1280": (35.0, "HDPE"),   # TRW deck slide 3, not in the SCS report
    "PLS12804": (25.0, "HDPE"),  # Tata deck slides 3-4, not in the SCS report
    "FLC12101": (62.0, "HDPE"),
    "FLC12102": (64.0, "HDPE"),
    "FSC": (40.0, "HDPE"),
    "CRT6412": (1.9, "HDPE"),
    "CRT6418": (2.2, "HDPE"),
    "CRT6423": (2.64, "HDPE"),
    "CRT6435": (3.4, "HDPE"),
    "CRT4312": (1.1, "HDPE"),
    "CRT4323": (1.7, "HDPE"),
}

# (name, cargo L/B/H mm, payload kg)
VEHICLES = [
    ("Pickup", 2740, 1676, 1676, 1000),
    ("Tempo_407", 2896, 1676, 1828, 2500),
    ("13_Feet", 3962, 1676, 2134, 3500),
    ("14_Feet", 4267, 1829, 2134, 4000),
    ("17_Feet", 5182, 1829, 2134, 6000),
    ("20_ft_sxl", 6096, 2438, 2438, 6000),
    ("24_ft_sxl", 7315, 2438, 2438, 7000),
    ("32_ft_sxl", 9754, 2438, 2438, 9000),
    ("32_ft_sxl_HQ", 9754, 2529, 3048, 9000),
    ("32_ft_mxl", 9754, 2438, 2438, 18000),
    ("32_ft_mxl_HQ", 9754, 2529, 3048, 18000),
]


def seed_master_data(db: Session) -> None:
    # Per-code, NOT "only if the table is empty". Twice now a new asset was
    # appended to PACKAGING (PLS1280, then PLS12804) and never appeared on any
    # dev.db or compose volume that already had rows -- the ground-truth asset
    # simply missing from the catalogue, with nothing in the logs. Same shape as
    # the tare_kg backfill defect. Insert what is absent, fill NULLs on what is
    # present, and never overwrite a value: it is either already seeded or a
    # user edit, and neither is ours to clobber.
    have = {row.item_code: row for row in db.scalars(select(Packaging))}
    for code, il, ib, ih, ol, ob, oh, w in PACKAGING:
        tare, material = PACKAGING_TARE.get(code, (None, None))
        row = have.get(code)
        if row is None:
            db.add(Packaging(
                item_code=code,
                inner_l_mm=il, inner_b_mm=ib, inner_h_mm=ih,
                outer_l_mm=ol, outer_b_mm=ob, outer_h_mm=oh,
                max_weight_kg=w, status="checked", kind="container",
                tare_kg=tare, material=material,
            ))
            continue
        if row.tare_kg is None and tare is not None:
            row.tare_kg = tare
        if row.material is None and material is not None:
            row.material = material

    if db.scalars(select(Vehicle).limit(1)).first() is None:
        for name, cl, cb, ch, payload in VEHICLES:
            db.add(Vehicle(
                name=name,
                cargo_l_mm=cl, cargo_b_mm=cb, cargo_h_mm=ch,
                payload_kg=payload,
            ))
    db.commit()
