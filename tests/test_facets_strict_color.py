import asyncio

from app.services import search_pipeline


class DummySession:
    pass


def test_strict_color_never_returns_wrong_top1(monkeypatch):
    async def fake_search(_session, query, **_kwargs):
        if "спанбонд" in query:
            return [
                {"id": 1, "title_ru": "Спанбонд 70 коричневый", "sku": "SB-BR"},
                {"id": 2, "title_ru": "Спанбонд 70 беж", "sku": "SB-BE"},
            ]
        return []

    async def fake_alias(*_args, **_kwargs):
        return []

    async def fake_history(*_args, **_kwargs):
        return []

    async def fake_count(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(search_pipeline, "search_products", fake_search)
    monkeypatch.setattr(search_pipeline, "find_org_alias_candidates", fake_alias)
    monkeypatch.setattr(search_pipeline, "search_history_products", fake_history)
    monkeypatch.setattr(search_pipeline, "count_org_candidates", fake_count)

    payload = asyncio.run(
        search_pipeline.run_search_pipeline(
            DummySession(),
            org_id=42,
            user_id=None,
            text="спандбонд 70 белый",
            enable_llm_narrow=False,
            enable_llm_rewrite=False,
            enable_rerank=False,
        )
    )

    results = payload.get("results") or []
    decision = payload.get("decision") or {}
    if results:
        assert "корич" not in str(results[0].get("title_ru") or "").lower()
    else:
        assert decision.get("decision") == "needs_clarification"
        clar = decision.get("clarification") or {}
        assert clar.get("reason") == "facet_conflict"
