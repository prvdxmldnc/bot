import inspect
from app.services.search import search_products

def test_search_products_has_category_ids_param():
    sig = inspect.signature(search_products)
    assert "category_ids" in sig.parameters, "search_products must accept category_ids (C2 contract)"
