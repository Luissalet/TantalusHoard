# Tantalus's Hoard

[![CI](https://github.com/Luissalet/TantalusHoard/actions/workflows/ci.yml/badge.svg)](https://github.com/Luissalet/TantalusHoard/actions/workflows/ci.yml)

[English](README.md)

<img src="app-icon.png" alt="" width="96" align="right">

Tantalus's Hoard es un vigilante de productos local. Te avisa cuando un producto vuelve a estar en stock, cuando baja de precio o cruza tu límite, cuando abre la reserva o aparece un SKU nuevo en una tienda. También encuentra gangas de segunda mano en Wallapop o Facebook Marketplace y detecta novedades relevantes en páginas oficiales, feeds y búsquedas web. El panel se abre con lo nuevo desde tu última visita. Los avisos llegan como notificación de Windows, evento del bus de la familia, push de ntfy al móvil, mensaje de Telegram o correo. Cada función es además una herramienta MCP para asistentes.

También lee los correos de oferta que las tiendas de juegos y librerías mandan a tu buzón (con la cuenta configurada en Faustus), los guarda como «ofertas del correo» y solo te avisa si coinciden con una lista de deseados o con un vigilante. Un informe de solo lectura muestra qué remitentes llenan el buzón de promociones.

Todo corre en tu ordenador: la base SQLite, el planificador y el perfil del navegador. No sale nada salvo las propias peticiones a las páginas y los avisos que actives.

Para productos de cartas sigue además el stock tienda a tienda y el calendario de lanzamientos que publican los agregadores públicos de stock (stocktcg.net y stocktcg.es). Así puede decir «hoy sale la oleada 2: agotada en Carrefour y GAME, en preventa en estas tiendas» y avisar cuando una cadena como Carrefour repone, aunque Carrefour no se deje leer directamente.

## Qué vigila

| Modo | Qué hace | Ejemplo |
|---|---|---|
| **availability** | Fichas de producto y búsquedas de tiendas. En cada comprobación guarda el estado de stock (en stock, recogida en tienda, reserva abierta, reposición anunciada, próximamente, agotado, solo vendedores externos, desconocido), el precio, el vendedor y las evidencias. | ETB del 30.º aniversario de Pokémon en GAME, El Corte Inglés y xtralife; NVIDIA DGX Spark por debajo de 4.800 € |
| **secondhand** | Wallapop (API pública de búsqueda) y Facebook Marketplace (tu propia sesión en el perfil del navegador). Cada anuncio se puntúa con un pack de señales explicables. | Libros gratis en lote cerca de Madrid (el antiguo Radar de Libros); ETB precintada a no más de 1,3 × PVP |
| **information** | Páginas oficiales (diff del texto legible), feeds RSS/Atom y búsquedas de noticias y web (por defecto, los RSS de Google News y Bing News). Solo avisa de novedad material y marca cada una como confirmada, filtración o estimación. | Equipos RTX Spark / N1X de 128 GB en Europa |

## Cómo decide una comprobación

Descubrimiento, verificación, detección de cambios y aviso van por separado:

1. **Escalera de lectura.**
   - Primero HTTP normal, con robots.txt, intervalo mínimo por dominio, peticiones condicionales y protección SSRF.
   - Solo si la página es un armazón de JavaScript o la respuesta está bloqueada pasa al navegador sin ventana: Edge en Windows, con perfil persistente.
   - Algunas tiendas rechazan los navegadores sin ventana pero atienden a un navegador normal (las fichas de Carrefour). Esas páginas se leen en una ventana normal de Edge que se abre minimizada para una página y se cierra, como mucho una vez por minuto en esa tienda (ajuste `browser.window`, activado por defecto).
   - Si el sitio pide CAPTCHA o login, el objetivo queda como **necesita tu ayuda**. El botón «Resolver» abre una ventana visible para que lo pases tú. Tantalus nunca resuelve, falsea ni evita nada: ni parches de sigilo, ni servicios de CAPTCHA, ni huellas inventadas.
2. **Extracción.**
   - Van primero los adaptadores de sitio (la API de productos de NVIDIA y el estado embebido de El Corte Inglés).
   - Después JSON-LD `Product`/`Offer`, microdatos y OpenGraph.
   - Después, reglas de frases en español e inglés sobre botones y líneas cortas.
   - Por último, un modelo local opcional, que solo se acepta si aporta evidencia literal.
   - Las búsquedas dan una oferta por cada ficha de producto, y los productos nuevos que encajan pasan a ser objetivos propios.
3. **Política.** La política de vendedor es una de `retail_only`, `retail_plus_marketplace` o `any_below`. El techo anti-reventa es PVP × multiplicador, y una bajada que sigue por encima del techo nunca se llama oferta.
4. **Confianza (0–100).**
   - Suman: página oficial +45; botón de compra activo o stock positivo en el endpoint +30 (la disponibilidad estructurada también cuenta +30); stock en una tienda objetivo +20; precio y SKU coherentes +10; segunda confirmación +15.
   - Restan: solo fragmento de buscador −25; vendedor externo −35; CAPTCHA o login −20; contradicciones −20; SKU que no coincide −20.
   - Con 75 o más, avisa. Entre 55 y 74, revalida antes. Por debajo de 55, solo registra.
5. **Eventos.** Solo en transiciones útiles: `RESTOCK`, `LOCAL_RESTOCK`, `PREORDER_OPEN`, `SALE_OPEN` (un producto que ponía «Próximamente» ya se puede comprar o reservar), `PRICE_DROP`, `PRICE_THRESHOLD_CROSSED`, `NEW_SKU`, `RESTOCK_DATE_CONFIRMED` y `SOLD_OUT`. Para segunda mano y noticias: `NEW_LISTING`, `LISTING_PRICE_DROP`, `INFO_CHANGE` y `CANDIDATE_FOUND`. Un correo de oferta que coincide con una lista de deseados o un vigilante es `MAIL_DEAL`. El radar de agregadores crea `RELEASE` para las fechas de lanzamiento.
   - Cada evento tiene clave de deduplicado (objetivo, tipo, estado, precio redondeado y tienda) y un enfriamiento que absorbe los vaivenes IN→OUT→IN.
   - Los avisos de prioridad alta se vuelven a comprobar 60 s después, antes de enviar nada.
6. **Aviso.** Cada evento sale una sola vez por canal.

## Packs de segunda mano

- `books_bulk`: las reglas de Radar de Libros, tal cual.
  - Da peso a precio gratis, «me deshago», bibliotecas completas, lotes, cajas y mudanzas.
  - Penaliza libros de texto, ebooks, ventas sueltas y ofertas que solo son de envío.
  - Estima el número de libros y el precio por libro, y puntúa la distancia por municipio.
- `generic`: palabras a incluir y excluir, precio máximo, PVP con multiplicador anti-reventa, distancia, envío, anuncios reservados y antigüedad.
- `collectibles_sealed`: el pack genérico ajustado a producto TCG precintado. Premia precintado y nuevo, y descarta cajas vacías, proxies, réplicas, producto abierto y accesorios.

Cada punto que muestra la interfaz viene de una señal con nombre. Un modelo local opcional solo afina las puntuaciones dudosas.

## Avisos

| Canal | Configuración |
|---|---|
| Notificación de Windows | Ninguna. Usa `winotify` si está instalado y, si no, PowerShell. |
| Bus de la familia | Ninguna. Emite `tantalus.alert` y `tantalus.event.*` a Hoard Hub. La regla recomendada del hub `rule-watcher-alert-digest` convierte cada aviso en un `digest.item` para el resumen diario. |
| ntfy | Elige un tema largo y aleatorio, suscríbete a él en la app de ntfy y guárdalo en Ajustes (`NTFY_TOPIC`). |
| Telegram | Crea un bot con @BotFather, guarda el token, escríbele `/start` y pulsa «Buscar chat id». |
| Correo | Nada si Faustus tiene una cuenta de correo: Tantalus envía con esa cuenta y la contraseña se queda en Faustus. Los avisos llegan a la propia cuenta salvo que indiques destinatarios. Si no, servidor, puerto, usuario, contraseña de aplicación, remitente y destinatario (Gmail pide contraseña de aplicación). El ajuste **Enviar con** elige `auto`, `faustus` o `smtp`. |

Las credenciales van en `.env` (`TANTALUS_TELEGRAM_TOKEN=…`) o se guardan desde Ajustes como solo escritura. La API nunca las devuelve. Cada canal tiene interruptor y gravedad mínima.

## Vigilantes preparados

En el primer arranque se instalan siete vigilantes:

- ETB y Booster Bundle del 30.º aniversario de Pokémon: búsquedas de GAME, El Corte Inglés y xtralife, más consultas de descubrimiento.
- DGX Spark en España: marketplace de NVIDIA y API de productos de NVIDIA, con umbral de 4.800 €, más un vigilante de noticias de venta en España, porque el marketplace bloquea la lectura automática.
- RTX Spark / N1X de 128 GB en Europa: vigilante de información.
- Noticias de reposición del 30.º aniversario de Pokémon anunciadas por tiendas y cadenas (confirmación secundaria).
- El radar de libros gratis.
- Un vigilante de ETB precintada de segunda mano, desactivado.

Puedes editarlos, desactivarlos o borrarlos. `config_export` y `config_import` mueven toda la configuración como datos.

## Radar de agregadores: stock por tienda y días de lanzamiento

Un vigilante de disponibilidad con `config.radar.enabled` lee también dos agregadores públicos de stock de cartas:

- **stocktcg.net** sigue unas 190 tiendas de España y Europa, cadenas incluidas (GAME, Carrefour, El Corte Inglés, Alcampo, Amazon, Toys R Us, Toy Planet). En cada ciclo Tantalus lee su feed en directo (`/api/pulse.json`), la página de cada cadena que nombra el vigilante (`/tiendas/<cadena>`: lo que tiene ahora y lo que se agotó hace poco), el calendario (`/lanzamientos`), la página de cada lanzamiento que encaja desde una semana antes hasta una semana después de su fecha (en cada ciclo el mismo día) y unas pocas fichas de producto (`/p/<producto>`: cada tienda con stock, precio e idioma de la edición), las más antiguas primero.
- **stocktcg.es** aporta su feed de reposiciones y su propio calendario.

Cada oferta de una tienda es una fila. Cuando pasa a comprable, el vigilante recibe `RESTOCK` (o `PREORDER_OPEN`) con la tienda como vendedor. Una cadena avisa siempre. Cualquier otra tienda solo avisa en un idioma de edición permitido (`radar.languages`, ES y EN por defecto), en euros, y a no más del precio de cadena más barato de ese producto × el multiplicador anti-reventa; los precios de reventa quedan registrados sin avisar. Una oferta de cadena en un sitio que Tantalus puede leer (GAME, El Corte Inglés, fichas de Carrefour por la ventana) se comprueba antes en la ficha de la tienda; si allí sale agotado, el aviso queda como registro. Los productos de cadena de esos sitios pasan además a ser objetivos directos del vigilante.

Los lanzamientos crean `RELEASE` cuando uno que encaja entra en el calendario, tres días antes (`radar.release_days_before`) y el mismo día. El aviso dice dónde comprar: cada cadena (en stock, preventa o agotado, con precio) y las tiendas más baratas con stock o preventa. El primer ciclo de un vigilante es silencioso, salvo un lanzamiento de hoy o de esos días. El radar pasa cada 10 minutos, y cada 5 si hay un lanzamiento hoy o mañana. **Novedades** enseña los lanzamientos y un panel por cadena.

Configuración (`config.radar`): `enabled`, `sources` (`stocktcg.net`, `stocktcg.es`), `chains`, `languages`, `alert_other_shops`, `price_multiplier`, `release_days_before`, `max_products`, `track_chain_products`. Herramientas: `radar_status`, `radar_run`, `releases_list`, `radar_offers`, `radar_setup`.

## Ofertas del correo e informe de ruido

La página **Correo** tiene dos pestañas, **Ofertas** y **Ruido**. Las dos leen el buzón con la cuenta configurada en Faustus: Tantalus ejecuta un lector pequeño (`tantalus_hoard/mail/faustus_reader.py`) con el Python de Faustus, así que la contraseña del correo nunca llega a Tantalus. El lector abre las carpetas en solo lectura y descarga con `BODY.PEEK`. En Tantalus no hay código que envíe, mueva, marque, etiquete, archive, dé de baja ni borre un correo, un test comprueba que el lector no lo tiene, y nunca se abre un enlace de un correo.

- **Ofertas.** Cada pocas horas (`mail.deals.interval_min`, 180 por defecto) el planificador lee los correos nuevos de las tiendas (Steam, GOG, GAME, Epic, Humble, Fanatical, Green Man Gaming, Instant Gaming, xtralife, Bibliostock, Casa del Libro, Agapea, Planeta de Libros, Book Depository, Fnac, más los dominios que añadas). Un analizador convierte cada correo de oferta en filas: tienda, título, descuento («hasta» si es un rango), precio anterior y nuevo, fecha de fin y enlace. Si la tienda manda «un artículo de tu lista de deseados está en oferta», sale una fila por artículo; cualquier otro correo de oferta da una fila de campaña con los títulos de sus imágenes. Una oferta termina en la fecha de fin de la rebaja, o `mail.deals.ttl_days` (7) días después del correo si no dice ninguna.
- **A quién avisa.** Cada oferta se compara con: la biblioteca de juegos de la app hermana de colección de juegos (juegos en pendientes sin plataforma propia, o con la etiqueta `wishlist`; los que ya tienes nunca avisan), tu propia lista en Ajustes (`mail.deals.wishlist`), los correos de lista de deseados de la propia tienda y tus vigilantes activos (mismos términos de producto). Solo una coincidencia crea un evento `MAIL_DEAL`, una vez, y solo si el correo tiene como mucho tres días. El primer escaneo es silencioso: llena la tabla y no avisa de nada.
- **Informe de ruido.** Por dominio remitente en los últimos `mail.noise.days` (30): número de correos y porcentaje, categoría de Gmail, si hay enlace o dirección de baja (se muestra como texto, nunca se abre), el último correo y qué Hoard lee ese remitente (Ledger pagos, Phileas envíos, Kafka papeles, JobHunter empleo, Tantalus ofertas de tiendas). Los remitentes promocionales que ningún Hoard lee salen primero, como candidatos a limpiar. No cambia nada.

Ajustes: `mail.deals.enabled`, `mail.deals.interval_min`, `mail.deals.history_days`, `mail.deals.ttl_days`, `mail.deals.stores`, `mail.deals.domains`, `mail.deals.gamerhoard_file`, `mail.deals.wishlist`, `mail.noise.days`.

## Arrancar

```sh
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python -m tantalus_hoard        # http://127.0.0.1:5197
```

La interfaz compilada va en el repositorio. Si cambias el cliente, `npm install && npm run build` la vuelve a compilar. Hoard Hub arranca la app desde `faustus-plugin.json`. En Windows, el navegador sin ventana usa Edge a través de Playwright, así que no hay que descargar ningún navegador. En otros sistemas hay que ejecutar una vez `python -m playwright install chromium`.

Variables de entorno:

- `TANTALUS_PORT`: puerto, 5197 por defecto.
- `TANTALUS_DATA_DIR`: carpeta de datos.
- `TANTALUS_SCHEDULER=0`: sin comprobaciones en segundo plano.
- `TANTALUS_BROWSER=0`: solo HTTP.
- `TANTALUS_OFFLINE=1`: sin red.
- `TANTALUS_SEARXNG_URL` y `TANTALUS_BRAVE_KEY`: motores de búsqueda adicionales.
- `TANTALUS_FAUSTUS_DIR`: carpeta de Faustus para el correo, si no está junto a esta app.

## Asistentes (MCP)

`python mcp_server.py` es el puente stdio. Nunca abre la base de datos: pasa cada llamada a la app en marcha y la arranca si hace falta. Tiene 53 herramientas:

- Para empezar: `tantalus_overview`.
- Para consultas sueltas: `inspect_url`, `secondhand_search` y `web_search`.
- Para dejar algo vigilado: `watcher_create`, `target_add`, `discovery_run` y `candidate_accept`.

Todas están en [docs/API.md](docs/API.md):

`tantalus_overview`, `tantalus_status`, `watcher_list`, `watcher_get`, `watcher_create`, `watcher_update`, `watcher_delete`, `watcher_run`, `watcher_rescore`, `target_add`, `target_list`, `target_get`, `target_update`, `target_delete`, `target_check`, `target_resolve`, `inspect_url`, `events_list`, `events_mark_seen`, `event_dismiss`, `event_notify`, `listings_list`, `listing_set`, `info_items_list`, `info_item_set`, `candidates_list`, `candidate_accept`, `candidate_reject`, `discovery_run`, `web_search`, `secondhand_search`, `secondhand_facebook_login`, `packs_list`, `presets_list`, `presets_install`, `notify_status`, `notify_test`, `telegram_find_chat_id`, `settings_set`, `secret_set`, `scheduler_status`, `runs_list`, `config_export`, `config_import`, `mail_deals`, `mail_deals_scan`, `mail_noise_report`, `mail_deal_set`, `radar_status`, `radar_run`, `releases_list`, `radar_offers`, `radar_setup`.

El texto de las páginas, los títulos, los fragmentos y los asuntos de los correos son datos de terceros. Los resultados de las herramientas lo indican, y el modelo opcional los recibe marcados como contenido no fiable.

## Límites

- **Sitios bloqueados.** El 30-09-2026 bloqueaban la lectura automática en los dos niveles PcComponentes, Fnac, Toys R Us, Cardmarket, eBay y las páginas del marketplace de NVIDIA. Esos objetivos quedan en «necesita tu ayuda» y se reintentan cada tres horas. Las fichas de Carrefour se leen por la ventana visible (02-10-2026); su API de búsqueda contesta con una regla de Cloudflare «you have been blocked» incluso ahí, así que los productos nuevos de Carrefour llegan por el radar de agregadores. El radar es tan fresco como los agregadores: una tienda que no siguen, o el stock en el lineal de un centro, no aparece.
- **Stock por tienda.** Solo se lee cuando la tienda lo muestra en la página. Si no, la lista de tiendas del vigilante es una preferencia.
- **DGX Spark.** La API de productos de NVIDIA no la incluye hoy, así que ese objetivo sale «desconocido» hasta que la incluya.
- **Búsqueda web.** La búsqueda web sin clave es poco fiable desde un programa: DuckDuckGo pide una comprobación anti-bot tras pocas consultas y Bing degrada las consultas largas. Las búsquedas de noticias usan los RSS de Google News y Bing News, que funcionan bien. Para productos, lo fiable para descubrir SKUs nuevos es vigilar las búsquedas de las propias tiendas. SearXNG o una clave de Brave añaden motores web de verdad.
- **Ofertas del correo.** Necesitan Faustus con una cuenta de correo. El análisis es por reglas sobre el texto de los correos: si una tienda cambia el diseño del suyo, puede salir una fila de campaña en vez de una por artículo. La app de colección de libros es una base de datos en la nube sin archivo local, así que las listas de libros solo salen de tu propia lista en Ajustes. La biblioteca de juegos se lee de `mail.deals.gamerhoard_file`, `GAMERHOARD_DATA_FILE` o `~/.gamerhoard/library.json`; sin ella solo coinciden tu lista, los correos de deseados de la tienda y los vigilantes.
- **Facebook.** Sus condiciones prohíben el acceso automatizado. Marketplace está desactivado por defecto y usa tu propia sesión en el perfil del navegador de la app.

## Tests

```sh
python -m pytest -q
```

## Licencia

MIT, © Luis María Salete Cuartero.
