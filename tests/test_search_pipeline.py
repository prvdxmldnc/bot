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


def test_pipeline_uses_clean_query_without_qty_units(monkeypatch):
    captured = {"alias_query": None, "search_query": None}

    async def fake_search_products(_session, query, limit=5, category_ids=None, product_ids=None):
        captured["search_query"] = query
        return [{"id": 1, "title_ru": "Карандаш меловой белый", "sku": "CH-1"}]

    async def fake_find_alias(_session, _org_id, query, limit=5):
        captured["alias_query"] = query
        return [1]

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    async def fake_history_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history_empty)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
    monkeypatch.setattr(
        search_pipeline,
        "parse_order_text",
        lambda _query: [{"query": "мел белый", "raw": "мел белый 2 коробочки", "query_core": "мел белый"}],
    )
    monkeypatch.setattr(
        search_pipeline,
        "handle_message",
        lambda *args, **kwargs: types.SimpleNamespace(items=[types.SimpleNamespace(normalized="мел белый 2 коробочки", attributes=None)]),
    )

    payload = asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="мел белый 2 коробочки"))

    assert payload["results"]
    assert captured["alias_query"] == "мел белый"
    assert captured["search_query"] == "мел белый"


def test_history_scored_stage_sets_conflict_flag(monkeypatch):
    async def fake_find_alias(*args, **kwargs):
        return []

    async def fake_history(_session, _org_id, query_core, limit=5):
        assert "236" in query_core
        return [
            {
                "id": 10,
                "title_ru": "Механизм подъема 236 без пружин",
                "sku": "M236",
                "attribute_conflict": True,
            }
        ]

    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_find_alias)
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history)
    async def fake_count_one(*args, **kwargs):
        return 1
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_one)
    async def fake_search_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_empty)
    monkeypatch.setattr(
        search_pipeline,
        "parse_order_text",
        lambda q: [{"query": q, "raw": q, "query_core": q}],
    )
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(
            DummySession(),
            org_id=42,
            user_id=None,
            text="механизм подъема 236 с пружинами",
        )
    )
    history_stage = payload["trace"]["stages"][0]
    assert payload["results"]
    assert history_stage["attribute_conflict"] is True


def test_llm_disabled_skips_llm_stages(monkeypatch):
    async def _must_not_call(*args, **kwargs):
        raise AssertionError("LLM stage should not be called")

    async def fake_search_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_empty)
    async def fake_alias_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_alias_empty)
    async def fake_history_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history_empty)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q, "query_core": q}])
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(search_pipeline, "suggest_queries", _must_not_call)
    monkeypatch.setattr(search_pipeline, "narrow_categories", _must_not_call)
    monkeypatch.setattr(search_pipeline, "rewrite_query", _must_not_call)
    monkeypatch.setattr(search_pipeline, "rerank_products", _must_not_call)
    monkeypatch.setattr(settings, "llm_enabled", False)

    payload = asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="неизвестно"))

    assert payload["decision"]["llm_narrow_reason"] == "llm_disabled"
    assert payload["decision"]["llm_called"] is False


def test_multi_item_payload_includes_each_item(monkeypatch):
    async def fake_runner(_session, query, limit=5, category_ids=None, product_ids=None):
        return [{"id": 1, "title_ru": f"{query} товар", "sku": "S1"}]

    monkeypatch.setattr(search_pipeline, "search_products", fake_runner)
    async def fake_alias_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_alias_empty)
    async def fake_history_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history_empty)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
    def fake_parse(q):
        if "," in q:
            return [
                {"query": "молния серая", "query_core": "молния серая", "raw": "молния серая", "qty": 1, "unit": ""},
                {"query": "молния беж", "query_core": "молния беж", "raw": "беж по 5 шт", "qty": 5, "unit": "шт"},
            ]
        return [{"query": q, "query_core": q, "raw": q, "qty": 1, "unit": ""}]

    monkeypatch.setattr(search_pipeline, "parse_order_text", fake_parse)
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))

    payload = asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="молния серая, беж по 5 шт"))

    assert payload["decision"]["multi_item"] is True
    assert len(payload["items"]) == 2
    assert "молния" in payload["items"][1]["query_core"]


def test_bolt_8_30_behavior_unchanged(monkeypatch):
    async def fake_history(_session, _org_id, query_core, limit=5):
        if query_core.startswith("болт"):
            return [{"id": 5, "title_ru": "Болт мебельный 8 30", "sku": "B-830"}]
        return []

    async def fake_alias_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_alias_empty)
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history)
    async def fake_count_40(*args, **kwargs):
        return 40
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count_40)
    async def fake_search_empty(*args, **kwargs):
        return []
    monkeypatch.setattr(search_pipeline, "search_products", fake_search_empty)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q, "query_core": q}])
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))

    payload = asyncio.run(search_pipeline.run_search_pipeline(DummySession(), org_id=42, user_id=None, text="болт 8 30"))
    assert payload["results"]
    assert payload["decision"]["decision"] in {"history_ok", "alias_ok", "local_ok"}


def test_pipeline_can_skip_llm_narrow_for_bot_flow(monkeypatch):
    async def fake_search_empty(*args, **kwargs):
        return []

    async def fake_alias_empty(*args, **kwargs):
        return []

    async def fake_history_empty(*args, **kwargs):
        return []

    async def fake_rewrite(query):
        return query

    async def must_not_call_narrow(*args, **kwargs):
        raise AssertionError("narrow_categories must be skipped when enable_llm_narrow=False")

    monkeypatch.setattr(search_pipeline, "search_products", fake_search_empty)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_alias_empty)
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history_empty)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
    monkeypatch.setattr(search_pipeline, "rewrite_query", fake_rewrite)
    monkeypatch.setattr(search_pipeline, "narrow_categories", must_not_call_narrow)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q, "query_core": q}])
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(
            DummySession(),
            org_id=42,
            user_id=None,
            text="поролон 10мм",
            enable_llm_narrow=False,
        )
    )

    assert payload["decision"]["llm_narrow_reason"] == "llm_narrow_disabled"


def test_pipeline_import_and_history_signature_alignment(monkeypatch):
    import app.main as _main  # noqa: F401
    import app.services.history_candidates as history_candidates

    async def fake_get_org_candidates(_session, _org_id, limit=200):
        assert isinstance(limit, (int, type(None)))
        return [1]

    async def fake_history_search_products(_session, query, limit=10, category_ids=None, product_ids=None):
        assert query == "test query"
        assert product_ids == [1]
        return []

    monkeypatch.setattr(history_candidates, "get_org_candidates", fake_get_org_candidates)
    monkeypatch.setattr(history_candidates, "search_products", fake_history_search_products)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", lambda *args, **kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(search_pipeline, "search_products", lambda *args, **kwargs: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(search_pipeline, "count_org_candidates", _count_zero)
    monkeypatch.setattr(search_pipeline, "parse_order_text", lambda q: [{"query": q, "raw": q, "query_core": q}])
    monkeypatch.setattr(search_pipeline, "handle_message", lambda *args, **kwargs: types.SimpleNamespace(items=[]))
    monkeypatch.setattr(settings, "llm_enabled", False)

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(
            DummySession(),
            org_id=42,
            user_id=None,
            text="test query",
        )
    )

    assert isinstance(payload, dict)
    assert "decision" in payload
