from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Organization, OrgAlias, Product
from app.services.org_aliases import autolearn_org_alias, normalize_alias_for_autolearn


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


def test_normalize_alias_for_autolearn():
    assert normalize_alias_for_autolearn("поролон 10мм -2рол") == "поролон 10мм"
    assert normalize_alias_for_autolearn("1010 40") != ""
    assert normalize_alias_for_autolearn("1") == ""


def test_autolearn_increments_weight():
    session = _make_session()
    org = Organization(name="Org")
    product = Product(title_ru="A")
    session.add_all([org, product])
    session.flush()
    async_session = AsyncSessionWrapper(session)

    asyncio.run(autolearn_org_alias(async_session, org.id, "поролон 10мм -2рол", product.id))
    asyncio.run(autolearn_org_alias(async_session, org.id, "поролон 10мм -2рол", product.id))
    session.commit()

    row = session.query(OrgAlias).filter_by(org_id=org.id, product_id=product.id).one()
    assert row.weight == 2
