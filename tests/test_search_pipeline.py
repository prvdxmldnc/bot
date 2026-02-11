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


async def _count_zero(*args, **kwargs):
    return 0


def test_history_first_uses_product_ids(monkeypatch):
    async def fake_search_products(session, query, limit=5, category_ids=None, product_ids=None):
        assert product_ids == [1, 2]
        return [{"id": 1, "title_ru": "Item", "sku": "SKU"}]

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_get_org_candidates(*args, **kwargs):
        return [1, 2]

    async def fake_count(*args, **kwargs):
        return 2

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count)
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
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
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
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
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
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
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

    async def fake_get_org_candidates(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
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
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
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


def test_history_adaptive_widening_uses_2000(monkeypatch):
    calls: list[int | None] = []

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_count_org_candidates(*args, **kwargs):
        return 2500

    async def fake_get_org_candidates(_session, _org_id, limit=200):
        calls.append(limit)
        if limit == 200:
            return [1]
        if limit == 2000:
            return [999]
        return []

    async def fake_search_products(_session, query, limit=5, category_ids=None, product_ids=None):
        if product_ids == [999]:
            return [{"id": 999, "title_ru": "Синтепон Pro 60 гр/м", "sku": "ST-60"}]
        return []

    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_org_candidates)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q}])
    handler_result = types.SimpleNamespace(items=[types.SimpleNamespace(normalized="синтепон 60", attributes=None)])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: handler_result)
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="синтепон 60")
    )

    assert payload["results"]
    assert calls[:2] == [200, 2000]
    history_stage = payload["trace"]["stages"][0]
    assert history_stage["history_total_available"] == 2500
    assert history_stage["limit_used"] == 2000
    assert history_stage["history_used"] is True
    assert history_stage["attempts"][0]["limit_used"] == 200
    assert history_stage["attempts"][0]["candidates_found"] == 0
    assert history_stage["attempts"][1]["limit_used"] == 2000
    assert history_stage["attempts"][1]["candidates_found"] > 0


def test_history_adaptive_widening_skips_all_when_over_3000(monkeypatch):
    calls: list[int | None] = []

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_count_org_candidates(*args, **kwargs):
        return 5000

    async def fake_get_org_candidates(_session, _org_id, limit=200):
        calls.append(limit)
        return []

    async def fake_search_products(*args, **kwargs):
        return []

    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_org_candidates)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q}])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="синтепон 60"))
    assert calls == [200, 2000]


def test_molniya_seraya_not_terminal_no_match(monkeypatch):
    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_count_org_candidates(*args, **kwargs):
        return 10

    async def fake_get_org_candidates(*args, **kwargs):
        return [1, 2]

    async def fake_search_products(_session, query, limit=5, category_ids=None, product_ids=None):
        if "сер" in query:
            return [{"id": 1, "title_ru": "Молния рулонная серый", "sku": "MZ-1"}]
        return []

    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_org_candidates)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q}])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="молния серая"))

    assert payload["results"]
    assert payload["decision"]["decision"] != "no_match"


def test_noisy_query_reduces_attempt_queries_and_finds_result(monkeypatch):
    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_count_org_candidates(*args, **kwargs):
        return 120

    async def fake_get_org_candidates(*args, **kwargs):
        return [77]

    async def fake_search_products(_session, query, limit=5, category_ids=None, product_ids=None):
        if "лл70" in query and ("нитк" in query or "нитки" in query):
            return [{"id": 77, "title_ru": "Нитки ЛЛ70", "sku": "NT-77"}]
        return []

    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_org_candidates)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q}])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="нитки лл70 светло серые по кор")
    )

    assert payload["results"]
    history_stage = payload["trace"]["stages"][0]
    assert history_stage["attempt_queries"]
    assert "лл70" in (history_stage["attempt_query_used"] or "")


def test_bolt_8_30_behavior_unchanged(monkeypatch):
    async def fake_search_products(_session, query, limit=5, category_ids=None, product_ids=None):
        if query.startswith("болт") and product_ids:
            return [{"id": 5, "title_ru": "Болт мебельный 8 30", "sku": "B-830"}]
        return []

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_count_org_candidates(*args, **kwargs):
        return 40

    async def fake_get_org_candidates(*args, **kwargs):
        return [5]

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_org_candidates)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q}])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(settings, "gigachat_basic_auth_key", "")

    payload = asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="болт 8 30"))
    assert payload["results"]
    assert payload["decision"]["decision"] in {"history_ok", "alias_ok", "local_ok"}


def test_llm_disabled_skips_llm_stages(monkeypatch):
    async def fake_search_products(_session, query, limit=5, category_ids=None, product_ids=None):
        return []

    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_get_org_candidates(*args, **kwargs):
        return []

    async def _must_not_call(*args, **kwargs):
        raise AssertionError("LLM stage should not be called when LLM is disabled")

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda query: [{"query": query, "raw": query}])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(search_pipeline, "suggest_queries", _must_not_call)
    monkeypatch.setattr(search_pipeline, "narrow_categories", _must_not_call)
    monkeypatch.setattr(search_pipeline, "rewrite_query", _must_not_call)
    monkeypatch.setattr(search_pipeline, "rerank_products", _must_not_call)
    monkeypatch.setattr(settings, "llm_enabled", False)

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="неизвестный товар")
    )

    assert payload["results"] == []
    assert payload["decision"]["llm_narrow_reason"] == "llm_disabled"
    llm_rewrite_stage = next(stage for stage in payload["trace"]["stages"] if stage and stage["name"] == "llm_rewrite")
    llm_narrow_stage = next(stage for stage in payload["trace"]["stages"] if stage and stage["name"] == "llm_narrow")
    assert llm_rewrite_stage["notes"] == "skipped: llm disabled"
    assert llm_narrow_stage["notes"] == "skipped: llm disabled"
