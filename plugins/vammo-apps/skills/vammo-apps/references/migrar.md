# Vammo Apps — migrar una app que ya existe

Para cuando la persona ya tiene la app hecha en otro lado: una carpeta con
código abierta en Claude Code, un repo, algo que corre en otro servidor o
en su computadora. Típicamente dice "migrá esta app a Vammo Apps" o "quiero
pasar esto a la plataforma". (Si quiere una app **nueva**, seguí la guía
normal: `vammo_guia`.)

## 0. Reglas de la migración

- **Nunca modifiques la carpeta original.** Leela; los cambios van en una
  **copia de trabajo** al lado (ej. `mi-app-vammo/`). Decíselo a la persona.
- **Primero el plan, después los cambios.** No crees la app, no copies ni
  edites nada hasta que la persona confirme el plan (sección 3).
- **Secretos**: de los `.env` leé solo los **nombres** de las variables.
  Nunca muestres, copies ni subas valores; se cargan con el link de
  `vammo_secretos`. Si un valor aparece en el código, no lo repitas en el
  chat y avisale que conviene rotarlo.
- **Datos**: no subas datos de producción sin que la persona lo pida, y
  primero a dev.
- Hablá en el idioma de la persona, sin jerga (ver la guía).

## 1. Analizá el proyecto (solo lectura)

Mirá, sin cambiar nada: `README`, `package.json`, `requirements.txt` /
`pyproject.toml`, `Procfile`, `Dockerfile`, `docker-compose.yml`,
`.env.example` (y los nombres de `.env`), `crontab` o tareas programadas
(GitHub Actions con `schedule`, APScheduler, Celery beat, node-cron), las
migraciones o modelos de la base, el código de login, y las llamadas a
servicios externos (`requests`, `axios`, `fetch` a `https://…`, SDKs de
Slack, OpenAI, SendGrid, S3…).

Qué tipo de app de Vammo Apps le corresponde:

| Encontrás | Tipo | Qué cambia |
|---|---|---|
| Flask / FastAPI que sirve páginas (templates, `static/`) | **webapp** | Las rutas pasan a `main.py` (FastAPI, `def`); las páginas a `web/` y piden datos con `fetch("/api/…")` (las plantillas Jinja se reescriben como HTML + JS). |
| FastAPI / Flask sin páginas (solo JSON) | **api** | Endpoints en `main.py` (FastAPI). |
| Django | **webapp** | Vistas → endpoints FastAPI; modelos → tablas en `db` con migraciones SQL; templates → `web/`; el admin de Django → pantallas propias. |
| Streamlit | **dashboard** | Casi igual: `app.py`; los datos con `data.query` / `db`. |
| Express / Fastify / Koa (Node) | **webapp** (Python, recomendada) o **nestjs** si el equipo trabaja en Node | Las rutas se reescriben (FastAPI o controllers de NestJS). |
| React / Vite / Vue (página de una sola app) | **webapp** | La página va en `web/` con `web/package.json` y `npm run build` → `web/dist/`; su backend en `main.py`. |
| Next.js | **nextjs** | El SDK solo en route handlers / server components; NextAuth se va. |
| NestJS | **nestjs** | `VammoMiddleware` ya viene en la plantilla. |
| Scripts que corren cada tanto | **cronjob** (`run()` en `main.py`, `schedule:` en hora de la empresa) | O, si la tarea es parte de una webapp, una tarea programada dentro de la misma app (`jobs:` en `vammo.yaml`; mirá las reglas con `vammo_guia` tema `reglas`). |
| HTML estático | **webapp** (o frontend estático) | — |
| PHP, Ruby/Rails, Java/Spring, Go, .NET, otros | No corre en la plataforma | Explicalo sin vueltas y ofrecé **reescribirla** en Python (webapp/api) manteniendo lo que hace; estimá el tamaño (pantallas, tablas, integraciones). |

`vammo_plantillas` con `plantilla` te muestra los archivos del tipo elegido:
partí de ahí.

## 2. Cómo se traduce cada pieza

- **Login, usuarios y contraseñas**: se borran. La persona ya entra con su
  cuenta de la empresa y la identidad llega en `user` (`user.email`,
  `user.name`, `user.region`, `user.has_permission`). Si la app tenía roles
  propios (admins, editores), pasan a una tabla en `db` con los emails.
  Nunca guardes contraseñas.
- **Variables de entorno**:
  - las que da la plataforma se borran: conexión a la base
    (`DATABASE_URL`), Redis, SMTP, credenciales de AWS/S3;
  - las de configuración que no son secretas (URLs públicas, flags) van
    como constantes en el código;
  - las secretas (API keys, tokens) → `secrets.get("NOMBRE")`, el nombre
    en `vammo.yaml` → `secrets`, pedido con `vammo_pedir_acceso`
    tipo=`secreto`, y el valor lo carga la persona con el link de
    `vammo_secretos` (`accion: "pedir_carga"`).
- **APIs externas**: cada host en `vammo.yaml` → `egress` (pedilo con
  `vammo_pedir_acceso` tipo=`egress` y una justificación) + su secreto.
- **Base de datos propia** (SQLite, Postgres, MySQL, Mongo) → `db` (su
  Postgres). El esquema pasa a `migrations/001_*.sql`, `002_*.sql`… con
  `CREATE TABLE IF NOT EXISTS`, aplicadas con `db.migrate("migrations")` al
  arrancar. Los ORMs (SQLAlchemy, Prisma, Django ORM, Sequelize) se
  reemplazan por SQL con `db.query` / `db.execute` (los drivers están
  prohibidos). Los datos se cargan con `vammo_importar_datos`:
  - Postgres: `pg_dump --no-owner --no-privileges --format=plain <base> > dump.sql`
    (o solo los datos con `--data-only` si las tablas las crean las
    migraciones);
  - SQLite / MySQL: un CSV por tabla;
  - Mongo: `mongoexport --jsonArray` por colección (JSON).
- **Datos de la empresa** que la app leía directo de ClickHouse u otra base
  → `data.query` con los datasets de `vammo_datasets` (pedí los sensibles
  con `vammo_pedir_acceso`).
- **Archivos subidos** (disco local, S3) → SDK `files` (`files: true`). Los
  que ya existen se vuelven a subir desde la app, o, si son muchos,
  hablalo con el equipo de plataforma.
- **Mails** (SMTP, SendGrid, SES) → SDK `email` (`email: true`; afuera de
  la empresa, con revisión).
- **Avisos a Slack** (webhooks) → `notify.slack("<canal>", texto)` con un
  canal registrado (pedilo con `vammo_pedir_acceso` tipo=`slack`). Un bot
  que lee Slack o usa su API → `egress` + secreto.
- **IA** (OpenAI, Anthropic u otra con clave propia) → SDK `ai` (`ai:
  true`; si le manda datos personales, `ai.pii` con revisión). La clave
  vieja se borra.
- **Tareas programadas** → cronjob (u otra app cronjob que comparta la
  lógica) o `jobs:` dentro de la misma app.
- **Colas en segundo plano** (Celery, RQ, Sidekiq, BullMQ) → una tabla de
  pendientes en `db` que procesa un cronjob o un job. Lo que necesite
  tiempo real de verdad: consultalo con el equipo de plataforma.
- **WebSockets / tiempo real**: no hay; se resuelve con consultas cada
  algunos segundos desde la página.
- **Redis / caché** → `cache`. **Logs** → `log`.
- **Dockerfiles, docker-compose, nginx, configuración de servidores** → se
  borran (la plataforma arma todo).
- **Dependencias**: solo las que se usan, en `requirements.txt` (la primera
  línea `vammo`) o `package.json`. Las que no están en la lista aprobada
  pasan por revisión: pedí pocas y conocidas.

## 3. El plan: mostralo y esperá un "sí"

Resumilo en lenguaje simple, en una lista corta:

1. **Nombre** de la app en Vammo Apps y **tipo** (y por qué).
2. **Qué queda igual** para quien la usa (pantallas, funciones).
3. **Qué cambia**: el login (entran con la cuenta de la empresa), lo que se
   reescribe, lo que se saca (y por qué).
4. **Permisos a pedir** con su justificación: datasets, columnas
   personales, hosts externos, secretos, mails afuera, IA con datos
   personales, canales de Slack. Qué se aprueba solo y qué espera a alguien.
5. **Qué tiene que hacer la persona**: cargar los secretos (un link), quizás
   pedirle a un admin un canal de Slack, probar en dev.
6. **Datos a migrar** (qué tablas, desde dónde) y en qué momento.
7. Lo que **no se puede** (lenguaje no soportado, tiempo real…) y la
   alternativa.
8. Que se arma primero en **dev** (su URL de prueba) y pasa a prod solo
   cuando ella diga; la carpeta original queda intacta.

## 4. Hacer la migración

1. **Copia de trabajo**: copiá la carpeta al lado, sin dependencias ni
   `.git` (bash: `rsync -a --exclude node_modules --exclude .git --exclude .venv <orig>/ <orig>-vammo/`;
   PowerShell: `robocopy <orig> <orig>-vammo /E /XD node_modules .git .venv __pycache__`).
   Trabajá solo ahí.
2. **Adaptá el código** con las reglas (`vammo_guia` tema `reglas`) y la
   plantilla del tipo: `vammo.yaml` (nombre, tipo, datasets, secretos,
   egress, `email`, `ai`, `slack`, `access`), la estructura de archivos y
   `NOTAS.md` (qué era, qué se cambió, qué falta).
3. **Importá**: `vammo_importar_carpeta` con `app`, `crear: true` (si es
   nueva), `plantilla`, `mensaje`, `notas` y `carpeta` (la copia). Corré el
   comando que te devuelve (bash, o PowerShell en Windows) desde tu
   terminal: la respuesta trae el resultado.
   - `rejected`: corregí cada `archivo:línea` en la copia, pedí un link
     nuevo y volvé a subir.
   - `pending_review`: contale qué se está revisando y seguí con lo demás.
   - `merged`: se publica en dev; seguilo con `vammo_progreso_deploy`.
4. **Secretos**: `vammo_secretos` con `accion: "pedir_carga"` y los nombres;
   pasale el link y esperá a que los cargue.
5. **Datos**: `vammo_importar_datos` por tabla o con el dump (en dev), y
   verificá con `vammo_consultar_base` (conteos, algunas filas).
6. **Verificá**: `vammo_captura` de las pantallas principales (errores de la
   consola, recursos que no cargan), `vammo_logs` para errores del
   servidor. Corregí con `vammo_escribir_archivos` (cambios chicos) o
   reimportando la copia (cambios grandes, `modo: "reemplazar"`).
7. **Pruebas de la persona** en la URL de dev. Iterá.
8. **Producción**: `vammo_promover` (primero sin confirmar, después con
   `confirmar: true`). Si hay datos que migrar a prod,
   `vammo_importar_datos` con `ambiente: "prod"` después de que la persona
   lo confirme.
9. **Apagar la app vieja**: decile qué conviene apagar o revocar (servidor
   viejo, claves que ya no se usan); eso lo hace ella o su equipo, no vos.

## 5. Sin terminal (claude.ai, ChatGPT)

Si no podés correr comandos: pasale a la persona el comando exacto para su
sistema (bash o PowerShell) y, cuando lo corra, consultá el resultado con
`vammo_importar_carpeta` y `importacion: "<id>"`. Para apps chicas también
podés escribir los archivos directamente con `vammo_escribir_archivos`.
