import os
import sys
from datetime import datetime

from flask import Flask

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from routes import webhook
from services import ai_worker


def test_process_step_chain_multiple_triggers(monkeypatch):
    numero = "5212345"
    step = "consulta_envio"
    regla = (1, "Tenemos servicio de envío disponible", None, "text", None, None, None, "domicilio,envío")

    class DummyCursor:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return self._rows

    class DummyConnection:
        def __init__(self, rows):
            self._rows = rows

        def cursor(self):
            return DummyCursor(self._rows)

        def close(self):
            return None

    monkeypatch.setattr(webhook, "get_current_step", lambda _n: step)
    monkeypatch.setattr(webhook, "get_connection", lambda: DummyConnection([regla]))

    dispatched = []

    def fake_dispatch(numero_arg, regla_arg, step_arg=None):
        dispatched.append((numero_arg, regla_arg, step_arg))

    monkeypatch.setattr(webhook, "dispatch_rule", fake_dispatch)

    webhook.process_step_chain(numero, text_norm=webhook.normalize_text("domicilio"))
    assert dispatched == [(numero, regla, step)]

    dispatched.clear()
    webhook.process_step_chain(numero, text_norm=webhook.normalize_text("envío"))
    assert dispatched == [(numero, regla, step)]


def test_handle_text_message_keyword_redirect(monkeypatch):
    numero = "12345"
    redirect_step = "flujo_compra"

    monkeypatch.setattr(webhook.Config, "AI_HANDOFF_STEP", "ia_chat")
    monkeypatch.setattr(webhook.Config, "AI_KEYWORD_REDIRECT_STEP", redirect_step)

    captured_steps = []

    def fake_get_triggers(step_names):
        captured_steps.append(tuple(step_names))
        return {"pedido", "comprar"}

    monkeypatch.setattr(webhook, "get_step_triggers", fake_get_triggers)

    monkeypatch.setattr(webhook, "get_chat_state", lambda _n: ("ia_chat", "ia_activa", datetime.now()))
    monkeypatch.setattr(webhook, "delete_chat_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhook, "guardar_mensaje", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhook, "handle_global_command", lambda *args, **kwargs: False)
    monkeypatch.setattr(webhook, "is_ai_enabled", lambda: True)

    update_calls = []

    def fake_update(numero_arg, step, estado=None):
        update_calls.append((numero_arg, step, estado))

    monkeypatch.setattr(webhook, "update_chat_state", fake_update)

    advance_calls = []

    def fake_advance(numero_arg, steps_str):
        advance_calls.append((numero_arg, steps_str))

    monkeypatch.setattr(webhook, "advance_steps", fake_advance)

    process_calls = []

    def fake_process(numero_arg, text_norm=None):
        process_calls.append((numero_arg, text_norm))

    monkeypatch.setattr(webhook, "process_step_chain", fake_process)

    webhook.handle_text_message(numero, "Quiero hacer un pedido", save=False)

    assert advance_calls == [(numero, redirect_step)]
    assert process_calls == [(numero, None)]
    assert any(call[2] == webhook.AI_BLOCKED_STATE for call in update_calls)
    assert all(call[2] != webhook.AI_PENDING_STATE for call in update_calls)
    assert captured_steps == [("flujo_compra",)]


def test_handle_text_message_keyword_redirect_no_match(monkeypatch):
    numero = "55555"

    monkeypatch.setattr(webhook.Config, "AI_HANDOFF_STEP", "ia_chat")
    monkeypatch.setattr(webhook.Config, "AI_KEYWORD_REDIRECT_STEP", "flujo_compra")

    def fake_get_triggers(step_names):
        assert list(step_names) == ["flujo_compra"]
        return {"pedido"}

    monkeypatch.setattr(webhook, "get_step_triggers", fake_get_triggers)

    monkeypatch.setattr(webhook, "get_chat_state", lambda _n: ("ia_chat", None, datetime.now()))
    monkeypatch.setattr(webhook, "delete_chat_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhook, "guardar_mensaje", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhook, "handle_global_command", lambda *args, **kwargs: False)
    monkeypatch.setattr(webhook, "is_ai_enabled", lambda: True)

    update_calls = []

    def fake_update(numero_arg, step, estado=None):
        update_calls.append((numero_arg, step, estado))

    monkeypatch.setattr(webhook, "update_chat_state", fake_update)
    monkeypatch.setattr(webhook, "process_step_chain", lambda *args, **kwargs: None)

    advance_calls = []

    def fake_advance(numero_arg, steps_str):
        advance_calls.append((numero_arg, steps_str))

    monkeypatch.setattr(webhook, "advance_steps", fake_advance)

    webhook.handle_text_message(numero, "Necesito ayuda", save=False)

    assert any(call[2] == webhook.AI_PENDING_STATE for call in update_calls)
    assert not advance_calls


def test_webhook_keyword_short_circuit(monkeypatch):
    numero = "5212345"
    redirect_step = "flujo_compra"

    webhook.message_buffer.clear()
    webhook.pending_timers.clear()

    class DummyConn:
        def __init__(self):
            self.rowcount = 1

        def cursor(self):
            return self

        def execute(self, *args, **kwargs):
            self.rowcount = 1

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(webhook, "get_connection", lambda: DummyConn())
    monkeypatch.setattr(webhook.Config, "AI_HANDOFF_STEP", "ia_chat")
    monkeypatch.setattr(webhook.Config, "AI_KEYWORD_REDIRECT_STEP", redirect_step)
    monkeypatch.setattr(webhook, "is_ai_enabled", lambda: True)
    monkeypatch.setattr(webhook, "guardar_mensaje", lambda *args, **kwargs: None)
    monkeypatch.setattr(webhook, "handle_global_command", lambda *args, **kwargs: False)
    monkeypatch.setattr(webhook, "delete_chat_state", lambda *args, **kwargs: None)

    requested_steps = []

    def fake_get_triggers(step_names):
        requested_steps.append(tuple(step_names))
        return {"comprar", "pedido"}

    monkeypatch.setattr(webhook, "get_step_triggers", fake_get_triggers)

    monkeypatch.setattr(
        webhook,
        "get_chat_state",
        lambda _n: (webhook.Config.AI_HANDOFF_STEP, None, datetime.now()),
    )

    update_calls = []

    def fake_update(numero_arg, step, estado=None):
        update_calls.append((numero_arg, step, estado))

    monkeypatch.setattr(webhook, "update_chat_state", fake_update)

    process_calls = []
    monkeypatch.setattr(
        webhook,
        "process_step_chain",
        lambda numero_arg, text_norm=None: process_calls.append((numero_arg, text_norm)),
    )

    advance_calls = []

    def fake_advance(numero_arg, steps_str):
        advance_calls.append((numero_arg, steps_str))

    monkeypatch.setattr(webhook, "advance_steps", fake_advance)

    worker_calls = []

    def fake_get_messages(after_id, step, limit):
        assert advance_calls, "advance_steps debe ejecutarse antes del worker"
        worker_calls.append((after_id, step, limit))
        return []

    monkeypatch.setattr(ai_worker, "get_messages_for_ai", fake_get_messages)

    app = Flask(__name__)
    app.register_blueprint(webhook.webhook_bp)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.ABCD",
                                    "from": numero,
                                    "type": "text",
                                    "text": {"body": "Quiero comprar"},
                                }
                            ]
                        }
                    }
                ]
            }
        ],
    }

    with app.test_client() as client:
        response = client.post("/webhook", json=payload)

    assert response.status_code == 200
    assert response.get_json() == {"status": "processed_immediate"}
    assert advance_calls == [(numero, redirect_step)]
    assert process_calls == [(numero, None)]
    assert webhook.message_buffer == {}
    assert numero not in webhook.pending_timers
    assert requested_steps == [("flujo_compra",)]

    ai_worker.get_messages_for_ai(0, webhook.Config.AI_HANDOFF_STEP, 10)
    assert worker_calls == [(0, webhook.Config.AI_HANDOFF_STEP, 10)]
