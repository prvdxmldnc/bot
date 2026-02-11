from __future__ import annotations

import inspect

from app.services.search import search_products


def test_search_products_has_product_ids_param():
    signature = inspect.signature(search_products)
    assert "product_ids" in signature.parameters
