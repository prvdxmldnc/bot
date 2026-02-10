from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import OrgMember, Product
from app.request_handler import handle_message
from app.request_handler.types import DialogContext
from app.services.history_candidates import get_org_candidates
from app.services.llm_category_narrow import narrow_categories
from app.services.llm_normalize import suggest_queries
from app.services.llm_rerank import rerank_products
from app.services.order_parser import parse_order_text
from app.services.org_aliases import find_org_alias_candidates
from app.services.search import search_products

logger = logging.getLogger(__name__)


def _fallback_query(parsed_items: list[dict[str, Any]]) -> str:
    if not parsed_items:
        return ""
    item = parsed_items[0]
    return item.get("query") or item.get("raw") or ""


def _primary_query(handler_result) -> str:
    if handler_result.items:
        return handler_result.items[0].normalized
    return ""


async def _resolve_org_id(session: AsyncSession, user_id: int | None, org_id: int | None) -> int | None:
    if org_id:
        return org_id
    if not user_id:
        return None
    result = await session.execute(
        select(OrgMember)
        .where(OrgMember.user_id == user_id, OrgMember.status == "active")
        .order_by(OrgMember.org_id)
    )
    membership = result.scalars().first()
    return membership.org_id if membership else None


def _decision_payload(
    *,
    parsed_items: list[dict[str, Any]],
    original_query: str,
    alternatives: list[str],
    used_alternative: str | None,
    candidates_count_final: int,
    decision: str,
    history_org_id: int | None,
    history_candidates_count: int,
    history_used: bool,
    history_query_used: str | None,
    history_candidates_found: int,
    alias_candidates_count: int,
    alias_used: bool,
    alias_query_used: str | None,
    alias_candidates_found: int,
    category_ids: list[int],
    llm_narrow_confidence: float | None,
    llm_narrow_reason: str | None,
    narrowed_query: str | None,
    rerank_best_ids: list[int],
    rerank_top_score: float | None,
) -> dict[str, Any]:
    return {
        "parsed_items": parsed_items,
        "original_query": original_query,
        "alternatives": alternatives,
        "used_alternative": used_alternative,
        "candidates_count_final": candidates_count_final,
        "decision": decision,
        "history_org_id": history_org_id,
        "history_candidates_count": history_candidates_count,
        "history_used": history_used,
        "history_query_used": history_query_used,
        "history_candidates_found": history_candidates_found,
        "alias_candidates_count": alias_candidates_count,
        "alias_used": alias_used,
        "alias_query_used": alias_query_used,
        "alias_candidates_found": alias_candidates_found,
        "category_ids": category_ids,
        "llm_narrow_confidence": llm_narrow_confidence,
        "llm_narrow_reason": llm_narrow_reason,
        "narrowed_query": narrowed_query,
        "rerank_best_ids": rerank_best_ids,
        "rerank_top_score": rerank_top_score,
    }


async def run_search_pipeline(
    session: AsyncSession,
    *,
    org_id: int | None,
    user_id: int | None,
    text: str,
    limit: int = 5,
) -> dict[str, Any]:
    parsed_items = parse_order_text(text)
    handler_result = handle_message(text, DialogContext(last_state=None, last_items=[], topic="unknown"))
    fallback_query = _fallback_query(parsed_items)
    primary_query = _primary_query(handler_result) or fallback_query
    history_org_id = await _resolve_org_id(session, user_id, org_id)

    history_candidates_count = 0
    history_used = False
    history_query_used: str | None = None
    history_candidates_found = 0
    alias_candidates_count = 0
    alias_used = False
    alias_query_used: str | None = None
    alias_candidates_found = 0

    candidates: list[dict[str, Any]] = []
    if history_org_id:
        alias_product_ids = await find_org_alias_candidates(session, history_org_id, text, limit=5)
        alias_candidates_count = len(alias_product_ids)
        if alias_product_ids:
            candidates = await search_products(session, primary_query, limit=limit, product_ids=alias_product_ids)
            if candidates:
                alias_used = True
                alias_query_used = primary_query
                alias_candidates_found = len(candidates)

    if history_org_id and not candidates:
        history_candidate_ids = await get_org_candidates(session, history_org_id, limit=200)
        history_candidates_count = len(history_candidate_ids)
        if history_candidate_ids:
            candidates = await search_products(session, primary_query, limit=limit, product_ids=history_candidate_ids)
            if candidates:
                history_used = True
                history_query_used = primary_query
                history_candidates_found = len(candidates)

    if parsed_items and not candidates:
        candidates = await search_products(session, fallback_query, limit=limit)

    candidates_count = len(candidates)
    decision = (
        "alias_ok"
        if alias_used
        else ("history_ok" if history_used else ("local_ok" if candidates_count > 0 else "needs_llm"))
    )

    alternatives: list[str] = []
    used_alternative: str | None = None
    category_ids: list[int] = []
    llm_narrow_confidence: float | None = None
    llm_narrow_reason: str | None = None
    narrowed_query: str | None = None

    if not candidates and parsed_items and settings.gigachat_basic_auth_key:
        alternatives = await suggest_queries(primary_query or text)
        for alternative in alternatives:
            retry_candidates = await search_products(session, alternative, limit=limit)
            if retry_candidates:
                candidates = retry_candidates
                candidates_count = len(candidates)
                decision = "llm_ok"
                used_alternative = alternative
                break
        if not candidates:
            narrowed_query = primary_query or fallback_query or text
            narrow_result = await narrow_categories(narrowed_query, session)
            category_ids = narrow_result.get("category_ids", [])
            llm_narrow_confidence = narrow_result.get("confidence")
            llm_narrow_reason = narrow_result.get("reason")
            if category_ids:
                retry_candidates = await search_products(
                    session,
                    primary_query or fallback_query or text,
                    limit=limit,
                    category_ids=category_ids,
                )
                if retry_candidates:
                    candidates = retry_candidates
                    candidates_count = len(candidates)
                    decision = "llm_narrow_ok"
                else:
                    for alternative in alternatives:
                        retry_candidates = await search_products(
                            session,
                            alternative,
                            limit=limit,
                            category_ids=category_ids,
                        )
                        if retry_candidates:
                            candidates = retry_candidates
                            candidates_count = len(candidates)
                            decision = "llm_narrow_ok"
                            used_alternative = alternative
                            break
                    if not candidates:
                        decision = "needs_manager"
            else:
                decision = "needs_manager"
    elif not candidates:
        decision = "needs_manager"
        llm_narrow_reason = "llm_disabled"

    rerank_used = False
    rerank_best_ids: list[int] = []
    rerank_top_score: float | None = None
    if len(candidates) >= 2 and settings.gigachat_basic_auth_key:
        rerank_payload = [
            {
                "product_id": candidate.get("id"),
                "title": candidate.get("title_ru"),
                "category": None,
                "price": candidate.get("price"),
                "stock": candidate.get("stock_qty"),
            }
            for candidate in candidates
        ]
        attrs = handler_result.items[0].attributes if handler_result.items else None
        rerank = await rerank_products(primary_query or fallback_query or text, rerank_payload, attrs)
        best = rerank.get("best") if isinstance(rerank, dict) else None
        if isinstance(best, list) and best:
            rerank_used = True
            rerank_best_ids = [item.get("product_id") for item in best if isinstance(item, dict)]
            rerank_best_ids = [pid for pid in rerank_best_ids if isinstance(pid, int)]
            rerank_top_score = best[0].get("score") if isinstance(best[0], dict) else None
            score_by_id = {item["product_id"]: item.get("score", 0.0) for item in best if "product_id" in item}
            candidates.sort(
                key=lambda item: (
                    score_by_id.get(item.get("id"), -1),
                    item.get("score", 0),
                ),
                reverse=True,
            )

    decision_payload = _decision_payload(
        parsed_items=parsed_items,
        original_query=primary_query or fallback_query or text,
        alternatives=alternatives,
        used_alternative=used_alternative,
        candidates_count_final=len(candidates),
        decision=decision,
        history_org_id=history_org_id,
        history_candidates_count=history_candidates_count,
        history_used=history_used,
        history_query_used=history_query_used,
        history_candidates_found=history_candidates_found,
        alias_candidates_count=alias_candidates_count,
        alias_used=alias_used,
        alias_query_used=alias_query_used,
        alias_candidates_found=alias_candidates_found,
        category_ids=category_ids,
        llm_narrow_confidence=llm_narrow_confidence,
        llm_narrow_reason=llm_narrow_reason,
        narrowed_query=narrowed_query,
        rerank_best_ids=rerank_best_ids,
        rerank_top_score=rerank_top_score,
    )

    if candidates:
        ids = [candidate["id"] for candidate in candidates if isinstance(candidate.get("id"), int)]
        if ids:
            result = await session.execute(select(Product.id, Product.category_id).where(Product.id.in_(ids)))
            category_map = {row[0]: row[1] for row in result.all()}
            for candidate in candidates:
                candidate["category_id"] = category_map.get(candidate.get("id"))

    logger.info(
        "Admin debug search decision=%s history_org_id=%s alias_used=%s history_used=%s",
        decision,
        history_org_id,
        alias_used,
        history_used,
    )
    return {
        "results": candidates,
        "decision": {
            **decision_payload,
            "rerank_used": rerank_used,
            "rerank_best_ids": rerank_best_ids,
            "rerank_top_score": rerank_top_score,
        },
    }
