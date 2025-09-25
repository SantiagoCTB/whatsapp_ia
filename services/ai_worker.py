import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from config import Config
from services.ai_responder import get_catalog_responder
from services.db import (
    claim_ai_message,
    get_ai_settings,
    get_messages_for_ai,
    update_chat_state,
)
from services.whatsapp_api import enviar_mensaje
from services.normalize_text import normalize_text


_worker: Optional["AIWorker"] = None
_worker_lock = threading.Lock()


class AIWorker(threading.Thread):
    """Hilo en segundo plano que detecta nuevos mensajes y responde con IA."""

    def __init__(self) -> None:
        super().__init__(name="AIWorker", daemon=True)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        responder = get_catalog_responder()
        poll_seconds = max(float(Config.AI_POLL_INTERVAL), 1.0)
        while not self._stop_event.is_set():
            try:
                settings = get_ai_settings()
                if not settings.get("enabled") or not Config.AI_HANDOFF_STEP:
                    time.sleep(poll_seconds)
                    continue

                last_id = settings.get("last_processed_message_id") or 0
                mensajes = get_messages_for_ai(last_id, Config.AI_HANDOFF_STEP, Config.AI_BATCH_SIZE)
                if not mensajes:
                    time.sleep(poll_seconds)
                    continue

                for row in mensajes:
                    message_id = row["id"]
                    numero = row["numero"]
                    texto = row["mensaje"]

                    claimed = claim_ai_message(last_id, message_id)
                    if not claimed:
                        last_id = max(last_id, message_id)
                        continue

                    last_id = message_id

                    try:
                        answer, references = responder.answer(numero, texto)
                    except Exception:
                        logging.exception("Error generando respuesta IA para %s", numero)
                        continue

                    if answer:
                        enviado = enviar_mensaje(
                            numero,
                            answer,
                            tipo="bot",
                            tipo_respuesta="texto",
                            step=Config.AI_HANDOFF_STEP,
                        )
                        if enviado:
                            try:
                                self._send_reference_images(numero, answer, references)
                            except Exception:
                                logging.warning(
                                    "No se pudieron enviar las imágenes de referencia para %s",
                                    numero,
                                    exc_info=True,
                                )
                        update_chat_state(numero, Config.AI_HANDOFF_STEP, "ia_activa")
                    else:
                        fallback = (Config.AI_FALLBACK_MESSAGE or "").strip()
                        if fallback:
                            enviado = enviar_mensaje(
                                numero,
                                fallback,
                                tipo="bot",
                                tipo_respuesta="texto",
                                step=Config.AI_HANDOFF_STEP,
                            )
                            if enviado:
                                try:
                                    self._send_reference_images(numero, fallback, references)
                                except Exception:
                                    logging.warning(
                                        "No se pudieron enviar las imágenes de referencia para %s",
                                        numero,
                                        exc_info=True,
                                    )
                            update_chat_state(numero, Config.AI_HANDOFF_STEP, "ia_fallback")
            except Exception:
                logging.exception("Fallo general en el worker de IA")
            time.sleep(poll_seconds)

    def _send_reference_images(
        self,
        numero: str,
        answer_text: Optional[str],
        references: List[Dict[str, object]],
    ) -> None:
        if not references or not answer_text:
            return

        normalized_answer = normalize_text(answer_text)
        if not normalized_answer:
            return

        answer_tokens = set(normalized_answer.split())
        if not answer_tokens:
            return

        ranked: List[Tuple[int, float, Dict[str, object]]] = []
        for ref in references:
            if not isinstance(ref, dict):
                continue
            score_value = float(ref.get("score") or 0.0)

            match_points = 0
            for sku in ref.get("skus") or []:
                normalized_sku = normalize_text(str(sku))
                if normalized_sku and normalized_sku in answer_tokens:
                    match_points += 5

            ref_text_tokens = set()
            normalized_ref_text = normalize_text(ref.get("text") or "")
            if normalized_ref_text:
                ref_text_tokens = set(normalized_ref_text.split())
                match_points += len(ref_text_tokens & answer_tokens)

            if match_points <= 0:
                continue

            ranked.append((match_points, score_value, ref))

        if not ranked:
            return

        ranked.sort(key=lambda item: (-item[0], item[1]))

        seen: Set[str] = set()
        max_images = 3
        sent = 0
        for _, _, ref in ranked:
            image_url = ref.get("image_url")
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            caption_parts: List[str] = []
            source = ref.get("source")
            page = ref.get("page")
            if source:
                caption_parts.append(str(source))
            if page:
                caption_parts.append(f"pág. {page}")
            caption = " – ".join(caption_parts) if caption_parts else "Referencia del catálogo"
            enviar_mensaje(
                numero,
                caption,
                tipo="bot",
                tipo_respuesta="image",
                opciones=str(image_url),
                step=Config.AI_HANDOFF_STEP,
            )
            sent += 1
            if sent >= max_images:
                break


def start_ai_worker() -> AIWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = AIWorker()
            _worker.start()
    return _worker
