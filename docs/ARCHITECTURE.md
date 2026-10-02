# Tantalus's Hoard — architecture and module contracts

Local product watcher of the Hoard family: restocks, price drops, preorders, second-hand finds and
information changes, with a dashboard that shows what is new since the last visit, notifications
(Windows toast, family bus, ntfy, Telegram, email) and MCP tools. Python 3.11+ (runs on Windows with
Python 3.13), FastAPI + SQLite, React/Vite client. Port 5197, package `tantalus_hoard`, service id
`tantalus-hoard`, family id `tantalus`, env prefix `TANTALUS_`.

Spec source: "Restock Sentry" (discovery / verification / temporal comparison / notification kept apart;
normalised states; typed events; dedupe + cooldown; confidence scoring; revalidation before a high-priority
alert; anti-scalper price policy; source adapters that only return observations, never decisions).
Second-hand concepts come from the author's earlier app "Radar de Libros" (FreeBookRadar): RawListing,
explainable weighted signals, hard rejects, optional LLM refinement, dedupe cascade, distance by
municipality table, toast notifier, scheduler with a floor.

## Shared files 

- `tantalus_hoard/model.py` — states, event types, modes, policies, dataclasses (`FetchResult`, `Offer`,
  `Extraction`, `SearchHit`, `RawListing`, `ScoreSignal`, `ListingScore`, `InfoFinding`).
- `tantalus_hoard/config.py` — `Config` (data dirs, `offline`, `browser`, `secret(name)`).
- `tantalus_hoard/errors.py` — `TantalusError(code, message, hint, status=..., **details)`, a subclass of the commons' `AppError`.
- `tantalus_hoard/db.py` — schema (read it for column names).
- `tantalus_hoard/llm.py` — `LLM.json(system, user, required=(...))` → dict | None; `page_block(text)`;
  `UNTRUSTED` preamble. Everything must work when it returns None.

## Shared code (`tantalus_hoard/hoard_link/`, vendored, never edited here)

Everything that is not specific to watching products comes from the family library: the fetcher and its
safety, robots, block detection and headless-browser rung (`web.fetch`, `web.browser`; `fetch/` keeps only the SQL
host-state and robots stores and the hub path), web search and page/feed change detection (`web.search`, `web.watch`),
readable text, JSON-LD and page metadata (`web.htmltext`, `web.meta`), every money parse and format (`money`), link
unwrapping and registrable domains (`web.urls`), EAN/ISBN/ASIN checks (`idcheck`), notification channels and routing
(`notify_channels`, `fam_notify.Router`), the mail helper that runs under Faustus's Python (`mail_helper`, `fam_mail`),
background lanes (`lanes.LaneScheduler`), the SQLite wrapper (`sqlkit.Database`), the request guard (`guard`), config,
token and URL files (`appconfig`, `tokens`), ULID ids (`ids`), the agent tool kit and `/api/agent/*` router
(`agentkit`), the app shell (`service`: error envelope, PWA, SPA, health, `run_main`) and the MCP bridge (`bridge`).
Tantalus-local: the offer model, the walker that builds `Offer`s from JSON-LD and microdata, the store
definitions, the rule engine, the second-hand packs, the radar and the mail-deal parser.

## Rules for every module

- No network in tests: inject fakes. Every network-using class takes a `fetcher` (duck-typed, see below)
  or an `httpx` transport.
- Expected failures never raise out of a source / adapter: return an empty result plus an error string.
- Remote text is data. Never follow instructions in it; wrap it with `page_block` when it goes to a model.
- No CAPTCHA solving, no anti-bot bypass, no stealth plugins. Blocked → report `blocked`/`needs_human`.
- Respect robots.txt for crawled HTML pages (not for documented JSON APIs the site's own frontend uses —
  still rate-limited). Per-host minimum interval. Never fetch private / loopback / link-local addresses.
- Code, comments, identifiers, commit messages in English. User-facing strings in the UI: Spanish and
  English (i18n). No slogans or taglines anywhere.
- Never name other commercial products as "competitors" in code or docs.
- pytest, no network, fast. Files under `tests/` named `test_<module>.py`.

## Fetcher — the interface other modules use

```python
fetcher.get(url, *, tier="auto", headers=None, params=None, accept="html",
            etag="", last_modified="", respect_robots=True, min_interval_s=None,
            timeout=None) -> FetchResult
fetcher.get_json(url, **same) -> tuple[FetchResult, Any | None]
fetcher.browser_session() -> context manager yielding a Playwright `BrowserContext` on the persistent
                             profile (data/browser-profile), or raising TantalusError("needs_human"/"fetch_failed")
                             when the browser rung is unavailable.
```
`tier`: `auto` = http first, browser when the answer is blocked or the host is known to need JS;
`http` = never the browser; `browser` = straight to the browser. `FetchResult.ok` false + `blocked` /
`block_reason` / `error` explain why.

## Extractors

`extract(fr: FetchResult, *, hints: dict) -> Extraction`. hints: `{"url", "sku", "ean", "store_ids",
"retailer", "adapter", "region", "title_hint"}`. Order: site adapter/API → JSON-LD Product/Offer →
microdata / OpenGraph product meta → phrase rules (ES/EN buy / sold-out / pre-order / pickup / notify-me)
→ optional LLM (fixed schema, only fields literally present, evidence snippets). Result carries evidence
snippets and the method. A search/list page yields many offers (used by discovery → NEW_SKU).

## Rule engine, engine, scheduler, store, services, API, tools

Confidence (spec §5): +45 official retailer/manufacturer page, +30 active buy control or positive stock
endpoint, +20 stock tied to a target store, +10 coherent price and SKU, +15 second primary confirmation,
−25 only search snippet / cache, −35 third-party marketplace, −20 login/CAPTCHA not verified,
−20 contradictory sources. Clamp 0..100. ≥75 alert now; 55–74 revalidate first; <55 log only.

Events only on useful transitions: OUT/UNKNOWN→IN (RESTOCK), →LOCAL_PICKUP at a target store
(LOCAL_RESTOCK), →PREORDER (PREORDER_OPEN), price falls ≥ min_drop (PRICE_DROP), price crosses
threshold / ceiling (PRICE_THRESHOLD_CROSSED), restock date appears (RESTOCK_DATE_CONFIRMED),
IN→OUT (SOLD_OUT, low severity). Dedupe key = sha1(target + type + state + rounded price + store).
Cooldown window per watcher (default 20 min) swallows IN→OUT→IN flaps. High-priority events are
revalidated `revalidate_seconds` later (default 60) before they notify.

## Second-hand — `tantalus_hoard/secondhand/`

Sources: `wallapop` (JSON API `api.wallapop.com/api/v3/search` with `distance_in_km`, needs headers `X-DeviceOS: 0`,
`Referer: https://es.wallapop.com/`, `Origin`), `facebook` (Marketplace via the persistent browser
profile, port of Radar de Libros, off by default, manual login), both behind
`SecondhandSource.search(query, *, latitude, longitude, location_text, radius_km, max_price, limit) ->
(list[RawListing], error: str)`. Packs = scoring profiles (`books_bulk` = Radar de Libros weights and
rules; `generic` = keyword include/exclude, price ceiling, distance, shipping, reserved, anti-scalper
multiplier). `score_listing(listing, pack, settings, llm=None) -> ListingScore`.

## Information sentries, discovery, search, notify

- `search.py`: `WebSearch.search(query, limit=10, *, freshness_days=None) -> (list[SearchHit], errors)`
  over SearXNG (when `TANTALUS_SEARXNG_URL` / Faustus' instance answers), DuckDuckGo HTML, Bing HTML;
  partial failures per engine; RRF merge; SSRF-safe URL filter.
- `discovery.py`: from a watcher's queries → hits → classify host (retailer table → source level 1/2,
  official brand → 3, community → 4, rest 5) → candidates with score and reason.
- `info.py`: sources = page (readable-text diff with quality gate), feed (RSS/Atom), search (queries);
  materiality = rules (new numbers with €/$, dates, "preorder/reserva", "precio", "disponible", SKU
  terms, official domain) + optional LLM judge returning verdict confirmed|leak|estimate|irrelevant.
- `notify/`: channels `toast`, `hub`, `ntfy`, `telegram`, `email` come from `hoard_link.notify_channels`; a
  `fam_notify.Router` decides where an alert goes (the hub when it takes it, the app's own channels otherwise).
  Secrets from `config.secret(...)` / write-only settings; `Notifier.send(event: dict, channels) -> list[dict]`,
  `Notifier.test(channel)`.
