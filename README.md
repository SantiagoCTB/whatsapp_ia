# WhatsApp IA – Chatbot omnicanal para asesores

Este proyecto implementa una aplicación web en Flask que se integra con la WhatsApp Cloud API para automatizar la atención inicial de clientes y asistir a los asesores humanos. El sistema combina un chatbot basado en reglas, un panel estilo WhatsApp Web para los agentes y utilidades de backoffice para mantener el flujo conversacional, monitorear métricas y administrar roles. 

## Funcionalidades clave
- **Webhook oficial de WhatsApp** con verificación de token, deduplicación de mensajes y almacenamiento histórico en MySQL. Se procesan textos, listas, botones y adjuntos (imágenes, audio, video, documentos y enlaces de referidos). 
- **Flujos conversacionales configurables** por pasos (`step`) y reglas (`reglas`) que soportan comodines (`*`), saltos múltiples y asignación automática de roles. 
- **Comandos globales** (reiniciar, ayuda, etc.) que se evalúan antes del flujo y permiten reiniciar la conversación en caliente. 
- **Panel de agentes estilo WhatsApp** para responder, adjuntar archivos, asignar alias y roles por chat, con refresco dinámico de conversaciones. 
- **Administración visual de reglas, botones y roles**, incluyendo importación desde Excel (`.xlsx`) y carga de medios. 
- **Transcripción automática de audios** mediante Vosk + ffmpeg, procesada en segundo plano con `ThreadPoolExecutor` y límites de duración configurables. 
- **Dashboard analítico** con endpoints JSON para métricas (volumen, tipos de mensajes, top números, palabras frecuentes, etc.) consumidos desde Chart.js en `tablero.html`. 
- **Exportación de conversaciones** a JSON o CSV para respaldos rápidos o análisis adicionales. 
- **Script utilitario** para migrar contraseñas antiguas a hashes de Werkzeug (`scripts/rehash_passwords.py`). 

## Arquitectura del proyecto
```
/ (raíz)
├── app.py                     # Crea la app Flask, registra blueprints e inicializa la BD bajo demanda
├── config.py                  # Configuración unificada (tokens, DB, tiempo de sesión, paths)
├── routes/                    # Blueprints organizados por dominio (auth, chat, webhook, tablero, etc.)
├── services/                  # Capa de servicios reutilizables (DB, API de WhatsApp, transcripción, comandos)
├── templates/                 # Vistas Jinja2 (login, chat, configuración, tablero, roles, exportaciones)
├── static/                    # CSS/JS y carpeta `uploads/` para medios servidos públicamente
├── scripts/                   # Utilidades de mantenimiento (p.ej. migración de contraseñas)
├── requirements.txt           # Dependencias de Python para entorno local o contenedores
├── docker-compose.yml         # Orquestación de servicio web con Gunicorn y recarga automática
└── Dockerfile                 # Imagen base para despliegues (Flask + dependencias)
```

Los blueprints principales son:
- `auth_bp`: autenticación y sesiones (login/logout) con soporte para hashes legacy. 
- `chat_bp`: interfaz de agentes, envío de mensajes, gestión de alias/roles y adjuntos. 
- `config_bp`: mantenimiento de reglas, botones y carga masiva desde Excel. 
- `webhook_bp`: integración con WhatsApp, motor de reglas y comandos. 
- `roles_bp`: administración de roles de usuarios internos. 
- `tablero_bp`: endpoints de métricas para el dashboard. 
- `export_bp`: descarga de conversaciones en JSON/CSV. 

## Flujo de un mensaje entrante
1. **Recepción**: el endpoint `/webhook` recibe el `POST` de Meta, valida duplicados (`mensajes_procesados`) y guarda el payload en la tabla `mensajes`. 
2. **Normalización**: se unifican mayúsculas, acentos y puntuación con `normalize_text` antes de ejecutar reglas. 
3. **Comandos globales**: si el texto coincide con alguna palabra clave, se ejecuta el handler registrado y se detiene el flujo. 
4. **Evaluación de reglas**: se obtiene el `step` actual desde `chat_state`, se buscan reglas ordenadas por prioridad e incluso comodines `*` o saltos múltiples (`advance_steps`). 
5. **Respuesta**: `enviar_mensaje` construye el payload a la WhatsApp API (texto, lista, botón o medio) y registra la respuesta en la BD. 
6. **Asignación de roles/estado**: si la regla define un `rol_keyword`, se asocia el número al rol en `chat_roles` y se actualiza `chat_state` para futuros filtros. 
7. **Procesamiento diferido**: audios se encolan para transcripción y, tras completarse, se reutiliza el mismo pipeline (`handle_text_message`) como si fuera texto. 

## Modelo de datos (MySQL)
La función `init_db()` crea y migra las tablas necesarias en cada despliegue. 

| Tabla | Uso principal |
|-------|---------------|
| `mensajes` | Historial completo de conversaciones, incluyendo tipo, adjuntos y referencia a la regla disparada.  |
| `mensajes_procesados` | Previene procesar el mismo `wa_id` más de una vez.  |
| `usuarios`, `roles`, `user_roles` | Gestión de autenticación y permisos internos (seed de admin/admin123 incluido).  |
| `reglas`, `regla_medias` | Definición de flujo por pasos, tipo de respuesta, medios y handlers externos.  |
| `botones`, `boton_medias` | Catálogo de respuestas rápidas para asesores.  |
| `alias` | Nombres amigables asociados a un número.  |
| `chat_roles` | Relación entre números de clientes y roles permitidos.  |
| `chat_state` | Paso actual, estado y última actividad para controlar la sesión.  |

> 💡 Ejecuta `init_db()` al iniciar un entorno nuevo (ver sección de instalación) para asegurar migraciones y seeds.

## Configuración y variables de entorno
Las variables se cargan desde `.env` mediante `python-dotenv` y están centralizadas en `config.py`. 

| Variable | Descripción |
|----------|-------------|
| `SECRET_KEY` | Clave Flask para sesiones. |
| `META_TOKEN` | Token de acceso permanente de WhatsApp Cloud API. |
| `PHONE_NUMBER_ID` | Identificador del número de WhatsApp en Meta. |
| `VERIFY_TOKEN` | Token usado por Meta para validar el webhook. |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Credenciales de MySQL. |
| `INITIAL_STEP` | Paso inicial del flujo cuando un chat comienza o se reinicia. |
| `SESSION_TIMEOUT` | Inactividad (segundos) tras la cual se reinicia el flujo del usuario. |
| `MEDIA_ROOT` | Ruta persistente para guardar archivos subidos; por defecto `static/uploads`. |
| `MAX_TRANSCRIPTION_DURATION_MS`, `TRANSCRIPTION_MAX_AVG_TIME_SEC` | Límites para controlar la transcripción de audios. |
| `INIT_DB_ON_START` | (Opcional) Igual a `1` para ejecutar `init_db()` automáticamente al iniciar la app. |
| `AI_OCR_ENABLED` | Igual a `1` para activar el OCR en páginas sin texto embebido (requiere Tesseract). |
| `AI_OCR_DPI` | Resolución en DPI al rasterizar páginas para el OCR (por defecto 220). |
| `AI_OCR_LANG` | Idiomas instalados en Tesseract para el OCR (por defecto `spa+eng`, usa `eng` si solo tienes inglés). |
| `AI_OCR_TESSERACT_CONFIG` | Parámetros extra de Tesseract (por ejemplo, `--psm 6`). |
| `AI_OCR_TESSERACT_ENABLED` | Permite desactivar Tesseract sin deshabilitar el OCR completo (por defecto `1`). |
| `AI_OCR_EASYOCR_ENABLED` | Activa EasyOCR como alternativa cuando Tesseract no está disponible (por defecto `1`). |
| `AI_OCR_EASYOCR_LANGS` | Lista separada por comas con los idiomas de EasyOCR (ej. `es,en`); si se omite se deriva de `AI_OCR_LANG`. |
| `AI_OCR_EASYOCR_DOWNLOAD_ENABLED` | Permite que EasyOCR descargue automáticamente sus modelos si no existen (por defecto `0`). Actívalo solo si puedes esperar la descarga durante la ingesta del catálogo. |
| `AI_OCR_EASYOCR_VERBOSE` | Rehabilita los mensajes detallados/barras de progreso de EasyOCR (por defecto `0`). |
| `AI_PAGE_IMAGE_DIR` | Carpeta donde se guardan las miniaturas de cada página del catálogo (por defecto `static/uploads/catalogos/paginas`). |
| `AI_PAGE_IMAGE_FORMAT` | Formato de imagen para las vistas previas (`JPEG`, `PNG`, etc.). |
| `AI_PAGE_IMAGE_SCALE` | Factor de escala al renderizar la página antes de guardar la imagen (por defecto `2.0`). |
| `AI_PAGE_IMAGE_QUALITY` | Calidad de compresión cuando el formato es JPEG (por defecto `85`). |
| `MEDIA_PUBLIC_BASE_URL` | URL base pública para servir archivos en `MEDIA_ROOT` (por ejemplo `https://midominio.com/static/uploads/`). Si no se define se genera un enlace relativo usando `/static/`. |

## Requisitos previos
- Python 3.9+ (incluye `venv`).
- Servidor MySQL accesible y con base de datos creada.
- [ffmpeg](https://ffmpeg.org/) instalado en el sistema host (necesario para normalizar audios).
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) para interpretar catálogos escaneados o con texto embebido en imágenes. Instala también los paquetes de idioma que necesites (por ejemplo, español) si deseas aprovechar el OCR. Si no cuentas con Tesseract, la aplicación intentará usar [EasyOCR](https://www.jaided.ai/easyocr/) (incluido en `requirements.txt`) siempre que `AI_OCR_EASYOCR_ENABLED=1`. Habilita `AI_OCR_EASYOCR_DOWNLOAD_ENABLED=1` únicamente si deseas que EasyOCR descargue los modelos automáticamente durante la primera ingesta.
- Modelo de Vosk en español disponible; el primer uso lo descarga automáticamente (`vosk` >= 0.3).
- Credenciales activas de la WhatsApp Cloud API y webhook configurado hacia `/webhook`.

Para instalar `ffmpeg` manualmente:
```bash
# Ubuntu/Debian
sudo apt-get update && sudo apt-get install -y ffmpeg

# macOS (Homebrew)
brew install ffmpeg
```
Si usas contenedores, añade la instalación al `Dockerfile` o a la imagen base.

Para habilitar el OCR en catálogos escaneados instala Tesseract y sus idiomas (ejemplo en Ubuntu/Debian):

```bash
sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-spa
```
En macOS puedes usar Homebrew (`brew install tesseract tesseract-lang`), y en Windows descarga el instalador oficial. Ajusta la variable `AI_OCR_LANG` si tu instalación no incluye español.

> ℹ️ Si ejecutas la aplicación con Docker (incluyendo Docker Desktop en Windows/macOS), la imagen definida en `Dockerfile` ya instala `tesseract-ocr`, el paquete de idioma en español y dependencias como `ffmpeg`, por lo que no necesitas preparar el host manualmente para procesar catálogos PDF.

## Instalación local
```bash
python -m venv .venv
source .venv/bin/activate  # En Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env_example .env  # crea tus variables; ver tabla anterior
```
Configura tus credenciales en `.env` y asegúrate de que la base de datos exista.

### Inicializar la base de datos
Puedes crear tablas y seeds de dos maneras:
1. **Temporal**: exporta `INIT_DB_ON_START=1` y ejecuta la app una vez (`python app.py`). 
2. **Manual**: abre un shell de Python y ejecuta:
   ```python
   from app import create_app
   from services.db import init_db
   app = create_app()
   with app.app_context():
       init_db()
   ```
   Esto crea al usuario `admin` con contraseña `admin123` y los roles base. 

### Ejecutar la aplicación
```bash
python app.py  # escucha en http://0.0.0.0:5000 por defecto
```
Para producción se recomienda usar Gunicorn:
```bash
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

## Ejecución con Docker
El proyecto incluye un `docker-compose.yml` mínimo que monta el código y ejecuta Gunicorn si está instalado.
```bash
docker compose up --build
```
Recuerda proporcionar un `.env` con todas las variables y exponer MySQL (por ejemplo, mediante otro servicio en el mismo `docker-compose.yml`).

## Integración con la API de ChatGPT, embeddings y OCR

El modo conversacional con IA permite que un asesor automático responda preguntas usando un catálogo PDF vectorizado. La integración se basa en la API de OpenAI (ChatGPT), un motor de embeddings, OCR híbrido y un worker en segundo plano que detecta cuando una conversación debe pasar del flujo tradicional a la IA.

### Flujo general
1. **Ingesta del catálogo**: desde la vista de configuración (`/configuracion/ia`) un administrador puede subir un PDF que será procesado por `CatalogResponder.ingest_pdf`. Se extrae texto por página con tres estrategias en cascada: `PdfReader` (texto embebido), `pypdfium2` (texto avanzado) y OCR (`pytesseract` y/o `easyocr`) según lo permita la configuración. Cada página se divide en fragmentos solapados, se calculan SKU, se generan miniaturas y se almacena la metadata necesaria para referencias posteriores.【F:services/ai_responder.py†L107-L364】【F:routes/configuracion.py†L352-L416】
2. **Creación del índice vectorial**: los fragmentos se envían al endpoint de embeddings de OpenAI (`client.embeddings.create`), se guardan en un índice FAISS (`IndexFlatL2`) y se persiste la metadata asociada en un `.json`. También se registran estadísticas y la fecha de actualización en MySQL (`ia_settings`).【F:services/ai_responder.py†L370-L421】【F:services/db.py†L581-L760】
3. **Handoff desde el flujo de reglas**: cuando un chat alcanza el paso configurado en `AI_HANDOFF_STEP`, el webhook marca el estado como `ia_pendiente` y el mensaje se queda esperando a que el worker lo tome. Este paso se define en `.env` y, por defecto, equivale al paso `ia_chat` del flujo conversacional.【F:routes/webhook.py†L321-L360】【F:config.py†L35-L48】
4. **Worker de IA**: `AIWorker` (hilo daemon inicializado en `app.py`) consulta periódicamente los mensajes nuevos asociados al `handoff_step`. Utiliza `get_messages_for_ai` y `claim_ai_message` para procesar cada conversación de manera segura, llama a `CatalogResponder.answer` y envía la respuesta por WhatsApp. También comparte hasta tres imágenes de referencia y aplica un mensaje de fallback si el modelo no devuelve texto.【F:app.py†L13-L51】【F:services/ai_worker.py†L1-L141】【F:services/db.py†L642-L760】
5. **Respuesta de ChatGPT**: al responder, se calculan embeddings de la pregunta, se consultan los `top_k` fragmentos más similares en FAISS y se construye un prompt con instrucciones de uso exclusivo del catálogo. La generación se realiza vía `client.responses.create` con el modelo configurado en `AI_GEN_MODEL`. El texto se post-procesa para respetar límites de oraciones/caracteres y se registran referencias, métricas y bitácoras en `ia_logs`. El resultado se cachea opcionalmente en Redis para preguntas repetidas.【F:services/ai_responder.py†L423-L583】

### Variables de entorno relevantes

| Variable | Propósito |
|----------|-----------|
| `OPENAI_API_KEY` | Clave necesaria para consumir embeddings y respuestas de OpenAI. Sin ella se bloquea la ingesta de catálogos y el modo IA. |
| `AI_EMBED_MODEL` | Modelo de embeddings (por defecto `text-embedding-3-small`). Debe ser compatible con el endpoint `/embeddings`. |
| `AI_GEN_MODEL` | Modelo generativo usado por `responses.create` (por defecto `gpt-4o-mini`). |
| `AI_MODE_ENABLED` | Valor inicial por defecto para el interruptor del modo IA; se guarda en `ia_settings.enabled`. |
| `AI_HANDOFF_STEP` | Paso del flujo en el que una conversación se envía al worker de IA (por defecto `ia_chat`). |
| `AI_VECTOR_STORE_PATH` | Ruta base donde se guardan el índice FAISS (`.faiss`) y la metadata (`.json`). |
| `AI_CACHE_TTL` | TTL en segundos para cachear respuestas en Redis; requiere `REDIS_URL`. |
| `AI_MAX_OUTPUT_TOKENS`, `AI_RESPONSE_MAX_SENTENCES`, `AI_RESPONSE_MAX_CHARS` | Límites de longitud aplicados al texto generado antes de enviarlo a WhatsApp. |
| `AI_FALLBACK_MESSAGE` | Mensaje alterno que se envía cuando la IA no produce respuesta. |
| `AI_POLL_INTERVAL`, `AI_BATCH_SIZE` | Controlan la frecuencia y el tamaño de lote con el que `AIWorker` consulta mensajes pendientes. |
| `AI_OCR_*` | Agrupan las opciones para Tesseract/EasyOCR (activación, idiomas, DPI, escala, calidad, etc.). Permiten ajustar qué motor OCR se usa y cómo se generan las miniaturas de página. |
| `AI_PAGE_IMAGE_*` | Directorio y parámetros para renderizar imágenes de página que se envían como referencia visual al cliente. |

### Pipeline de OCR y miniaturas
- **Tesseract (`pytesseract`)**: se verifica que esté instalado y que existan los idiomas declarados. Si falta, el sistema registra una advertencia y usa EasyOCR como respaldo si está permitido.【F:services/ai_responder.py†L126-L224】
- **EasyOCR**: se instancia bajo demanda, sin GPU y con control sobre la descarga de modelos (`AI_OCR_EASYOCR_DOWNLOAD_ENABLED`). Si ninguno de los motores está disponible y el PDF carece de texto embebido, la ingesta se aborta con un mensaje descriptivo.【F:services/ai_responder.py†L226-L362】
- **Rasterizado de páginas**: `pypdfium2` renderiza cada página, opcionalmente cambiando escala, formato y calidad. Las imágenes resultantes se guardan en `AI_PAGE_IMAGE_DIR/<hash>/page_XXXX.jpg` y se exponen a través de `/static` o de `MEDIA_PUBLIC_BASE_URL` para ser enviadas por WhatsApp como evidencia del catálogo.【F:services/ai_responder.py†L246-L360】

### Persistencia y bitácoras
- La tabla `ia_settings` almacena el estado global (habilitado, puntero `last_processed_message_id`, estadísticas y rutas de catálogo). Se mantiene actualizada al ingerir catálogos, al activar/desactivar la IA y al reclamar mensajes desde el worker.【F:services/db.py†L581-L760】
- Las interacciones exitosas se registran en `ia_logs`, guardando la pregunta, la respuesta y la metadata (fragmentos usados, si vino de caché, etc.). Esto permite auditorías y métricas futuras.【F:services/ai_responder.py†L566-L583】【F:services/db.py†L779-L815】
- `reset_ai_conversations` reubica a los usuarios que se encontraban en el `handoff_step` cuando la IA se desactiva, devolviéndolos al `INITIAL_STEP` para que sigan en el flujo tradicional.【F:routes/configuracion.py†L352-L416】【F:services/db.py†L762-L778】

### Recomendaciones operativas
- Antes de activar la IA, ejecuta la ingesta del catálogo y verifica que `summary` muestre el número esperado de fragmentos y páginas en la vista de configuración. Asegúrate de que el `handoff_step` exista en tu flujo de reglas para evitar bloqueos.
- Si usas Docker, confirma que la imagen incluya `faiss`, `pypdfium2`, `tesseract-ocr`, modelos de idioma necesarios y los binarios de `poppler`/`ghostscript` según tu SO. Estas dependencias son imprescindibles para extraer texto e imágenes.
- Para aprovechar la caché de respuestas, configura `REDIS_URL`. Si Redis no está disponible, el sistema continúa funcionando sin cachear pero muestra advertencias en los logs.【F:services/ai_responder.py†L85-L124】

## Panel web y herramientas
- **/login**: acceso de usuarios internos (hashes PBKDF2 y compatibilidad con SHA-256 legacy). 
- **/**: vista principal tipo WhatsApp Web con lista de chats, filtros por rol, alias y envío de respuestas rápidas/botones/listas. 
- **/configuracion/reglas** y **/configuracion/botones**: CRUD de reglas y botones, subida de medios, importación desde Excel y validación de URLs. 
- **/roles**: administración de roles, keywords y asignaciones usuario ↔ rol. 
- **/tablero**: dashboard con filtros por rango de fechas, rol o número para analizar volumen, tipos de mensajes y engagement. 
- **/export/conversation/<numero>[.csv]**: exporta la última conversación agregada (bot + cliente + metadatos). 

## Automatización del flujo
- **Pasos (`step`)**: cada número mantiene su estado en `chat_state`. Cuando llega un mensaje, se evalúan las reglas del paso actual y se avanza según `siguiente_step`. 
- **Comodín `*`**: reglas con `input_text='*'` actúan como respuesta por defecto o se ejecutan en cascada durante un salto múltiple (`advance_steps`). 
- **Handlers personalizados**: reglas pueden definir `handler` y `calculo` para procesar medidas o lógica específica (ej. cálculos de mesones). 
- **Comandos globales**: antes de evaluar reglas, `handle_global_command` revisa palabras clave y puede reiniciar el flujo sin perder contexto. 
- **Sesiones**: si un usuario queda inactivo más del `SESSION_TIMEOUT`, el flujo se reinicia automáticamente (`delete_chat_state`). 

## Manejo de medios y transcripción
- Los archivos subidos por asesores se guardan en `MEDIA_ROOT` y se exponen vía `static/uploads/`.
- Los medios entrantes desde WhatsApp se descargan con el token de Meta y se almacenan localmente.
- Audios entrantes generan un job en `services/job_queue.py`; `services/tasks.py` convierte, transcribe y reinyecta el texto al flujo.
- `services/transcripcion.py` usa ffmpeg para normalizar a WAV mono 16 kHz, luego Vosk para convertir a texto. Si la duración excede `MAX_TRANSCRIPTION_DURATION_MS` o el tiempo promedio supera el umbral, la transcripción se omite.

## Modo IA conversacional
- El proyecto incluye un nuevo modo de atención híbrida basado en embeddings y modelos de la plataforma OpenAI. El paso inicial del flujo sigue siendo gestionado por las reglas; para entregar el control a la IA crea una regla que establezca el `step` en `ia_chat` (valor por defecto de `AI_HANDOFF_STEP`).
- En **Configuración → Modo IA conversacional** puedes activar o desactivar el modo, procesar un PDF con el catálogo del cliente y consultar métricas (fragmentos indexados, fuentes y fecha de actualización). El pipeline aplica: PDF → texto → _chunks_ → embeddings (`text-embedding-3-small`) → búsqueda semántica FAISS → respuesta generada con `gpt-4o-mini`.
- Durante la ingesta se generan miniaturas JPEG por página dentro de `AI_PAGE_IMAGE_DIR/<hash>/page_XXXX.jpg` y cada fragmento indexado incluye la ruta relativa (`image`) y el método de extracción (`backend`). Estos metadatos permiten enviar al cliente la página exacta del catálogo como soporte visual.
- El OCR prioriza Tesseract cuando está disponible, pero si no se encuentra la aplicación recurre automáticamente a EasyOCR (configurable mediante las variables `AI_OCR_TESSERACT_ENABLED` y `AI_OCR_EASYOCR_ENABLED`).
- El worker `services/ai_worker.py` vigila la tabla `mensajes` y responde únicamente cuando el estado del cliente coincide con `AI_HANDOFF_STEP`. El primer mensaje siempre pasa por el motor de reglas; al desactivar la IA, las conversaciones en el paso IA regresan al `INITIAL_STEP`.
- Las respuestas se cachean de forma opcional en Redis (`REDIS_URL`) para acelerar preguntas frecuentes y se registran en la tabla `ia_logs` junto con la página y SKU sugeridos. El índice FAISS y los metadatos se guardan en `AI_VECTOR_STORE_PATH` (por defecto `data/catalog_index.*`).
- Variables de entorno relevantes: `OPENAI_API_KEY`, `AI_HANDOFF_STEP`, `AI_VECTOR_STORE_PATH`, `AI_POLL_INTERVAL`, `AI_BATCH_SIZE`, `AI_CACHE_TTL`, `CATALOG_UPLOAD_DIR`, `AI_FALLBACK_MESSAGE` y `REDIS_URL`. Al activar el modo IA se actualiza automáticamente el puntero de mensajes procesados para evitar respuestas duplicadas.

## Exportes y analítica
- `routes/tablero_routes.py` expone JSON para gráficos de palabras frecuentes, top números, volumen por día/hora y desglose por tipo o rol. Esto permite construir widgets en `tablero.html`.
- `routes/export_routes.py` agrega la información relevante de una conversación (mensajes, últimos pasos, etc.) y la serializa a JSON/CSV bajo demanda.

## Scripts y mantenimiento
- `scripts/rehash_passwords.py` ayuda a migrar contraseñas antiguas (SHA-256 plano) a hashes modernos. Ejecuta el script en un entorno controlado, solicitando la contraseña actual de cada usuario. 
- Para generar nuevos comandos globales, crea un handler en `services/global_commands.py` y regístralo en `GLOBAL_COMMANDS`. 

## Buenas prácticas y consideraciones
- Mantén `MEDIA_ROOT` apuntando a un volumen persistente fuera del repositorio para evitar perder adjuntos en despliegues. 
- Excluye archivos de base de datos locales (`database.db`, `chat_support.db`) del control de versiones; la aplicación está pensada para MySQL en producción. 
- Protege el token de Meta y el `SECRET_KEY`. Considera usar un gestor de secretos en producción.
- Configura HTTPS y un dominio válido para que Meta entregue webhooks exitosamente.
- Monitorea el log `app.log` generado en producción cuando `DEBUG` está desactivado. 

## Próximos pasos
- Implementación IA para atención de clientes.
