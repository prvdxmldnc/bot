from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Organization, OrgProductStats, Product
from app.services.history_candidates import get_org_candidates, upsert_org_product_stats


class AsyncSessionWrapper:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self):
        self._session.flush()

    def add(self, instance) -> None:
        self._session.add(instance)


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_org_candidates_ordering():
    session = _make_session()
    org = Organization(name="Org")
    product_a = Product(title_ru="A")
    product_b = Product(title_ru="B")
    session.add_all([org, product_a, product_b])
    session.flush()
    now = datetime.utcnow()
    session.add_all(
        [
            OrgProductStats(
                org_id=org.id,
                product_id=product_a.id,
                orders_count=5,
                qty_sum=10,
                last_order_at=now - timedelta(days=1),
            ),
            OrgProductStats(
                org_id=org.id,
                product_id=product_b.id,
                orders_count=3,
                qty_sum=5,
                last_order_at=now,
            ),
        ]
    )
    session.commit()

    async_session = AsyncSessionWrapper(session)
    result = asyncio.run(get_org_candidates(async_session, org.id))
    assert result == [product_a.id, product_b.id]


def test_upsert_org_product_stats_updates_counts():
    session = _make_session()
    org = Organization(name="Org")
    product = Product(title_ru="A")
    session.add_all([org, product])
    session.flush()
    async_session = AsyncSessionWrapper(session)

    first_time = datetime.utcnow() - timedelta(days=2)
    second_time = datetime.utcnow()
    asyncio.run(
        upsert_org_product_stats(
            async_session,
            org.id,
            [{"product_id": product.id, "qty": 2, "unit": "шт", "ordered_at": first_time}],
        )
    )
    asyncio.run(
        upsert_org_product_stats(
            async_session,
            org.id,
            [{"product_id": product.id, "qty": 3, "unit": "шт", "ordered_at": second_time}],
        )
    )
    session.commit()

    stats = session.query(OrgProductStats).filter_by(org_id=org.id, product_id=product.id).one()
    assert stats.orders_count == 2
    assert float(stats.qty_sum) == 5.0
    assert stats.last_order_at == second_time
