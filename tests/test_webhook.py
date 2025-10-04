import os
import sys
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from routes import webhook


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


def test_handle_text_message_keyword_redirect_variations(monkeypatch):
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

    for message in ("Necesito domicilios", "Puedes llevarlo"):
        update_calls.clear()
        advance_calls.clear()
        process_calls.clear()

        webhook.handle_text_message(numero, message, save=False)

        assert advance_calls == [(numero, redirect_step)]
        assert process_calls == [(numero, None)]
        assert all(call[2] != "ia_pendiente" for call in update_calls)
