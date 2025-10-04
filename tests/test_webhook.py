import os
import sys
from datetime import datetime

from flask import Flask

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from routes import webhook
from services import ai_worker


def test_handle_text_message_keyword_redirect(monkeypatch):
    numero = "12345"
    redirect_step = "flujo_compra"

    monkeypatch.setattr(webhook.Config, "AI_HANDOFF_STEP", "ia_chat")
    monkeypatch.setattr(webhook.Config, "AI_KEYWORD_REDIRECT_STEP", redirect_step)

    monkeypatch.setattr(webhook, "get_chat_state", lambda _n: ("ia_chat", datetime.now()))
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
    assert all(call[2] != "ia_pendiente" for call in update_calls)


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

    monkeypatch.setattr(
        webhook,
        "get_chat_state",
        lambda _n: (webhook.Config.AI_HANDOFF_STEP, datetime.now()),
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

    ai_worker.get_messages_for_ai(0, webhook.Config.AI_HANDOFF_STEP, 10)
    assert worker_calls == [(0, webhook.Config.AI_HANDOFF_STEP, 10)]
