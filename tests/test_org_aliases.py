from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Organization, OrgAlias, Product
from app.services.org_aliases import find_org_alias_candidates, upsert_org_alias


class AsyncSessionWrapper:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    def add(self, instance) -> None:
        self._session.add(instance)


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_upsert_increments_weight():
    session = _make_session()
    org = Organization(name="Org")
    product = Product(title_ru="A")
    session.add_all([org, product])
    session.flush()
    async_session = AsyncSessionWrapper(session)

    asyncio.run(upsert_org_alias(async_session, org.id, "ППУ 10мм 2 рул", product.id))
    asyncio.run(upsert_org_alias(async_session, org.id, "ППУ 10мм 2 рул", product.id))
    session.commit()

    row = session.query(OrgAlias).filter_by(org_id=org.id, product_id=product.id).one()
    assert row.weight == 2


def test_find_candidates_orders_by_weight():
    session = _make_session()
    org = Organization(name="Org")
    product_a = Product(title_ru="A")
    product_b = Product(title_ru="B")
    session.add_all([org, product_a, product_b])
    session.flush()
    session.add_all(
        [
            OrgAlias(
                org_id=org.id,
                alias_text="ппу 10мм",
                normalized_alias="ппу 10мм",
                product_id=product_a.id,
                weight=5,
            ),
            OrgAlias(
                org_id=org.id,
                alias_text="ппу 10мм",
                normalized_alias="ппу 10мм",
                product_id=product_b.id,
                weight=2,
            ),
        ]
    )
    session.commit()

    async_session = AsyncSessionWrapper(session)
    result = asyncio.run(find_org_alias_candidates(async_session, org.id, "ППУ 10мм", limit=5))
    assert result == [product_a.id, product_b.id]
