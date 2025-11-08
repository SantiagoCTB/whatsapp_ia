import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

from config import Config
from services.ai_responder import get_catalog_responder
from services import db as db_module

AI_BLOCKED_STATE = getattr(db_module, "AI_BLOCKED_STATE", "ia_bloqueada")
claim_ai_message = getattr(db_module, "claim_ai_message")
get_catalog_media_keywords = getattr(db_module, "get_catalog_media_keywords")
get_ai_settings = getattr(db_module, "get_ai_settings")
get_messages_for_ai = getattr(db_module, "get_messages_for_ai")
get_recent_messages_for_context = getattr(db_module, "get_recent_messages_for_context")
log_ai_interaction = getattr(db_module, "log_ai_interaction")
update_ai_last_processed = getattr(db_module, "update_ai_last_processed")
update_chat_state = getattr(db_module, "update_chat_state")
from services.whatsapp_api import enviar_mensaje
from services.normalize_text import normalize_text


_worker: Optional["AIWorker"] = None
_worker_lock = threading.Lock()
_catalog_media_index: Optional[List[Dict[str, object]]] = None


def _get_catalog_media_index() -> List[Dict[str, object]]:
    global _catalog_media_index
    if _catalog_media_index is None:
        try:
            _catalog_media_index = get_catalog_media_keywords()
        except Exception:
            logging.warning("No se pudo cargar el catálogo de reglas para la IA", exc_info=True)
            _catalog_media_index = []
    return _catalog_media_index or []


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

                    previous_last_id = last_id
                    claimed = claim_ai_message(last_id, message_id)
                    if not claimed:
                        last_id = max(last_id, message_id)
                        continue

                    last_id = message_id

                    ai_step_lower = (Config.AI_HANDOFF_STEP or "").strip().lower()
                    current_step = (row.get("current_step") or "").strip().lower()
                    current_state = (row.get("current_estado") or "").strip().lower()
                    if not ai_step_lower:
                        try:
                            update_ai_last_processed(message_id)
                        except Exception:
                            logging.warning(
                                "No se pudo avanzar el puntero de IA cuando no hay step de IA configurado para %s",
                                numero,
                                exc_info=True,
                            )
                        continue

                    if current_step and current_step != ai_step_lower:
                        try:
                            update_ai_last_processed(message_id)
                        except Exception:
                            logging.warning(
                                "No se pudo avanzar el puntero de IA tras detectar cambio de flujo para %s",
                                numero,
                                exc_info=True,
                            )
                        continue

                    if current_state == AI_BLOCKED_STATE:
                        try:
                            update_ai_last_processed(message_id)
                        except Exception:
                            logging.warning(
                                "No se pudo avanzar el puntero de IA porque el estado está bloqueado para %s",
                                numero,
                                exc_info=True,
                            )
                        continue

                    history_limit = max(getattr(Config, "AI_HISTORY_MESSAGE_LIMIT", 0), 0)
                    history_records = []
                    if history_limit:
                        try:
                            history_records = get_recent_messages_for_context(
                                numero,
                                message_id,
                                history_limit,
                            )
                        except Exception:
                            logging.warning(
                                "No se pudo recuperar el historial para %s", numero, exc_info=True
                            )
                            history_records = []

                    history_payload: List[Dict[str, str]] = []
                    for item in history_records:
                        role_raw = str(item.get("tipo") or "").strip().lower()
                        if role_raw == "bot":
                            role = "assistant"
                        else:
                            role = "user"
                        content = (item.get("mensaje") or "").strip()
                        if not content:
                            continue
                        history_payload.append({"role": role, "content": content})

                    try:
                        answer, references = responder.answer(
                            numero,
                            texto,
                            history=history_payload,
                        )
                    except Exception as exc:
                        logging.exception("Error generando respuesta IA para %s", numero)

                        fallback_message = (Config.AI_FALLBACK_MESSAGE or "").strip()
                        if not fallback_message:
                            fallback_message = (
                                "Lo siento, ocurrió un problema con mi respuesta. Intenta nuevamente más tarde."
                            )

                        fallback_sent = False
                        if fallback_message:
                            try:
                                fallback_sent = enviar_mensaje(
                                    numero,
                                    fallback_message,
                                    tipo="bot",
                                    tipo_respuesta="texto",
                                    step=Config.AI_HANDOFF_STEP,
                                )
                            except Exception:
                                logging.exception(
                                    "Error enviando fallback de IA para %s", numero
                                )
                                fallback_sent = False

                        metadata = {
                            "status": "error",
                            "reason": "answer_exception",
                            "exception": repr(exc),
                            "fallback_sent": bool(fallback_sent),
                        }
                        if fallback_sent:
                            metadata["fallback_message"] = fallback_message

                        try:
                            log_ai_interaction(
                                numero,
                                texto,
                                fallback_message if fallback_sent else None,
                                metadata,
                            )
                        except Exception:
                            logging.warning(
                                "No se pudo registrar el fallo en ia_logs para %s",
                                numero,
                                exc_info=True,
                            )

                        if fallback_sent:
                            update_chat_state(
                                numero, Config.AI_HANDOFF_STEP, "ia_error"
                            )
                        else:
                            try:
                                update_ai_last_processed(previous_last_id)
                                last_id = previous_last_id
                            except Exception:
                                logging.warning(
                                    "No se pudo restablecer el puntero de IA tras fallo de fallback para %s",
                                    numero,
                                    exc_info=True,
                                )
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

        catalog_entries = _get_catalog_media_index()
        for entry in catalog_entries:
            image_url = entry.get("media_url")
            if not image_url:
                continue

            entry_tokens = entry.get("tokens") or set()
            if not isinstance(entry_tokens, set):
                try:
                    entry_tokens = set(entry_tokens)
                except TypeError:
                    continue
            if not entry_tokens:
                continue

            common_tokens = entry_tokens & answer_tokens
            if not common_tokens:
                continue

            required_matches = 1 if len(entry_tokens) == 1 else min(len(entry_tokens), 2)
            if len(common_tokens) < required_matches:
                continue

            label = entry.get("label") or entry.get("raw")
            caption_text = (entry.get("respuesta") or "").strip() or label
            pseudo_ref: Dict[str, object] = {
                "image_url": image_url,
                "source": label,
                "text": entry.get("raw") or label,
                "skus": [],
                "catalog_caption": caption_text,
            }

            match_points = max(len(common_tokens) * 10, 5)
            ranked.append((match_points, 0.0, pseudo_ref))

        if not ranked:
            fallback_ranked: List[Tuple[int, float, Dict[str, object]]] = []
            for order, ref in enumerate(references):
                if not isinstance(ref, dict):
                    continue
                image_url = ref.get("image_url")
                if not image_url:
                    continue
                try:
                    score_value = float(ref.get("score"))
                except (TypeError, ValueError):
                    score_value = float("inf")
                fallback_ranked.append((order, score_value, ref))

            if not fallback_ranked:
                for entry in catalog_entries:
                    image_url = entry.get("media_url")
                    if not image_url:
                        continue
                    label = entry.get("label") or entry.get("raw")
                    caption_text = (entry.get("respuesta") or "").strip() or label
                    pseudo_ref = {
                        "image_url": image_url,
                        "source": label,
                        "text": entry.get("raw") or label,
                        "skus": [],
                        "catalog_caption": caption_text,
                    }
                    ranked = [(0, 0.0, pseudo_ref)]
                    break
                else:
                    return

            fallback_ranked.sort(key=lambda item: (item[1], item[0]))
            ranked = [(0, score, ref) for _, score, ref in fallback_ranked]
        else:
            ranked.sort(key=lambda item: (-item[0], item[1]))

        seen: Set[str] = set()
        max_images = 1
        sent = 0
        for _, _, ref in ranked:
            image_url = ref.get("image_url")
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            caption_override = (ref.get("catalog_caption") or "").strip()
            if caption_override:
                caption = caption_override
            else:
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
