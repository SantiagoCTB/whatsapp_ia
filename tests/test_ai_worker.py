import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import types

import pytest

# Stubs para evitar dependencias externas durante las pruebas
faiss_stub = types.ModuleType("faiss")
faiss_stub.Index = type("Index", (), {})
faiss_stub.IndexFlatL2 = lambda *_, **__: object()
faiss_stub.read_index = lambda *_args, **_kwargs: object()
faiss_stub.write_index = lambda *_args, **_kwargs: None
sys.modules.setdefault("faiss", faiss_stub)

ai_responder_stub = types.ModuleType("services.ai_responder")
ai_responder_stub.get_catalog_responder = lambda: None
sys.modules.setdefault("services.ai_responder", ai_responder_stub)

db_stub = types.ModuleType("services.db")
for _name in (
    "claim_ai_message",
    "get_catalog_media_keywords",
    "get_ai_settings",
    "get_messages_for_ai",
    "get_recent_messages_for_context",
    "log_ai_interaction",
    "update_ai_last_processed",
    "update_chat_state",
):
    setattr(db_stub, _name, lambda *args, **kwargs: None)
sys.modules.setdefault("services.db", db_stub)

whatsapp_stub = types.ModuleType("services.whatsapp_api")
whatsapp_stub.enviar_mensaje = lambda *args, **kwargs: True
whatsapp_stub.guardar_mensaje = lambda *args, **kwargs: None
whatsapp_stub.requests = types.SimpleNamespace(post=lambda *args, **kwargs: None)
sys.modules.setdefault("services.whatsapp_api", whatsapp_stub)

from config import Config
from services import ai_worker


class _DummyStopEvent:
    def __init__(self):
        self._calls = 0

    def is_set(self):
        self._calls += 1
        return self._calls > 1

    def set(self):
        self._calls = 1


class _FailingResponder:
    def answer(self, *args, **kwargs):  # pragma: no cover - simple stub
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def patch_sleep(monkeypatch):
    monkeypatch.setattr(ai_worker.time, "sleep", lambda *_: None)


@pytest.fixture
def worker_instance(monkeypatch):
    monkeypatch.setattr(ai_worker, "get_catalog_responder", lambda: _FailingResponder())
    monkeypatch.setattr(ai_worker, "get_recent_messages_for_context", lambda *_, **__: [])
    monkeypatch.setattr(Config, "AI_HISTORY_MESSAGE_LIMIT", 0)
    instance = ai_worker.AIWorker()
    instance._stop_event = _DummyStopEvent()
    return instance


def _base_patches(monkeypatch, *, fallback_result):
    monkeypatch.setattr(Config, "AI_FALLBACK_MESSAGE", "Mensaje fallback")

    monkeypatch.setattr(
        ai_worker, "get_ai_settings", lambda: {"enabled": True, "last_processed_message_id": 5}
    )
    monkeypatch.setattr(
        ai_worker,
        "get_messages_for_ai",
        lambda *_: [
            {"id": 6, "numero": "+521234567890", "mensaje": "Hola"},
        ],
    )

    claim_calls = {}

    def fake_claim(expected_last_id, new_last_id):
        claim_calls["called_with"] = (expected_last_id, new_last_id)
        return True

    monkeypatch.setattr(ai_worker, "claim_ai_message", fake_claim)

    sent_messages = []

    def fake_send(numero, mensaje, **kwargs):
        sent_messages.append({"numero": numero, "mensaje": mensaje, **kwargs})
        return fallback_result

    monkeypatch.setattr(ai_worker, "enviar_mensaje", fake_send)

    states = []
    monkeypatch.setattr(
        ai_worker,
        "update_chat_state",
        lambda numero, step, estado: states.append((numero, step, estado)),
    )

    log_entries = []

    def fake_log(numero, pregunta, respuesta, metadata):
        log_entries.append(
            {
                "numero": numero,
                "pregunta": pregunta,
                "respuesta": respuesta,
                "metadata": metadata,
            }
        )

    monkeypatch.setattr(ai_worker, "log_ai_interaction", fake_log)

    revert_calls = []

    def fake_revert(message_id):
        revert_calls.append(message_id)

    monkeypatch.setattr(ai_worker, "update_ai_last_processed", fake_revert)

    return {
        "claim_calls": claim_calls,
        "sent_messages": sent_messages,
        "states": states,
        "log_entries": log_entries,
        "revert_calls": revert_calls,
    }


def test_worker_sends_fallback_and_marks_error(worker_instance, monkeypatch):
    tracking = _base_patches(monkeypatch, fallback_result=True)

    worker_instance.run()

    assert tracking["claim_calls"]["called_with"] == (5, 6)

    assert tracking["sent_messages"] == [
        {
            "numero": "+521234567890",
            "mensaje": "Mensaje fallback",
            "tipo": "bot",
            "tipo_respuesta": "texto",
            "step": Config.AI_HANDOFF_STEP,
        }
    ]

    assert tracking["states"] == [
        (
            "+521234567890",
            Config.AI_HANDOFF_STEP,
            "ia_error",
        )
    ]

    assert tracking["revert_calls"] == []

    assert len(tracking["log_entries"]) == 1
    logged = tracking["log_entries"][0]
    assert logged["numero"] == "+521234567890"
    assert logged["pregunta"] == "Hola"
    assert logged["respuesta"] == "Mensaje fallback"
    assert logged["metadata"]["fallback_sent"] is True
    assert logged["metadata"]["reason"] == "answer_exception"


def test_worker_reverts_pointer_when_fallback_fails(worker_instance, monkeypatch):
    tracking = _base_patches(monkeypatch, fallback_result=False)

    worker_instance.run()

    assert tracking["sent_messages"][0]["mensaje"] == "Mensaje fallback"
    assert tracking["states"] == []
    assert tracking["revert_calls"] == [5]

    assert len(tracking["log_entries"]) == 1
    logged = tracking["log_entries"][0]
    assert logged["respuesta"] is None
    assert logged["metadata"]["fallback_sent"] is False


def test_send_reference_images_uses_fallback(monkeypatch):
    ai_worker._catalog_media_index = []
    worker = ai_worker.AIWorker()

    sent_messages = []

    def fake_send(numero, mensaje, **kwargs):
        sent_messages.append({"numero": numero, "mensaje": mensaje, **kwargs})
        return True

    monkeypatch.setattr(ai_worker, "enviar_mensaje", fake_send)

    references = [
        {"image_url": "https://example.com/a.jpg", "score": 0.8, "source": "Catálogo", "page": 3},
        {"image_url": "https://example.com/b.jpg", "score": 0.2},
    ]

    worker._send_reference_images("+521234000000", "Respuesta breve", references)

    assert sent_messages, "Se esperaba al menos una imagen de referencia"
    first = sent_messages[0]
    assert first["tipo_respuesta"] == "image"
    assert first["opciones"] == "https://example.com/b.jpg"


def teardown_module(module):  # pragma: no cover - limpieza defensiva
    for name, stub in (
        ("services.ai_responder", ai_responder_stub),
        ("services.db", db_stub),
        ("services.whatsapp_api", whatsapp_stub),
        ("faiss", faiss_stub),
    ):
        if sys.modules.get(name) is stub:
            del sys.modules[name]
