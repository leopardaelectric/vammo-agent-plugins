# Vammo Apps — reglas técnicas para escribir el código

La plataforma **chequea cada cambio y rechaza** lo que no cumpla estas reglas
(`vammo_escribir_archivos` te devuelve los errores con `archivo:línea`).

## Reglas absolutas

1. **Datos**: dos fuentes, siempre por el SDK.
   - Datos de la empresa → `data.query(...)`, o `data.sql(...)` para cruzar
     datasets (ClickHouse, **solo lectura**).
   - Datos propios de la app → `db` (su Postgres, lectura y escritura; cada
     app y cada ambiente tiene la suya).
   Nunca importes drivers ni clientes de bases o infraestructura (psycopg,
   sqlalchemy, redis, clickhouse, pg, boto3…) ni leas variables `VAMMO_*`.
2. **Secretos**: en el código, `secrets.get("NOMBRE")`, con el nombre
   declarado en `vammo.yaml` → `secrets`. El valor lo carga la persona con
   el link de `vammo_secretos`. Nunca escribas un valor en un archivo.
3. **Login**: no lo implementes. La identidad llega validada en `user`.
   Los roles dentro de la app (quién edita, quién solo mira) se declaran en
   `vammo.yaml` → `roles` y se chequean con `user.can("permiso")`: no armes
   tablas propias de usuarios o roles en `db`.
4. **Internet**: las apps no tienen. Un host externo se declara en
   `vammo.yaml` → `egress` (solo el host) y requiere aprobación.
5. **Solo el SDK `vammo`** para hablar con la plataforma, y librerías
   conocidas para la lógica. Dependencias fuera de la lista aprobada pasan
   por revisión: no inventes nombres de paquetes. Sin Dockerfiles ni
   configuración de infraestructura (la plataforma los genera).

## vammo.yaml

```yaml
name: ventas-norte           # minúsculas, números y guiones (no termina en -dev)
type: webapp                 # webapp | dashboard | cronjob | api | frontend
runtime: python              # python | node | static
description: "Ventas diarias por sucursal"
files: true                  # usa el SDK files (se aprueba solo)
email: true                  # manda mails con el SDK email a la empresa (se aprueba solo)
ai: true                     # usa la IA de la plataforma (SDK ai; se aprueba solo)
slack: [ventas-alertas]      # canales de Slack registrados en la plataforma (notify.slack)
data:
  datasets: [vammo_r.bike]   # SOLO nombres de vammo_datasets
  pii: []                    # datasets de los que necesita columnas personales (aprobación)
  tier: basic                # basic | standard | elevated (estos dos con aprobación)
secrets: []                  # nombres de secretos que usa el código
egress: []                   # hosts externos (ej. api.ejemplo.com), con aprobación
resources:
  tier: small                # small | medium (medium con aprobación)
access: [todos]              # todos, emails o grupo:<nombre>
roles:                       # roles DENTRO de la app (opcional, se aprueba solo)
  admin: [ver_costos, editar, administrar]
  editor: [editar]
  lector: []
default_role: lector         # rol de quien no tiene uno asignado
# schedule: "0 7 * * *"      # cronjob: cron en hora de la empresa (America/Sao_Paulo)
jobs:                        # tareas programadas DENTRO de la app (webapp, api, dashboard, Node)
  - name: snapshot_deuda     # minúsculas, números, _ o -
    schedule: "0 6 * * *"    # cron de 5 campos, hora de la empresa
    run: jobs.py:snapshot    # archivo.py:funcion (Node: jobs.js#snapshot)
```

- Todo `data.query("x")` del código tiene que estar en `data.datasets`, y
  todo `secrets.get("X")` en `secrets`. Lo mismo las tablas de `data.sql`
  (FROM / JOIN): el chequeo las lee del texto y el Bridge las vuelve a
  validar al correr.
- `access`: además del dueño y el equipo técnico, que entran siempre. Si no
  está: todos, **salvo que la app lea datos sensibles** (ahí solo dueño y
  equipo técnico hasta declararlo; abrirla a más gente pide aprobación).
- `roles`: cada rol con sus permisos (o solo nombres: `roles: [admin,
  lector]`). Nombres en minúsculas (`a-z0-9_-`); permisos `a-z0-9_`. `admin`
  existe siempre: tiene todos los permisos declarados más `administrar`
  (manejar los roles), que es solo suyo. El dueño y los miembros son `admin`
  siempre. `default_role` no puede ser `admin`. Las asignaciones (a un email
  o a `grupo:<nombre>`) son por ambiente y se hacen con `vammo_roles_app`,
  en el portal o desde la app con `app_users`.
- `web: { spa: true }` (webapp/frontend): las rutas que no son archivos
  devuelven `index.html`. En una webapp es el default.
- `backend: <api>` (solo frontend estático): el proyecto `api` que atiende
  `/api/*` del mismo sitio.
- `email`: `email: true` manda solo a direcciones de la empresa. Para
  mandar también afuera, el bloque (pasa por revisión; pedilo con
  `vammo_pedir_acceso` tipo=email valor=externo y una justificación):

  ```yaml
  email:
    external: true
    name: Reportes Norte     # opcional: el remitente es "<name> via Vammo Apps"
  ```

- `ai`: `ai: true` sin datos personales. Si la app le manda a la IA datos
  personales (de `data.pii` o de su propia base), el bloque (pasa por
  revisión; pedilo con `vammo_pedir_acceso` tipo=ai valor=pii):

  ```yaml
  ai:
    pii: true
  ```

  Una app que declara `data.pii` y usa `ai` **tiene** que decirlo: `pii:
  true` o `pii: false` (si no, el chequeo la rechaza).
- `slack`: nombres de canales que registró un admin (minúsculas, números y
  guiones). Nunca la URL del webhook (el chequeo rechaza URLs de
  `hooks.slack.com` en el código).

## SDK (Python)

```python
from vammo import data, db, user, secrets, cache, log, files, email

data.query(dataset, **filtros, limit=N)          # datos de la empresa (solo lectura)
data.sql("SELECT ... FROM a JOIN b ON ...", params={...}, limit=N)  # cruzar datasets (solo SELECT)
db.migrate("migrations")                         # tablas: migrations/001_*.sql, 002_*.sql… (una vez cada uno)
db.query("SELECT ... WHERE id = %s", valor)      # leer tu base (parámetros %s; en Node $1, $2…)
db.execute("INSERT/UPDATE ...", *params)         # escribir tu base
with db.transaction() as tx: tx.execute(...)     # varias escrituras: todo o nada
user.id / user.email / user.name / user.region   # identidad validada (del directorio de la empresa)
user.has_permission("ver_costos")                 # permiso de la PLATAFORMA (directorio)
user.groups / user.in_group("finanzas")           # grupos del directorio de la empresa
user.role / user.roles / user.has_role("editor")  # roles DENTRO de esta app (vammo.yaml → roles)
user.can("editar")                                # permiso DENTRO de esta app
app_users.list() / app_users.assign("ana@vammo.com", "editor") / app_users.remove("grupo:finanzas")
                                                  # administrar roles desde la app (solo con `administrar`)
secrets.get("NOMBRE")
cache.get(k) / cache.set(k, v, ttl=seg) / cache.delete(k)   # una caché para toda la app
log.info(msg, **campos) / log.debug / log.warn / log.error
files.put(ruta, datos, public=True) / files.get(ruta) / files.list("fotos/") / files.delete(ruta)
files.url(ruta)                                  # enlace que vence (privados)
files.upload_url(ruta, public=True, content_type="image/jpeg", max_bytes=N)  # subida desde el navegador
email.send(to=["ana@vammo.com"], subject="...", text="...", html=None, reply_to=None,
           attachments=["reporte.csv", ("datos.csv", contenido_bytes)])     # mails (email: true)
ai.complete("Resumí en 3 líneas: ...", system=None, model="default", max_tokens=1024)   # IA (ai: true)
ai.complete(prompt, json_schema={...})          # respuesta como dict según el esquema
ai.classify(texto, ["reclamo", "consulta", "elogio"])   # una de las categorías
ai.extract(texto, {"nombre": "nombre del cliente", "monto": "monto en reales"})  # dict
notify.slack("ventas-alertas", "Reporte listo", blocks=None)   # Slack (slack: [ventas-alertas])
```

Node: `import { data, db, user, secrets, cache, log, files, email, ai, notify } from "@vammo/sdk"`
(mismas primitivas, con `await`; `user.hasPermission`, `user.inGroup`,
`user.hasRole`, `user.can`, `appUsers.assign`, `files.uploadUrl`,
`email.send({ to, subject, text, html, replyTo, attachments })`,
`ai.complete({ prompt, system, model, maxTokens, jsonSchema })` → `{ text, json, … }`,
`ai.text(prompt)`, `ai.classify`, `ai.extract`, `notify.slack(canal, texto, { blocks })`). En
Next.js el SDK va **solo** del lado servidor (nunca en `"use client"`).

**`data.query`** (columnas: `vammo_datasets` con filtro o
`vammo_muestra_datos`; no inventes columnas). Que la base haga el trabajo:

```python
filas = data.query("<dataset>", fecha__gte="2026-09-01", region__in=["norte", "sur"],
                   columns=["fecha", "sucursal", "monto"], limit=5000)
ultimos = data.query("<dataset>", order_by=["-fecha"], limit=50)       # los últimos 50
totales = data.query("<dataset>", group_by=["sucursal"],               # total por sucursal
                     aggregates={"total": "sum(monto)", "ventas": "count()"}, order_by=["-total"])
for pagina in data.pages("<dataset>", order_by=["fecha"], limit=5000):  # todo, de a páginas
    ...
```

- Filtros (solo columnas filtrables): igualdad, `col__gte`, `col__lte`,
  `col__gt`, `col__lt`, `col__ne`, `col__in=[...]`. `order_by` ("-" =
  descendente) solo columnas filtrables u ordenables. `aggregates`:
  `count()`, `sum`, `avg`, `min`, `max`, `uniq`; con `group_by` no se usa
  `columns`.
- Si `filas.truncated` es verdadero, había más filas que el límite: el
  resultado **no** está completo (filtrá, agregá o paginá; nunca lo
  muestres como total).
- **Un filtro vacío es un error**: `region=user.region` con una persona sin
  región lanza `DataError` (no trae todas las regiones). Chequealo antes y
  mostrá un mensaje; para no filtrar a propósito, `region=data.ANY`.
- El error del Bridge dice qué columnas se pueden usar; el de límite por
  minuto (status 429) dice cuándo reintentar: usá `cache`.
- Node: `{ orderBy, groupBy, aggregates, columns, limit }`, `filas.nextPage`,
  `for await (const p of data.pages(...))`, `data.ANY`.

**`data.sql`**: un SELECT de ClickHouse para cruzar datasets, subconsultas,
CASE o funciones de fecha. Buscá los datasets y las claves de cruce con
`vammo_buscar_datos`; confirmá columnas con `vammo_muestra_datos`.

```python
deuda = data.sql("""
    SELECT toDate(r.start_date) AS dia, b.user_id, sum(r.total_debts) AS deuda
    FROM vammo_r.bike AS b
    JOIN vammo_r.reconciliation_reports AS r ON r.subscription_id = b.subscription_id
    WHERE b.status = :estado AND r.start_date >= {desde:Date}
    GROUP BY dia, b.user_id
    ORDER BY dia
""", params={"estado": "active", "desde": "2026-09-01"}, limit=5000)
```

- Los valores **siempre** como parámetros: `:nombre` (el tipo sale del
  valor; una lista sirve para `col IN :lista`) o `{nombre:Tipo}` (`Date`,
  `DateTime`, `Int64`, `String`…). Nunca f-strings ni `+` con valores: el
  chequeo lo marca.
- Se acepta: `SELECT` con `WITH nombre AS (…)`, `JOIN … ON/USING`, `WHERE`,
  `GROUP BY`, `HAVING`, `ORDER BY`, `LIMIT`, `UNION ALL` y funciones comunes
  (agregados, fechas, textos, `if`, `CASE`, `CAST`…). Se rechaza: varias
  sentencias, `SETTINGS`, `FORMAT`, `INTO OUTFILE`, funciones de tabla,
  tablas del sistema, funciones de ventana (`OVER`) y lambdas.
- Solo datasets declarados y sus columnas visibles: una columna personal
  sin `data.pii` aprobado o una interna da error (también en `WHERE` y
  `JOIN`). `SELECT *` trae solo las visibles.
- El tope de filas del tier se aplica igual: mirá `.truncated`. Errores:
  `DataError` con el motivo y la posición del SQL.
- Node: `await data.sql(sql, { params: {...}, limit: 1000 })`.

**Identidad**: región y permisos salen del directorio de la empresa.
`user.has_permission("x")` es verdadero solo si el directorio le da ese
permiso exacto (en un cronjob no hay persona). No inventes nombres de
permisos, grupos ni regiones: preguntá; uno nuevo se le pide al equipo
técnico (los administra en el portal).

**Roles de la app**: `user.can("editar")` es verdadero si alguno de los
roles de la persona en ESTA app (y en este ambiente) da ese permiso; en un
cronjob, siempre falso. Chequealo en el servidor (el endpoint que guarda o
borra), no solo para mostrar u ocultar un botón. Un permiso que no está en
ningún rol de `vammo.yaml` da un aviso al deployar (siempre sería falso).
Para una pantalla "Administrar usuarios": mostrala solo si
`user.can("administrar")` y usá `app_users` (en un dashboard, si la sesión
venció, `AppUsersError` con status 401: pedí recargar la página).

**FastAPI**: endpoints con `def`, no `async def` (el SDK bloquea; en un
`async def` frena a todos los pedidos). Si hace falta `async def`, usá
`await run_in_threadpool(data.query, ...)`.

**Caché**: compartida por todas las personas. Si el valor depende de quién
pregunta, ponelo en la clave: `cache.set(f"ventas:{user.region}", filas, ttl=300)`.

**Archivos (`files: true`)**: públicos (`public=True`) los ve cualquiera que
pueda abrir la app en `/files/<ruta>` (nunca internet; solo tipos seguros:
imágenes, PDF, texto, CSV, Office, zip; nunca HTML/SVG/JS). Privados (por
defecto): solo los lee la API; `files.url(ruta)` da un enlace que vence en
minutos. Subida desde el navegador: la API da `files.upload_url(...)` y el
navegador hace `fetch(url, {method: "PUT", headers, body: archivo})`.
Rutas con letras, números, `.`, `_`, `-`, `/`. Topes: 25 MB por archivo, 5
GB por app. Los registros van en `db`; `files` es para archivos.

**Mails (`email: true`)**: salen de la plataforma como `"<app> via Vammo
Apps" <noreply-apps@vammo.com>`, con Reply-To al dueño de la app (o
`reply_to`). Nada de SMTP, SES ni APIs de mail propias (no hay internet ni
credenciales). Sin `email.external` aprobado, solo a dominios de la empresa
(si no, error 403 que lo dice). En **dev** el asunto lleva `[DEV]` y solo
reciben el dueño y los miembros de la app (al resto lo saltea: `skipped`).
Topes: 50 destinatarios por mail, 10 adjuntos y 5 MB en total (para algo
más grande, guardalo con `files` y mandá el link), cuota diaria por app
(500 destinatarios en tier basic; error 429 al pasarla) y un límite por
minuto. El HTML va tal cual pero sin `<script>`, `<iframe>`, `<object>` ni
`<embed>` (HTML simple, estilos en línea). Errores: `EmailError` (`status`,
`retry_after`). Ejemplo, un cronjob con resumen diario:

```python
from vammo import data, email

def run():
    filas = data.query("<dataset>", fecha="2026-10-14", group_by=["sucursal"],
                       aggregates={"total": "sum(monto)"})
    texto = "\n".join(f"{f['sucursal']}: {f['total']}" for f in filas)
    email.send(to=["ventas@vammo.com"], subject="Ventas de ayer", text=texto)
```

**IA (`ai: true`)**: la app usa la IA de la plataforma (Claude) por el
Bridge, sin claves propias ni internet. `model`: `"default"` (equilibrado)
o `"fast"` (rápido y barato, para clasificar o extraer); `max_tokens` hasta
8192 (por defecto 1024); `effort` low (por defecto), medium o high. Con
`json_schema` la respuesta respeta el esquema y vuelve como dict/lista.
Cuota diaria de tokens por app (200 mil en tier basic; error
`AIQuotaError` al pasarla) y un límite por minuto. Si el modelo se niega
(contenido que sus reglas no permiten), `AIRefusalError`. Los prompts no se
guardan; igual, no le mandes datos personales sin `ai.pii` aprobado.
Cacheá respuestas que se repiten (`cache`) y no llames a la IA en un loop
sobre miles de filas: agrupá. Ejemplo, clasificar reclamos nuevos en un
cronjob:

```python
from vammo import ai, db

def run():
    for r in db.query("SELECT id, texto FROM reclamos WHERE categoria IS NULL LIMIT 50"):
        cat = ai.classify(r["texto"], ["entrega", "cobro", "bici rota", "otro"])
        db.execute("UPDATE reclamos SET categoria = %s WHERE id = %s", cat, r["id"])
```

**Slack (`slack: [canal]`)**: `notify.slack("ventas-alertas", texto)` manda
al canal registrado con ese nombre (texto con formato de Slack: `*negrita*`,
`<https://…|link>`; `blocks` de Block Kit opcionales). El mensaje sale
firmado con el nombre de la app; en dev lleva `[DEV]`. Cuota diaria por app
(200 mensajes) y un límite por minuto: juntá avisos en un solo mensaje.
Errores: `NotifyError` (404 si el canal no está registrado: lo registra un
admin).

## Estructura por tipo

- **webapp**: la página en `web/` (`index.html` + JS/CSS) y la API en
  `main.py` (FastAPI). La página llama `fetch("/api/<ruta>")` y la API
  define la ruta **sin** `/api` (`@app.get("/hola")` es `/api/hola`). En
  `web/` no uses carpetas `api/` ni `files/`. Para React/Vite:
  `web/package.json` con `npm run build` que deje el sitio en `web/dist/`
  (la plataforma compila; no subas `node_modules` ni `dist`). La página no
  lee datos directo: se los pide a su API.
- **dashboard**: UI en `app.py` (Streamlit).
- **cronjob**: función `run()` en `main.py`; corre según `schedule`. Probalo
  con `vammo_ejecutar`; la salida queda en `vammo_logs`.
- **jobs** (en una webapp, api, dashboard o app Node): cada job es una
  función sin argumentos (`run: jobs.py:snapshot` → `def snapshot():` en
  `jobs.py`; Node `jobs.js#snapshot`, con TypeScript el compilado
  `dist/jobs.js#snapshot`) que corre a su hora con la misma base, caché,
  archivos y permisos de datos que la app (no con la persona: `user` está
  vacío). Tope de 1 hora por corrida; si la anterior sigue, la nueva se
  omite: que sea idempotente. Probalo con `vammo_ejecutar` + `job`; las
  corridas se ven en `vammo_logs` con `job` y en el portal (Logs).
- **api**: endpoints en `main.py` (FastAPI, el middleware de identidad ya
  viene) o controllers en `src/` (NestJS, `VammoMiddleware` ya está).
- **frontend** estático: `index.html` que usa su `backend` con `fetch("/api/...")`.
- **nextjs**: route handlers / server components con el SDK (nunca en el cliente).

Mantené el código en los archivos que trae la plantilla. Dependencias de
Python en `requirements.txt` (la primera línea, `vammo`, va sin versión: el
SDK viene en la imagen y nunca se baja de PyPI); de Node en `package.json`
(`@vammo/sdk` ya viene en la imagen).

## Ambientes

Cada app tiene **dev** (`<nombre>-dev`, su propia base, archivos y URL) y
**prod**. `vammo_escribir_archivos` siempre escribe en dev; `vammo_promover`
lleva dev a prod. Las aprobaciones son del proyecto: lo aprobado en uno no
se vuelve a pedir en el otro.
