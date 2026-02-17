from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.integrations.onec import one_c_catalog
from app.models import Base, Category, Product


class AsyncSessionWrapper:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self):
        self._session.flush()

    def add(self, instance) -> None:
        self._session.add(instance)

    @property
    def no_autoflush(self):
        return self._session.no_autoflush

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_onec_catalog_accepts_1c_payload_format():
    session = _make_session()
    async_session = AsyncSessionWrapper(session)
    payload = {
        "categories": [
            {
                "external_id": "cat-guid-1",
                "title_ru": "Спанбонд",
                "title_lat": "spanbond",
                "parent_external_id": "",
                "order_index": "1",
            }
        ],
        "products": [
            {
                "external_id": "prod-guid-1",
                "sku": "SB-70-W",
                "title_ru": "Спанбонд 70 белый",
                "category_external_id": "cat-guid-1",
                "price": "123,45",
                "stock_qty": "12",
            }
        ],
        "price_type": "1BASE",
    }

    result = asyncio.run(one_c_catalog(payload=payload, session=async_session))

    assert result["ok"] is True
    assert isinstance(result.get("request_id"), str) and result["request_id"]
    assert result["categories_received"] == 1
    assert result["products_received"] == 1
    assert result["products_upserted"] == 1
    assert session.query(Category).count() == 1
    assert session.query(Product).count() == 1


def test_onec_catalog_invalid_payload_contains_errors_and_request_id():
    session = _make_session()
    async_session = AsyncSessionWrapper(session)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(one_c_catalog(payload={"price_type": "1BASE"}, session=async_session))

    exc = exc_info.value
    assert exc.status_code == 422
    assert isinstance(exc.detail, dict)
    assert exc.detail.get("request_id")
    assert exc.detail.get("errors")

