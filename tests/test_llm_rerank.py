from __future__ import annotations

from app.services.llm_rerank import _extract_json_object, _parse_rerank_content


def test_extract_json_object_with_noise() -> None:
    content = "noise {\"best\":[{\"product_id\":1,\"score\":0.9}],\"need_clarify\":[]} tail"
    assert _extract_json_object(content).startswith("{")


def test_parse_rerank_content_limits_best() -> None:
    content = (
        "{\"best\":[{\"product_id\":1,\"score\":0.9},{\"product_id\":1,\"score\":0.8},"
        "{\"product_id\":2,\"score\":0.7},{\"product_id\":3,\"score\":0.6},"
        "{\"product_id\":4,\"score\":0.5},{\"product_id\":5,\"score\":0.4},"
        "{\"product_id\":6,\"score\":0.3}],\"need_clarify\":[]}".
        replace("\n", "")
    )
    parsed = _parse_rerank_content(content)
    best = parsed["best"]
    assert len(best) == 5
    assert {item["product_id"] for item in best} == {1, 2, 3, 4, 5}

