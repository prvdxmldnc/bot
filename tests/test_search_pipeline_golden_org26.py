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
            Organization(id=26, name="Org 26"),
            User(id=200, tg_id=26200, fio="U26", phone="+700000002", password_hash="x", role="admin"),
            OrgMember(org_id=26, user_id=200, status="active"),
            Category(id=20, title_ru="Спанбонд"),
            Product(id=11, sku="SB-70-W-26", title_ru="Спанбонд 70 белый рулон", category_id=20, stock_qty=12, price=100),
            Product(id=12, sku="SB-70-B-26", title_ru="Спанбонд 70 коричневый рулон", category_id=20, stock_qty=10, price=95),
            Product(id=13, sku="SB-17-W-26", title_ru="Спанбонд 17 белый", category_id=20, stock_qty=9, price=60),
            SearchAlias(org_id=None, src="спандбон", dst="спанбонд", kind="token", enabled=True),
        ]
    )
    session.add(OrgProductStats(org_id=26, product_id=11, orders_count=5, last_order_at=datetime.utcnow()))
    await session.commit()
    return engine, session


def test_golden_org26_clarify_respects_facets_when_no_exact():
    async def _run():
        engine, session = await _prepare_session()
        try:
            payload = await run_search_pipeline(
                session,
                org_id=26,
                user_id=200,
                text="спандбон 70 белый extra",
                enable_llm_narrow=False,
                enable_llm_rewrite=False,
                enable_rerank=False,
            )
            decision = payload.get("decision") or {}
            assert decision.get("synonym_map")
            clar = decision.get("clarification") or {}
            if decision.get("decision") == "needs_clarification":
                labels = [str(o.get("label") or "").lower() for o in (clar.get("options") or [])]
                assert labels
                assert any("70" in x for x in labels)
                assert any("бел" in x for x in labels)
        finally:
            await session.close()
            await engine.dispose()

    asyncio.run(_run())
