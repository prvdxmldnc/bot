import asyncio
import types

from app.config import settings
from app.services import search_pipeline


class DummyResult:
    def all(self):
        return []


class DummySession:
    async def execute(self, *_args, **_kwargs):
        return DummyResult()


def test_history_first_uses_product_ids(monkeypatch):
    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        assert product_ids == [1, 2]
        return [{"id": 1, "title_ru": "Item", "sku": "SKU"}]

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_get_org_candidates(*args, **kwargs):
        return [1, 2]

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda query: [{"query": query, "raw": query}])

    handler_result = types.SimpleNamespace(items=[types.SimpleNamespace(normalized="болт 8 30", attributes=None)])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=1, user_id=None, text="болт 8 30")
    )
    assert payload["decision"]["history_used"] is True
    assert payload["results"]


def test_alias_stage_sets_alias_used(monkeypatch):
    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        assert product_ids == [42]
        return [{"id": 42, "title_ru": "Alias", "sku": "AL"}]

    async def fake_find_alias(*args, **kwargs):
        return [42]

    async def fake_get_org_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda query: [{"query": query, "raw": query}])

    handler_result = types.SimpleNamespace(items=[types.SimpleNamespace(normalized="поролон 10мм", attributes=None)])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=2, user_id=None, text="поролон 10мм")
    )
    assert payload["decision"]["alias_used"] is True


def test_llm_disabled_sets_reason(monkeypatch):
    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        return []

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_get_org_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda query: [{"query": query, "raw": query}])

    handler_result = types.SimpleNamespace(items=[])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=None, user_id=None, text="неизвестно")
    )
    assert payload["decision"]["llm_narrow_confidence"] is None
    assert payload["decision"]["llm_narrow_reason"] == "llm_disabled"


def test_pipeline_uses_clean_query_without_qty_units(monkeypatch):
    captured = {"alias_query": None, "search_queries": []}

    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        captured["search_queries"].append(query)
        return [{"id": 1, "title_ru": "Карандаш меловой белый", "sku": "CH-1"}]

    async def fake_find_alias(_session, _org_id, query, limit=5):
        captured["alias_query"] = query
        return [1]

    async def fake_get_org_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(
        search_pipeline,
        "parse_order_text",
        lambda _query: [{"query": "мел белый", "raw": "мел белый 2 коробочки"}],
    )

    handler_result = types.SimpleNamespace(items=[types.SimpleNamespace(normalized="мел белый 2 коробочки", attributes=None)])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="мел белый 2 коробочки")
    )

    assert payload["results"]
    assert captured["alias_query"] == "мел белый"
    assert captured["search_queries"]
    assert captured["search_queries"][0] == "мел белый"


def test_pipeline_uses_clean_query_for_zipper_qty_input(monkeypatch):
    captured = {"alias_query": None, "search_query": None}

    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        captured["search_query"] = query
        return [{"id": 2, "title_ru": "Молния рулонная бежевый", "sku": "MZ-2"}]

    async def fake_find_alias(_session, _org_id, query, limit=5):
        captured["alias_query"] = query
        return [2]

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    async def fake_get_org_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(
        search_pipeline,
        "parse_order_text",
        lambda _query: [{"query": "молния беж", "raw": "молния беж 5 шт"}],
    )
    handler_result = types.SimpleNamespace(items=[types.SimpleNamespace(normalized="молния беж 5 шт", attributes=None)])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="молния беж 5 шт")
    )

    assert payload["results"]
    assert captured["alias_query"] == "молния беж"
    assert captured["search_query"] == "молния беж"


def test_pipeline_returns_trace_with_clean_query(monkeypatch):
    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        return [{"id": 10, "title_ru": "Карандаш меловой белый", "sku": "CH-10"}]

    async def fake_find_alias(*args, **kwargs):
        return [10]

    async def fake_get_org_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(
        search_pipeline,
        "parse_order_text",
        lambda _query: [{"query": "мел белый", "raw": "мел белый 2 коробочки"}],
    )
    handler_result = types.SimpleNamespace(items=[types.SimpleNamespace(normalized="мел белый 2 коробочки", attributes=None)])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=7, text="мел белый 2 коробочки")
    )

    assert "trace" in payload
    assert payload["trace"]["stages"]
    assert payload["trace"]["input"]["normalized_text"] == "мел белый"
    history_stage = payload["trace"]["stages"][0]
    alias_stage = payload["trace"]["stages"][1]
    assert history_stage["name"] == "history"
    assert alias_stage["name"] == "alias"
    assert alias_stage["query_used"] == "мел белый"
