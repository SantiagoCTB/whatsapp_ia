import os
import json
import mimetypes
import requests
from flask import url_for
from config import Config
from services.db import guardar_mensaje

TOKEN    = Config.META_TOKEN
PHONE_ID = Config.PHONE_NUMBER_ID
os.makedirs(Config.MEDIA_ROOT, exist_ok=True)

def enviar_mensaje(numero, mensaje, tipo='bot', tipo_respuesta='texto', opciones=None, reply_to_wa_id=None, step=None, regla_id=None):
    url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }
    media_link = None

    if tipo_respuesta == 'texto':
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "text",
            "text": {"body": mensaje}
        }

    elif tipo_respuesta == 'image':
        media_link = opciones
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "image",
            "image": {
                "link": opciones,
                "caption": mensaje
            }
        }

    elif tipo_respuesta == 'lista':
        try:
            opts = json.loads(opciones) if opciones else {}
        except Exception:
            opts = {}

        if isinstance(opts, list):
            sections = opts
            header = "Menú"
            footer = "Selecciona una opción"
            button = "Ver opciones"
        elif isinstance(opts, dict):
            sections = opts.get("sections", [])
            header = opts.get("header") or "Menú"
            footer = opts.get("footer") or "Selecciona una opción"
            button = opts.get("button") or "Ver opciones"
        else:
            sections = []
            header = "Menú"
            footer = "Selecciona una opción"
            button = "Ver opciones"
        if not sections:
            fallback = mensaje or "No hay opciones disponibles."
            print("[WA API] Lista vacía; enviando mensaje de texto de fallback")
            return enviar_mensaje(numero, fallback, tipo, 'texto', None, reply_to_wa_id)

        sections_clean = []
        for sec in sections:
            rows_clean = []
            for row in sec.get("rows", []):
                row_clean = {k: v for k, v in row.items() if k not in {"step", "next_step"}}
                rows_clean.append(row_clean)
            sec_clean = {k: v for k, v in sec.items() if k != "rows"}
            sec_clean["rows"] = rows_clean
            sections_clean.append(sec_clean)

        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header},
                "body": {"text": mensaje},
                "footer": {"text": footer},
                "action": {
                    "button": button,
                    "sections": sections_clean
                }
            }
        }

    elif tipo_respuesta == 'boton':
        try:
            botones = json.loads(opciones) if opciones else []
        except Exception:
            botones = []
        botones_clean = []
        for b in botones:
            btn_clean = {k: v for k, v in b.items() if k not in {"step", "next_step"}}
            botones_clean.append(btn_clean)
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": mensaje},
                "action": {"buttons": botones_clean}
            }
        }

    elif tipo_respuesta == 'flow':
        try:
            opts = json.loads(opciones) if opciones else {}
        except Exception:
            opts = {}

        allowed_header_types = {"text"}
        header_raw = opts.get("header")
        header_text = None
        header_type = "text"
        if isinstance(header_raw, str):
            header_text = header_raw.strip()
        elif isinstance(header_raw, dict):
            header_value = header_raw.get("text")
            if header_value is not None:
                header_text = str(header_value).strip()
            header_type_candidate = header_raw.get("type")
            if isinstance(header_type_candidate, str):
                header_type_candidate = header_type_candidate.strip().lower() or None
                if header_type_candidate in allowed_header_types:
                    header_type = header_type_candidate
        elif header_raw is not None:
            header_text = str(header_raw).strip()
        if header_text == "":
            header_text = None
        footer = opts.get("footer")
        if isinstance(footer, str):
            footer = footer.strip() or None

        action_raw = opts.get("action") or {}
        parameters_raw = action_raw.get("parameters") or opts.get("parameters") or {}

        flow_parameters = {}

        flow_message_version = parameters_raw.get("flow_message_version") or opts.get("flow_message_version") or "3"
        flow_parameters["flow_message_version"] = str(flow_message_version)

        keys_to_copy = [
            "flow_cta",
            "flow_id",
            "flow_name",
            "mode",
            "flow_token",
            "flow_action",
        ]
        for key in keys_to_copy:
            value = parameters_raw.get(key)
            if value is None:
                value = action_raw.get(key)
            if value is None:
                value = opts.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value:
                flow_parameters[key] = value

        payload_raw = (
            parameters_raw.get("flow_action_payload")
            or action_raw.get("flow_action_payload")
            or opts.get("flow_action_payload")
            or {}
        )
        if isinstance(payload_raw, dict):
            payload = {}
            screen_value = payload_raw.get("screen")
            if isinstance(screen_value, str):
                screen_value = screen_value.strip()
            if screen_value:
                payload["screen"] = screen_value
            data_value = payload_raw.get("data")
            if data_value not in (None, ""):
                payload["data"] = data_value
            if payload:
                flow_parameters["flow_action_payload"] = payload

        flow_cta = flow_parameters.get("flow_cta")
        flow_id = flow_parameters.get("flow_id")
        flow_name = flow_parameters.get("flow_name")
        if not flow_cta or not (flow_id or flow_name):
            return False

        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "body": {"text": mensaje},
                "action": {
                    "name": "flow",
                    "parameters": flow_parameters,
                },
            },
        }

        if header_text:
            if header_type not in allowed_header_types:
                header_type = "text"
            data["interactive"]["header"] = {"type": header_type, "text": header_text}
        if footer:
            data["interactive"]["footer"] = {"text": footer}

    elif tipo_respuesta == 'audio':
        if opciones and os.path.isfile(opciones):
            filename   = os.path.basename(opciones)
            public_url = url_for('static', filename=f'uploads/{filename}', _external=True)
            audio_obj  = {"link": public_url}
        else:
            audio_obj = {"link": opciones}

        if mensaje:
            audio_obj["caption"] = mensaje

        media_link = audio_obj.get("link")
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "audio",
            "audio": audio_obj
        }

    elif tipo_respuesta == 'video':
        if opciones and os.path.isfile(opciones):
            filename   = os.path.basename(opciones)
            public_url = url_for('static', filename=f'uploads/{filename}', _external=True)
            video_obj  = {"link": public_url}
        else:
            video_obj  = {"link": opciones}

        if mensaje:
            video_obj["caption"] = mensaje

        media_link = video_obj.get("link")
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "video",
            "video": video_obj
        }

    elif tipo_respuesta == 'document':
        media_link = opciones
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "document",
            "document": {
                "link": opciones,
                "caption": mensaje
            }
        }

    else:
        data = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "text",
            "text": {"body": mensaje}
        }

    if reply_to_wa_id:
        data["context"] = {"message_id": reply_to_wa_id}

    # Validar URLs externas antes de enviar a la API de WhatsApp
    if media_link and isinstance(media_link, str) and media_link.startswith(('http://', 'https://')):
        try:
            check = requests.head(media_link, allow_redirects=True, timeout=5)
        except requests.RequestException:
            return False
        if check.status_code != 200:
            return False
    resp = requests.post(url, headers=headers, json=data)
    print(f"[WA API] {resp.status_code} — {resp.text}")
    if not resp.ok:
        return False
    try:
        wa_id = resp.json().get("messages", [{}])[0].get("id")
    except Exception:
        wa_id = None
    tipo_db = tipo
    if tipo_respuesta in {"image", "audio", "video", "document", "flow"} and "_" not in tipo:
        tipo_db = f"{tipo}_{tipo_respuesta}"

    media_url_db = None
    if tipo_respuesta == 'video':
        media_url_db = video_obj.get("link")
    elif tipo_respuesta == 'audio':
        media_url_db = audio_obj.get("link")
    elif tipo_respuesta not in {"flow"}:
        media_url_db = opciones

    guardar_mensaje(
        numero,
        mensaje,
        tipo_db,
        wa_id=wa_id,
        reply_to_wa_id=reply_to_wa_id,
        media_id=None,
        media_url=media_url_db,
        step=step,
        regla_id=regla_id,
    )
    return True

def get_media_url(media_id):
    resp1 = requests.get(
        f"https://graph.facebook.com/v19.0/{media_id}",
        params={"access_token": TOKEN}
    )
    resp1.raise_for_status()
    media_url = resp1.json().get("url")

    resp2 = requests.get(media_url, headers={"Authorization": f"Bearer {TOKEN}"})
    resp2.raise_for_status()

    ext = resp2.headers.get("Content-Type", "").split("/")[-1] or "bin"
    filename = f"{media_id}.{ext}"
    path     = os.path.join(Config.MEDIA_ROOT, filename)
    with open(path, "wb") as f:
        f.write(resp2.content)

    return url_for("static", filename=f"uploads/{filename}", _external=True)

def subir_media(ruta_archivo):
    mime_type, _ = mimetypes.guess_type(ruta_archivo)
    if not mime_type:
        raise ValueError(f"No se pudo inferir el MIME type de {ruta_archivo}")

    url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/media"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = {
        "messaging_product": "whatsapp",
        "type": mime_type
    }
    with open(ruta_archivo, "rb") as f:
        files = {"file": (os.path.basename(ruta_archivo), f, mime_type)}
        resp = requests.post(url, headers=headers, data=data, files=files)
    resp.raise_for_status()
    return resp.json().get("id")

def download_audio(media_id):
    # sirve tanto para audio como para video
    url_media = f"https://graph.facebook.com/v19.0/{media_id}"
    r1        = requests.get(url_media, params={"access_token": TOKEN})
    r1.raise_for_status()
    media_url = r1.json()["url"]
    r2        = requests.get(media_url, headers={"Authorization": f"Bearer {TOKEN}"}, stream=True)
    r2.raise_for_status()
    return r2.content
