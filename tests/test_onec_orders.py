from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.integrations.onec import process_orders_payload
from app.models import Base, OrgProductStats, Organization, Product


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


def test_process_orders_payload_updates_stats_by_sku():
    session = _make_session()
    product = Product(sku="SKU-1", title_ru="Item 1")
    session.add(product)
    session.flush()
    async_session = AsyncSessionWrapper(session)

    payload = {
        "org_name": "Org",
        "orders": [
            {
                "ordered_at": datetime.utcnow().isoformat(),
                "items": [
                    {"sku": "SKU-1", "qty": 2, "unit": "рулон"},
                ],
            }
        ],
    }
    result = asyncio.run(process_orders_payload(async_session, payload))
    session.commit()

    assert result["updated_rows"] == 1
    stats = session.query(OrgProductStats).one()
    assert stats.orders_count == 1
    assert float(stats.qty_sum) == 2.0


def test_process_orders_payload_skips_unknown_items():
    session = _make_session()
    async_session = AsyncSessionWrapper(session)

    payload = {
        "org_name": "Org",
        "items": [
            {"sku": "UNKNOWN", "qty": 1, "unit": "шт"},
            {"title": "Missing", "qty": 1, "unit": "шт"},
        ],
    }
    result = asyncio.run(process_orders_payload(async_session, payload))
    session.commit()

    assert result["skipped"] == 2
    assert session.query(OrgProductStats).count() == 0
    assert session.query(Organization).count() == 1


def test_process_orders_payload_uses_external_id():
    session = _make_session()
    product = Product(sku="SKU-2", title_ru="Item 2")
    session.add(product)
    session.flush()
    async_session = AsyncSessionWrapper(session)

    payload = {
        "org_external_id": "x-1",
        "org_name": "ORG A",
        "items": [{"sku": "SKU-2", "qty": 1}],
    }
    result = asyncio.run(process_orders_payload(async_session, payload))
    session.commit()

    assert result["org_id"] is not None
    org = session.query(Organization).one()
    assert org.external_id == "x-1"

    payload_second = {
        "org_external_id": "x-1",
        "org_name": "ORG B",
        "items": [{"sku": "SKU-2", "qty": 2}],
    }
    result_second = asyncio.run(process_orders_payload(async_session, payload_second))
    session.commit()

    assert result_second["org_id"] == org.id
    assert session.query(Organization).count() == 1
