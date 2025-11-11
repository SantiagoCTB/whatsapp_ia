import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin

from config import Config
from services.ai_responder import get_catalog_responder
from services.catalog_entities import (
    collect_normalized_tokens,
    find_entities_in_text,
    score_fields_against_entities,
)
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
                                self._send_reference_images(
                                    numero,
                                    answer,
                                    references,
                                    question_text=texto,
                                )
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
                                    self._send_reference_images(
                                        numero,
                                        fallback,
                                        references,
                                        question_text=texto,
                                    )
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
        *,
        question_text: Optional[str] = None,
    ) -> None:
        if not references and not question_text:
            return
        if not (answer_text or question_text):
            return

        normalized_answer = normalize_text(answer_text or "")
        normalized_question = normalize_text(question_text or "")
        normalized_answer_phrase = f" {normalized_answer} " if normalized_answer else ""
        normalized_question_phrase = f" {normalized_question} " if normalized_question else ""
        combined_tokens = collect_normalized_tokens(answer_text or "", question_text or "")

        ai_step_lower = (Config.AI_HANDOFF_STEP or "").strip().lower()

        entity_matches = find_entities_in_text(question_text or "")
        if not entity_matches and answer_text:
            entity_matches = find_entities_in_text(answer_text)

        ranked: List[Tuple[int, float, Dict[str, object]]] = []
        for ref in references:
            if not isinstance(ref, dict):
                continue
            score_value = float(ref.get("score") or 0.0)

            match_points = 0
            fields = [
                ref.get("text"),
                ref.get("source"),
                ref.get("catalog_caption"),
                " ".join(ref.get("skus") or []),
            ]
            if entity_matches:
                entity_score = score_fields_against_entities(fields, entity_matches)
                if entity_score <= 0:
                    continue
                match_points += entity_score * 10

            for sku in ref.get("skus") or []:
                normalized_sku = normalize_text(str(sku))
                if normalized_sku and normalized_sku in combined_tokens:
                    match_points += 5

            normalized_ref_text = normalize_text(ref.get("text") or "")
            if normalized_ref_text:
                ref_tokens = set(normalized_ref_text.split())
                if combined_tokens:
                    match_points += len(ref_tokens & combined_tokens)

            if match_points <= 0:
                continue

            ranked.append((match_points, score_value, ref))

        raw_catalog_entries = _get_catalog_media_index()
        catalog_entries: List[Dict[str, object]] = []
        for entry in raw_catalog_entries:
            if not isinstance(entry, dict):
                continue
            entry_step_raw = entry.get("step")
            entry_step = str(entry_step_raw).strip().lower() if entry_step_raw else ""
            if entry_step and ai_step_lower and entry_step != ai_step_lower:
                continue
            catalog_entries.append(entry)

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

            normalized_trigger = ""
            trigger_value = entry.get("normalized")
            if isinstance(trigger_value, str):
                normalized_trigger = trigger_value.strip()

            full_phrase_matched = False
            if normalized_trigger:
                if normalized_answer_phrase and f" {normalized_trigger} " in normalized_answer_phrase:
                    full_phrase_matched = True
                elif normalized_question_phrase and f" {normalized_trigger} " in normalized_question_phrase:
                    full_phrase_matched = True

            common_tokens = entry_tokens & combined_tokens if combined_tokens else set()

            entry_match_points = 0
            if entity_matches:
                entry_fields = [
                    entry.get("label"),
                    entry.get("raw"),
                    entry.get("respuesta"),
                    " ".join(entry_tokens),
                ]
                entity_score = score_fields_against_entities(entry_fields, entity_matches)
                if entity_score <= 0:
                    continue
                entry_match_points = max(entity_score * 10, 5)
                if common_tokens:
                    entry_match_points += len(common_tokens)
            else:
                if not common_tokens:
                    continue
                required_matches = 1 if len(entry_tokens) == 1 else min(len(entry_tokens), 2)
                if len(common_tokens) < required_matches:
                    continue
                if not full_phrase_matched and entry_tokens and common_tokens != entry_tokens:
                    continue
                entry_match_points = max(len(common_tokens) * 10, 5)
                if full_phrase_matched:
                    entry_match_points += 5

            label = entry.get("label") or entry.get("raw")
            caption_text = (entry.get("respuesta") or "").strip() or label
            pseudo_ref: Dict[str, object] = {
                "image_url": image_url,
                "source": label,
                "text": entry.get("raw") or label,
                "skus": [],
                "catalog_caption": caption_text,
            }

            ranked.append((entry_match_points, 0.0, pseudo_ref))

        if not ranked:
            if entity_matches:
                entity_fallback: List[Tuple[int, Dict[str, object]]] = []
                for ref in references:
                    if not isinstance(ref, dict):
                        continue
                    fields = [
                        ref.get("text"),
                        ref.get("source"),
                        ref.get("catalog_caption"),
                    ]
                    entity_score = score_fields_against_entities(fields, entity_matches)
                    if entity_score <= 0:
                        continue
                    entity_fallback.append((entity_score * 10, ref))

                if not entity_fallback:
                    entry_candidates: List[Tuple[int, Dict[str, object]]] = []
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
                        entry_fields = [
                            entry.get("label"),
                            entry.get("raw"),
                            entry.get("respuesta"),
                            " ".join(entry_tokens),
                        ]
                        entity_score = score_fields_against_entities(entry_fields, entity_matches)
                        if entity_score <= 0:
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
                        entry_candidates.append((entity_score * 10, pseudo_ref))

                    if not entry_candidates:
                        return

                    entry_candidates.sort(key=lambda item: -item[0])
                    ranked = [(score, 0.0, ref) for score, ref in entry_candidates]
                else:
                    entity_fallback.sort(key=lambda item: -item[0])
                    ranked = [(score, 0.0, ref) for score, ref in entity_fallback]
            else:
                fallback_ranked: List[Tuple[int, float, Dict[str, object]]] = []
                for order, ref in enumerate(references):
                    if not isinstance(ref, dict):
                        continue
                    media_payload = self._resolve_reference_media(ref)
                    if not media_payload:
                        continue
                    dedupe_key = (
                        media_payload.get("link")
                        or media_payload.get("path")
                        or media_payload.get("id")
                    )
                    if not dedupe_key:
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
        try:
            max_images = int(getattr(Config, "AI_REFERENCE_IMAGE_LIMIT", 1))
        except Exception:
            max_images = 1
        if max_images <= 0:
            return
        sent = 0
        for _, _, ref in ranked:
            if not isinstance(ref, dict):
                continue
            media_payload = self._resolve_reference_media(ref)
            if not media_payload:
                continue
            dedupe_key = (
                media_payload.get("link")
                or media_payload.get("path")
                or media_payload.get("id")
            )
            if dedupe_key and dedupe_key in seen:
                continue
            if dedupe_key:
                seen.add(str(dedupe_key))
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
            opciones_payload: object
            if "path" not in media_payload and set(media_payload.keys()) == {"link"}:
                opciones_payload = media_payload["link"]
            else:
                opciones_payload = media_payload

            enviar_mensaje(
                numero,
                caption,
                tipo="bot",
                tipo_respuesta="image",
                opciones=opciones_payload,
                step=Config.AI_HANDOFF_STEP,
            )
            sent += 1
            if sent >= max_images:
                break


    @staticmethod
    def _normalize_media_link(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        candidate = str(value).strip()
        if not candidate:
            return None
        if candidate.startswith(("http://", "https://")):
            return candidate
        base_url = (Config.MEDIA_PUBLIC_BASE_URL or "").strip()
        if not base_url:
            return None
        if not base_url.endswith("/"):
            base_url = f"{base_url}/"
        return urljoin(base_url, candidate.lstrip("/"))

    def _resolve_reference_media(self, ref: Dict[str, object]) -> Optional[Dict[str, str]]:
        image_url_raw = ref.get("image_url")
        if isinstance(image_url_raw, str) and image_url_raw.strip():
            normalized = self._normalize_media_link(image_url_raw)
            if normalized:
                return {"link": normalized}
            if image_url_raw.strip().startswith(("http://", "https://")):
                return {"link": image_url_raw.strip()}

        image_path = ref.get("image")
        if isinstance(image_path, str) and image_path.strip():
            return {"path": image_path.strip()}

        return None


def start_ai_worker() -> AIWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = AIWorker()
            _worker.start()
    return _worker
