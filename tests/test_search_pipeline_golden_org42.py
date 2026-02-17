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

    session.add_all(
        [
            Organization(id=42, name="Org 42"),
            User(id=100, tg_id=42100, fio="U", phone="+700000001", password_hash="x", role="admin"),
            OrgMember(org_id=42, user_id=100, status="active"),
            Category(id=10, title_ru="Спанбонд"),
            Product(id=1, sku="SB-70-W", title_ru="Спанбонд 70 белый", category_id=10, stock_qty=12, price=100),
            Product(id=2, sku="SB-70-B", title_ru="Спанбонд 70 коричневый", category_id=10, stock_qty=10, price=95),
            Product(id=3, sku="SB-20-W", title_ru="Спанбонд 20 белый", category_id=10, stock_qty=9, price=70),
            Product(id=4, sku="POR-10", title_ru="Поролон 10", category_id=10, stock_qty=10, price=50),
            SearchAlias(org_id=None, src="спандбонд", dst="спанбонд", kind="token", enabled=True),
        ]
    )
    session.add(OrgProductStats(org_id=42, product_id=1, orders_count=9, last_order_at=datetime.utcnow()))
    await session.commit()
    return engine, session


def test_golden_org42_facet_and_alias_invariants():
    async def _run():
        engine, session = await _prepare_session()
        try:
            payload = await run_search_pipeline(
                session,
                org_id=42,
                user_id=100,
                text="спандбонд 70 белый",
                enable_llm_narrow=False,
                enable_llm_rewrite=False,
                enable_rerank=False,
            )
            decision = payload.get("decision") or {}
            assert decision.get("synonym_map")
            assert "query_retry" in decision
            results = payload.get("results") or []
            if results:
                assert "корич" not in str(results[0].get("title_ru") or "").lower()
            else:
                clar = decision.get("clarification") or {}
                labels = " ".join(str(o.get("label") or "").lower() for o in (clar.get("options") or []))
                assert "70" in labels and "бел" in labels
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())
