# Herramientas del conector Vammo Apps

Todas devuelven JSON con `mensaje_para_el_usuario` (en español: traducilo al
idioma de la persona) y `next_steps` (qué hacer después). `ambiente` es
`"dev"` (prueba) o `"prod"`; salvo que se diga otra cosa, el default es dev.

## Orientarse

| Herramienta | Para qué |
|---|---|
| `vammo_guia` | La guía (`tema: guia`), las reglas técnicas (`reglas`) o las plantillas (`plantillas`). |
| `vammo_novedades` | Lo que pasó desde la última vez: deploys listos o fallidos, pedidos aprobados/cerrados/comentados, cronjobs que fallaron, secretos cargados. Marca como leídas. |
| `vammo_mis_apps` | Las apps de la persona con URLs, último deploy y si esperan aprobación. |
| `vammo_datasets` | Catálogo de datos (`filtro`): sensibilidad, si requiere aprobación, columnas, filtrables y personales. |
| `vammo_muestra_datos` | Esquema y hasta 10 filas de ejemplo de un dataset, leídas con los permisos de una app ya publicada (`app`); columnas personales tapadas. |
| `vammo_plantillas` | Plantillas y cuándo usar cada una; con `plantilla`, su contenido. |

## Construir

| Herramienta | Para qué |
|---|---|
| `vammo_crear_app` | Crea la app desde una plantilla (`nombre`, `plantilla`, `descripcion`, `acceso`, `notas`), con su ambiente dev, y arranca el primer deploy. Idempotente. |
| `vammo_estado` | Todo de una app: URLs, deploys, manifiesto, secretos cargados, pedidos con comentarios del revisor, problemas. |
| `vammo_archivos` | Lista de archivos (rutas y tamaños). |
| `vammo_leer_archivo` | Contenido de uno o varios archivos (`ruta` / `rutas`). |
| `vammo_escribir_archivos` | Escribe en dev (`archivos` completos, `ediciones` buscar/reemplazar, `borrar`) con un `mensaje`. Estados: `merged` (+`deploy_id`), `pending_review` (+`link_revision`), `rejected` (+`hallazgos` con archivo:línea; no se guardó nada), `conflict`, `no_changes`. |
| `vammo_progreso_deploy` | Etapas del deploy; espera hasta `esperar_segundos`. Si falló: `error`, `log_final` y `sugerencia`. |
| `vammo_deploy` | Vuelve a publicar la versión actual (reintentos, secretos nuevos). |

## Publicar y mantener

| Herramienta | Para qué |
|---|---|
| `vammo_promover` | Dev → prod. Sin `confirmar`: vista previa (archivos que cambian, qué pediría aprobación). Con `confirmar: true`: lo hace. |
| `vammo_logs` | Logs con `buscar`, `nivel` (error/warn), `desde` (15m, 2h, 1d), `lineas`; para cronjobs, sus corridas. |
| `vammo_ejecutar` | Corre un cronjob ahora. |
| `vammo_historial` | Versiones y deploys (prod por defecto). |
| `vammo_volver_version` | Vuelve a un commit anterior (prod por defecto). Sin `confirmar`: vista previa. |
| `vammo_uso` | Consumo: base de datos, archivos, página publicada, deploys y corridas. |

## Permisos y personas

| Herramienta | Para qué |
|---|---|
| `vammo_pedir_acceso` | Pide `dataset`, `pii`, `egress`, `secreto`, `recursos`, `tier_datos` o `acceso` con una `justificacion` para el revisor. Si no necesita aprobación, se aplica solo. |
| `vammo_secretos` | `listar` (nombres declarados y cargados) o `pedir_carga` (link de un solo uso para cargar valores en el navegador). |
| `vammo_miembros` | `listar`, `agregar` o `quitar` personas que pueden modificar la app (solo el dueño). |

## Prompts y recursos

- Prompts: `nueva_app`, `mejorar_app`, `diagnosticar_falla`.
- Recursos: `vammo://guia`, `vammo://reglas`, `vammo://plantillas`,
  `vammo://plantillas/<tipo>`, `vammo://apps/<app>/estado`.
