import logging
import threading
import time
from typing import Optional

from config import Config
from services.ai_responder import get_catalog_responder
from services.db import (
    get_ai_settings,
    get_messages_for_ai,
    update_ai_last_processed,
    update_chat_state,
)
from services.whatsapp_api import enviar_mensaje


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
                    try:
                        answer, _references = responder.answer(numero, texto)
                    except Exception:
                        logging.exception("Error generando respuesta IA para %s", numero)
                        update_ai_last_processed(message_id)
                        continue

                    if answer:
                        enviar_mensaje(
                            numero,
                            answer,
                            tipo="bot",
                            tipo_respuesta="texto",
                            step=Config.AI_HANDOFF_STEP,
                        )
                        update_chat_state(numero, Config.AI_HANDOFF_STEP, "ia_activa")
                    else:
                        fallback = (Config.AI_FALLBACK_MESSAGE or "").strip()
                        if fallback:
                            enviar_mensaje(
                                numero,
                                fallback,
                                tipo="bot",
                                tipo_respuesta="texto",
                                step=Config.AI_HANDOFF_STEP,
                            )
                            update_chat_state(numero, Config.AI_HANDOFF_STEP, "ia_fallback")
                    update_ai_last_processed(message_id)
            except Exception:
                logging.exception("Fallo general en el worker de IA")
            time.sleep(poll_seconds)


def start_ai_worker() -> AIWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = AIWorker()
            _worker.start()
    return _worker
