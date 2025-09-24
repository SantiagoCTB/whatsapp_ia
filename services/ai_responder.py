import json
import logging
import os
import threading
import hashlib
import re
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np
from openai import OpenAI
from pypdf import PdfReader

try:
    import redis
except Exception:  # pragma: no cover - redis es opcional
    redis = None

from config import Config
from services.db import (
    log_ai_interaction,
    set_ai_last_processed_to_latest,
    update_ai_catalog_metadata,
)


SKU_PATTERN = re.compile(r"\bSKU[:\s-]*([A-Z0-9-]{3,})\b", re.IGNORECASE)


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
            self._last_mtime = mtime

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 200) -> List[str]:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if not cleaned:
            return []
        chunks: List[str] = []
        start = 0
        length = len(cleaned)
        while start < length:
            end = min(length, start + chunk_size)
            chunk = cleaned[start:end]
            if chunk:
                chunks.append(chunk)
            if end >= length:
                break
            start = max(0, end - overlap)
        return chunks

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

    def _cache_key(self, question: str) -> str:
        normalized = question.strip().lower()
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_stats(metadata: List[Dict[str, object]]) -> Dict[str, object]:
        sources = sorted({str(m.get("source")) for m in metadata if m.get("source")})
        pages = max((int(m.get("page") or 0) for m in metadata), default=0)
        return {"chunks": len(metadata), "sources": sources, "pages": pages}

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
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                logging.warning("No se pudo extraer texto de la página %s", page_number, exc_info=True)
                page_text = ""
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
                    }
                )
                chunks.append(chunk)
        if not chunks:
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

    def answer(self, numero: str, question: str, top_k: int = 4) -> Tuple[Optional[str], List[Dict[str, object]]]:
        """Genera una respuesta basada en el catálogo."""
        if not question or not question.strip():
            return None, []

        self._ensure_index_loaded()
        if not self._index or not self._metadata:
            logging.warning("Se solicitó respuesta IA pero el índice está vacío")
            return None, []

        client = self._ensure_client()
        normalized_question = question.strip()

        cache_payload = None
        if self._redis:
            cache_payload = self._redis.get(self._cache_key(normalized_question))
        if cache_payload:
            try:
                cached = json.loads(cache_payload)
                answer = cached.get("answer")
                references = cached.get("references") or []
            except Exception:
                logging.warning("No se pudo decodificar caché de IA, se descarta.")
                answer = None
                references = []
                if self._redis:
                    self._redis.delete(self._cache_key(normalized_question))
            else:
                log_ai_interaction(
                    numero,
                    normalized_question,
                    answer,
                    {"references": references, "from_cache": True},
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
            ref = dict(self._metadata[idx])
            ref["score"] = float(dist)
            references.append(ref)

        prompt = self._build_prompt(normalized_question, references)
        answer = self._generate_response(client, prompt)

        metadata_log = {"references": references, "from_cache": False}
        if answer:
            if self._redis:
                try:
                    payload = json.dumps({"answer": answer, "references": references}, ensure_ascii=False)
                    self._redis.setex(self._cache_key(normalized_question), self._cache_ttl, payload)
                except Exception:
                    logging.warning("No se pudo almacenar la respuesta en Redis", exc_info=True)
            log_ai_interaction(numero, normalized_question, answer, metadata_log)
        return answer, references

    # --- helpers de generación -----------------------------------------------

    @staticmethod
    def _build_prompt(question: str, references: List[Dict[str, object]]) -> str:
        if not references:
            return (
                "El catálogo está vacío. Si no encuentras información, responde que no está disponible.\n"
                f"Pregunta: {question}"
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
            "Entrega la respuesta en español neutro, proponiendo opciones con SKU y una breve invitación a continuar la compra.\n"
            "Si el dato no aparece, informa que no está en el catálogo.\n\n"
            f"Pregunta del cliente: {question}\n\n"
            f"Catálogo disponible:\n{context}"
        )

    def _generate_response(self, client: OpenAI, prompt: str) -> Optional[str]:
        try:
            response = client.responses.create(
                model=Config.AI_GEN_MODEL,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Eres un asistente experto en productos. Utiliza solo la información suministrada en el contexto."
                                    " Indica SKU y página cuando sea posible."
                                ),
                            }
                        ],
                    },
                    {"role": "user", "content": [{"type": "text", "text": prompt}]},
                ],
                temperature=0.2,
            )
            text = (response.output_text or "").strip()
            return text or None
        except Exception:
            logging.exception("Fallo al generar respuesta con OpenAI")
            return None


def get_catalog_responder() -> CatalogResponder:
    """Shortcut para acceder al singleton."""
    return CatalogResponder.instance()
