from __future__ import annotations

import asyncio

from app.models import Product
from app.services.search import search_products


class DummyResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class DummySession:
    def __init__(self, items):
        self._items = items

    async def execute(self, *_args, **_kwargs):
        return DummyResult(self._items)


def test_search_products_token_and_match():
    products = [
        Product(id=1, title_ru="Молния рулонная Тип 5 (спираль) бежевый цвет КТ 308", sku="MZ-1"),
        Product(id=2, title_ru="Нитки лл70 серые", sku="NT-1"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "молния беж", limit=5))
    assert results
    assert results[0]["id"] == 1


def test_search_products_numbers_not_regressed():
    products = [
        Product(id=1, title_ru="Болт мебельный 8 * 30 (din 603)", sku="BOLT-1"),
        Product(id=2, title_ru="Болт мебельный 6 * 20", sku="BOLT-2"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "болт 8 30", limit=5))
    assert results
    assert results[0]["id"] == 1


def test_search_products_color_stem_with_numbers_matches():
    products = [
        Product(id=1, title_ru="Молния рулонная Тип 5 (спираль) бежевый цвет КТ 308", sku="MZ-1"),
        Product(id=2, title_ru="Молния рулонная Тип 5 (спираль) черный цвет", sku="MZ-2"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "молния рулонная тип 5 беж", limit=5))
    assert results
    assert results[0]["id"] == 1


def test_search_products_no_crash_for_stopwords_query():
    products = [
        Product(id=1, title_ru="Уголок металлический 20x25", sku="UG-1"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "уголок дешевый", limit=5))
    assert isinstance(results, list)


def test_search_products_matches_chalk_query():
    products = [
        Product(id=1, title_ru="Карандаш меловой белый для разметки", sku="CH-1"),
        Product(id=2, title_ru="Карандаш меловой синий", sku="CH-2"),
    ]
    session = DummySession(products)
    results = asyncio.run(search_products(session, "мел белый", limit=5))
    assert results
    assert results[0]["id"] == 1
