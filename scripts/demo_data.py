"""Fill a data directory with realistic fake data so the web UI can be tried and screenshotted offline.

    python scripts/demo_data.py /tmp/tdemo/data [--reset] [--port 5197]
    TANTALUS_DATA_DIR=/tmp/tdemo/data TANTALUS_OFFLINE=1 TANTALUS_SCHEDULER=0 TANTALUS_PORT=5197 python -m tantalus_hoard

Starts nothing: it builds the services (no scheduler, offline, no browser), installs the templates and inserts
observations, events, listings, news, proposals, runs and host state straight through the store. Prices, titles and
sellers are invented; the URLs point at example domains. Thumbnails use the app's own icon (``--port`` says where).
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tantalus_hoard.config import Config  # noqa: E402
from tantalus_hoard.model import InfoFinding  # noqa: E402
from tantalus_hoard.services import Services  # noqa: E402


class FakeLink:
    """No model: every chat call fails, status is empty."""

    def chat(self, *args, **kwargs):
        raise RuntimeError("no model in the demo")

    def status(self):
        return {}

    def close(self):
        pass


H = 3600.0
D = 86400.0


def build(data_dir: Path, port: int) -> Services:
    config = Config(data_dir=data_dir, port=port, scheduler=False, offline=True, browser=False, data_dir_configured=True)
    return Services(config, link=FakeLink(), install_presets=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--reset", action="store_true", help="delete the existing database first")
    ap.add_argument("--port", type=int, default=5197)
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    db = data_dir / "tantalus.db"
    if db.exists():
        if not args.reset:
            print(f"{db} exists; pass --reset to rebuild it", file=sys.stderr)
            return 1
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
    svc = build(data_dir, args.port)
    store = svc.store
    rng = random.Random(7)
    now = time.time()
    img = f"http://127.0.0.1:{args.port}/icon-192.png"

    watchers = {w["name"]: w for w in store.watchers()}
    poke = next(w for n, w in watchers.items() if n.startswith("Pokémon JCC"))
    dgx = next(w for n, w in watchers.items() if n.startswith("NVIDIA DGX"))
    rtx = next(w for n, w in watchers.items() if n.startswith("RTX Spark"))
    books = next(w for n, w in watchers.items() if n.startswith("Libros"))
    etb2 = next(w for n, w in watchers.items() if n.startswith("Pokémon ETB precintada"))

    # one more availability watcher with its own targets
    ns2 = store.create_watcher(
        name="Nintendo Switch 2 — Mario Kart World", mode="availability", interval_min=15,
        config={"product": {"terms": ["switch 2", "mario kart world"], "exclude": ["funda"]}, "region": "ES",
                "policies": {"seller": "retail_plus_marketplace", "alert_on": ["RESTOCK", "PRICE_DROP", "PRICE_THRESHOLD_CROSSED"],
                             "require_confidence": 75, "price_threshold": 449, "msrp": 509.99, "scalper_multiplier": 1.2},
                "discovery": {"queries": ["Switch 2 Mario Kart World pack comprar"], "retailers": ["game.es", "mediamarkt.es", "amazon.es"]}},
        notes="Pack de consola con juego. Aviso si baja de 449 €.")

    # ---------------------------------------------------------------- targets and their observations
    def add_target(watcher, url, label, retailer, **kw):
        t = store.create_target(watcher["id"], url, label=label, retailer=retailer, **kw)
        return t

    t_game = add_target(poke, "https://www.game.es/buscar/pokemon%2030%20aniversario", "GAME — búsqueda «pokemon 30 aniversario»", "GAME", fetch_tier="browser", source_level=2)
    t_eci = add_target(poke, "https://www.elcorteingles.es/search-nwx/1/?s=pokemon+30+aniversario", "El Corte Inglés — búsqueda", "El Corte Inglés", source_level=2)
    t_xtra = add_target(poke, "https://www.xtralife.com/buscar/pokemon%2030%20aniversario", "xtralife — búsqueda", "xtralife", fetch_tier="browser", source_level=2)
    t_car = add_target(poke, "https://www.carrefour.es/pokemon-jcc-30-aniversario-elite-trainer-box/VC4A-1234567/p", "Pokémon 30.º aniversario — Elite Trainer Box", "Carrefour", msrp=54.95, price_threshold=54.95)
    t_nv = add_target(dgx, "https://marketplace.nvidia.com/es-es/enterprise/personal-ai-supercomputers/dgx-spark/", "NVIDIA Marketplace España — DGX Spark", "NVIDIA", adapter="html", price_threshold=4800, source_level=1)
    t_nvapi = add_target(dgx, "nvidia-api:search?term=DGX%20Spark&locale=es-es", "NVIDIA API de productos — «DGX Spark»", "NVIDIA", price_threshold=4800, source_level=1)
    t_sw_mm = add_target(ns2, "https://www.mediamarkt.es/es/product/_nintendo-switch-2-mario-kart-world-1234.html", "Switch 2 + Mario Kart World — MediaMarkt", "MediaMarkt", msrp=509.99, price_threshold=449)
    t_sw_am = add_target(ns2, "https://www.amazon.es/dp/B0FAKE1234", "Switch 2 + Mario Kart World — Amazon", "Amazon", msrp=509.99, price_threshold=449, seller_policy="retail_plus_marketplace")
    t_sw_game = add_target(ns2, "https://www.game.es/videojuegos/consolas/switch-2-mario-kart-world/123456", "Switch 2 + Mario Kart World — GAME", "GAME", msrp=509.99)

    def curve(target, start_price, days, seq):
        """seq: list of (fraction_of_period, state, price_multiplier) segments over `days` days, checks every 6 h."""
        n = int(days * 4)
        confidence_by = {"IN_STOCK": 92, "LOCAL_PICKUP": 84, "PREORDER": 88, "OUT_OF_STOCK": 90, "RESTOCK_SCHEDULED": 78, "MARKETPLACE_ONLY": 70, "UNKNOWN": 40}
        last = None
        for i in range(n):
            frac = i / max(1, n - 1)
            state, mult = seq[0][1], seq[0][2]
            for start, s, m in seq:
                if frac >= start:
                    state, mult = s, m
            price = round(start_price * mult * (1 + 0.004 * math.sin(i)), 2)
            if state == "OUT_OF_STOCK" and rng.random() < 0.5:
                price = None
            conf = confidence_by[state] + rng.randint(-4, 4)
            obs = store.add_observation(target["id"], {
                "checked_at": now - (n - 1 - i) * 6 * H, "source_url": target["url"], "tier": "browser" if target["fetch_tier"] == "browser" else "http",
                "http_status": 200, "availability": state, "price": price, "currency": "EUR" if price else "", "seller": target["retailer"] if state != "MARKETPLACE_ONLY" else "Vendedor externo",
                "seller_is_retailer": state != "MARKETPLACE_ONLY", "buy_button": state in ("IN_STOCK", "PREORDER"),
                "evidence": ['"availability":"http://schema.org/' + ("InStock" if state == "IN_STOCK" else "OutOfStock") + '"', "Añadir a la cesta" if state == "IN_STOCK" else "Producto agotado"],
                "confidence": conf, "method": rng.choice(["jsonld", "microdata", "site:" + target["host"]]),
                "factors": [{"key": "structured_data", "points": 45, "label": "datos estructurados de la página"}, {"key": "buy_button", "points": 25 if state == "IN_STOCK" else 0, "label": "botón de compra activo"},
                            {"key": "retailer_seller", "points": 15, "label": "vende la propia tienda"}, {"key": "recent", "points": conf - 85 if conf > 85 else -5, "label": "ajuste por antigüedad"}],
                "is_revalidation": i % 17 == 5})
            last = (state, price, conf, obs)
        state, price, conf, obs = last
        prices = [p["price"] for p in store.price_history(target["id"])]
        store.update_target(target["id"], last_state=state, last_price=price, last_currency="EUR" if price else "", last_confidence=conf,
                            last_check_ts=now - 12 * 60, last_image=img, min_price=min(prices) if prices else None, next_check_ts=now + 8 * 60)

    curve(t_car, 54.95, 14, [(0, "OUT_OF_STOCK", 1.0), (0.45, "RESTOCK_SCHEDULED", 1.0), (0.7, "IN_STOCK", 1.0), (0.92, "IN_STOCK", 0.9)])
    store.update_target(t_car["id"], last_title="Pokémon JCC 30.º aniversario Elite Trainer Box (español)")
    curve(t_nv, 4999.0, 14, [(0, "PREORDER", 1.0), (0.3, "PREORDER", 0.98), (0.6, "IN_STOCK", 0.96), (0.85, "IN_STOCK", 0.96)])
    store.update_target(t_nv["id"], last_title="NVIDIA DGX Spark Founders Edition 4 TB", last_state="IN_STOCK")
    curve(t_sw_mm, 509.99, 10, [(0, "IN_STOCK", 1.0), (0.4, "IN_STOCK", 0.96), (0.7, "IN_STOCK", 0.93), (0.9, "IN_STOCK", 0.86)])
    store.update_target(t_sw_mm["id"], last_title="Nintendo Switch 2 + Mario Kart World Bundle")
    curve(t_sw_am, 509.99, 10, [(0, "MARKETPLACE_ONLY", 1.25), (0.5, "MARKETPLACE_ONLY", 1.18), (0.85, "OUT_OF_STOCK", 1.0)])
    store.update_target(t_sw_am["id"], last_title="Nintendo Switch 2 Mario Kart World — vendido por terceros", last_state="MARKETPLACE_ONLY", last_price=612.5, last_currency="EUR")
    curve(t_sw_game, 509.99, 10, [(0, "OUT_OF_STOCK", 1.0), (0.6, "RESTOCK_SCHEDULED", 1.0)])
    store.update_target(t_sw_game["id"], last_title="Switch 2 + Mario Kart World", last_state="RESTOCK_SCHEDULED", last_price=None, last_currency="")
    curve(t_eci, 54.95, 14, [(0, "UNKNOWN", 1.0), (0.5, "OUT_OF_STOCK", 1.0)])
    store.update_target(t_eci["id"], last_title="Resultados de búsqueda: pokemon 30 aniversario", last_state="OUT_OF_STOCK", last_price=None, last_currency="")
    curve(t_nvapi, 4999.0, 6, [(0, "IN_STOCK", 1.0)])
    store.update_target(t_nvapi["id"], last_title="DGX Spark (API de NVIDIA)")
    # blocked ones
    for tg, why in ((t_game, "CAPTCHA detectado (cloudflare) en www.game.es"), (t_xtra, "Pide iniciar sesión (login) en www.xtralife.com")):
        store.add_observation(tg["id"], {"checked_at": now - 25 * 60, "source_url": tg["url"], "tier": "browser", "http_status": 403, "availability": "UNKNOWN",
                                         "blocked_reason": "captcha" if "game" in tg["url"] else "login", "confidence": 0, "error": why})
        store.update_target(tg["id"], status="needs_human", last_error=why, last_state="UNKNOWN", last_check_ts=now - 25 * 60, last_title="")

    # ---------------------------------------------------------------- events
    def ev(watcher, target, type_, title, summary, *, price=None, old=None, conf=90, status="confirmed", age_h=1.0, seen=False, state="", old_state="", sev="medium", image=True, url=None, listing="", info="", candidate=""):
        e = store.add_event({
            "watcher_id": watcher["id"], "target_id": target["id"] if target else "", "type": type_, "severity": sev, "status": status, "old_state": old_state, "new_state": state,
            "price": price, "old_price": old, "currency": "EUR" if price is not None else "", "confidence": conf, "title": title, "summary": summary,
            "url": url or (target["url"] if target else "https://example.com/"), "detected_at": now - age_h * H, "listing_id": listing, "info_id": info, "candidate_id": candidate,
            "data": {**({"image": img} if image else {}), "factors": [{"key": "structured_data", "points": 45, "label": "datos estructurados"}, {"key": "buy_button", "points": 25, "label": "botón de compra"}], "evidence": ["Añadir a la cesta"]},
            "revalidate_at": now + 60 if status == "pending" else None})
        if seen:
            store.update_event(e["id"], seen=True)
        return e

    ev(poke, t_car, "RESTOCK", "Carrefour: Pokémon 30.º aniversario ETB vuelve a estar en stock", "Precio 54,95 €, vendido por Carrefour. Botón de compra activo en la ficha.", price=54.95, conf=93, age_h=0.4, state="IN_STOCK", old_state="OUT_OF_STOCK", sev="high")
    ev(dgx, t_nv, "PRICE_THRESHOLD_CROSSED", "DGX Spark por debajo de 4.800 €", "NVIDIA Marketplace España lo muestra a 4.799,00 €, en stock.", price=4799.0, old=4999.0, conf=88, age_h=2.5, state="IN_STOCK", sev="high")
    ev(ns2, t_sw_mm, "PRICE_DROP", "Switch 2 + Mario Kart World baja a 439,99 € en MediaMarkt", "Antes 509,99 €. Un 13,7 % menos.", price=439.99, old=509.99, conf=81, age_h=5, state="IN_STOCK")
    ev(ns2, t_sw_game, "RESTOCK_DATE_CONFIRMED", "GAME anuncia reposición de Switch 2 + Mario Kart World", "Fecha indicada en la ficha: 14 de octubre.", conf=78, age_h=9, state="RESTOCK_SCHEDULED", old_state="OUT_OF_STOCK", image=False)
    ev(poke, t_eci, "NEW_SKU", "El Corte Inglés: producto nuevo «Pokémon 30 aniversario Booster Bundle»", "Aparece en la búsqueda a 29,95 €, agotado por ahora.", price=29.95, conf=76, age_h=20, state="OUT_OF_STOCK", sev="low")
    ev(dgx, t_nv, "PREORDER_OPEN", "DGX Spark: reserva abierta", "La ficha permite reservar con entrega estimada en 3 semanas.", price=4999.0, conf=90, age_h=70, seen=True, state="PREORDER", old_state="UNKNOWN")
    ev(poke, t_game, "NEEDS_HUMAN", "GAME pide un CAPTCHA", "www.game.es bloquea las comprobaciones. Pulsa Resolver en Novedades.", conf=100, age_h=0.4, state="UNKNOWN", sev="high", image=False, status="logged")
    ev(poke, t_car, "SOLD_OUT", "Carrefour: ETB agotada", "La ficha ya muestra «producto agotado».", conf=86, age_h=150, seen=True, state="OUT_OF_STOCK", old_state="IN_STOCK", sev="low", status="logged")
    ev(ns2, t_sw_am, "RESTOCK", "Amazon: Switch 2 pack disponible (vendedor externo)", "Solo vendedores externos a 612,50 €: no se avisa.", price=612.5, conf=48, age_h=30, seen=True, status="logged", state="MARKETPLACE_ONLY")
    ev(ns2, t_sw_mm, "RESTOCK", "MediaMarkt: Switch 2 pack en stock", "Pendiente de una segunda lectura para confirmarlo.", price=449.99, conf=63, age_h=0.05, status="pending", state="IN_STOCK", old_state="OUT_OF_STOCK")
    ev(poke, t_xtra, "PRICE_DROP", "xtralife: falso positivo de precio", "El precio venía de un producto relacionado.", price=9.9, old=29.95, conf=60, age_h=80, seen=True, status="dismissed")

    # ---------------------------------------------------------------- second-hand listings
    def listing(watcher, source, title, price, dist, place, score, relevant, reason, signals, *, age_h, status="new", reserved=False, shipping=False, category="", desc=""):
        row = store.insert_listing(watcher["id"], {
            "source": source, "external_id": hashlib.md5(title.encode()).hexdigest()[:10], "url": f"https://es.wallapop.com/item/{hashlib.md5(title.encode()).hexdigest()[:12]}",
            "title": title, "description": desc, "price": price, "distance_km": dist, "location_text": place, "image_url": img, "score": score, "relevant": relevant,
            "signals": signals, "reason": reason, "category": category, "reserved": reserved, "shipping": shipping, "matched_query": "lote libros", "status": status})
        svc.db.execute("UPDATE listings SET first_seen_ts = ?, last_seen_ts = ? WHERE id = ?", (now - age_h * H, now - min(age_h, 1) * H, row["id"]))
        return row

    l1 = listing(books, "wallapop", "Regalo biblioteca completa por mudanza (unos 300 libros)", 0.0, 4.2, "Getafe", 34,
                 True, "Regalo de una biblioteca completa por mudanza, muy cerca.",
                 [{"key": "free_or_zero_price", "points": 10, "label": "gratis"}, {"key": "complete_library", "points": 7, "label": "biblioteca completa"}, {"key": "moving", "points": 5, "label": "mudanza"},
                  {"key": "high_quantity_detected", "points": 4, "label": "cantidad estimada alta"}, {"key": "very_close_location", "points": 3, "label": "ubicación cercana"}], age_h=1.5)
    l2 = listing(books, "wallapop", "Lote 40 libros novela histórica y fantasía", 15.0, 11.8, "Alcorcón", 21, True, "Lote grande a precio bajo.",
                 [{"key": "lot", "points": 6, "label": "lote de libros"}, {"key": "high_quantity_detected", "points": 4, "label": "cantidad estimada alta"}, {"key": "in_active_municipality", "points": 4, "label": "en un municipio activo"}], age_h=4)
    l3 = listing(books, "wallapop", "Cajas de libros para recoger, me los quito de encima", 0.0, 17.0, "Fuenlabrada", 26, True, "Se quiere deshacer de ellos; varias cajas.",
                 [{"key": "getting_rid_of_it", "points": 8, "label": "se quiere deshacer de ellos"}, {"key": "boxes", "points": 5, "label": "cajas de libros"}, {"key": "free_or_zero_price", "points": 10, "label": "gratis"}], age_h=7)
    listing(books, "wallapop", "Enciclopedia Espasa completa, envío gratis", 60.0, 30.0, "Madrid", -6, False, "Enciclopedia con envío gratis: falso positivo típico.",
            [{"key": "encyclopedia", "points": -5, "label": "enciclopedia"}, {"key": "free_shipping", "points": -10, "label": "envío gratis (no el lote)"}], age_h=9, shipping=True)
    listing(books, "wallapop", "Colección Harry Potter tapa dura 7 libros", 30.0, 9.0, "Leganés", 12, True, "Colección completa a precio razonable.",
            [{"key": "collection", "points": 6, "label": "colección"}, {"key": "in_active_municipality", "points": 4, "label": "en un municipio activo"}], age_h=26, status="saved")
    listing(etb2, "wallapop", "Pokémon Elite Trainer Box 30 aniversario PRECINTADA con factura", 58.0, 6.5, "Getafe", 24, True, "Precintada, con factura y por debajo de 1,3 × PVP.",
            [{"key": "sealed", "points": 8, "label": "precintado / sellado"}, {"key": "receipt", "points": 2, "label": "con factura o ticket"}, {"key": "below_msrp", "points": 0, "label": "cerca del PVP"},
             {"key": "include_all_match", "points": 4, "label": "contiene «30 aniversario»"}, {"key": "very_close_location", "points": 3, "label": "ubicación cercana"}], age_h=3, desc="Precintada, factura de Carrefour.")
    listing(etb2, "wallapop", "ETB Pokémon 30 aniversario a 140 €", 140.0, 12.0, "Getafe", -38, False, "Precio de reventa: muy por encima del límite.",
            [{"key": "scalper", "points": -40, "label": "precio de reventa"}, {"key": "sealed", "points": 8, "label": "precintado / sellado"}], age_h=12, reserved=True)
    listing(etb2, "facebook", "Booster Bundle Pokémon 30 aniversario x2", 38.0, 22.0, "Alcalá de Henares", 9, True, "Dos bundles sellados.",
            [{"key": "sealed", "points": 8, "label": "precintado / sellado"}, {"key": "include_any_match", "points": 6, "label": "contiene palabras clave"}, {"key": "too_far", "points": -5, "label": "algo lejos"}], age_h=30, status="dismissed")
    ev(books, None, "NEW_LISTING", "Wallapop: Regalo biblioteca completa por mudanza (unos 300 libros)", "34 puntos: gratis, biblioteca completa, mudanza. A 4 km.", price=0.0, conf=95, age_h=1.5,
       url=l1["url"], listing=l1["id"], sev="high", state="")

    # ---------------------------------------------------------------- information items and sources
    def info(watcher, title, url, snippet, verdict, material, reason, *, age_h, kind="search_hit", level=5, diff=""):
        source = (store.info_sources(watcher["id"]) or [{"id": ""}])[0]["id"]
        f = InfoFinding(url=url, title=title, snippet=snippet, kind=kind, content_hash=hashlib.sha1((title + url).encode()).hexdigest(), verdict=verdict, material=material,
                        score=70 if material else 20, reason=reason, source_level=level, diff=diff, published=time.strftime("%Y-%m-%d", time.gmtime(now - age_h * H)))
        row = store.insert_info_item(watcher["id"], source, f)
        svc.db.execute("UPDATE info_items SET first_seen_ts = ? WHERE id = ?", (now - age_h * H, row["id"]))
        return row

    i1 = info(rtx, "ASUS abre la reserva del ProArt RTX Spark con 128 GB en España", "https://www.asus.com/es/news/rtx-spark-proart-reserva/", "ASUS confirma la reserva de su estación con RTX Spark y 128 GB de memoria unificada a partir del 20 de octubre.",
              "confirmed", True, "Página oficial de un fabricante: precio y reserva confirmados.", age_h=6, level=3, kind="page_change", diff="+ Reserva disponible en España desde el 20/10\n+ Precio desde 4.190 €")
    info(rtx, "Filtración: Lenovo preparará un N1X de 128 GB para Europa en noviembre", "https://videocardz.example/lenovo-n1x-128gb", "Una fuente anónima asegura que el modelo llegará en noviembre.", "leak", True, "Solo una filtración de un medio: sin confirmación oficial.", age_h=30)
    info(rtx, "Estimación de precio del RTX Spark 128 GB: entre 4.000 y 5.000 €", "https://techblog.example/rtx-spark-precio", "Análisis basado en el precio del DGX Spark.", "estimate", True, "Estimación de un medio, no un precio oficial.", age_h=55)
    info(rtx, "Reseña de un portátil con RTX 50", "https://techblog.example/portatil-rtx-50", "Nada que ver con el producto vigilado.", "irrelevant", False, "No menciona RTX Spark ni N1X.", age_h=70)
    for src in store.info_sources(rtx["id"]):
        store.update_info_source(src["id"], {"last_check_ts": now - rng.randint(20, 90) * 60, "last_error": ""})
    srcs = store.info_sources(rtx["id"])
    if srcs:
        store.update_info_source(srcs[-1]["id"], {"last_error": "HTTP 429: demasiadas peticiones, se reintenta más tarde"})
    ev(rtx, None, "INFO_CHANGE", i1["title"], "Página oficial: reserva desde el 20/10, precio desde 4.190 €.", conf=92, age_h=6, url=i1["url"], info=i1["id"], sev="high", image=False)

    # ---------------------------------------------------------------- candidates
    for w, url, title, retailer, score, reason in (
        (poke, "https://www.mediamarkt.es/es/product/_pokemon-jcc-30-aniversario-etb-777.html", "Pokémon JCC 30.º aniversario Elite Trainer Box — MediaMarkt", "MediaMarkt", 82, "Coincide con las palabras del producto y con una tienda permitida."),
        (poke, "https://www.fnac.es/Pokemon-30-aniversario-Booster-Bundle/a1234567", "Booster Bundle Pokémon 30 aniversario — Fnac", "Fnac", 74, "Página de producto de una tienda permitida."),
        (dgx, "https://www.pccomponentes.example/nvidia-dgx-spark", "NVIDIA DGX Spark — PcComponentes", "PcComponentes", 61, "Tienda no incluida en la lista permitida: revisa antes de aceptar."),
        (ns2, "https://www.elcorteingles.es/videojuegos/switch-2-mario-kart-world", "Nintendo Switch 2 Mario Kart World — El Corte Inglés", "El Corte Inglés", 70, "Ficha de producto con el pack buscado."),
    ):
        store.insert_candidate(w["id"], {"url": url, "title": title, "snippet": "Resultado de la búsqueda de descubrimiento.", "retailer": retailer, "engine": "duckduckgo", "query": "comprar", "score": score, "reason": reason, "source_level": 2})

    # ---------------------------------------------------------------- watcher timings, runs, hosts, notifications
    for w, last, nxt, err in ((poke, 14, 6, ""), (dgx, 40, 20, ""), (ns2, 8, 7, ""), (rtx, 95, 85, "Una fuente respondió 429; se reintenta en la próxima ejecución"), (books, 22, 23, ""), (etb2, None, None, "")):
        store.update_watcher(w["id"], last_run_ts=(now - last * 60) if last else None, next_run_ts=(now + nxt * 60) if nxt else None, last_error=err)
    for kind, w, ok, secs, summary in (("check", poke, True, 6.2, {"target": t_car["id"], "state": "IN_STOCK", "price": 54.95, "confidence": 93, "events": 1}),
                                       ("check", poke, False, 31.0, {"target": t_game["id"], "blocked": True, "error": "CAPTCHA"}),
                                       ("secondhand", books, True, 18.4, {"queries": 5, "found": 41, "new": 3, "relevant": 3}),
                                       ("information", rtx, True, 22.1, {"sources": 5, "new_items": 2, "material": 1}),
                                       ("discovery", poke, True, 12.3, {"queries": 6, "proposed": 2}),
                                       ("check", dgx, True, 4.4, {"target": t_nv["id"], "state": "IN_STOCK", "price": 4799.0})):
        rid = store.start_run(kind, watcher_id=w["id"])
        finished_at = now - rng.randint(5, 600) * 60
        store.finish_run(rid, ok, summary)
        svc.db.execute("UPDATE runs SET started_ts = ?, finished_ts = ? WHERE id = ?", (finished_at - secs, finished_at, rid))
    for host, ok, fail, blocked, reason, tier in (("www.carrefour.es", 88, 2, None, "", "http"), ("marketplace.nvidia.com", 40, 0, None, "", "http"), ("www.game.es", 12, 9, now + 4 * H, "captcha", "browser"),
                                                   ("www.xtralife.com", 5, 4, now + 2 * H, "login", "browser"), ("www.mediamarkt.es", 30, 1, None, "", "http"), ("es.wallapop.com", 210, 3, None, "", "http")):
        svc.db.execute("INSERT INTO host_state(host, last_fetch_ts, min_interval_s, blocked_until_ts, block_reason, preferred_tier, ok_count, fail_count) VALUES (?,?,?,?,?,?,?,?)"
                       " ON CONFLICT(host) DO UPDATE SET last_fetch_ts=excluded.last_fetch_ts, blocked_until_ts=excluded.blocked_until_ts, block_reason=excluded.block_reason,"
                       " preferred_tier=excluded.preferred_tier, ok_count=excluded.ok_count, fail_count=excluded.fail_count",
                       (host, now - rng.randint(3, 50) * 60, 20, blocked, reason, tier, ok, fail))
    first = store.events(statuses=["confirmed"], limit=3)
    for e, channel, ok, err in ((first[0], "hub", True, ""), (first[0], "toast", False, "not windows"), (first[1], "hub", True, "")):
        store.record_notification(e["id"], channel, ok, err)
    svc.db.set_setting("dashboard.last_visit_ts", str(now - 3 * H))
    counts = store.counts()
    svc.stop()
    print(f"demo data ready in {data_dir}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
