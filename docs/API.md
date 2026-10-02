# Tantalus's Hoard — agent tools

Every tool is served by the app at `GET /api/agent/tools` and `POST /api/agent/call` (Bearer token from `data/mcp-token`), by the stdio bridge `mcp_server.py`, and to the bundled UI through `POST /api/ui/call`. The argument tables are generated from the code (`python scripts/gen_api_doc.py`).

## `tantalus_overview`

What is new since the last visit: alerts, buyable now, blocked, top finds. Novedades del vigilante.

The dashboard in one call: unseen confirmed events (restock, price drop, pre-order, new SKU, second-hand find, news), targets buyable now, targets that need a human (CAPTCHA/login), per-watcher status, top second-hand listings, material news, proposed URLs.
Sinónimos: novedades, qué hay nuevo, restock, reposición, ofertas, alertas, resumen, estado de los vigilantes.

Annotations: readOnlyHint, idempotentHint.

## `tantalus_status`

Health: scheduler, notification channels, model, search engines, browser, counts. Estado de Tantalus.

Sinónimos: salud, canales de aviso, navegador, motores de búsqueda.

Annotations: readOnlyHint, idempotentHint.

## `watcher_list`

List watchers (availability, second-hand, information) with their counts. Lista de vigilantes.

Sinónimos: vigilantes, alertas configuradas, qué estoy vigilando.

Annotations: readOnlyHint, idempotentHint.

## `watcher_get`

One watcher with its config, targets, sources, candidates and recent events. Detalle de un vigilante.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |

## `watcher_create`

Create a watcher: availability (stock, price), secondhand or information (news). Crear vigilante.

Secondhand covers Wallapop and Facebook Marketplace. For availability, pass targets (product or retailer-search URLs) and discovery queries. Sinónimos: vigila, avísame cuando haya stock, alerta de precio, busca de segunda mano, seguimiento de noticias.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `name` (string) | yes |  |
| `mode` (availability \| secondhand \| information) | yes |  |
| `config` (object) | no | availability: {product:{terms:[...], must:[...], exclude:[...]}, region, stores:[...], policies:{seller: retail_only\|retail_plus_marketplace\|any_below, alert_on:[RESTOCK,...], require_confidence:75, revalidate_seconds:60, cooldown_minutes:20, min_drop_pct:5, price_threshold, msrp, scalper_multiplier}, discovery:{queries:[...], retailers:[...], terms:[...]}, channels:[...]}. secondhand: {pack: books_bulk\|generic\|collectibles_sealed, sources:[wallapop, facebook], queries:[...], settings:{origin_location, radius_km, price_ceiling, msrp, include_any, include_all, exclude}}. information: {sources:[{kind: page\|feed\|search, value, label}], info:{must_terms, boost_terms, exclude_terms, official_domains, freshness_days}}. |
| `interval_min` (integer) | no | Check cadence. Hot restock 5-15, normal retail 15-30, stable 60, news 60-240. |
| `discovery_interval_h` (integer) | no |  |
| `enabled` (boolean) | no |  |
| `notes` (string) | no |  |
| `targets` (array) | no | availability: product or retailer-search URLs to add right away. |
| `budget_category` (string) | no | Ledger budget category: alerts with a price add 'quedan X € en <categoría>'. |

## `watcher_update`

Change a watcher's name, config, cadence, discovery cadence or enabled flag. Editar vigilante.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |
| `name` (string/null) | no |  |
| `config` (object/null) | no | Replaces the whole config; read watcher_get first. |
| `interval_min` (integer/null) | no |  |
| `discovery_interval_h` (integer/null) | no |  |
| `enabled` (boolean/null) | no |  |
| `notes` (string/null) | no |  |
| `budget_category` (string/null) | no | Ledger budget category for the alert text; empty clears it. |

## `watcher_delete`

Delete a watcher and all its history (confirm=true). Borrar vigilante.

Annotations: destructiveHint.

| Argument | Required | Description |
|---|---|---|
| `id` (string) | yes |  |
| `confirm` (boolean) | no |  |

## `watcher_run`

Run a watcher now: check every target / sweep second-hand / check news sources. Comprobar ahora.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |

## `watcher_rescore`

Score a second-hand watcher's stored listings again with its current pack (no alerts). Repuntuar anuncios.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |

## `target_add`

Add a product or retailer-search URL to an availability watcher and check it. Añadir URL a vigilar.

Supports product pages (JSON-LD / buttons), retailer search pages (new SKUs appear as NEW_SKU) and nvidia-api:search?term=... Sinónimos: vigila esta URL, añade esta tienda, seguimiento de precio de este producto.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |
| `url` (string) | yes | Product page, retailer search page, or nvidia-api:search?term=... |
| `label` (string) | no |  |
| `retailer` (string) | no |  |
| `sku` (string) | no |  |
| `ean` (string) | no |  |
| `store_ids` (array) | no |  |
| `msrp` (number/null) | no | Reference retail price; the ceiling becomes msrp x scalper_multiplier. |
| `price_ceiling` (number/null) | no |  |
| `price_threshold` (number/null) | no | Alert when the price falls to or below this. |
| `seller_policy` (string/null) | no |  |
| `fetch_tier` (auto \| http \| browser \| window) | no |  |
| `adapter` (string) | no | auto \| html \| nvidia |
| `interval_min` (integer/null) | no |  |
| `check_now` (boolean) | no | Run a first check right away. |

## `target_list`

List targets with their last state, price and confidence. Lista de productos vigilados.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/null) | no |  |
| `status` (string/null) | no |  |

## `target_get`

One target with observations (evidence, confidence), price history and events. Historial de producto.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `target_id` (string) | yes |  |
| `history` (integer) | no |  |

## `target_update`

Change a target: label, URL, SKU, MSRP, price limits, seller policy, fetch tier, pause. Editar objetivo.

SKU/EAN, price ceiling and price threshold are separate fields.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `target_id` (string) | yes |  |
| `label` (string/null) | no |  |
| `url` (string/null) | no |  |
| `retailer` (string/null) | no |  |
| `sku` (string/null) | no |  |
| `ean` (string/null) | no |  |
| `store_ids` (array/null) | no |  |
| `msrp` (number/null) | no |  |
| `price_ceiling` (number/null) | no |  |
| `price_threshold` (number/null) | no |  |
| `seller_policy` (string/null) | no |  |
| `fetch_tier` (string/null) | no |  |
| `adapter` (string/null) | no |  |
| `interval_min` (integer/null) | no |  |
| `status` (string/null) | no |  |
| `clear` (array) | no | Fields to reset to empty (null means 'unchanged' elsewhere). |

## `target_delete`

Delete a target and its history (confirm=true). Borrar objetivo.

Annotations: destructiveHint.

| Argument | Required | Description |
|---|---|---|
| `id` (string) | yes |  |
| `confirm` (boolean) | no |  |

## `target_check`

Check one target now (fetch ladder, extraction, events). Comprobar este producto ahora.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `target_id` (string) | yes |  |

## `target_resolve`

Open a visible browser so the user passes a CAPTCHA or login, then re-check. Resolver bloqueo.

Only when the user is at the computer and asks. Never solves anything itself.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `target_id` (string/null) | no |  |
| `url` (string/null) | no |  |

## `inspect_url`

One-off stock, price and seller of a product or search page, without saving. ¿Hay stock? ¿Precio?

Sinónimos: mira esta página, comprueba precio, está disponible, agotado.

Annotations: readOnlyHint, idempotentHint, openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `url` (string) | yes |  |
| `tier` (auto \| http \| browser \| window) | no |  |
| `sku` (string) | no |  |
| `show_text` (boolean) | no | Include up to 3000 chars of the readable page text. |

## `events_list`

Events (RESTOCK, PRICE_DROP, PREORDER_OPEN, NEW_SKU, NEW_LISTING...) with filters. Historial de avisos.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/null) | no |  |
| `target_id` (string/null) | no |  |
| `types` (array/null) | no | RESTOCK, LOCAL_RESTOCK, PREORDER_OPEN, PRICE_DROP, PRICE_THRESHOLD_CROSSED, NEW_SKU, RESTOCK_DATE_CONFIRMED, SOLD_OUT, NEW_LISTING, LISTING_PRICE_DROP, INFO_CHANGE, CANDIDATE_FOUND, NEEDS_HUMAN, MAIL_DEAL, RELEASE, SALE_OPEN |
| `statuses` (array/null) | no |  |
| `unseen_only` (boolean) | no |  |
| `limit` (integer) | no |  |

## `events_mark_seen`

Mark events seen (all, or some ids) and record a dashboard visit. Marcar como visto.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `event_ids` (array/null) | no | Omit to mark everything seen (records a dashboard visit). |

## `event_dismiss`

Dismiss an event (false positive, not interested). Descartar aviso.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `event_id` (string) | yes |  |

## `event_notify`

Send an event again through the notification channels that did not get it. Reenviar aviso.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `event_id` (string) | yes |  |

## `listings_list`

Second-hand listings found by the watchers, with score, signals and distance. Anuncios de segunda mano.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/null) | no |  |
| `relevant_only` (boolean) | no |  |
| `statuses` (array/null) | no |  |
| `order` (recent \| score) | no |  |
| `limit` (integer) | no |  |

## `listing_set`

Mark a second-hand listing seen / saved / dismissed / gone. Guardar o descartar anuncio.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `listing_id` (string) | yes |  |
| `status` (new \| seen \| saved \| dismissed \| gone) | yes |  |

## `info_items_list`

News found by information watchers with verdict (confirmed / leak / estimate). Novedades informativas.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/null) | no |  |
| `material_only` (boolean) | no |  |
| `include_dismissed` (boolean) | no |  |
| `limit` (integer) | no |  |

## `info_item_set`

Mark a news item seen or dismissed. Marcar noticia vista o descartada.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `item_id` (string) | yes |  |
| `status` (new \| seen \| dismissed) | yes |  |

## `candidates_list`

URLs proposed by discovery for availability watchers. Páginas propuestas.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/null) | no |  |
| `status` (string/null) | no |  |
| `limit` (integer) | no |  |

## `candidate_accept`

Turn a proposed URL into a watched target. Aceptar propuesta.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `candidate_id` (string) | yes |  |
| `label` (string/null) | no |  |
| `msrp` (number/null) | no |  |
| `price_threshold` (number/null) | no |  |
| `check_now` (boolean) | no |  |

## `candidate_reject`

Reject a proposed URL. Rechazar propuesta.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `candidate_id` (string) | yes |  |

## `discovery_run`

Search the web for new product / retailer URLs for an availability watcher. Descubrir nuevas URLs.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |

## `web_search`

Web or news search (DuckDuckGo, Bing, Google News, Bing News, SearXNG, Brave). Buscar en web o noticias.

Annotations: readOnlyHint, idempotentHint, openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `query` (string) | yes |  |
| `limit` (integer) | no |  |
| `freshness_days` (integer/null) | no |  |
| `news` (boolean) | no | Search news (Google News and Bing News RSS) instead of the web. |

## `secondhand_search`

One-off Wallapop or Facebook search scored by a pack, near a town, without saving. Buscar de segunda mano.

Packs: books_bulk (free books in bulk), generic, collectibles_sealed (sealed TCG, anti-scalper). Sinónimos: wallapop, segunda mano, lotes, gratis, cerca de mí.

Annotations: readOnlyHint, idempotentHint, openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `query` (string) | yes |  |
| `source` (wallapop \| facebook) | no |  |
| `pack` (string) | no | books_bulk \| generic \| collectibles_sealed |
| `origin_location` (string) | no |  |
| `radius_km` (number) | no |  |
| `max_price` (number/null) | no |  |
| `msrp` (number/null) | no |  |
| `limit` (integer) | no |  |

## `secondhand_facebook_login`

Open a visible browser to log in to Facebook once (for Marketplace). Iniciar sesión en Facebook.

Annotations: openWorldHint.

## `packs_list`

Second-hand scoring packs with their fields. Perfiles de puntuación.

Annotations: readOnlyHint, idempotentHint.

## `presets_list`

Ready-made watchers and whether they are installed. Plantillas.

Annotations: readOnlyHint, idempotentHint.

## `presets_install`

Install ready-made watchers. Instalar plantillas.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `ids` (array/null) | no | Omit to install every preset that is not installed yet. |

## `notify_status`

Notification channels (toast, hub, ntfy, telegram, email): setup, switches, recent sends. Canales de aviso.

Annotations: readOnlyHint, idempotentHint.

## `notify_test`

Send a test notification through one channel. Probar canal.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `channel` (toast \| hub \| ntfy \| telegram \| email) | no |  |
| `via` (own \| hub) | no | hub = a sample notification through the family hub's notification centre (it decides the channels); own = test the channel itself. |

## `telegram_find_chat_id`

After the user writes to the bot, find the chat id with getUpdates. Obtener chat id de Telegram.

Annotations: readOnlyHint, idempotentHint, openWorldHint.

## `settings_set`

Change settings: channels, ntfy server, e-mail backend and Faustus folder, language, model, pause. Ajustes.

Keys: notify.via auto|hub|own, mail.source auto|hub|faustus, notify.<channel>.enabled|min_severity, notify.ntfy.server, notify.email.backend|faustus_dir|faustus_owner, notify.language, llm.enabled, scheduler.paused, mail.deals.enabled|interval_min|history_days|ttl_days|stores|domains|gamerhoard_file|wishlist, mail.noise.days.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `values` (object) | yes | notify.<channel>.enabled 1\|0, notify.<channel>.min_severity low\|medium\|high, notify.ntfy.server, notify.language es\|en, llm.enabled 1\|0, scheduler.paused 0\|1 |

## `secret_set`

Save a write-only secret (Telegram, ntfy, SMTP, Brave key, SearXNG URL). Guardar credencial.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `name` (string) | yes | TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, NTFY_TOPIC, NTFY_TOKEN, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, SMTP_TO, BRAVE_KEY, SEARXNG_URL |
| `value` (string) | no | Empty removes it. Write-only: the value is never returned. |

## `scheduler_status`

Scheduler queue and per-host fetch state (blocks, cooldowns, preferred tier). Estado del planificador.

Annotations: readOnlyHint, idempotentHint.

## `runs_list`

Recent runs (checks, sweeps, discovery) with their summaries. Ejecuciones recientes.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/null) | no |  |
| `limit` (integer) | no |  |

## `config_export`

Export every watcher and target as data (declarative config). Exportar configuración.

Annotations: readOnlyHint, idempotentHint.

## `config_import`

Import watchers and targets from config_export data (updates by name). Importar configuración.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `data` (object) | yes | The object config_export returns: {tantalus: 1, watchers: [...]} |

## `mail_deals`

Sale mails of game and book stores found in the mailbox, with wishlist matches. Ofertas del correo.

Read from the Faustus mailbox (read-only), parsed into deals: store, title, discount, price, end date, link, and whether it matches a wishlist (game library, own list, the store's wishlist mails) or a watcher. Only matches notify. Sinónimos: ofertas de Steam, GOG, libros, rebajas del correo, lista de deseados.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `status` (active \| expired \| dismissed \| all) | no |  |
| `matched_only` (boolean) | no | Only deals that match a wishlist or a watcher. |
| `store` (string) | no | Store id: steam, gog, game, epic, bibliostock, casadellibro, fnac... |
| `query` (string) | no | Text in the title, the item titles or the subject. |
| `limit` (integer) | no |  |

## `mail_deals_scan`

Read new sale mails now (read-only) and match them to wishlists. Escanear ofertas del correo.

The first scan is quiet (no alerts); later matches notify once. rematch_only re-checks stored deals; rebuild re-reads everything. Sinónimos: buscar ofertas en el correo, actualizar ofertas, revisar correo de tiendas.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `days` (integer/null) | no | How far back to read (default: setting mail.deals.history_days). |
| `rematch_only` (boolean) | no | Do not read the mailbox: only check the stored deals against the lists again. |
| `rebuild` (boolean) | no | Forget the stored deals and read the mailbox again with the current parser (quiet, no alerts). |

## `mail_noise_report`

Which sender domains flood the mailbox with promotions, and who reads them. Informe de ruido del correo.

Read-only: counts per domain, share, Gmail category, unsubscribe link or address (never opened), last mail, and whether a Hoard uses that sender. Nothing is unsubscribed, moved or deleted. Sinónimos: spam, newsletters, darse de baja, promociones, limpieza del correo.

Annotations: readOnlyHint, idempotentHint, openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `days` (integer/null) | no | Window in days (default: setting mail.noise.days). |
| `top` (integer) | no |  |
| `refresh` (boolean) | no | Ignore the ten-minute cache and read the headers again. |

## `mail_deal_set`

Dismiss a mail deal or bring it back. Descartar o restaurar una oferta del correo.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `deal_id` (integer) | yes |  |
| `status` (active \| dismissed) | yes |  |

## `radar_status`

Aggregator radar: shops with stock now per chain (Carrefour, GAME, El Corte Inglés...). Radar de tiendas.

Per chain, the matching products it has in stock or on pre-order and the ones that sold out recently, read from stock aggregators (stocktcg.net / stocktcg.es) because Carrefour and others block direct reading. Sinónimos: stock en Carrefour, qué hay en GAME, tiendas.

Annotations: readOnlyHint, idempotentHint.

## `radar_run`

Read the stock aggregators now: feeds, chain pages, release pages, product pages. Comprobar radar ahora.

Raises RESTOCK / PREORDER_OPEN per shop and RELEASE for matching releases. Sinónimos: actualizar stock, mirar tiendas ahora.

Annotations: openWorldHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | no | Only this watcher; empty = every watcher with the radar on. |

## `releases_list`

Upcoming releases for the watchers: date, where to buy, where sold out. Calendario de lanzamientos.

Date, products, chains (in stock / pre-order / sold out with price) and the cheapest shops with stock or pre-order. Sinónimos: qué sale hoy, próximos lanzamientos, oleada, preventas, dónde comprar.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | no |  |
| `upcoming_days` (integer) | no |  |
| `past_days` (integer) | no |  |

## `radar_offers`

Shop offers seen on the stock aggregators, filterable by shop, state and product. Ofertas por tienda.

Sinónimos: quién tiene stock, precio por tienda, dónde hay, agotado en.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | no |  |
| `store` (string) | no | Shop slug or name: carrefour, game, el-corte-ingles, amazon, alcampo, toys-r-us... |
| `buyable` (boolean/null) | no | true = in stock or pre-order now; false = sold out. |
| `chains_only` (boolean) | no |  |
| `product_key` (string) | no | Aggregator product slug, e.g. 30th-anniversary--etb. |
| `limit` (integer) | no |  |

## `watchers_match_purchase`

Which watchers were waiting for this purchase? Score 0..1 by title, shop and EAN/ASIN. ¿Ya lo he comprado?

Fuzzy match of a purchase (title, merchant, URL) against active watchers: shared product words, same EAN, ASIN or product page. Read-only. Sinónimos: compré esto, vigilante de este producto, coincide con una compra.

Annotations: readOnlyHint, idempotentHint.

| Argument | Required | Description |
|---|---|---|
| `title` (string) | yes | What was bought (the order's item title). |
| `merchant` (string) | no |  |
| `url` (string) | no | Product URL of the purchase when known (EAN, ASIN or product id are matched). |
| `min_score` (number) | no | Hide matches below this score. |

## `watcher_mark_bought`

Mark a watcher as bought: stops its checks, keeps the history. Ya lo he comprado, dejar de vigilar.

Status bought, emits tantalus.watcher.bought {watcher_id, purchase_ref}. watcher_update enabled=true watches it again. Sinónimos: comprado, dejar de vigilar, archivar vigilante.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string/integer) | yes | Watcher id (from watcher_list or watchers_match_purchase). |
| `purchase_ref` (string) | no | hoard:// reference of the purchase, kept on the watcher. |

## `watcher_add`

Watch a product for a price or a restock from just a name (and URL). Vigilar algo, idea de regalo.

Creates an availability watcher with the name's words, an optional product page and price limit (budget), idempotent by source_ref. Sinónimos: vigila esto, avísame cuando baje de precio, regalo, lista de deseos.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `name` (string) | no | What to watch for (product name). title or text work as aliases. |
| `title` (string) | no |  |
| `text` (string) | no |  |
| `url` (string) | no | Optional product page to watch right away. |
| `budget` (number/null) | no | Alert when the price falls to or below this. max_price is an alias. |
| `max_price` (number/null) | no |  |
| `source_ref` (string) | no | hoard:// reference of where the idea came from (a gift idea); same ref = same watcher. |
| `budget_category` (string) | no |  |
| `notes` (string) | no |  |
| `interval_min` (integer) | no |  |

## `radar_setup`

Turn the aggregator radar on or off for a watcher and choose chains, languages, sources. Configurar radar.

Sinónimos: activar radar, avisar de Carrefour, cadenas, idiomas de edición.

Annotations: none.

| Argument | Required | Description |
|---|---|---|
| `watcher_id` (string) | yes |  |
| `enabled` (boolean/null) | no |  |
| `sources` (array/null) | no |  |
| `chains` (array/null) | no | Shop slugs that always alert (game, carrefour, el-corte-ingles, alcampo, amazon, toys-r-us, toy-planet...). |
| `languages` (array/null) | no | Edition languages that may alert, e.g. [ES, EN]. Empty = any. |
| `alert_other_shops` (boolean/null) | no | Small shops alert too, only at a sane price (cheapest chain price or MSRP × multiplier). |
| `price_multiplier` (number/null) | no |  |
| `release_days_before` (integer/null) | no |  |

## REST routes for the UI

- `GET /api/health` (the shared probe: `service`, `version`, `dataDirConfigured`, `offline`, `counts`, `scheduler`, `hoard_link`), `GET /api/status`
- `GET /api/agent/tools` (catalogue, no token) and `POST /api/agent/call` (Bearer token) are the MCP bridge's routes. Every error is JSON `{error, code?, hint?, details?, issues?}`: 400 `invalid_arguments` for bad arguments, 401 for a wrong token, 404 `unknown_tool`/`not_found`, and the app's own codes (`fetch_failed` 502, `blocked`/`needs_human`/`robots_disallowed` 409, `rate_limited` 429, `unsafe_url` 400...). An unknown `/api/*` path is a JSON 404.
- `GET /api/dashboard` — news (unseen confirmed events), buyable now, needs human, watchers, top listings, material news, proposed URLs, recent events, scheduler.
- `POST /api/dashboard/visit` — marks everything seen and records the visit.
- `POST /api/ui/call` `{name, arguments}` — any tool above, uncapped.
- `GET /api/targets/{id}/history?limit=300` — observations and price history.
