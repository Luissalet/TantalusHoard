"""NVIDIA partners API adapter, on responses captured 2026-09-30 (Spain). No network."""

from __future__ import annotations

import json
from pathlib import Path

from tantalus_hoard.extract import adapter_for, extract, nvidia
from tantalus_hoard.model import IN_STOCK, OUT_OF_STOCK, FetchResult

API = Path(__file__).parent / "fixtures" / "api"


def load(name: str) -> dict:
    return json.loads((API / name).read_text(encoding="utf-8"))


def api_result(name: str, url: str) -> FetchResult:
    return FetchResult(url=url, final_url=url, status=200, ok=True, tier="http", content_type="application/json",
                       text=(API / name).read_text(encoding="utf-8"))


def test_search_url_builder_and_scheme_form():
    assert nvidia.build_search_url("DGX Spark") == "https://api.nvidia.partners/edge/product/search?page=1&limit=12&locale=es-es&search=DGX%20Spark"
    spec = adapter_for("nvidia-api:search?term=DGX%20Spark&locale=es-es")
    assert spec and spec["adapter"] == "nvidia" and spec["url"].endswith("&search=DGX%20Spark")
    assert spec["accept"] == "json" and spec["respect_robots"] is False and spec["tier"] == "http"


def test_marketplace_urls_derive_a_search_term_from_the_slug():
    dgx = adapter_for("https://marketplace.nvidia.com/es-es/enterprise/personal-ai-supercomputers/dgx-spark/")
    assert dgx and dgx["url"].endswith("&search=dgx%20spark") and "locale=es-es" in dgx["url"]
    gpu = adapter_for("https://marketplace.nvidia.com/es-es/consumer/graphics-cards/nvidia-geforce-rtx-5090/")
    assert gpu and gpu["url"].endswith("&search=rtx%205090")


def test_no_adapter_for_other_targets():
    assert adapter_for("https://www.game.es/x/1234") is None
    assert adapter_for("https://www.amazon.es/dp/B0F18B4CVX", {"adapter": "auto"}) is None
    assert adapter_for("nvidia-api:search?term=x", {"adapter": "other"}) is None


def test_founder_edition_is_out_of_stock_and_partner_listings_are_in_stock():
    url = nvidia.build_search_url("RTX 5090")
    ex = extract(api_result("nvidia_rtx5090.json", url), hints={"url": url, "adapter": "nvidia"})
    assert ex.page_kind == "search" and ex.methods == ["api:nvidia"] and ex.quality_ok
    fe = ex.offers[0]
    assert fe.availability == OUT_OF_STOCK and fe.price == 2099.0 and fe.currency == "EUR"
    assert fe.seller == "nvidia.com" and fe.seller_is_retailer is True
    assert fe.extra["is_founder_edition"] is True and fe.extra["prd_status"] == "out_of_stock"
    assert any("out_of_stock" in e for e in fe.evidence)
    assert fe.method == "api:nvidia" and fe.sku == "LCFEGF50LD90"
    partner = ex.offers[1]
    assert partner.availability == IN_STOCK and partner.price == 7835.19
    assert partner.seller == "pccomponentes.com" and partner.url.startswith("https://www.pccomponentes.com/")
    assert partner.extra["msrp"] == 2754.99
    assert all(o.extra["term_match"] for o in ex.offers)


def test_look_alike_suggestions_are_flagged_when_nothing_matches():
    url = nvidia.build_search_url("DGX Spark")
    ex = extract(api_result("nvidia_dgx_spark_suggested.json", url), hints={"url": url, "adapter": "nvidia"})
    assert ex.page_kind == "search"
    assert ex.offers and all(o.extra["suggested"] for o in ex.offers)
    assert not any(o.extra["term_match"] for o in ex.offers)
    assert any("no exact match" in n for n in ex.notes)


def test_product_page_url_narrows_to_that_product():
    page = "https://marketplace.nvidia.com/es-es/consumer/graphics-cards/nvidia-geforce-rtx-5090/"
    ex = extract(api_result("nvidia_rtx5090.json", nvidia.adapter_url(page)), hints={"url": page, "adapter": "nvidia"})
    assert ex.page_kind == "product"
    assert len(ex.offers) == 1 and ex.offers[0].extra["is_founder_edition"] is True


def test_partner_flagged_unavailable_is_out_of_stock():
    payload = {"searchedProducts": {"productDetails": [{
        "displayName": "X RTX 5080", "productTitle": "X RTX 5080", "productSKU": "S1", "productPrice": "€999.00", "mrp": "999.0",
        "productAvailable": False, "prdStatus": "buy_now", "internalLink": "https://marketplace.nvidia.com/es-es/consumer/graphics-cards/x/",
        "retailers": [{"isAvailable": False, "salePrice": "999.0", "directPurchaseLink": "https://www.neobyte.es/x", "stock": 0,
                       "retailerName": "https://www.neobyte.es/", "partnerId": "1"},
                      {"isAvailable": True, "salePrice": "1010.0", "directPurchaseLink": "https://www.amazon.es/x", "stock": 0,
                       "retailerName": "https://www.amazon.es/", "partnerId": "2"}]}], "suggestedProductDetails": []}}
    offers = nvidia.parse(payload, term="rtx 5080")
    assert [o.availability for o in offers] == [OUT_OF_STOCK, IN_STOCK]
    assert offers[1].price == 1010.0


def test_unknown_json_is_not_pretended_to_be_products():
    fr = FetchResult(url="https://api.example.com/x", status=200, ok=True, content_type="application/json", text='{"hello": 1}')
    ex = extract(fr, hints={})
    assert ex.page_kind == "unknown" and not ex.offers and not ex.quality_ok
