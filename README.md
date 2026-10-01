# Tantalus's Hoard

[![CI](https://github.com/Luissalet/TantalusHoard/actions/workflows/ci.yml/badge.svg)](https://github.com/Luissalet/TantalusHoard/actions/workflows/ci.yml)

[Español](README.es.md)

<img src="app-icon.png" alt="" width="96" align="right">

Tantalus's Hoard is a local product watcher. It tells you when a product comes back in stock, when its price falls or crosses your limit, when pre-orders open or a new SKU appears at a retailer. It also finds second-hand bargains on Wallapop or Facebook Marketplace and flags material news from official pages, feeds and web searches. The dashboard opens on what is new since your last visit. Alerts go out as Windows notifications, family bus events, ntfy pushes to your phone, Telegram messages or email. Every function is also an MCP tool for assistants.

It also reads the sale mails that game stores and book retailers send to your mailbox (through the account configured in Faustus), keeps them as "mail deals", and alerts you only when one matches a wishlist or a watcher. A read-only report shows which senders fill the mailbox with promotions.

Everything runs on your computer: the SQLite store, the scheduler and the browser profile. Nothing leaves the machine except the page requests themselves and the notifications you enable.

## What it watches

| Mode | What it does | Example |
|---|---|---|
| **availability** | Product pages and retailer search pages. It records a stock state (in stock, local pickup, pre-order, restock scheduled, sold out, marketplace only, unknown), price, seller and evidence on every check. | Pokémon TCG 30th anniversary Elite Trainer Box at GAME, El Corte Inglés and xtralife; NVIDIA DGX Spark under 4,800 € |
| **secondhand** | Wallapop (public search API) and Facebook Marketplace (your own logged-in browser profile). Listings are scored by a pack of explainable signals. | Free books in bulk near Madrid (the former Radar de Libros); sealed ETB at no more than 1.3 × MSRP |
| **information** | Official pages (readable-text diff), RSS/Atom feeds, and news and web searches (Google News and Bing News RSS by default). It reports only material news and marks each item as confirmed, leak or estimate. | RTX Spark / N1X 128 GB systems in Europe |

## How a check decides

Discovery, verification, change detection and notification are separate steps:

1. **Fetch ladder.** Plain HTTP comes first, with robots.txt, a per-host minimum interval, conditional requests and an SSRF guard. A headless browser follows only when the page is a JavaScript shell or the HTTP answer is blocked. The browser is Edge on Windows, with a persistent profile. When a site shows a CAPTCHA or asks for a login, the target is marked **needs human**. The "Resolve" button opens a visible window where you pass the check yourself. Tantalus never solves or bypasses anything.
2. **Extraction.** Site adapters come first (the NVIDIA product API, El Corte Inglés embedded state). Then JSON-LD `Product`/`Offer`, microdata and OpenGraph, then ES/EN phrase rules on buttons and short lines. A local model is optional and only accepted with literal evidence. Search pages yield one offer per product tile, and matching new products become targets of their own.
3. **Policy.** The seller policy is one of `retail_only`, `retail_plus_marketplace` or `any_below`. The anti-scalper ceiling is MSRP × multiplier, and a price drop above the ceiling is never called an offer.
4. **Confidence (0–100).**
   - Points added: official page +45, active buy control or positive stock endpoint +30 (structured availability also counts +30), stock at a target store +20, coherent price and SKU +10, second confirmation +15.
   - Points removed: search snippet only −25, third-party seller −35, CAPTCHA or login −20, contradictions −20, SKU mismatch −20.
   - At 75 or more the event alerts. From 55 to 74 it is revalidated first. Below 55 it is only logged.
5. **Events.** They fire only on useful transitions: `RESTOCK`, `LOCAL_RESTOCK`, `PREORDER_OPEN`, `PRICE_DROP`, `PRICE_THRESHOLD_CROSSED`, `NEW_SKU`, `RESTOCK_DATE_CONFIRMED` and `SOLD_OUT`. Second-hand and news events are `NEW_LISTING`, `LISTING_PRICE_DROP`, `INFO_CHANGE` and `CANDIDATE_FOUND`. A sale mail that matches a wishlist or a watcher is `MAIL_DEAL`.
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
| Family bus | None. Emits `tantalus.alert` and `tantalus.event.*` to Hoard Hub. The hub's recommended rule `rule-watcher-alert-digest` turns each alert into a `digest.item` for the daily recap. |
| ntfy | Pick a long random topic, subscribe to it in the ntfy app, and save it in Settings (`NTFY_TOPIC`). |
| Telegram | Create a bot with @BotFather, save the token, write `/start` to the bot, then press "Find chat id". |
| Email | None when Faustus has a mail account: Tantalus sends through that account and the password stays in Faustus. Alerts go to the account itself unless you set recipients. Otherwise give SMTP host, port, user, app password, from and to (Gmail needs an app password). The **Send with** setting picks `auto`, `faustus` or `smtp`. |

Secrets live in `.env` (`TANTALUS_TELEGRAM_TOKEN=…`) or are saved write-only from Settings. They are never returned by the API. Each channel has an on/off switch and a minimum severity.

## Ready-made watchers

The first start installs seven watchers:

- Pokémon TCG 30th anniversary ETB and Booster Bundle: search pages at GAME, El Corte Inglés and xtralife, plus discovery queries.
- DGX Spark in Spain: the NVIDIA marketplace and the NVIDIA product API, with a 4,800 € threshold, plus a news watcher for Spanish sale announcements, because the marketplace blocks automated reads.
- RTX Spark / N1X 128 GB in Europe: an information watcher.
- News about Pokémon 30th anniversary restocks announced by shops and chains (secondary confirmation).
- The free-books radar.
- A disabled sealed-ETB second-hand watcher.

Edit, disable or delete any of them. `config_export` and `config_import` move the whole setup as data.

## Mail deals and the noise report

The **Correo** page has two tabs, **Ofertas** and **Ruido**. Both read the mailbox through the account configured in Faustus: Tantalus runs a small reader (`tantalus_hoard/mail/faustus_reader.py`) under Faustus's own Python, so the mail password never reaches Tantalus. The reader opens folders read-only and fetches with `BODY.PEEK`. There is no code anywhere in Tantalus that sends, moves, flags, labels, archives, unsubscribes or deletes a message, a test checks that the reader has none, and no mail link is ever fetched.

- **Deals.** Every few hours (`mail.deals.interval_min`, default 180) the scheduler reads new mails from the stores (Steam, GOG, GAME, Epic, Humble, Fanatical, Green Man Gaming, Instant Gaming, xtralife, Bibliostock, Casa del Libro, Agapea, Planeta de Libros, Book Depository, Fnac, plus any sender domain you add). A parser turns each sale mail into rows: store, title, discount (or "up to" for ranges), old and new price, end date, link. A store that mails "an item of your wishlist is on sale" gives one row per item; any other sale mail gives one campaign row with the item titles from its images. A deal ends at the sale's end date, or `mail.deals.ttl_days` (7) after the mail when it states none.
- **Who it alerts.** A deal is checked against: the game library of the sibling game-collection app (backlog games owned on no platform, or tagged `wishlist`; owned games never alert), your own list in Settings (`mail.deals.wishlist`), the store's own wishlist mails, and your enabled watchers (same product terms). Only a match raises a `MAIL_DEAL` event, once, and only if the mail is at most three days old. The first scan is quiet: it fills the table and sends nothing.
- **Noise report.** Per sender domain over the last `mail.noise.days` (30): mail count and share, Gmail category, whether an unsubscribe link or address exists (shown as text, never opened), the last mail, and which Hoard reads that sender (Ledger for payments, Phileas for shipments, Kafka for paperwork, JobHunter for jobs, Tantalus for store deals). Promotional senders that no Hoard reads are listed first as the clean-up candidates. It changes nothing.

Settings: `mail.deals.enabled`, `mail.deals.interval_min`, `mail.deals.history_days`, `mail.deals.ttl_days`, `mail.deals.stores`, `mail.deals.domains`, `mail.deals.gamerhoard_file`, `mail.deals.wishlist`, `mail.noise.days`.

## Run

```sh
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m tantalus_hoard        # http://127.0.0.1:5197
```

The built UI is committed. `npm install && npm run build` rebuilds it after client changes. Hoard Hub starts the app from `faustus-plugin.json`. The headless browser uses Edge through Playwright, so no browser download is needed on Windows. Elsewhere, run `python -m playwright install chromium` once.

Environment: `TANTALUS_PORT` (5197), `TANTALUS_DATA_DIR`, `TANTALUS_SCHEDULER=0` (no background checks), `TANTALUS_BROWSER=0` (HTTP only), `TANTALUS_OFFLINE=1`, `TANTALUS_SEARXNG_URL`, `TANTALUS_BRAVE_KEY`, `TANTALUS_FAUSTUS_DIR` (the Faustus folder for e-mail, when it is not next to this app).

## Assistants (MCP)

`python mcp_server.py` is a stdio bridge. It never opens the database: it proxies to the running app and starts it when needed. There are 48 tools. Start with `tantalus_overview`. For one-off questions use `inspect_url`, `secondhand_search` and `web_search`. To set up watching use `watcher_create`, `target_add`, `discovery_run` and `candidate_accept`. [docs/API.md](docs/API.md) lists every tool:

`tantalus_overview`, `tantalus_status`, `watcher_list`, `watcher_get`, `watcher_create`, `watcher_update`, `watcher_delete`, `watcher_run`, `watcher_rescore`, `target_add`, `target_list`, `target_get`, `target_update`, `target_delete`, `target_check`, `target_resolve`, `inspect_url`, `events_list`, `events_mark_seen`, `event_dismiss`, `event_notify`, `listings_list`, `listing_set`, `info_items_list`, `info_item_set`, `candidates_list`, `candidate_accept`, `candidate_reject`, `discovery_run`, `web_search`, `secondhand_search`, `secondhand_facebook_login`, `packs_list`, `presets_list`, `presets_install`, `notify_status`, `notify_test`, `telegram_find_chat_id`, `settings_set`, `secret_set`, `scheduler_status`, `runs_list`, `config_export`, `config_import`, `mail_deals`, `mail_deals_scan`, `mail_noise_report`, `mail_deal_set`.

Page text, titles, snippets and mail subjects are third-party data. Tool results say so, and the optional model receives them wrapped as untrusted content.

## Limits

- **Blocked sites.** On 30-09-2026 these blocked automated reads in both tiers: Carrefour, PcComponentes, Fnac, Toys R Us, Cardmarket, eBay and the NVIDIA marketplace pages. Such targets stay in "needs human" and are retried every three hours.
- **Local stock.** Stock at a specific store is only read when the retailer shows it on the page. The store list of a watcher is otherwise a preference.
- **DGX Spark.** The NVIDIA product API does not list DGX Spark today, so that target stays "unknown" until it does.
- **Web search.** Keyless web search is unreliable from a script: DuckDuckGo's HTML endpoint answers a bot check after a few queries and Bing degrades long queries. News searches use the Google News and Bing News RSS feeds, which work well. For products, watching the retailers' own search pages is the dependable way to discover new SKUs. A SearXNG instance or a Brave key adds proper web engines.
- **Mail deals.** They need Faustus with a mail account. Parsing is by rules on the mails' text: a store that changes its mail layout can yield a campaign row instead of per-item rows. The book-collection app is a cloud database with no local file, so book wishlists come only from your own list in Settings. The game library file is read from `mail.deals.gamerhoard_file`, `GAMERHOARD_DATA_FILE` or `~/.gamerhoard/library.json`; without it only your own list, the store wishlist mails and the watchers match.
- **Facebook.** Facebook's terms forbid automated access. The Marketplace source is off by default and uses your own session in the app's browser profile.

## Tests

```sh
python -m pytest -q
```

## License

MIT, © Luis María Salete Cuartero.
