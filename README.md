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

## Requisitos previos
- Python 3.9+ (incluye `venv`).
- Servidor MySQL accesible y con base de datos creada.
- [ffmpeg](https://ffmpeg.org/) instalado en el sistema host (necesario para normalizar audios). 
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

## Próximos pasos sugeridos
- Automatizar despliegues con CI/CD y migraciones controladas.
- Añadir pruebas unitarias para handlers y reglas críticas.
- Extender la capa analítica con alertas (ej. chats sin respuesta en X minutos).
- Incorporar paginación o archivado de chats históricos para mejorar rendimiento.
