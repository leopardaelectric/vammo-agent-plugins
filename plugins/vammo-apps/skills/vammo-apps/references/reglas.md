# Vammo Apps — reglas técnicas para escribir el código

La plataforma **chequea cada cambio y rechaza** lo que no cumpla estas reglas
(`vammo_escribir_archivos` te devuelve los errores con `archivo:línea`).

## Reglas absolutas

1. **Datos**: dos fuentes, siempre por el SDK.
   - Datos de la empresa → `data.query(...)` (ClickHouse, **solo lectura**).
   - Datos propios de la app → `db` (su Postgres, lectura y escritura; cada
     app y cada ambiente tiene la suya).
   Nunca importes drivers ni clientes de bases o infraestructura (psycopg,
   sqlalchemy, redis, clickhouse, pg, boto3…) ni leas variables `VAMMO_*`.
2. **Secretos**: en el código, `secrets.get("NOMBRE")`, con el nombre
   declarado en `vammo.yaml` → `secrets`. El valor lo carga la persona con
   el link de `vammo_secretos`. Nunca escribas un valor en un archivo.
3. **Login**: no lo implementes. La identidad llega validada en `user`.
   Roles propios de la app sí (ej. una tabla `admins` en `db` con los
   `user.email` que pueden editar).
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
data:
  datasets: [vammo_r.bike]   # SOLO nombres de vammo_datasets
  pii: []                    # datasets de los que necesita columnas personales (aprobación)
  tier: basic                # basic | standard | elevated (estos dos con aprobación)
secrets: []                  # nombres de secretos que usa el código
egress: []                   # hosts externos (ej. api.ejemplo.com), con aprobación
resources:
  tier: small                # small | medium (medium con aprobación)
access: [todos]              # todos, emails o grupo:<nombre>
# schedule: "0 7 * * *"      # cronjob: cron en hora de la empresa (America/Sao_Paulo)
```

- Todo `data.query("x")` del código tiene que estar en `data.datasets`, y
  todo `secrets.get("X")` en `secrets`.
- `access`: además del dueño y el equipo técnico, que entran siempre. Si no
  está: todos, **salvo que la app lea datos sensibles** (ahí solo dueño y
  equipo técnico hasta declararlo; abrirla a más gente pide aprobación).
- `web: { spa: true }` (webapp/frontend): las rutas que no son archivos
  devuelven `index.html`. En una webapp es el default.
- `backend: <api>` (solo frontend estático): el proyecto `api` que atiende
  `/api/*` del mismo sitio.

## SDK (Python)

```python
from vammo import data, db, user, secrets, cache, log, files

data.query(dataset, **filtros, limit=N)          # datos de la empresa (solo lectura)
db.migrate("migrations")                         # tablas: migrations/001_*.sql, 002_*.sql… (una vez cada uno)
db.query("SELECT ... WHERE id = %s", valor)      # leer tu base (parámetros %s; en Node $1, $2…)
db.execute("INSERT/UPDATE ...", *params)         # escribir tu base
with db.transaction() as tx: tx.execute(...)     # varias escrituras: todo o nada
user.id / user.email / user.name / user.region   # identidad validada (del directorio de la empresa)
user.has_permission("ver_costos")
secrets.get("NOMBRE")
cache.get(k) / cache.set(k, v, ttl=seg) / cache.delete(k)   # una caché para toda la app
log.info(msg, **campos) / log.debug / log.warn / log.error
files.put(ruta, datos, public=True) / files.get(ruta) / files.list("fotos/") / files.delete(ruta)
files.url(ruta)                                  # enlace que vence (privados)
files.upload_url(ruta, public=True, content_type="image/jpeg", max_bytes=N)  # subida desde el navegador
```

Node: `import { data, db, user, secrets, cache, log, files } from "@vammo/sdk"`
(mismas primitivas, con `await`; `user.hasPermission`, `files.uploadUrl`). En
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

**Identidad**: región y permisos salen del directorio de la empresa.
`user.has_permission("x")` es verdadero solo si el directorio le da ese
permiso exacto (en un cronjob no hay persona). No inventes nombres de
permisos ni regiones: preguntá; uno nuevo se le pide al equipo técnico.

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
