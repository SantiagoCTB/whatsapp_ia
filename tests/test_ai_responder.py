import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from services.ai_responder import CatalogResponder
from services import ai_worker


def test_build_prompt_includes_history_order():
    history = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "¿En qué puedo ayudarte?"},
    ]
    references = [
        {"page": 5, "skus": ["SKU123"], "text": "Producto destacado"},
    ]

    prompt = CatalogResponder._build_prompt("¿Tienen zapatillas?", references, history)

    assert "Historial reciente:" in prompt
    history_section = prompt.split("Pregunta del cliente:")[0]
    assert "Cliente: Hola" in history_section
    assert "Bot: ¿En qué puedo ayudarte?" in history_section
    assert history_section.index("Cliente: Hola") < history_section.index("Bot: ¿En qué puedo ayudarte?")


def test_worker_passes_history(monkeypatch):
    worker = ai_worker.AIWorker()

    monkeypatch.setattr(ai_worker.Config, "AI_POLL_INTERVAL", 0)
    monkeypatch.setattr(ai_worker.Config, "AI_BATCH_SIZE", 1)
    monkeypatch.setattr(ai_worker.Config, "AI_HISTORY_MESSAGE_LIMIT", 5)

    def fake_sleep(_):
        return None

    monkeypatch.setattr(ai_worker.time, "sleep", fake_sleep)

    def fake_get_ai_settings():
        return {"enabled": True, "last_processed_message_id": 0}

    monkeypatch.setattr(ai_worker, "get_ai_settings", fake_get_ai_settings)

    messages = [
        {"id": 42, "numero": "+123", "mensaje": "Necesito precios"},
    ]

    def fake_get_messages_for_ai(after_id, handoff_step, limit):
        return messages if messages else []

    monkeypatch.setattr(ai_worker, "get_messages_for_ai", fake_get_messages_for_ai)

    def fake_claim(last_id, message_id):
        return True

    monkeypatch.setattr(ai_worker, "claim_ai_message", fake_claim)

    history_rows = [
        {"id": 10, "tipo": "cliente", "mensaje": "Hola"},
        {"id": 11, "tipo": "bot", "mensaje": "Hola, ¿en qué te apoyo?"},
    ]

    def fake_get_recent(numero, before_id, limit):
        return history_rows

    monkeypatch.setattr(ai_worker, "get_recent_messages_for_context", fake_get_recent)

    monkeypatch.setattr(ai_worker, "enviar_mensaje", lambda *args, **kwargs: True)
    monkeypatch.setattr(ai_worker, "update_chat_state", lambda *args, **kwargs: None)

    captured = {}

    class DummyResponder:
        def answer(self, numero, texto, history=None):
            captured["args"] = (numero, texto)
            captured["history"] = history
            worker.stop()
            messages.clear()
            return "Respuesta", []

    monkeypatch.setattr(ai_worker, "get_catalog_responder", lambda: DummyResponder())

    worker.run()

    assert captured["args"] == ("+123", "Necesito precios")
    assert captured["history"] == [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Hola, ¿en qué te apoyo?"},
    ]


def test_chunk_text_splits_catalog_sections():
    text = (
        "Cabaña Tunúpa $780.000\n"
        "Incluye desayuno americano.\n"
        "Cabaña Cóndor $720.000\n"
        "Vista al lago y chimenea."
    )

    chunks = CatalogResponder._chunk_text(text)

    assert len(chunks) == 2
    assert chunks[0].startswith("Cabaña Tunúpa")
    assert chunks[1].startswith("Cabaña Cóndor")


def test_chunk_text_removes_bullets():
    text = "• Cabaña Taypi con tina de hidromasaje\n- Cabaña Inti con terraza"

    chunks = CatalogResponder._chunk_text(text)

    assert chunks == [
        "Cabaña Taypi con tina de hidromasaje",
        "Cabaña Inti con terraza",
    ]
