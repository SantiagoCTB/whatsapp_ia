import os


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    META_TOKEN = os.getenv('META_TOKEN')
    PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
    VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')
    DB_PATH = 'database.db'
    SESSION_TIMEOUT = 600
    INITIAL_STEP = os.getenv('INITIAL_STEP', 'menu_principal')

    MAX_TRANSCRIPTION_DURATION_MS = int(os.getenv('MAX_TRANSCRIPTION_DURATION_MS', 60000))
    TRANSCRIPTION_MAX_AVG_TIME_SEC = float(os.getenv('TRANSCRIPTION_MAX_AVG_TIME_SEC', 10))

    DB_HOST     = os.getenv('DB_HOST')
    DB_PORT     = int(os.getenv('DB_PORT', 3306))
    DB_USER     = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME     = os.getenv('DB_NAME')

    BASEDIR    = os.path.dirname(os.path.abspath(__file__))
    MEDIA_ROOT = os.getenv("MEDIA_ROOT", os.path.join(BASEDIR, "static", "uploads"))

    OPENAI_API_KEY   = os.getenv('OPENAI_API_KEY')
    AI_EMBED_MODEL   = os.getenv('AI_EMBED_MODEL', 'text-embedding-3-small')
    AI_GEN_MODEL     = os.getenv('AI_GEN_MODEL', 'gpt-4o-mini')
    AI_MODE_DEFAULT  = _env_bool('AI_MODE_ENABLED', False)
    AI_HANDOFF_STEP  = os.getenv('AI_HANDOFF_STEP', 'ia_chat').strip().lower()
    AI_VECTOR_STORE_PATH = os.getenv(
        'AI_VECTOR_STORE_PATH',
        os.path.join(BASEDIR, 'data', 'catalog_index')
    )
    AI_POLL_INTERVAL = float(os.getenv('AI_POLL_INTERVAL', 3))
    AI_BATCH_SIZE    = int(os.getenv('AI_BATCH_SIZE', 10))
    AI_CACHE_TTL     = int(os.getenv('AI_CACHE_TTL', 3600))
    AI_FALLBACK_MESSAGE = os.getenv(
        'AI_FALLBACK_MESSAGE',
        'Por ahora no tengo información del catálogo, intentaré más tarde.'
    )
    REDIS_URL = os.getenv('REDIS_URL')
    CATALOG_UPLOAD_DIR = os.getenv(
        'CATALOG_UPLOAD_DIR',
        os.path.join(MEDIA_ROOT, 'catalogos')
    )

    os.makedirs(MEDIA_ROOT, exist_ok=True)
    os.makedirs(CATALOG_UPLOAD_DIR, exist_ok=True)
