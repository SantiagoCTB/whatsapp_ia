import json
import inspect
import logging
import os
import subprocess
import threading
import hashlib
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import faiss
import numpy as np
from openai import OpenAI
from pypdf import PdfReader

try:
    import pypdfium2 as pdfium
except Exception:  # pragma: no cover - OCR es opcional
    pdfium = None

try:
    import redis
except Exception:  # pragma: no cover - redis es opcional
    redis = None

try:
    import pytesseract
except Exception:  # pragma: no cover - OCR es opcional
    pytesseract = None

try:
    import easyocr  # type: ignore
except Exception:  # pragma: no cover - OCR es opcional
    easyocr = None

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - OCR es opcional
    Image = None

from config import Config
from services.catalog_entities import (
    find_entities_in_text,
    get_known_entity_names,
    score_fields_against_entities,
)
from services.db import (
    log_ai_interaction,
    set_ai_last_processed_to_latest,
    update_ai_catalog_metadata,
)


SKU_PATTERN = re.compile(r"\bSKU[:\s-]*([A-Z0-9-]{3,})\b", re.IGNORECASE)


_CATALOG_NAME_BULLETS = "".join(f"- {name}\n" for name in get_known_entity_names())


class CatalogResponder:
    """Gestiona la ingesta de catálogos y respuestas basadas en embeddings."""

    _instance: Optional["CatalogResponder"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._client: Optional[OpenAI] = OpenAI(api_key=Config.OPENAI_API_KEY) if Config.OPENAI_API_KEY else None
        self._index: Optional[faiss.Index] = None
        self._metadata: List[Dict[str, object]] = []
        self._index_lock = threading.RLock()
        self._base_path = Config.AI_VECTOR_STORE_PATH
        self._index_path = f"{self._base_path}.faiss"
        self._metadata_path = f"{self._base_path}.json"
        base_dir = os.path.dirname(self._index_path)
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)
        self._redis = self._init_redis()
        self._cache_ttl = Config.AI_CACHE_TTL
        self._last_mtime: float = 0.0
        self._tesseract_ready: Optional[bool] = None
        self._tesseract_lang_arg: Optional[str] = None
        self._easyocr_reader: Optional[object] = None
        self._easyocr_failed = False

    @classmethod
    def instance(cls) -> "CatalogResponder":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    # --- utilidades internas -------------------------------------------------

    def _init_redis(self):
        if not Config.REDIS_URL or redis is None:
            return None
        try:
            client = redis.from_url(Config.REDIS_URL)
            client.ping()
            return client
        except Exception:
            logging.warning("No se pudo inicializar Redis para caché de IA", exc_info=True)
            return None

    def _ensure_client(self) -> OpenAI:
        if self._client is None:
            if not Config.OPENAI_API_KEY:
                raise RuntimeError("OPENAI_API_KEY no configurada")
            self._client = OpenAI(api_key=Config.OPENAI_API_KEY)
        return self._client

    def _ensure_index_loaded(self) -> None:
        with self._index_lock:
            if not os.path.exists(self._index_path) or not os.path.exists(self._metadata_path):
                self._index = None
                self._metadata = []
                self._last_mtime = 0.0
                return
            mtime = max(os.path.getmtime(self._index_path), os.path.getmtime(self._metadata_path))
            if self._index is not None and self._last_mtime == mtime:
                return
            self._index = faiss.read_index(self._index_path)
            with open(self._metadata_path, "r", encoding="utf-8") as fh:
                self._metadata = json.load(fh)
            self._augment_metadata_with_images(self._metadata)
            self._last_mtime = mtime

    def _ensure_tesseract_available(self) -> bool:
        if self._tesseract_ready is not None:
            return self._tesseract_ready

        if pytesseract is None:
            self._tesseract_ready = False
            self._tesseract_lang_arg = None
            return False

        try:
            pytesseract.get_tesseract_version()
            required_langs = self._resolve_tesseract_langs()
            self._tesseract_lang_arg = "+".join(required_langs) if required_langs else None
            available_langs: Optional[List[str]] = None
            if hasattr(pytesseract, "get_languages"):
                try:
                    tess_config = Config.AI_OCR_TESSERACT_CONFIG or ""
                    available_langs = pytesseract.get_languages(config=tess_config)
                except Exception:
                    logging.warning(
                        "No se pudieron obtener los idiomas instalados de Tesseract.",
                        exc_info=True,
                    )
            if available_langs is not None:
                available_set = set(available_langs or [])
                present = [lang for lang in required_langs if lang in available_set]
                missing = [lang for lang in required_langs if lang not in available_set]
                if missing and present:
                    logging.warning(
                        "Tesseract está instalado pero faltan los paquetes de idioma requeridos: %s. "
                        "Se utilizarán únicamente los disponibles: %s.",
                        ", ".join(missing),
                        ", ".join(present),
                    )
                    self._tesseract_lang_arg = "+".join(present) if present else None
                elif missing and not present:
                    logging.warning(
                        "Tesseract está instalado pero no cuenta con los idiomas solicitados (%s). "
                        "Se usará el idioma predeterminado del sistema.",
                        ", ".join(missing),
                    )
                    self._tesseract_lang_arg = None
            else:
                if self._tesseract_lang_arg:
                    logging.info(
                        "No fue posible validar los idiomas de Tesseract; se continuará con la configuración declarada (%s).",
                        self._tesseract_lang_arg,
                    )
                else:
                    logging.info(
                        "No fue posible validar los idiomas de Tesseract; se utilizará el idioma predeterminado del sistema."
                    )
        except Exception as exc:
            tesseract_mod = getattr(pytesseract, "pytesseract", None)
            not_found_exc = getattr(tesseract_mod, "TesseractNotFoundError", None) if tesseract_mod else None
            if not_found_exc is not None and isinstance(exc, not_found_exc):
                logging.warning("Tesseract no está instalado en el sistema.")
            else:
                logging.warning("No se pudo comprobar la instalación de Tesseract", exc_info=True)
            self._tesseract_ready = False
            self._tesseract_lang_arg = None
            return False

        self._tesseract_ready = True
        return True

    def _get_tesseract_lang_argument(self) -> Optional[str]:
        if self._tesseract_lang_arg:
            return self._tesseract_lang_arg
        raw = (Config.AI_OCR_LANG or "").strip()
        return raw or None

    def _ensure_easyocr_reader(self):
        if not Config.AI_OCR_ENABLED or not Config.AI_OCR_EASYOCR_ENABLED:
            return None
        if easyocr is None:
            return None
        if self._easyocr_failed:
            return None
        if self._easyocr_reader is not None:
            return self._easyocr_reader

        languages = self._resolve_easyocr_langs()
        try:
            reader_kwargs = {"gpu": False}
            try:
                signature = inspect.signature(easyocr.Reader)
            except (TypeError, ValueError):
                signature = None

            if signature is not None:
                parameters = signature.parameters
                if "download_enabled" in parameters:
                    reader_kwargs["download_enabled"] = getattr(
                        Config, "AI_OCR_EASYOCR_DOWNLOAD_ENABLED", False
                    )
                if "verbose" in parameters:
                    reader_kwargs["verbose"] = getattr(Config, "AI_OCR_EASYOCR_VERBOSE", False)

            self._easyocr_reader = easyocr.Reader(languages, **reader_kwargs)
        except FileNotFoundError:
            logging.warning(
                "EasyOCR no cuenta con los modelos requeridos. Descárgalos previamente o activa "
                "AI_OCR_EASYOCR_DOWNLOAD_ENABLED=1 para permitir la descarga automática."
            )
            self._easyocr_failed = True
            self._easyocr_reader = None
            return None
        except Exception:
            logging.warning("No se pudo inicializar EasyOCR", exc_info=True)
            self._easyocr_failed = True
            self._easyocr_reader = None
            return None

        return self._easyocr_reader

    @staticmethod
    def _resolve_tesseract_langs() -> List[str]:
        raw = (Config.AI_OCR_LANG or "").strip()
        if not raw:
            return ["spa", "eng"]

        tokens = [tok for tok in re.split(r"[+,;\s]+", raw) if tok]
        mapping = {
            "es": "spa",
            "spa": "spa",
            "esp": "spa",
            "español": "spa",
            "english": "eng",
            "en": "eng",
            "inglés": "eng",
            "eng": "eng",
            "fr": "fra",
            "fra": "fra",
            "francés": "fra",
            "it": "ita",
            "ita": "ita",
            "pt": "por",
            "por": "por",
            "portugués": "por",
            "de": "deu",
            "ger": "deu",
            "deu": "deu",
        }

        resolved: List[str] = []
        for token in tokens:
            norm = token.strip().lower()
            if not norm:
                continue
            mapped = mapping.get(norm, norm)
            if mapped not in resolved:
                resolved.append(mapped)

        if "spa" not in resolved:
            resolved.insert(0, "spa")
        if "eng" not in resolved:
            resolved.append("eng")
        return resolved

    @staticmethod
    def _resolve_easyocr_langs() -> List[str]:
        raw = (Config.AI_OCR_EASYOCR_LANGS or Config.AI_OCR_LANG or "").strip()
        if not raw:
            return ["es", "en"]

        tokens = [tok for tok in re.split(r"[+,;\s]+", raw) if tok]
        mapping = {
            "spa": "es",
            "esp": "es",
            "es": "es",
            "eng": "en",
            "en": "en",
            "fra": "fr",
            "fre": "fr",
            "ita": "it",
            "por": "pt",
            "pt": "pt",
            "deu": "de",
            "ger": "de",
        }
        resolved: List[str] = []
        for token in tokens:
            norm = token.strip().lower()
            if not norm:
                continue
            mapped = mapping.get(norm, norm)
            if mapped not in resolved:
                resolved.append(mapped)
        if "es" not in resolved:
            resolved.insert(0, "es")
        return resolved or ["es", "en"]

    @staticmethod
    def _compute_pdf_hash(pdf_path: str) -> str:
        sha1 = hashlib.sha1()
        try:
            with open(pdf_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(8192), b""):
                    if not chunk:
                        break
                    sha1.update(chunk)
        except Exception:
            logging.warning("No se pudo calcular el hash del PDF, se usará la ruta como clave.", exc_info=True)
            return hashlib.sha1(pdf_path.encode("utf-8")).hexdigest()
        return sha1.hexdigest()

    def _augment_metadata_with_images(self, metadata: List[Dict[str, object]]) -> None:
        for item in metadata:
            if not isinstance(item, dict):
                continue
            image_path = item.get("image")
            if image_path and not item.get("image_url"):
                url = self._build_public_image_url(str(image_path))
                if url:
                    item["image_url"] = url

    def _build_public_image_url(self, relative_path: Optional[str]) -> Optional[str]:
        if not relative_path:
            return None

        relative_path = str(relative_path).strip()
        if not relative_path:
            return None

        normalized_rel = relative_path.replace("\\", "/")
        base_url = (Config.MEDIA_PUBLIC_BASE_URL or "").strip()
        if base_url:
            if not base_url.endswith("/"):
                base_url = f"{base_url}/"
            return urljoin(base_url, normalized_rel.lstrip("/"))

        static_root = os.path.join(Config.BASEDIR, "static")
        abs_media_path = os.path.normpath(os.path.join(Config.MEDIA_ROOT, relative_path))
        try:
            common = os.path.commonpath([abs_media_path, static_root])
        except ValueError:
            common = None

        if common and os.path.normpath(common) == os.path.normpath(static_root):
            rel_to_static = os.path.relpath(abs_media_path, static_root).replace(os.sep, "/")
            try:
                from flask import url_for

                return url_for("static", filename=rel_to_static, _external=True)
            except Exception:
                return f"/static/{rel_to_static}"

        return None

    def _prepare_reference(self, reference: Dict[str, object]) -> Dict[str, object]:
        ref = dict(reference)
        image_path = ref.get("image")
        if image_path and not ref.get("image_url"):
            url = self._build_public_image_url(str(image_path))
            if url:
                ref["image_url"] = url
                if isinstance(reference, dict):
                    reference.setdefault("image_url", url)
        return ref

    def _ensure_page_image(
        self,
        pdf_path: str,
        page_number: int,
        pdf_hash: str,
        image_context: Dict[str, object],
        pil_image=None,
    ) -> Optional[str]:
        if not Config.AI_PAGE_IMAGE_DIR:
            return None

        cache = image_context.setdefault("cache", {})
        cached = cache.get(page_number)
        if cached:
            return cached

        if pil_image is None:
            if pdfium is None:
                image_context.setdefault("error", "missing_libs")
                return None
            if image_context.get("doc") is None:
                try:
                    image_context["doc"] = pdfium.PdfDocument(pdf_path)
                except Exception:
                    logging.warning(
                        "No se pudo abrir el PDF para generar imágenes", exc_info=True
                    )
                    image_context["error"] = "init_failed"
                    return None
            doc = image_context.get("doc")
            try:
                page = doc[page_number - 1]
            except Exception:
                logging.warning(
                    "No se pudo acceder a la página %s para generar la imagen", page_number, exc_info=True
                )
                image_context["error"] = "page_failed"
                return None
            try:
                try:
                    scale = max(1.0, float(Config.AI_PAGE_IMAGE_SCALE))
                except Exception:
                    scale = 2.0
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()
            except Exception:
                logging.warning(
                    "No se pudo renderizar la página %s para imagen", page_number, exc_info=True
                )
                image_context["error"] = "render_failed"
                page.close()
                return None
            finally:
                page.close()

        if pil_image is None:
            return None

        try:
            image_format = (Config.AI_PAGE_IMAGE_FORMAT or "JPEG").upper()
        except Exception:
            image_format = "JPEG"
        ext = "jpg" if image_format == "JPEG" else image_format.lower()

        try:
            if image_format == "JPEG" and getattr(pil_image, "mode", "") in {"RGBA", "P", "LA"}:
                pil_image = pil_image.convert("RGB")
            elif getattr(pil_image, "mode", "") == "P":
                pil_image = pil_image.convert("RGB")
        except Exception:
            logging.warning("No se pudo normalizar la imagen de la página %s", page_number, exc_info=True)
            image_context["error"] = "prepare_failed"
            return None

        output_dir = os.path.join(Config.AI_PAGE_IMAGE_DIR, pdf_hash)
        os.makedirs(output_dir, exist_ok=True)
        filename = f"page_{page_number:04d}.{ext}"
        full_path = os.path.join(output_dir, filename)

        save_kwargs: Dict[str, object] = {}
        if image_format == "JPEG":
            try:
                save_kwargs["quality"] = int(Config.AI_PAGE_IMAGE_QUALITY)
            except Exception:
                save_kwargs["quality"] = 85

        try:
            pil_image.save(full_path, format=image_format, **save_kwargs)
        except Exception:
            logging.warning("No se pudo guardar la imagen de la página %s", page_number, exc_info=True)
            image_context["error"] = "save_failed"
            return None

        rel_path = os.path.relpath(full_path, Config.MEDIA_ROOT)
        cache[page_number] = rel_path
        return rel_path

    def _prepare_image_for_ocr(self, pil_image):
        if pil_image is None:
            return None

        image = pil_image

        try:
            if getattr(image, "mode", "") not in {"RGB", "L"}:
                image = image.convert("RGB")
        except Exception:
            logging.warning("No se pudo convertir la imagen para OCR", exc_info=True)
            return pil_image

        try:
            max_dim = int(getattr(Config, "AI_OCR_MAX_IMAGE_DIMENSION", 0))
        except Exception:
            max_dim = 0

        max_dim = max(0, max_dim)

        if max_dim and Image is not None:
            try:
                width, height = image.size
                largest = max(width, height)
                if largest > max_dim:
                    scale = max_dim / float(largest)
                    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                    resample = getattr(Image, "LANCZOS", getattr(Image, "BICUBIC", getattr(Image, "BILINEAR", 1)))
                    image = image.resize(new_size, resample=resample)
            except Exception:
                logging.warning("No se pudo redimensionar la imagen para OCR", exc_info=True)

        try:
            desired_format = getattr(Config, "AI_OCR_TESSERACT_IMAGE_FORMAT", "TIFF")
            if not desired_format:
                desired_format = "TIFF"
            desired_format = desired_format.upper()
        except Exception:
            desired_format = "TIFF"

        allowed_formats = {
            "JPEG",
            "JPEG2000",
            "PNG",
            "PBM",
            "PGM",
            "PPM",
            "TIFF",
            "BMP",
            "GIF",
            "WEBP",
        }
        if desired_format not in allowed_formats:
            desired_format = "TIFF"

        try:
            image.format = desired_format
        except Exception:
            pass

        return image

    @staticmethod
    def _chunk_text(text: str) -> List[str]:
        """Normaliza el texto de una página y lo devuelve como un solo fragmento."""

        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if not cleaned:
            return []
        return [cleaned]

    @staticmethod
    def _extract_skus(text: str) -> List[str]:
        if not text:
            return []
        found = SKU_PATTERN.findall(text)
        seen = set()
        result: List[str] = []
        for sku in found:
            sku_up = sku.strip().upper()
            if sku_up and sku_up not in seen:
                seen.add(sku_up)
                result.append(sku_up)
        return result

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        client = self._ensure_client()
        response = client.embeddings.create(model=Config.AI_EMBED_MODEL, input=texts)
        return [item.embedding for item in response.data]

    def _cache_key(self, question: str, history: Optional[List[Dict[str, str]]]) -> str:
        normalized_question = question.strip().lower()
        history_payload = ""
        if history:
            try:
                history_payload = json.dumps(history, ensure_ascii=False, sort_keys=True)
            except Exception:
                history_payload = str(history)
        cache_input = f"{normalized_question}\n{history_payload}" if history_payload else normalized_question
        return hashlib.sha1(cache_input.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_stats(metadata: List[Dict[str, object]]) -> Dict[str, object]:
        sources = sorted({str(m.get("source")) for m in metadata if m.get("source")})
        pages = max((int(m.get("page") or 0) for m in metadata), default=0)
        return {"chunks": len(metadata), "sources": sources, "pages": pages}

    def _extract_text_via_pdfium(
        self, pdf_path: str, page_number: int, pdfium_context: Dict[str, object]
    ) -> str:
        """Intenta una extracción directa de texto usando pypdfium2 antes de recurrir a OCR."""

        if pdfium is None:
            pdfium_context.setdefault("error", "missing_libs")
            return ""

        if pdfium_context.get("error") in {"init_failed", "page_failed", "text_failed"}:
            return ""

        if pdfium_context.get("doc") is None:
            try:
                pdfium_context["doc"] = pdfium.PdfDocument(pdf_path)
            except Exception:
                logging.warning(
                    "No se pudo abrir el PDF con pypdfium2 para extracción de texto", exc_info=True
                )
                pdfium_context["error"] = "init_failed"
                return ""

        doc = pdfium_context.get("doc")
        try:
            page = doc[page_number - 1]
        except Exception:
            logging.warning(
                "No se pudo acceder a la página %s con pypdfium2", page_number, exc_info=True
            )
            pdfium_context["error"] = "page_failed"
            return ""

        textpage = None
        text = ""
        try:
            textpage = page.get_textpage()
            text = textpage.get_text_bounded() or ""
            text = textpage.get_text_range() or ""
        except Exception:
            logging.warning(
                "Falló la extracción de texto vía pypdfium2 en la página %s", page_number, exc_info=True
            )
            pdfium_context["error"] = "text_failed"
        finally:
            if textpage is not None:
                try:
                    textpage.close()
                except Exception:
                    pass
            page.close()

        if text.strip():
            pdfium_context.pop("error", None)
        return text

    def _extract_text_via_ocr(
        self,
        pdf_path: str,
        page_number: int,
        ocr_context: Dict[str, object],
        image_context: Dict[str, object],
        pdf_hash: str,
    ) -> Tuple[str, Optional[str]]:
        """Intenta reconocer texto mediante OCR cuando una página no tiene texto embebido."""

        if not Config.AI_OCR_ENABLED:
            return "", None
        if pdfium is None:
            ocr_context.setdefault("error", "missing_libs")
            return "", None

        fatal_errors = {"missing_libs", "init_failed"}
        if ocr_context.get("error") in fatal_errors:
            return "", None

        if ocr_context.get("doc") is None:
            try:
                ocr_context["doc"] = pdfium.PdfDocument(pdf_path)
            except Exception:
                logging.warning("No se pudo abrir el PDF para OCR", exc_info=True)
                ocr_context["error"] = "init_failed"
                return "", None

        doc = ocr_context.get("doc")
        try:
            page = doc[page_number - 1]
        except Exception:
            logging.warning("No se pudo acceder a la página %s para OCR", page_number, exc_info=True)
            ocr_context["error"] = "page_failed"
            return "", None

        try:
            dpi = max(72, Config.AI_OCR_DPI)
        except Exception:
            dpi = 220

        pil_image = None
        try:
            scale = dpi / 72.0
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil()
        except Exception:
            logging.warning("No se pudo renderizar la página %s para OCR", page_number, exc_info=True)
            ocr_context["error"] = "render_failed"
            return "", None
        finally:
            page.close()

        self._ensure_page_image(pdf_path, page_number, pdf_hash, image_context, pil_image=pil_image)

        pil_image_for_ocr = self._prepare_image_for_ocr(pil_image)
        if pil_image_for_ocr is None:
            pil_image_for_ocr = pil_image

        text = ""
        backend_used: Optional[str] = None
        backends_available = False
        local_error: Optional[str] = None

        tess_kwargs: Dict[str, object] = {}
        if Config.AI_OCR_TESSERACT_CONFIG:
            tess_kwargs["config"] = Config.AI_OCR_TESSERACT_CONFIG
        if Config.AI_OCR_TESSERACT_TIMEOUT:
            tess_kwargs["timeout"] = max(1, int(Config.AI_OCR_TESSERACT_TIMEOUT))

        if Config.AI_OCR_TESSERACT_ENABLED and pytesseract is not None:
            if self._ensure_tesseract_available():
                backends_available = True
                lang_arg = self._get_tesseract_lang_argument()
                if lang_arg:
                    tess_kwargs["lang"] = lang_arg
                else:
                    tess_kwargs.pop("lang", None)
                try:
                    text = pytesseract.image_to_string(pil_image_for_ocr, **tess_kwargs)
                    if text and text.strip():
                        backend_used = "tesseract"
                        ocr_context.pop("error", None)
                        return text, backend_used
                except Exception as exc:
                    tesseract_mod = getattr(pytesseract, "pytesseract", None)
                    not_found_exc = getattr(tesseract_mod, "TesseractNotFoundError", None) if tesseract_mod else None
                    timeout_exc = getattr(subprocess, "TimeoutExpired", None)

                    if not_found_exc and isinstance(exc, not_found_exc):
                        logging.warning("Tesseract no está instalado en el sistema.")
                        local_error = "tesseract_missing"
                        self._tesseract_ready = False
                        self._tesseract_lang_arg = None
                    elif timeout_exc and isinstance(exc, timeout_exc):
                        logging.warning(
                            "Tesseract excedió el tiempo máximo de procesamiento para la página %s",
                            page_number,
                        )
                        local_error = "ocr_timeout"
                    elif (
                        lang_arg
                        and "Failed loading language" in str(exc)
                        and tesseract_mod
                        and hasattr(tesseract_mod, "TesseractError")
                    ):
                        logging.warning(
                            "No se encontró el paquete de idioma '%s' para Tesseract, se usará el idioma por defecto.",
                            lang_arg,
                        )
                        tess_kwargs.pop("lang", None)
                        lang_arg = None
                        try:
                            text = pytesseract.image_to_string(pil_image_for_ocr, **tess_kwargs)
                            if text and text.strip():
                                backend_used = "tesseract"
                                ocr_context.pop("error", None)
                                return text, backend_used
                        except Exception:
                            logging.warning("Falló el OCR incluso con el idioma por defecto", exc_info=True)
                            local_error = "ocr_failed"
                    else:
                        logging.warning("Falló el OCR en la página %s", page_number, exc_info=True)
                        local_error = "ocr_failed"
            else:
                local_error = "tesseract_missing"
        elif Config.AI_OCR_TESSERACT_ENABLED:
            local_error = "tesseract_missing"

        if not text.strip() and Config.AI_OCR_EASYOCR_ENABLED:
            reader = self._ensure_easyocr_reader()
            if reader is not None:
                backends_available = True
                try:
                    results = reader.readtext(np.array(pil_image_for_ocr), detail=0)
                    candidate = "\n".join(res.strip() for res in results if res and res.strip())
                    if candidate.strip():
                        backend_used = "easyocr"
                        ocr_context.pop("error", None)
                        return candidate, backend_used
                except Exception:
                    logging.warning("Falló EasyOCR en la página %s", page_number, exc_info=True)
                    local_error = "ocr_failed"
            else:
                if easyocr is None or self._easyocr_failed:
                    local_error = local_error or "easyocr_missing"

        if backend_used:
            return text, backend_used

        if not backends_available:
            if local_error in {"tesseract_missing", "easyocr_missing"}:
                ocr_context["error"] = local_error
            else:
                ocr_context["error"] = local_error or "no_backend"
        else:
            ocr_context["error"] = local_error or "ocr_failed"

        return "", None

    def reload(self) -> None:
        """Forza recarga desde disco (usado tras ingesta)."""
        self._last_mtime = 0.0
        self._ensure_index_loaded()

    def get_summary(self) -> Dict[str, object]:
        self._ensure_index_loaded()
        return self._build_stats(self._metadata)

    # --- flujo principal -----------------------------------------------------

    def ingest_pdf(self, pdf_path: str, source_name: Optional[str] = None) -> Dict[str, object]:
        """Procesa un PDF y reconstruye el índice FAISS."""
        reader = PdfReader(pdf_path)
        metadata: List[Dict[str, object]] = []
        chunks: List[str] = []
        pdfium_text_context: Dict[str, object] = {"doc": None}
        ocr_context: Dict[str, object] = {"doc": None}
        image_context: Dict[str, object] = {"doc": None, "cache": {}}
        pdf_hash = self._compute_pdf_hash(pdf_path)
        try:
            for page_number, page in enumerate(reader.pages, start=1):
                page_backend: Optional[str] = None
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    logging.warning("No se pudo extraer texto de la página %s", page_number, exc_info=True)
                    page_text = ""
                if page_text.strip():
                    page_backend = "pypdf"
                else:
                    pdfium_text = self._extract_text_via_pdfium(pdf_path, page_number, pdfium_text_context)
                    if pdfium_text.strip():
                        page_text = pdfium_text
                        page_backend = "pdfium"
                    else:
                        ocr_text, ocr_backend = self._extract_text_via_ocr(
                            pdf_path,
                            page_number,
                            ocr_context,
                            image_context,
                            pdf_hash,
                        )
                        page_text = ocr_text or ""
                        page_backend = ocr_backend

                image_path = self._ensure_page_image(pdf_path, page_number, pdf_hash, image_context)
                for chunk_idx, chunk in enumerate(self._chunk_text(page_text), start=1):
                    if not chunk.strip():
                        continue
                    metadata.append(
                        {
                            "page": page_number,
                            "chunk": chunk_idx,
                            "text": chunk,
                            "source": source_name or os.path.basename(pdf_path),
                            "skus": self._extract_skus(chunk),
                            "backend": page_backend,
                            "image": image_path,
                            "image_url": self._build_public_image_url(image_path),
                        }
                    )
                    chunks.append(chunk)
        finally:
            for ctx in (pdfium_text_context, ocr_context, image_context):
                doc = ctx.get("doc")
                if doc is not None:
                    try:
                        doc.close()
                    except Exception:
                        pass

        if not chunks:
            error_reason = ocr_context.get("error")
            pdfium_error = pdfium_text_context.get("error")
            if error_reason == "missing_libs":
                raise ValueError(
                    "El PDF no contiene texto utilizable y faltan dependencias de OCR (pypdfium2/pytesseract)."
                )
            if error_reason == "tesseract_missing":
                raise ValueError(
                    "El PDF no contiene texto utilizable y Tesseract no está instalado en el sistema."
                )
            if error_reason == "easyocr_missing":
                raise ValueError(
                    "El PDF no contiene texto utilizable y EasyOCR no está disponible. Instala easyocr, descarga los modelos "
                    "necesarios o activa AI_OCR_EASYOCR_DOWNLOAD_ENABLED=1 para permitir la descarga automática, o habilita otro "
                    "motor OCR."
                )
            if error_reason == "no_backend":
                raise ValueError(
                    "El PDF no contiene texto utilizable y no hay motor OCR disponible (instala Tesseract o habilita EasyOCR)."
                )
            if error_reason in {"init_failed", "render_failed", "ocr_failed", "page_failed"}:
                raise ValueError(
                    "No se pudo extraer texto del PDF ni con OCR, revisa la calidad del archivo."
                )
            if pdfium_error == "missing_libs" and Config.AI_OCR_ENABLED:
                raise ValueError(
                    "El PDF no contiene texto utilizable y pypdfium2 no está instalado para intentar una extracción avanzada."
                )
            if pdfium_error in {"init_failed", "page_failed", "text_failed"}:
                logging.warning("No se pudo extraer texto usando pypdfium2; se continuará con el mensaje genérico.")
            if Config.AI_OCR_ENABLED:
                raise ValueError(
                    "El PDF no contiene texto utilizable incluso con OCR."
                )
            raise ValueError("El PDF no contiene texto utilizable.")

        embeddings: List[List[float]] = []
        batch_size = 20
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            embeddings.extend(self._embed_texts(batch))

        matrix = np.array(embeddings, dtype="float32")
        if matrix.ndim != 2 or matrix.shape[0] == 0:
            raise ValueError("No se generaron embeddings válidos.")

        index = faiss.IndexFlatL2(matrix.shape[1])
        index.add(matrix)

        with self._index_lock:
            self._augment_metadata_with_images(metadata)
            faiss.write_index(index, self._index_path)
            with open(self._metadata_path, "w", encoding="utf-8") as fh:
                json.dump(metadata, fh, ensure_ascii=False, indent=2)
            self._index = index
            self._metadata = metadata
            self._last_mtime = max(os.path.getmtime(self._index_path), os.path.getmtime(self._metadata_path))

        stats = self._build_stats(metadata)
        update_ai_catalog_metadata(stats)
        set_ai_last_processed_to_latest()
        return stats

    def answer(
        self,
        numero: str,
        question: str,
        top_k: int = 4,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[Optional[str], List[Dict[str, object]]]:
        """Genera una respuesta basada en el catálogo."""
        if not question or not question.strip():
            return None, []

        self._ensure_index_loaded()
        if not self._index or not self._metadata:
            logging.warning("Se solicitó respuesta IA pero el índice está vacío")
            return None, []

        client = self._ensure_client()
        normalized_question = question.strip()
        normalized_history = self._normalize_history(history)

        cache_payload = None
        if self._redis:
            cache_payload = self._redis.get(self._cache_key(normalized_question, normalized_history))
        if cache_payload:
            try:
                cached = json.loads(cache_payload)
                answer = cached.get("answer")
                raw_references = cached.get("references") or []
                references = [
                    self._prepare_reference(ref)
                    for ref in raw_references
                    if isinstance(ref, dict)
                ]
            except Exception:
                logging.warning("No se pudo decodificar caché de IA, se descarta.")
                answer = None
                references = []
                if self._redis:
                    self._redis.delete(self._cache_key(normalized_question, normalized_history))
            else:
                self._log_interaction(
                    numero,
                    normalized_question,
                    answer,
                    {"references": references, "from_cache": True, "history": normalized_history},
                )
                return answer, references

        query_embedding = self._embed_texts([normalized_question])[0]
        query_vector = np.array([query_embedding], dtype="float32")

        with self._index_lock:
            distances, indices = self._index.search(query_vector, min(top_k, len(self._metadata)))

        references: List[Dict[str, object]] = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(self._metadata):
                continue
            ref = self._prepare_reference(self._metadata[idx])
            ref["score"] = float(dist)
            references.append(ref)

        references = self._prioritize_references_by_entities(question, references)

        prompt = self._build_prompt(normalized_question, references, normalized_history)
        answer = self._generate_response(client, prompt)

        metadata_log = {"references": references, "from_cache": False, "history": normalized_history}
        if answer:
            if self._redis:
                try:
                    payload = json.dumps({"answer": answer, "references": references}, ensure_ascii=False)
                    self._redis.setex(
                        self._cache_key(normalized_question, normalized_history),
                        self._cache_ttl,
                        payload,
                    )
                except Exception:
                    logging.warning("No se pudo almacenar la respuesta en Redis", exc_info=True)
            self._log_interaction(numero, normalized_question, answer, metadata_log)
        return answer, references

    # --- helpers de generación -----------------------------------------------

    @staticmethod
    def _normalize_history(history: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
        if not history:
            return []

        normalized: List[Dict[str, str]] = []
        for turn in history:
            if not isinstance(turn, dict):
                continue
            role_raw = str(turn.get("role") or "").strip().lower()
            if role_raw in {"cliente", "customer", "user"}:
                role = "user"
            elif role_raw in {"assistant", "bot", "agente"}:
                role = "assistant"
            else:
                continue
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _build_prompt(
        question: str,
        references: List[Dict[str, object]],
        history: Optional[List[Dict[str, str]]],
    ) -> str:
        history_lines: List[str] = []
        for turn in history or []:
            role = turn.get("role")
            content = turn.get("content")
            if not role or not content:
                continue
            label = "Cliente" if role == "user" else "Bot"
            history_lines.append(f"{label}: {content}")

        history_block = ""
        if history_lines:
            history_block = "Historial reciente:\n" + "\n".join(history_lines) + "\n\n"

        if not references:
            return (
                "El catálogo está vacío. Si no encuentras información, responde que no está disponible.\n\n"
                f"{history_block}Pregunta: {question}"
            )

        context_parts = []
        for idx, ref in enumerate(references, start=1):
            sku_text = ", ".join(ref.get("skus") or []) or "sin SKU"
            context_parts.append(
                f"[Fragmento {idx}] Página {ref.get('page', '?')} ({sku_text})\n{ref.get('text')}"
            )
        context = "\n\n".join(context_parts)
        return (
            "Actúas como asesor comercial. Usa únicamente el catálogo proporcionado para responder.\n"
            "Entrega la respuesta en español neutro y servicial, proponiendo opciones de productos.\n"
            "Responde en un único mensaje con frases muy concretas (máximo "
            f"{max(Config.AI_RESPONSE_MAX_SENTENCES, 1)} oraciones cortas). Evita listas extensas y despedidas largas.\n"
            "Si el usuario necesita una cabaña o habitación de más de 2 personas, menciona el precio adicional por cada persona\n"
            "IMPORTANTE: Usa los nombres EXACTOS de las cabañas, habitaciones y precios tal como aparecen en el catálogo.\n"
            "Los nombres correctos son:\n"
            f"{_CATALOG_NAME_BULLETS}"
            "Si el OCR proporciona un nombre distinto o con errores, corrígelo usando esta lista.\n\n"
            f"{history_block}Pregunta del cliente: {question}\n\n"
            f"Catálogo disponible:\n{context}"
        )

    def _prioritize_references_by_entities(
        self,
        question: str,
        references: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        entities = find_entities_in_text(question or "")
        if not entities:
            return references

        scored: List[Tuple[int, int, Dict[str, object]]] = []
        for order, ref in enumerate(references):
            if not isinstance(ref, dict):
                continue
            fields = [
                ref.get("text"),
                ref.get("source"),
                ref.get("catalog_caption"),
                " ".join(ref.get("skus") or []),
            ]
            score = score_fields_against_entities(fields, entities)
            scored.append((score, order, ref))

        if not scored:
            return references

        if not any(score > 0 for score, _, _ in scored):
            return references

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored]


    def _generate_response(self, client: OpenAI, prompt: str) -> Optional[str]:
        try:
            response = client.responses.create(
                model=Config.AI_GEN_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Eres un asistente experto en productos. Utiliza solo la información suministrada en el contexto."
                                    "Indica el producto menciona la página y un breve texto junto con la respuesta de la imagen"
                                ),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                        ],
                    },
                ],
                temperature=0.2,
                max_output_tokens=max(Config.AI_MAX_OUTPUT_TOKENS, 1),
            )
            text = (response.output_text or "").strip()
            cleaned = self._post_process_answer(text)
            return cleaned or None
        except Exception:
            logging.exception("Fallo al generar respuesta con OpenAI")
            return None

    def _post_process_answer(self, text: str) -> Optional[str]:
        """Ajusta la respuesta para que sea breve y en un solo mensaje."""
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        sentences = [
            segment.strip()
            for segment in re.split(r"(?<=[.!?])\s+", cleaned)
            if segment.strip()
        ]
        max_sentences = max(getattr(Config, "AI_RESPONSE_MAX_SENTENCES", 1), 1)
        if sentences:
            cleaned = " ".join(sentences[:max_sentences])

        max_chars = max(getattr(Config, "AI_RESPONSE_MAX_CHARS", 0), 0)
        if max_chars and len(cleaned) > max_chars:
            truncated = cleaned[:max_chars]
            if " " in truncated:
                truncated = truncated.rsplit(" ", 1)[0]
            cleaned = truncated.rstrip(",;:-") + "…"

        return cleaned


    def _log_interaction(
        self,
        numero: str,
        pregunta: str,
        respuesta: Optional[str],
        metadata: Optional[Dict[str, object]] = None,
    ) -> None:
        """Registra la interacción sin interrumpir el flujo si la BD falla."""

        if not respuesta:
            return

        try:
            log_ai_interaction(numero, pregunta, respuesta, metadata)
        except Exception:
            logging.warning("No se pudo registrar la interacción de IA", exc_info=True)

def get_catalog_responder() -> CatalogResponder:
    """Shortcut para acceder al singleton."""
    return CatalogResponder.instance()
