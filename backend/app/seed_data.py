"""Master data seed — imported from packaging_and_vehicles (1).xlsx
(2026-06-11). Inserted on startup only when the tables are empty, so user
edits and custom boxes are never overwritten.

Rows: (item_code, inner L/B/H, outer L/B/H, max weight kg)
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
    if db.scalars(select(Packaging).limit(1)).first() is None:
        for code, il, ib, ih, ol, ob, oh, w in PACKAGING:
            db.add(Packaging(
                item_code=code,
                inner_l_mm=il, inner_b_mm=ib, inner_h_mm=ih,
                outer_l_mm=ol, outer_b_mm=ob, outer_h_mm=oh,
                max_weight_kg=w, status="checked",
            ))
    if db.scalars(select(Vehicle).limit(1)).first() is None:
        for name, cl, cb, ch, payload in VEHICLES:
            db.add(Vehicle(
                name=name,
                cargo_l_mm=cl, cargo_b_mm=cb, cargo_h_mm=ch,
                payload_kg=payload,
            ))
    db.commit()
