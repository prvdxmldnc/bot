from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Category, OrgMember, OrgProductStats, Organization, Product, SearchAlias, User
from app.services.search_pipeline import run_search_pipeline


async def _prepare_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    session = Session()

    org = Organization(id=42, name="Org 42")
    user = User(id=100, tg_id=999001, fio="Admin", phone="+79990000001", password_hash="x", role="admin")
    member = OrgMember(org_id=42, user_id=100, status="active")
    cat = Category(id=10, title_ru="Спанбонд")
    p1 = Product(id=1, sku="SB-W-70", title_ru="Спанбонд 70 белый", category_id=10, stock_qty=10, price=100)
    p2 = Product(id=2, sku="SB-B-70", title_ru="Спанбонд 70 беж", category_id=10, stock_qty=10, price=90)
    p3 = Product(id=3, sku="POR-10", title_ru="Поролон 10мм", category_id=10, stock_qty=10, price=50)
    p4 = Product(id=4, sku="SIN-60", title_ru="Синтепон 60", category_id=10, stock_qty=10, price=70)

    session.add_all([org, user, member, cat, p1, p2, p3, p4])
    session.add_all([
        OrgProductStats(org_id=42, product_id=1, orders_count=10, last_order_at=datetime.utcnow()),
        OrgProductStats(org_id=42, product_id=3, orders_count=8, last_order_at=datetime.utcnow()),
    ])
    session.add_all([
        SearchAlias(org_id=None, src="сентипон", dst="синтепон", kind="token", enabled=True),
        SearchAlias(org_id=None, src="паралон", dst="поролон", kind="token", enabled=True),
    ])
    await session.commit()
    return engine, session


def test_org42_golden_invariants():
    async def _run():
        engine, session = await _prepare_session()
        try:
            queries = [
                "спандбонд 70 белый",
                "спанбонд 70 белый",
                "сентипон",
                "паралон",
                "поролон 10мм",
                "спанбонд 70 беж",
                "спанбонд 70 коричневый",
                "спанбонд",
                "поролон",
                "синтепон 60",
            ]
            payloads = []
            for q in queries:
                payload = await run_search_pipeline(
                    session,
                    org_id=42,
                    user_id=100,
                    text=q,
                    enable_llm_narrow=False,
                    enable_llm_rewrite=False,
                    enable_rerank=False,
                )
                payloads.append((q, payload))

            p_by_query = dict(payloads)
            white = p_by_query["спанбонд 70 белый"]
            if white["results"]:
                assert "корич" not in (white["results"][0].get("title_ru") or "").lower()

            typo = p_by_query["спандбонд 70 белый"]
            assert "synonym_retry_attempted" in typo["decision"]
            assert "synonym_map" in typo["decision"]
            assert "query_retry" in typo["decision"]
            assert "retry_results_count" in typo["decision"]

            facet_case = p_by_query["спанбонд 70 коричневый"]
            assert "query_facets" in facet_case["decision"]
            assert "applied_filters" in facet_case["decision"]

            for q, payload in payloads:
                if payload["decision"].get("decision") == "needs_clarification":
                    clar = payload["decision"].get("clarification") or {}
                    assert clar.get("question")
                    if payload.get("results"):
                        assert isinstance(clar.get("options") or [], list)

            fast = p_by_query["поролон 10мм"]
            assert fast["decision"].get("llm_stage") in {"none", ""}
            assert fast["decision"].get("llm_called") in {False, None}
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())
