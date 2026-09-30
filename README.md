# Tantalus's Hoard

[Español](README.es.md)

<img src="app-icon.png" alt="" width="96" align="right">

Tantalus's Hoard is a local product watcher. It tells you when a product comes back in stock, when its price falls or crosses your limit, when pre-orders open or a new SKU appears at a retailer. It also finds second-hand bargains on Wallapop or Facebook Marketplace and flags material news from official pages, feeds and web searches. The dashboard opens on what is new since your last visit. Alerts go out as Windows notifications, family bus events, ntfy pushes to your phone, Telegram messages or email. Every function is also an MCP tool for assistants.

Everything runs on your computer: the SQLite store, the scheduler and the browser profile. Nothing leaves the machine except the page requests themselves and the notifications you enable.

## What it watches

| Mode | What it does | Example |
|---|---|---|
| **availability** | Product pages and retailer search pages. It records a stock state (in stock, local pickup, pre-order, restock scheduled, sold out, marketplace only, unknown), price, seller and evidence on every check. | Pokémon TCG 30th anniversary Elite Trainer Box at GAME, El Corte Inglés and xtralife; NVIDIA DGX Spark under 4,800 € |
| **secondhand** | Wallapop (public search API) and Facebook Marketplace (your own logged-in browser profile). Listings are scored by a pack of explainable signals. | Free books in bulk near Madrid (the former Radar de Libros); sealed ETB at no more than 1.3 × MSRP |
| **information** | Official pages (readable-text diff), RSS/Atom feeds and web searches. It reports only material news and marks each item as confirmed, leak or estimate. | RTX Spark / N1X 128 GB systems in Europe |

## How a check decides

Discovery, verification, change detection and notification are separate steps:

1. **Fetch ladder.** Plain HTTP comes first, with robots.txt, a per-host minimum interval, conditional requests and an SSRF guard. A headless browser follows only when the page is a JavaScript shell or the HTTP answer is blocked. The browser is Edge on Windows, with a persistent profile. When a site shows a CAPTCHA or asks for a login, the target is marked **needs human**. The "Resolve" button opens a visible window where you pass the check yourself. Tantalus never solves or bypasses anything.
2. **Extraction.** Site adapters come first (the NVIDIA product API, El Corte Inglés embedded state). Then JSON-LD `Product`/`Offer`, microdata and OpenGraph, then ES/EN phrase rules on buttons and short lines. A local model is optional and only accepted with literal evidence. Search pages yield one offer per product tile, and matching new products become targets of their own.
3. **Policy.** The seller policy is one of `retail_only`, `retail_plus_marketplace` or `any_below`. The anti-scalper ceiling is MSRP × multiplier, and a price drop above the ceiling is never called an offer.
4. **Confidence (0–100).**
   - Points added: official page +45, active buy control or positive stock endpoint +30 (structured availability also counts +30), stock at a target store +20, coherent price and SKU +10, second confirmation +15.
   - Points removed: search snippet only −25, third-party seller −35, CAPTCHA or login −20, contradictions −20, SKU mismatch −20.
   - At 75 or more the event alerts. From 55 to 74 it is revalidated first. Below 55 it is only logged.
5. **Events.** They fire only on useful transitions: `RESTOCK`, `LOCAL_RESTOCK`, `PREORDER_OPEN`, `PRICE_DROP`, `PRICE_THRESHOLD_CROSSED`, `NEW_SKU`, `RESTOCK_DATE_CONFIRMED` and `SOLD_OUT`. Second-hand and news events are `NEW_LISTING`, `LISTING_PRICE_DROP`, `INFO_CHANGE` and `CANDIDATE_FOUND`.
   - Each event has a dedupe key (target, type, state, rounded price, store) and a cooldown, which absorbs IN→OUT→IN flaps.
   - High-priority events are checked again 60 s later before anything is sent.
6. **Notification.** Each event is sent once per channel.

## Second-hand packs

- `books_bulk`: the Radar de Libros rules, unchanged. It weighs free price, "getting rid of it", whole libraries, lots, boxes and moving house, and penalises textbooks, e-books, single sales and shipping-only offers. It also estimates the number of books and the price per book, and scores distance by municipality.
- `generic`: include and exclude keywords, price ceiling, MSRP and scalper multiplier, distance, shipping, reserved listings and recency.
- `collectibles_sealed`: the generic rules tuned for sealed trading-card products. It rewards sealed and new, and rejects empty boxes, proxies, replicas, opened items and accessories.

Every point shown in the UI comes from a named signal. An optional local model only refines borderline scores.

## Notifications

| Channel | Setup |
|---|---|
| Windows toast | None. Uses `winotify` when installed, otherwise PowerShell. |
| Family bus | None. Emits `tantalus.alert` and `tantalus.event.*` to Hoard Hub. |
| ntfy | Pick a long random topic, subscribe to it in the ntfy app, and save it in Settings (`NTFY_TOPIC`). |
| Telegram | Create a bot with @BotFather, save the token, write `/start` to the bot, then press "Find chat id". |
| Email | SMTP host, port, user, app password, from and to. Gmail needs an app password. |

Secrets live in `.env` (`TANTALUS_TELEGRAM_TOKEN=…`) or are saved write-only from Settings. They are never returned by the API. Each channel has an on/off switch and a minimum severity.

## Ready-made watchers

The first start installs five watchers:

- Pokémon TCG 30th anniversary ETB and Booster Bundle: search pages at GAME, El Corte Inglés and xtralife, plus discovery queries.
- DGX Spark in Spain: the NVIDIA marketplace and the NVIDIA product API, with a 4,800 € threshold.
- RTX Spark / N1X 128 GB in Europe: an information watcher.
- The free-books radar.
- A disabled sealed-ETB second-hand watcher.

Edit, disable or delete any of them. `config_export` and `config_import` move the whole setup as data.

## Run

```sh
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m tantalus_hoard        # http://127.0.0.1:5197
```

The built UI is committed. `npm install && npm run build` rebuilds it after client changes. Hoard Hub starts the app from `faustus-plugin.json`. The headless browser uses Edge through Playwright, so no browser download is needed on Windows. Elsewhere, run `python -m playwright install chromium` once.

Environment: `TANTALUS_PORT` (5197), `TANTALUS_DATA_DIR`, `TANTALUS_SCHEDULER=0` (no background checks), `TANTALUS_BROWSER=0` (HTTP only), `TANTALUS_OFFLINE=1`, `TANTALUS_SEARXNG_URL`, `TANTALUS_BRAVE_KEY`.

## Assistants (MCP)

`python mcp_server.py` is a stdio bridge. It never opens the database: it proxies to the running app and starts it when needed. There are 43 tools. Start with `tantalus_overview`. For one-off questions use `inspect_url`, `secondhand_search` and `web_search`. To set up watching use `watcher_create`, `target_add`, `discovery_run` and `candidate_accept`. [docs/API.md](docs/API.md) lists every tool:

`tantalus_overview`, `tantalus_status`, `watcher_list`, `watcher_get`, `watcher_create`, `watcher_update`, `watcher_delete`, `watcher_run`, `target_add`, `target_list`, `target_get`, `target_update`, `target_delete`, `target_check`, `target_resolve`, `inspect_url`, `events_list`, `events_mark_seen`, `event_dismiss`, `event_notify`, `listings_list`, `listing_set`, `info_items_list`, `info_item_set`, `candidates_list`, `candidate_accept`, `candidate_reject`, `discovery_run`, `web_search`, `secondhand_search`, `secondhand_facebook_login`, `packs_list`, `presets_list`, `presets_install`, `notify_status`, `notify_test`, `telegram_find_chat_id`, `settings_set`, `secret_set`, `scheduler_status`, `runs_list`, `config_export`, `config_import`.

Page text, titles and snippets are third-party data. Tool results say so, and the optional model receives them wrapped as untrusted content.

## Limits

- **Blocked sites.** On 30-09-2026 these blocked automated reads in both tiers: Carrefour, PcComponentes, Fnac, Toys R Us, Cardmarket, eBay and the NVIDIA marketplace pages. Such targets stay in "needs human" and are retried every three hours.
- **Local stock.** Stock at a specific store is only read when the retailer shows it on the page. The store list of a watcher is otherwise a preference.
- **DGX Spark.** The NVIDIA product API does not list DGX Spark today, so that target stays "unknown" until it does.
- **DuckDuckGo.** Its HTML endpoint rate-limits after a few queries. Discovery runs at most every few hours and falls back to Bing, and a SearXNG instance or a Brave key adds engines.
- **Facebook.** Facebook's terms forbid automated access. The Marketplace source is off by default and uses your own session in the app's browser profile.

## Tests

```sh
python -m pytest -q
```

## License

MIT, © Luis María Salete Cuartero.
