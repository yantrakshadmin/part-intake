"""Catalogue adapter: packaging rows -> assets the nesting engine can pack into.

There is no Alembic and `create_all` never ALTERs, so `kind` (like `tare_kg`
and `material`) is added to an existing dev.db by `main._ensure_added_columns`,
idempotently, at import. Nothing to do by hand -- this used to say "drop
dev.db", which now destroys data for no reason.

TODO(PM): only the 17 seeded PACKAGING rows are exposed here. The other 32
catalogue assets (PLANNING §5: mislabeled types, impossible inner/outer pairs,
trailing-zero weight errors) are not surfaced until that cleanup lands.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Packaging
from .seed_data import PACKAGING, PACKAGING_TARE


@dataclass(frozen=True)
class Container:
    name: str
    inner: tuple[float, float, float]
    outer: tuple[float, float, float]
    max_weight_kg: float
    kind: str
    # None = no tare on file (C-TARE) -- never 0 standing in for "unknown".
    tare_kg: float | None = None


def excluded_drafts(db: Session | None) -> list[str]:
    """Container rows `containers()` drops for not being status="checked".

    Lives next to the filter it mirrors, deliberately. `POST /api/packaging`
    saves with status="draft" -- which is what the UI's "+ Custom box" button
    creates -- so an engineer can add the box the customer actually stocks,
    re-solve, and see no card, no warning and no explanation. Unverified dims
    should not be ranked; being dropped in silence is the defect.
    """
    if db is None:
        return []
    return list(db.scalars(
        select(Packaging.item_code).where(Packaging.kind == "container",
                                          Packaging.status != "checked")
    ).all())


def containers_named(db: Session | None, codes: list[str]) -> tuple[list[Container], list[str]]:
    """Container-kind packaging assets named explicitly by the caller.

    Unlike `containers()`, this ignores `status` -- a draft box is ranked
    too, because an engineer typing a box code is asking for it, not
    silently exposed to unverified dims (see `excluded_drafts` for why the
    unrestricted path drops drafts instead). Returns (found, missing) so the
    caller can warn about codes that matched nothing without a second query.
    """
    if db is not None:
        rows = db.scalars(
            select(Packaging).where(Packaging.kind == "container",
                                   Packaging.item_code.in_(codes))
        ).all()
        by_name = {
            r.item_code: Container(
                name=r.item_code,
                inner=(r.inner_l_mm, r.inner_b_mm, r.inner_h_mm),
                outer=(r.outer_l_mm, r.outer_b_mm, r.outer_h_mm),
                max_weight_kg=r.max_weight_kg,
                kind=r.kind,
                tare_kg=r.tare_kg,
            )
            for r in rows
        }
    else:
        by_name = {c.name: c for c in containers(None) if c.name in codes}

    found = [by_name[c] for c in codes if c in by_name]
    missing = [c for c in codes if c not in by_name]
    if db is not None and missing:
        # A code can be absent from `by_name` for two different reasons, and
        # "not found" is a lie for the second one: the row exists but is a
        # rack/pallet/accessory, which the nesting engine cannot pack into.
        # Name the reason, or the engineer goes looking for a typo that isn't
        # there.
        wrong_kind = dict(db.execute(
            select(Packaging.item_code, Packaging.kind)
            .where(Packaging.item_code.in_(missing),
                   Packaging.kind != "container")
        ).all())
        missing = [f"{c} (exists, but kind={wrong_kind[c]})" if c in wrong_kind
                   else c for c in missing]
    return found, missing


def containers(db: Session | None = None) -> list[Container]:
    """Container-kind packaging assets, for the nesting engine.

    Reads the DB when given a Session; otherwise falls back to the
    `seed_data.PACKAGING` constants so callers (and tests) work with no
    database at all.
    """
    if db is not None:
        rows = db.scalars(
            select(Packaging).where(Packaging.kind == "container",
                                   Packaging.status == "checked")
        ).all()
        return [
            Container(
                name=r.item_code,
                inner=(r.inner_l_mm, r.inner_b_mm, r.inner_h_mm),
                outer=(r.outer_l_mm, r.outer_b_mm, r.outer_h_mm),
                max_weight_kg=r.max_weight_kg,
                kind=r.kind,
                tare_kg=r.tare_kg,
            )
            for r in rows
        ]

    return [
        Container(
            name=code,
            inner=(il, ib, ih),
            outer=(ol, ob, oh),
            max_weight_kg=w,
            kind="container",
            tare_kg=PACKAGING_TARE.get(code, (None, None))[0],
        )
        for code, il, ib, ih, ol, ob, oh, w in PACKAGING
    ]
