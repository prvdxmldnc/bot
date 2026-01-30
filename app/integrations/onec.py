from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, status

from app.config import settings

router = APIRouter()


def _extract_token(
    authorization: str | None,
    token_header: str | None,
    x_token_header: str | None,
    token_query: str | None,
) -> str | None:
    if token_header:
        return token_header
    if x_token_header:
        return x_token_header
    if token_query:
        return token_query
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
    return None


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            return payload["items"]
        if isinstance(payload.get("catalog"), list):
            return payload["catalog"]
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid payload")


def _validate_items(items: list[dict[str, Any]]) -> None:
    required_fields = ("sku", "title", "category", "price", "stock_qty", "description")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Item at index {index} must be an object",
            )
        for field in required_fields:
            if field not in item:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Missing field '{field}' at index {index}",
                )
        if not isinstance(item["sku"], str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field 'sku' must be string at index {index}",
            )
        if not isinstance(item["title"], str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field 'title' must be string at index {index}",
            )
        if not isinstance(item["category"], str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field 'category' must be string at index {index}",
            )
        if not isinstance(item["description"], str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field 'description' must be string at index {index}",
            )
        if not isinstance(item["price"], (int, float)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field 'price' must be number at index {index}",
            )
        if not isinstance(item["stock_qty"], (int, float)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field 'stock_qty' must be number at index {index}",
            )


@router.post("/integrations/1c/catalog")
@router.post("/onec/catalog")
@router.post("/api/onec/catalog")
async def one_c_catalog(
    payload: Any = Body(...),
    authorization: str | None = Header(default=None, alias="Authorization"),
    token_header: str | None = Header(default=None, alias="X-1C-Token"),
    x_token_header: str | None = Header(default=None, alias="X-Token"),
    token_query: str | None = Query(default=None, alias="token"),
) -> dict[str, Any]:
    if settings.one_c_webhook_token:
        provided_token = _extract_token(authorization, token_header, x_token_header, token_query)
        if provided_token != settings.one_c_webhook_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    items = _extract_items(payload)
    _validate_items(items)
    return {"ok": True, "received": len(items)}
