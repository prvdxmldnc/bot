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
