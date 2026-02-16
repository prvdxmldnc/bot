from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OrgMember, Product
from app.request_handler import handle_message
from app.request_handler.types import DialogContext
from app.services.history_candidates import count_org_candidates, search_history_products
from app.services.clarify import build_clarification, build_facet_options, extract_head_token, history_suggestions, suggestions_to_options
from app.services.llm_category_narrow import narrow_categories
from app.services.llm_client import llm_available
from app.services.llm_normalize import suggest_queries
from app.services.llm_rerank import rerank_products
from app.services.llm_rewrite import rewrite_query
from app.services.order_parser import parse_order_text
from app.services.org_aliases import find_org_alias_candidates
from app.services.search import normalize_query_text, search_products
from app.services.search_aliases import get_alias_map, normalize_query_with_aliases

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
_STOP_WORDS = {
    "шт",
    "штук",
    "кор",
    "короб",
    "коробка",
    "коробочки",
    "рул",
    "рулон",
    "рулонная",
    "уп",
    "упак",
    "упаковка",
    "мм",
    "см",
    "м",
    "м2",
    "кг",
    "гр",
    "г",
    "тип",
    "номер",
    "цвет",
    "no",
    "n",
}
_DECORATOR_TOKENS = {
    "светло",
    "темно",
    "универсальн",
    "по",
    "кор",
    "короб",
    "шт",
    "уп",
    "рул",
    "и",
    "на",
    "для",
    "нужно",
    "нужны",
    "дешев",
    "дешевая",
    "дешевый",
}
_COLOR_STEMS = {"сер", "беж", "бел", "черн", "син", "зел", "красн"}
_COLOR_TOKEN_MAP = {
    "серая": "сер",
    "серый": "сер",
    "серые": "сер",
    "белый": "бел",
    "белая": "бел",
    "черный": "черн",
    "черная": "черн",
    "бежевый": "бежев",
    "бежевая": "бежев",
}
_ADJ_ENDINGS = ("ая", "яя", "ый", "ий", "ое", "ее", "ые", "ие", "ого", "ему", "ым", "ой", "ую", "юю")

def apply_token_synonyms(text: str, alias_map: dict[str, str]) -> tuple[str, dict[str, str]]:
    return normalize_query_with_aliases(text, alias_map)


def _fallback_query(parsed_items: list[dict[str, Any]]) -> str:
    if not parsed_items:
        return ""
    item = parsed_items[0]
    return item.get("query") or item.get("raw") or ""


def _primary_query(handler_result) -> str:
    if handler_result.items:
        return handler_result.items[0].normalized
    return ""


def _clean_search_query(parsed_items: list[dict[str, Any]], handler_result) -> str:
    if parsed_items:
        parsed_query = (parsed_items[0].get("query") or "").strip()
        if parsed_query:
            return parsed_query
    primary = (_primary_query(handler_result) or "").strip()
    if primary:
        return primary
    return _fallback_query(parsed_items).strip()


def _normalize_ru_adj_stem(token: str) -> str:
    if token in _COLOR_TOKEN_MAP:
        return _COLOR_TOKEN_MAP[token]
    if token.isdigit() or len(token) < 5:
        return token
    for ending in _ADJ_ENDINGS:
        if token.endswith(ending):
            stem = token[: -len(ending)]
            if len(stem) >= 3:
                return stem
    return token


def _extract_trace_tokens_numbers(query: str) -> tuple[list[str], list[int]]:
    normalized = normalize_query_text(query)
    tokens: list[str] = []
    numbers: list[int] = []
    for token in _TOKEN_RE.findall(normalized):
        if token.isdigit():
            numbers.append(int(token))
            continue
        token = _normalize_ru_adj_stem(token)
        if token in _STOP_WORDS:
            continue
        tokens.append(token)
    return tokens, numbers


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _build_attempt_queries(query: str) -> list[str]:
    normalized = normalize_query_text(query)
    base_tokens = [_normalize_ru_adj_stem(token) for token in _TOKEN_RE.findall(normalized)]
    if not base_tokens:
        return [normalized] if normalized else []

    full_query = " ".join(base_tokens)

    reduced_tokens = [t for t in base_tokens if t not in _DECORATOR_TOKENS and t not in _STOP_WORDS]
    reduced_query = " ".join(reduced_tokens)

    no_color_tokens = [t for t in reduced_tokens if t not in _COLOR_STEMS]
    no_color_query = " ".join(no_color_tokens)

    core_tokens = [
        t
        for t in no_color_tokens
        if t.isdigit()
        or any(ch.isdigit() for ch in t)
        or t in {"тип", "din", "лл", "лл70", "ll", "ll70"}
        or len(t) >= 4
    ]
    core_query = " ".join(core_tokens[:6])

    return _dedupe_keep_order([full_query, reduced_query, no_color_query, core_query])


def _stage_entry(
    *,
    name: str,
    query_used: str,
    tokens_used: list[str],
    numbers_used: list[int],
    candidates_before: int,
    candidates_after: int,
    notes: str,
    product_ids_filter_count: int | None = None,
    category_ids_filter: list[int] | None = None,
    top_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "query_used": query_used,
        "tokens_used": tokens_used,
        "numbers_used": numbers_used,
        "product_ids_filter_count": product_ids_filter_count,
        "category_ids_filter": category_ids_filter or [],
        "candidates_before": candidates_before,
        "candidates_after": candidates_after,
        "top5_titles": [
            str(item.get("title_ru") or "")
            for item in (top_candidates or [])[:5]
            if item.get("title_ru")
        ],
        "notes": notes,
    }


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
    enable_llm_narrow: bool = True,
    enable_llm_rewrite: bool = True,
    enable_rerank: bool = True,
    clarify_offset: int = 0,
) -> dict[str, Any]:
    parsed_items = parse_order_text(text)
    if len(parsed_items) > 1:
        item_payloads: list[dict[str, Any]] = []
        for item in parsed_items:
            item_query = str(item.get("query_core") or item.get("query") or item.get("raw") or "").strip()
            if not item_query:
                continue
            sub_payload = await run_search_pipeline(
                session,
                org_id=org_id,
                user_id=user_id,
                text=item_query,
                limit=limit,
                enable_llm_narrow=enable_llm_narrow,
                enable_llm_rewrite=enable_llm_rewrite,
                enable_rerank=enable_rerank,
                clarify_offset=clarify_offset,
            )
            item_payloads.append(
                {
                    "item": item,
                    "query_core": item_query,
                    "results": sub_payload.get("results", []),
                    "decision": sub_payload.get("decision", {}),
                    "trace": sub_payload.get("trace"),
                }
            )
        primary = item_payloads[0] if item_payloads else {"results": [], "decision": {}, "trace": None}
        decision = dict(primary.get("decision") or {})
        decision["multi_item"] = True
        trace = dict(primary.get("trace") or {})
        trace["items"] = item_payloads
        return {
            "results": primary.get("results", []),
            "decision": decision,
            "trace": trace,
            "items": item_payloads,
        }

    handler_result = handle_message(text, DialogContext(last_state=None, last_items=[], topic="unknown"))
    fallback_query = _fallback_query(parsed_items)
    primary_query = _primary_query(handler_result) or fallback_query
    search_query = _clean_search_query(parsed_items, handler_result) or fallback_query
    normalized_text = normalize_query_text(search_query or text)
    history_org_id = await _resolve_org_id(session, user_id, org_id)
    alias_map = await get_alias_map(session, history_org_id)
    canonical_query, applied_aliases = apply_token_synonyms(search_query or text, alias_map)
    if canonical_query:
        search_query = canonical_query
        normalized_text = normalize_query_text(search_query or text)
    trace_tokens, trace_numbers = _extract_trace_tokens_numbers(search_query or text)
    attempt_queries = _build_attempt_queries(search_query or text)

    history_candidates_count = 0
    history_used = False
    history_query_used: str | None = None
    history_candidates_found = 0
    history_total_available = 0
    history_limit_used: int | None = None
    history_attempts: list[dict[str, Any]] = []
    alias_candidates_count = 0
    alias_used = False
    alias_query_used: str | None = None
    alias_candidates_found = 0

    synonym_retry_attempted = False
    synonym_map: dict[str, str] = dict(applied_aliases)
    synonym_retry_query: str | None = None
    synonym_retry_results_count = 0

    candidates: list[dict[str, Any]] = []
    trace_by_name: dict[str, dict[str, Any]] = {}

    alias_before = len(candidates)
    alias_product_ids: list[int] = []
    alias_note = "skipped: org_id unresolved"
    if history_org_id:
        alias_product_ids = await find_org_alias_candidates(session, history_org_id, search_query, limit=5)
        alias_candidates_count = len(alias_product_ids)
        if alias_product_ids:
            candidates = await search_products(session, search_query, limit=limit, product_ids=alias_product_ids)
            if candidates:
                alias_used = True
                alias_query_used = search_query
                alias_candidates_found = len(candidates)
                alias_note = "alias product_ids matched"
            else:
                alias_note = "alias product_ids найден, но search_products вернул 0"
        else:
            alias_note = "alias candidates not found"
    trace_by_name["alias"] = _stage_entry(
        name="alias",
        query_used=search_query,
        tokens_used=trace_tokens,
        numbers_used=trace_numbers,
        product_ids_filter_count=len(alias_product_ids),
        candidates_before=alias_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=alias_note,
    )

    history_before = len(candidates)
    history_note = "skipped: already have candidates"
    history_attempt_query_used: str | None = None
    history_attribute_conflict = False
    if history_org_id and not candidates:
        history_total_available = await count_org_candidates(session, history_org_id)
        history_candidates_count = history_total_available
        for attempt_query in attempt_queries:
            history_results = await search_history_products(
                session,
                history_org_id,
                attempt_query,
                limit=limit,
            )
            history_attempts.append(
                {
                    "query_used": attempt_query,
                    "limit_used": None,
                    "candidates_count": history_total_available,
                    "candidates_found": len(history_results),
                    "note": "hit" if history_results else "search returned 0",
                }
            )
            if history_results:
                candidates = history_results
                history_used = True
                history_query_used = attempt_query
                history_attempt_query_used = attempt_query
                history_candidates_found = len(candidates)
                history_limit_used = None
                history_attribute_conflict = any(bool(item.get("attribute_conflict")) for item in candidates)
                history_note = "history scored retrieval matched"
                break
        if not history_used:
            history_note = "history_soft_miss -> continue"
    elif not history_org_id:
        history_note = "skipped: org_id unresolved"

    history_stage = _stage_entry(
        name="history",
        query_used=history_query_used or search_query,
        tokens_used=_extract_trace_tokens_numbers(history_query_used or search_query)[0],
        numbers_used=_extract_trace_tokens_numbers(history_query_used or search_query)[1],
        product_ids_filter_count=history_candidates_count,
        candidates_before=history_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=history_note,
    )
    history_stage.update(
        {
            "attempt_queries": attempt_queries,
            "attempt_query_used": history_attempt_query_used,
            "history_total_available": history_total_available,
            "attempts": history_attempts,
            "limit_used": history_limit_used,
            "history_used": history_used,
            "attribute_conflict": history_attribute_conflict,
            "top_titles": [str(item.get("title_ru") or "") for item in candidates[:3] if item.get("title_ru")],
        }
    )
    trace_by_name["history"] = history_stage

    local_before = len(candidates)
    local_note = "skipped: already have candidates"
    local_attempts: list[dict[str, Any]] = []
    local_attempt_query_used: str | None = None
    if parsed_items and not candidates:
        for attempt_query in attempt_queries:
            local_results = await search_products(session, attempt_query, limit=limit)
            if local_results:
                candidates = local_results
                local_attempt_query_used = attempt_query
                local_note = "local search matched"
                local_attempts.append(
                    {
                        "query_used": attempt_query,
                        "candidates_found": len(local_results),
                        "note": "hit",
                    }
                )
                break
            local_attempts.append(
                {
                    "query_used": attempt_query,
                    "candidates_found": 0,
                    "note": "search returned 0",
                }
            )
        if not candidates:
            local_note = "local search returned 0"
    elif not parsed_items:
        local_note = "skipped: parse_order_text returned empty"

    local_stage = _stage_entry(
        name="local",
        query_used=local_attempt_query_used or search_query,
        tokens_used=_extract_trace_tokens_numbers(local_attempt_query_used or search_query)[0],
        numbers_used=_extract_trace_tokens_numbers(local_attempt_query_used or search_query)[1],
        candidates_before=local_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=local_note,
    )
    local_stage.update(
        {
            "attempt_queries": attempt_queries,
            "attempt_query_used": local_attempt_query_used,
            "attempts": local_attempts,
        }
    )
    trace_by_name["local"] = local_stage

    candidates_count = len(candidates)
    decision = "alias_ok" if alias_used else ("history_ok" if history_used else ("local_ok" if candidates_count > 0 else "needs_llm"))

    candidates_count_before_llm = len(candidates)
    llm_called = False
    llm_stage = "none"

    llm_rewrite_before = len(candidates)
    llm_rewrite_note = "skipped: already have candidates"
    llm_rewrite_query = search_query
    llm_rewrite_candidates_found = 0
    if not candidates and enable_llm_rewrite and llm_available():
        llm_called = True
        llm_stage = "rewrite"
        rewritten_query = await rewrite_query(search_query or text)
        llm_rewrite_query = rewritten_query
        if rewritten_query and rewritten_query != (search_query or text):
            rewritten_results = await search_products(session, rewritten_query, limit=limit)
            if rewritten_results:
                candidates = rewritten_results
                candidates_count = len(candidates)
                decision = "llm_rewrite_ok"
                llm_rewrite_candidates_found = len(candidates)
                llm_rewrite_note = "rewrite matched"
            else:
                llm_rewrite_note = "rewrite returned 0"
        else:
            llm_rewrite_note = "rewrite unchanged"
    elif not candidates:
        llm_rewrite_note = "skipped: llm_rewrite_disabled" if not enable_llm_rewrite else "skipped: llm disabled"

    llm_rewrite_stage = _stage_entry(
        name="llm_rewrite",
        query_used=llm_rewrite_query,
        tokens_used=_extract_trace_tokens_numbers(llm_rewrite_query)[0],
        numbers_used=_extract_trace_tokens_numbers(llm_rewrite_query)[1],
        candidates_before=llm_rewrite_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=llm_rewrite_note,
    )
    llm_rewrite_stage.update(
        {
            "input_query": search_query,
            "rewritten_query": llm_rewrite_query,
            "candidates_found": llm_rewrite_candidates_found,
        }
    )
    trace_by_name["llm_rewrite"] = llm_rewrite_stage

    synonym_before = len(candidates)
    synonym_note = "skipped: already have candidates"
    if not candidates:
        synonym_retry_attempted = True
        synonym_retry_query, retry_aliases = apply_token_synonyms(search_query or text, alias_map)
        synonym_map.update(retry_aliases)
        if synonym_map and synonym_retry_query and synonym_retry_query != (search_query or text):
            synonym_results = await search_products(session, synonym_retry_query, limit=limit)
            synonym_retry_results_count = len(synonym_results)
            if synonym_results:
                candidates = synonym_results
                synonym_note = "synonym retry matched"
            else:
                synonym_note = "synonym retry returned 0"
        else:
            synonym_note = "synonym retry no changes"

    trace_by_name["synonym_retry"] = _stage_entry(
        name="synonym_retry",
        query_used=synonym_retry_query or search_query,
        tokens_used=_extract_trace_tokens_numbers(synonym_retry_query or search_query)[0],
        numbers_used=_extract_trace_tokens_numbers(synonym_retry_query or search_query)[1],
        candidates_before=synonym_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=synonym_note,
    )
    trace_by_name["synonym_retry"].update({
        "synonyms_applied": bool(synonym_map),
        "synonym_map": synonym_map,
        "query_retry": synonym_retry_query,
        "retry_results_count": synonym_retry_results_count,
    })

    clarification = None
    clarify_reason = "skipped"
    clarification_options: list[dict[str, Any]] = []

    if not candidates:
        head_token = extract_head_token(search_query or text)
        suggestions: list[dict[str, Any]] = []
        if history_org_id and head_token:
            suggestions = await history_suggestions(session, history_org_id, head_token, limit=60)
        if not suggestions and head_token:
            global_suggestions = await search_products(session, head_token, limit=60)
            suggestions = [
                {"product_id": item.get("id"), "title": item.get("title_ru")}
                for item in global_suggestions
                if item.get("title_ru")
            ]
        if not suggestions:
            for src, dst in alias_map.items():
                if src in (search_query or text).lower() and dst:
                    suggestions.append({"product_id": None, "title": dst})
        clarification_options = suggestions_to_options(suggestions)
        clarification = build_clarification(
            reason="no_candidates",
            options=clarification_options,
            offset=clarify_offset,
            page_size=10,
            question="Уточни товар:",
        )
        clarify_reason = clarification.get("reason")

    elif len(candidates) > 30:
        facet = build_facet_options(candidates, max_values=30)
        if facet:
            facet_name, facet_options = facet
            clarification_options = facet_options
            clarification = build_clarification(
                reason="conflict",
                options=clarification_options,
                offset=clarify_offset,
                page_size=10,
                question=f"Уточни {facet_name}:",
            )
            clarify_reason = clarification.get("reason")

    if clarification and clarification.get("options"):
        decision = "needs_clarification"
        llm_reason = clarify_reason
        if not candidates:
            if not enable_llm_narrow:
                llm_reason = "llm_narrow_disabled"
            elif not llm_available():
                llm_reason = "llm_disabled"

        decision_payload = _decision_payload(
            parsed_items=parsed_items,
            original_query=search_query or text,
            alternatives=[],
            used_alternative=None,
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
            category_ids=[],
            llm_narrow_confidence=None,
            llm_narrow_reason=llm_reason,
            narrowed_query=search_query,
            rerank_best_ids=[],
            rerank_top_score=None,
        )
        decision_payload["clarification"] = clarification
        decision_payload["llm_called"] = False
        decision_payload["llm_stage"] = "none"
        decision_payload["synonym_retry_attempted"] = synonym_retry_attempted
        decision_payload["synonym_map"] = synonym_map
        decision_payload["query_retry"] = synonym_retry_query or search_query
        decision_payload["retry_results_count"] = synonym_retry_results_count

        trace_by_name["clarify"] = {
            "name": "clarify",
            "query_used": search_query,
            "tokens_used": _extract_trace_tokens_numbers(search_query)[0],
            "numbers_used": _extract_trace_tokens_numbers(search_query)[1],
            "product_ids_filter_count": None,
            "category_ids_filter": [],
            "candidates_before": len(candidates),
            "candidates_after": len(candidates),
            "top5_titles": [str(item.get("title_ru") or "") for item in candidates[:5]],
            "notes": clarify_reason,
            "options_count": len(clarification.get("options") or []),
            "clarify_total": clarification.get("total"),
            "clarify_offset": clarification.get("offset"),
        }
        trace = {
            "input": {
                "raw_text": text,
                "normalized_text": normalized_text,
                "parsed_items": parsed_items,
                "org_id": history_org_id,
                "user_id": user_id,
            },
            "history_attempts": history_attempts,
            "local_attempts": local_attempts,
            "candidates_count_before_llm": len(candidates),
            "llm_called": False,
            "llm_stage": "none",
            "synonym_retry_attempted": synonym_retry_attempted,
            "synonym_map": synonym_map,
            "query_retry": synonym_retry_query or search_query,
            "retry_results_count": synonym_retry_results_count,
            "stages": [
                trace_by_name.get("history"),
                trace_by_name.get("alias"),
                trace_by_name.get("local"),
                trace_by_name.get("synonym_retry"),
                trace_by_name.get("clarify"),
            ],
            "clarify": {
                "reason": clarify_reason,
                "options_count": len(clarification.get("options") or []),
                "selected_facet": None,
                "clarify_total": clarification.get("total"),
                "clarify_offset": clarification.get("offset"),
            },
        }
        return {"results": candidates[:limit], "decision": decision_payload, "trace": trace}

    alternatives: list[str] = []
    used_alternative: str | None = None
    category_ids: list[int] = []
    llm_narrow_confidence: float | None = None
    llm_narrow_reason: str | None = None
    narrowed_query: str | None = None

    llm_before = len(candidates)
    llm_note = "skipped: already have candidates"
    llm_query_used = search_query
    if not candidates and parsed_items and enable_llm_narrow and llm_available():
        llm_called = True
        llm_stage = "normalize"
        alternatives = await suggest_queries(search_query or text)
        for alternative in alternatives:
            retry_candidates = await search_products(session, alternative, limit=limit)
            if retry_candidates:
                candidates = retry_candidates
                candidates_count = len(candidates)
                decision = "llm_ok"
                used_alternative = alternative
                llm_query_used = alternative
                llm_note = "llm alternative matched"
                break
        if not candidates:
            narrowed_query = search_query or text
            llm_stage = "narrow"
            narrow_result = await narrow_categories(narrowed_query, session)
            category_ids = narrow_result.get("category_ids", [])
            llm_narrow_confidence = narrow_result.get("confidence")
            llm_narrow_reason = narrow_result.get("reason")
            if category_ids:
                retry_candidates = await search_products(
                    session,
                    search_query or text,
                    limit=limit,
                    category_ids=category_ids,
                )
                if retry_candidates:
                    candidates = retry_candidates
                    candidates_count = len(candidates)
                    decision = "llm_narrow_ok"
                    llm_note = "llm narrow categories matched"
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
                            llm_query_used = alternative
                            llm_note = "llm narrow + alternative matched"
                            break
                    if not candidates:
                        decision = "no_match"
                        llm_note = "llm narrow categories returned 0"
            else:
                decision = "no_match"
                llm_note = "llm narrow returned empty categories"
    elif not candidates:
        decision = "no_match"
        llm_narrow_reason = "llm_disabled"
        llm_note = "skipped: llm disabled"

    if not candidates:
        decision = "no_match"
        if not enable_llm_narrow:
            llm_narrow_reason = "llm_narrow_disabled"
            llm_note = "skipped: llm_narrow_disabled"
        elif not llm_available():
            llm_narrow_reason = "llm_disabled"
            llm_note = "skipped: llm disabled"

    trace_by_name["llm_narrow"] = _stage_entry(
        name="llm_narrow",
        query_used=llm_query_used,
        tokens_used=_extract_trace_tokens_numbers(llm_query_used)[0],
        numbers_used=_extract_trace_tokens_numbers(llm_query_used)[1],
        category_ids_filter=category_ids,
        candidates_before=llm_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=llm_note,
    )

    rerank_used = False
    rerank_best_ids: list[int] = []
    rerank_top_score: float | None = None
    rerank_before = len(candidates)
    rerank_note = "skipped: rerank disabled" if not enable_rerank else "skipped: less than 2 candidates or llm disabled"
    if enable_rerank and 2 <= len(candidates) <= 30 and llm_available():
        llm_called = True
        llm_stage = "rerank"
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
        rerank = await rerank_products(search_query or text, rerank_payload, attrs)
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
            rerank_note = "rerank applied"
        else:
            rerank_note = "rerank returned empty best list"

    trace_by_name["rerank"] = _stage_entry(
        name="rerank",
        query_used=search_query,
        tokens_used=trace_tokens,
        numbers_used=trace_numbers,
        candidates_before=rerank_before,
        candidates_after=len(candidates),
        top_candidates=candidates,
        notes=rerank_note,
    )

    if not candidates and decision == "needs_llm":
        decision = "no_match"

    decision_payload = _decision_payload(
        parsed_items=parsed_items,
        original_query=search_query or text,
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
    trace = {
        "input": {
            "raw_text": text,
            "normalized_text": normalized_text,
            "parsed_items": parsed_items,
            "org_id": history_org_id,
            "user_id": user_id,
        },
        "history_attempts": history_attempts,
        "local_attempts": local_attempts,
        "candidates_count_before_llm": candidates_count_before_llm,
        "llm_called": llm_called,
        "llm_stage": llm_stage,
        "synonym_retry_attempted": synonym_retry_attempted,
        "synonym_map": synonym_map,
        "query_retry": synonym_retry_query or search_query,
        "retry_results_count": synonym_retry_results_count,
        "stages": [
            trace_by_name.get("history"),
            trace_by_name.get("alias"),
            trace_by_name.get("local"),
            trace_by_name.get("synonym_retry"),
            trace_by_name.get("clarify"),
            trace_by_name.get("llm_rewrite"),
            trace_by_name.get("llm_narrow"),
            trace_by_name.get("rerank"),
        ],
    }
    return {
        "results": candidates,
        "decision": {
            **decision_payload,
            "rerank_used": rerank_used,
            "rerank_best_ids": rerank_best_ids,
            "rerank_top_score": rerank_top_score,
            "candidates_count_before_llm": candidates_count_before_llm,
            "llm_called": llm_called,
            "llm_stage": llm_stage,
            "synonym_retry_attempted": synonym_retry_attempted,
            "synonym_map": synonym_map,
            "query_retry": synonym_retry_query or search_query,
            "retry_results_count": synonym_retry_results_count,
        },
        "trace": trace,
    }
