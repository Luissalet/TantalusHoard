# Tantalus's Hoard

[English](README.md)

<img src="app-icon.png" alt="" width="96" align="right">

Tantalus's Hoard es un vigilante de productos local. Te avisa cuando un producto vuelve a estar en stock, cuando baja de precio o cruza tu límite, cuando abre la reserva o aparece un SKU nuevo en una tienda. También encuentra gangas de segunda mano en Wallapop o Facebook Marketplace y detecta novedades relevantes en páginas oficiales, feeds y búsquedas web. El panel se abre con lo nuevo desde tu última visita. Los avisos llegan como notificación de Windows, evento del bus de la familia, push de ntfy al móvil, mensaje de Telegram o correo. Cada función es además una herramienta MCP para asistentes.

Todo corre en tu ordenador: la base SQLite, el planificador y el perfil del navegador. No sale nada salvo las propias peticiones a las páginas y los avisos que actives.

## Qué vigila

| Modo | Qué hace | Ejemplo |
|---|---|---|
| **availability** | Fichas de producto y búsquedas de tiendas. En cada comprobación guarda el estado de stock (en stock, recogida en tienda, reserva abierta, reposición anunciada, agotado, solo vendedores externos, desconocido), el precio, el vendedor y las evidencias. | ETB del 30.º aniversario de Pokémon en GAME, El Corte Inglés y xtralife; NVIDIA DGX Spark por debajo de 4.800 € |
| **secondhand** | Wallapop (API pública de búsqueda) y Facebook Marketplace (tu propia sesión en el perfil del navegador). Cada anuncio se puntúa con un pack de señales explicables. | Libros gratis en lote cerca de Madrid (el antiguo Radar de Libros); ETB precintada a no más de 1,3 × PVP |
| **information** | Páginas oficiales (diff del texto legible), feeds RSS/Atom y búsquedas de noticias y web (por defecto, los RSS de Google News y Bing News). Solo avisa de novedad material y marca cada una como confirmada, filtración o estimación. | Equipos RTX Spark / N1X de 128 GB en Europa |

## Cómo decide una comprobación

Descubrimiento, verificación, detección de cambios y aviso van por separado:

1. **Escalera de lectura.**
   - Primero HTTP normal, con robots.txt, intervalo mínimo por dominio, peticiones condicionales y protección SSRF.
   - Solo si la página es un armazón de JavaScript o la respuesta está bloqueada pasa al navegador sin ventana: Edge en Windows, con perfil persistente.
   - Si el sitio pide CAPTCHA o login, el objetivo queda como **necesita tu ayuda**. El botón «Resolver» abre una ventana visible para que lo pases tú. Tantalus nunca resuelve ni evita nada.
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
5. **Eventos.** Solo en transiciones útiles: `RESTOCK`, `LOCAL_RESTOCK`, `PREORDER_OPEN`, `PRICE_DROP`, `PRICE_THRESHOLD_CROSSED`, `NEW_SKU`, `RESTOCK_DATE_CONFIRMED` y `SOLD_OUT`. Para segunda mano y noticias: `NEW_LISTING`, `LISTING_PRICE_DROP`, `INFO_CHANGE` y `CANDIDATE_FOUND`.
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
| Bus de la familia | Ninguna. Emite `tantalus.alert` y `tantalus.event.*` a Hoard Hub. |
| ntfy | Elige un tema largo y aleatorio, suscríbete a él en la app de ntfy y guárdalo en Ajustes (`NTFY_TOPIC`). |
| Telegram | Crea un bot con @BotFather, guarda el token, escríbele `/start` y pulsa «Buscar chat id». |
| Correo | Servidor, puerto, usuario, contraseña de aplicación, remitente y destinatario. Gmail pide contraseña de aplicación. |

Las credenciales van en `.env` (`TANTALUS_TELEGRAM_TOKEN=…`) o se guardan desde Ajustes como solo escritura. La API nunca las devuelve. Cada canal tiene interruptor y gravedad mínima.

## Vigilantes preparados

En el primer arranque se instalan seis vigilantes:

- ETB y Booster Bundle del 30.º aniversario de Pokémon: búsquedas de GAME, El Corte Inglés y xtralife, más consultas de descubrimiento.
- DGX Spark en España: marketplace de NVIDIA y API de productos de NVIDIA, con umbral de 4.800 €, más un vigilante de noticias de venta en España, porque el marketplace bloquea la lectura automática.
- RTX Spark / N1X de 128 GB en Europa: vigilante de información.
- El radar de libros gratis.
- Un vigilante de ETB precintada de segunda mano, desactivado.

Puedes editarlos, desactivarlos o borrarlos. `config_export` y `config_import` mueven toda la configuración como datos.

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

## Asistentes (MCP)

`python mcp_server.py` es el puente stdio. Nunca abre la base de datos: pasa cada llamada a la app en marcha y la arranca si hace falta. Tiene 43 herramientas:

- Para empezar: `tantalus_overview`.
- Para consultas sueltas: `inspect_url`, `secondhand_search` y `web_search`.
- Para dejar algo vigilado: `watcher_create`, `target_add`, `discovery_run` y `candidate_accept`.

Todas están en [docs/API.md](docs/API.md):

`tantalus_overview`, `tantalus_status`, `watcher_list`, `watcher_get`, `watcher_create`, `watcher_update`, `watcher_delete`, `watcher_run`, `target_add`, `target_list`, `target_get`, `target_update`, `target_delete`, `target_check`, `target_resolve`, `inspect_url`, `events_list`, `events_mark_seen`, `event_dismiss`, `event_notify`, `listings_list`, `listing_set`, `info_items_list`, `info_item_set`, `candidates_list`, `candidate_accept`, `candidate_reject`, `discovery_run`, `web_search`, `secondhand_search`, `secondhand_facebook_login`, `packs_list`, `presets_list`, `presets_install`, `notify_status`, `notify_test`, `telegram_find_chat_id`, `settings_set`, `secret_set`, `scheduler_status`, `runs_list`, `config_export`, `config_import`.

El texto de las páginas, los títulos y los fragmentos son datos de terceros. Los resultados de las herramientas lo indican, y el modelo opcional los recibe marcados como contenido no fiable.

## Límites

- **Sitios bloqueados.** El 30-09-2026 bloqueaban la lectura automática en los dos niveles Carrefour, PcComponentes, Fnac, Toys R Us, Cardmarket, eBay y las páginas del marketplace de NVIDIA. Esos objetivos quedan en «necesita tu ayuda» y se reintentan cada tres horas.
- **Stock por tienda.** Solo se lee cuando la tienda lo muestra en la página. Si no, la lista de tiendas del vigilante es una preferencia.
- **DGX Spark.** La API de productos de NVIDIA no la incluye hoy, así que ese objetivo sale «desconocido» hasta que la incluya.
- **Búsqueda web.** La búsqueda web sin clave es poco fiable desde un programa: DuckDuckGo pide una comprobación anti-bot tras pocas consultas y Bing degrada las consultas largas. Las búsquedas de noticias usan los RSS de Google News y Bing News, que funcionan bien. Para productos, lo fiable para descubrir SKUs nuevos es vigilar las búsquedas de las propias tiendas. SearXNG o una clave de Brave añaden motores web de verdad.
- **Facebook.** Sus condiciones prohíben el acceso automatizado. Marketplace está desactivado por defecto y usa tu propia sesión en el perfil del navegador de la app.

## Tests

```sh
python -m pytest -q
```

## Licencia

MIT, © Luis María Salete Cuartero.
