import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from services import whatsapp_api


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def stub_guardar(monkeypatch):
    monkeypatch.setattr(whatsapp_api, "guardar_mensaje", lambda *args, **kwargs: None)


@pytest.fixture
def post_call(monkeypatch):
    calls = {}

    def fake_post(url, headers=None, json=None):
        calls["url"] = url
        calls["headers"] = headers
        calls["payload"] = json
        return DummyResponse({"messages": [{"id": "wamid.HASH"}]})

    monkeypatch.setattr(whatsapp_api.requests, "post", fake_post)
    return calls


def test_flow_header_from_string(post_call):
    opciones = json.dumps({
        "header": "   Encabezado Flow   ",
        "flow_cta": "CTA",
        "flow_id": "FLOW123",
        "mode": "draft",
        "flow_action": "navigate",
    })

    result = whatsapp_api.enviar_mensaje(
        numero="1234567890",
        mensaje="Mensaje",
        tipo="bot",
        tipo_respuesta="flow",
        opciones=opciones,
    )

    assert result is True
    payload = post_call["payload"]
    header = payload["interactive"]["header"]
    assert header["type"] == "text"
    assert header["text"] == "Encabezado Flow"
    assert isinstance(header["text"], str)


def test_flow_header_from_dict(post_call):
    opciones = json.dumps({
        "header": {"type": "image", "text": 12345},
        "flow_cta": "CTA",
        "flow_name": "Flow Name",
        "mode": "published",
        "flow_action": "navigate",
    })

    result = whatsapp_api.enviar_mensaje(
        numero="0987654321",
        mensaje="Otro mensaje",
        tipo="bot",
        tipo_respuesta="flow",
        opciones=opciones,
    )

    assert result is True
    header = post_call["payload"]["interactive"]["header"]
    assert header["type"] == "text"
    assert header["text"] == "12345"
    assert isinstance(header["text"], str)


def test_flow_action_payload_accepts_string(post_call):
    opciones = json.dumps({
        "flow_cta": "CTA",
        "flow_id": "FLOW123",
        "flow_action_payload": "RAW_STRING_PAYLOAD",
    })

    result = whatsapp_api.enviar_mensaje(
        numero="1111111111",
        mensaje="Mensaje",
        tipo="bot",
        tipo_respuesta="flow",
        opciones=opciones,
    )

    assert result is True
    payload = (
        post_call["payload"]["interactive"]["action"]["parameters"]["flow_action_payload"]
    )
    assert payload == "RAW_STRING_PAYLOAD"


def test_flow_action_payload_cleans_dict(post_call):
    opciones = json.dumps({
        "flow_cta": "CTA",
        "flow_name": "Flow Name",
        "flow_action_payload": {
            "screen": "   Screen Name   ",
            "data": {"foo": "bar"},
            "unused": "",
        },
    })

    result = whatsapp_api.enviar_mensaje(
        numero="2222222222",
        mensaje="Mensaje",
        tipo="bot",
        tipo_respuesta="flow",
        opciones=opciones,
    )

    assert result is True
    payload = (
        post_call["payload"]["interactive"]["action"]["parameters"]["flow_action_payload"]
    )
    assert payload == {"screen": "Screen Name", "data": {"foo": "bar"}}


def test_flow_action_payload_omits_empty_values(post_call):
    opciones = json.dumps({
        "flow_cta": "CTA",
        "flow_id": "FLOW456",
        "flow_action_payload": {
            "screen": "   ",
            "data": {},
        },
    })

    result = whatsapp_api.enviar_mensaje(
        numero="3333333333",
        mensaje="Mensaje",
        tipo="bot",
        tipo_respuesta="flow",
        opciones=opciones,
    )

    assert result is True
    parameters = post_call["payload"]["interactive"]["action"]["parameters"]
    assert "flow_action_payload" not in parameters
