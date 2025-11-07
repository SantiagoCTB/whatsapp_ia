import os
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int = 0, min_value: Optional[int] = None) -> int:
    value = os.getenv(name)
    if value is None:
        result = default
    else:
        raw = value.strip()
        if not raw:
            result = default
        else:
            try:
                result = int(raw)
            except ValueError:
                result = default

    if min_value is not None:
        result = max(result, min_value)

    return result


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY')
    META_TOKEN = os.getenv('META_TOKEN')
    PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
    VERIFY_TOKEN = os.getenv('VERIFY_TOKEN')
    DB_PATH = 'database.db'
    # Tiempo (en segundos) antes de reiniciar la sesión por inactividad. Puede
    # sobreescribirse estableciendo la variable de entorno ``SESSION_TIMEOUT``.
    SESSION_TIMEOUT = _env_int('SESSION_TIMEOUT', 1800, min_value=0)
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
    AI_KEYWORD_REDIRECT_STEP = os.getenv('AI_KEYWORD_REDIRECT_STEP', 'flow').strip().lower()
    AI_VECTOR_STORE_PATH = os.getenv(
        'AI_VECTOR_STORE_PATH',
        os.path.join(BASEDIR, 'data', 'catalog_index')
    )
    AI_POLL_INTERVAL = float(os.getenv('AI_POLL_INTERVAL', 3))
    AI_BATCH_SIZE    = int(os.getenv('AI_BATCH_SIZE', 10))
    AI_CACHE_TTL     = int(os.getenv('AI_CACHE_TTL', 3600))
    AI_HISTORY_MESSAGE_LIMIT = _env_int('AI_HISTORY_MESSAGE_LIMIT', 6, min_value=0)
    AI_FALLBACK_MESSAGE = os.getenv(
        'AI_FALLBACK_MESSAGE',
        'Por ahora no tengo información del catálogo, intentaré más tarde.'
    )
    AI_MAX_OUTPUT_TOKENS = int(os.getenv('AI_MAX_OUTPUT_TOKENS', 200))
    AI_RESPONSE_MAX_SENTENCES = int(os.getenv('AI_RESPONSE_MAX_SENTENCES', 3))
    AI_RESPONSE_MAX_CHARS = int(os.getenv('AI_RESPONSE_MAX_CHARS', 480))
    AI_OCR_ENABLED = _env_bool('AI_OCR_ENABLED', True)
    AI_OCR_DPI = int(os.getenv('AI_OCR_DPI', 220))
    AI_OCR_LANG = os.getenv('AI_OCR_LANG', 'spa+eng')
    AI_OCR_TESSERACT_CONFIG = os.getenv('AI_OCR_TESSERACT_CONFIG')
    AI_OCR_TESSERACT_ENABLED = _env_bool('AI_OCR_TESSERACT_ENABLED', True)
    AI_OCR_TESSERACT_TIMEOUT = int(os.getenv('AI_OCR_TESSERACT_TIMEOUT', 60))
    AI_OCR_EASYOCR_ENABLED = _env_bool('AI_OCR_EASYOCR_ENABLED', True)
    AI_OCR_EASYOCR_LANGS = os.getenv('AI_OCR_EASYOCR_LANGS')
    AI_OCR_EASYOCR_DOWNLOAD_ENABLED = _env_bool('AI_OCR_EASYOCR_DOWNLOAD_ENABLED', False)
    AI_OCR_EASYOCR_VERBOSE = _env_bool('AI_OCR_EASYOCR_VERBOSE', False)
    MEDIA_PUBLIC_BASE_URL = os.getenv('MEDIA_PUBLIC_BASE_URL')
    REDIS_URL = os.getenv('REDIS_URL')
    CATALOG_UPLOAD_DIR = os.getenv(
        'CATALOG_UPLOAD_DIR',
        os.path.join(MEDIA_ROOT, 'catalogos')
    )
    AI_PAGE_IMAGE_DIR = os.getenv(
        'AI_PAGE_IMAGE_DIR',
        os.path.join(CATALOG_UPLOAD_DIR, 'paginas')
    )
    AI_PAGE_IMAGE_FORMAT = os.getenv('AI_PAGE_IMAGE_FORMAT', 'JPEG')
    AI_PAGE_IMAGE_SCALE = float(os.getenv('AI_PAGE_IMAGE_SCALE', 2.0))
    AI_PAGE_IMAGE_QUALITY = int(os.getenv('AI_PAGE_IMAGE_QUALITY', 85))

    os.makedirs(MEDIA_ROOT, exist_ok=True)
    os.makedirs(CATALOG_UPLOAD_DIR, exist_ok=True)
    os.makedirs(AI_PAGE_IMAGE_DIR, exist_ok=True)
