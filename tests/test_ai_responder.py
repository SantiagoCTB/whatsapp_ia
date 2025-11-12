import os
import sys
import types
from typing import Dict

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

mysql_module = types.ModuleType("mysql")
mysql_connector = types.ModuleType("mysql.connector")
mysql_connector.connect = lambda *args, **kwargs: None
mysql_module.connector = mysql_connector
sys.modules.setdefault("mysql", mysql_module)
sys.modules.setdefault("mysql.connector", mysql_connector)

db_stub = types.ModuleType("services.db")
db_stub.AI_BLOCKED_STATE = "ia_bloqueada"
db_stub.claim_ai_message = lambda *args, **kwargs: True
db_stub.get_catalog_media_keywords = lambda: []
db_stub.get_ai_settings = lambda: {}
db_stub.get_messages_for_ai = lambda *args, **kwargs: []
db_stub.get_recent_messages_for_context = lambda *args, **kwargs: []
db_stub.log_ai_interaction = lambda *args, **kwargs: None
db_stub.update_ai_last_processed = lambda *args, **kwargs: None
db_stub.update_chat_state = lambda *args, **kwargs: None
db_stub.get_connection = lambda *args, **kwargs: None
db_stub.guardar_mensaje = lambda *args, **kwargs: None
db_stub.get_chat_state = lambda *args, **kwargs: None
db_stub.delete_chat_state = lambda *args, **kwargs: None
db_stub.is_ai_enabled = lambda *args, **kwargs: True
db_stub.get_step_triggers = lambda *args, **kwargs: []
db_stub.update_mensaje_texto = lambda *args, **kwargs: None
sys.modules.setdefault("services.db", db_stub)

requests_stub = types.ModuleType("requests")
requests_stub.post = lambda *args, **kwargs: None
requests_stub.get = lambda *args, **kwargs: None
sys.modules.setdefault("requests", requests_stub)

whatsapp_stub = types.ModuleType("services.whatsapp_api")
whatsapp_stub.enviar_mensaje = lambda *args, **kwargs: True
whatsapp_stub.download_audio = lambda *args, **kwargs: None
whatsapp_stub.get_media_url = lambda *args, **kwargs: "https://example.com/media"
whatsapp_stub.guardar_mensaje = lambda *args, **kwargs: None
whatsapp_stub.requests = requests_stub
sys.modules.setdefault("services.whatsapp_api", whatsapp_stub)

from services.ai_responder import CatalogResponder
from services import ai_worker


def _dummy_embeddings(texts):
    return [[float(i + 1), 0.0, 0.0] for i in range(len(texts))]


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


def test_ingest_text_generates_metadata(tmp_path, monkeypatch):
    text_path = tmp_path / "catalogo.txt"
    text_path.write_text("Suite Andina $120\nIncluye desayuno", encoding="utf-8")

    base_index = tmp_path / "index" / "catalog"
    media_root = tmp_path / "media"
    pages_dir = tmp_path / "pages"
    media_root.mkdir()
    pages_dir.mkdir()

    monkeypatch.setattr(ai_worker.Config, "AI_VECTOR_STORE_PATH", str(base_index))
    monkeypatch.setattr(ai_worker.Config, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(ai_worker.Config, "AI_PAGE_IMAGE_DIR", str(pages_dir))

    from services import ai_responder

    monkeypatch.setattr(ai_responder.Config, "AI_VECTOR_STORE_PATH", str(base_index))
    monkeypatch.setattr(ai_responder.Config, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(ai_responder.Config, "AI_PAGE_IMAGE_DIR", str(pages_dir))

    monkeypatch.setattr(ai_responder, "update_ai_catalog_metadata", lambda stats: None)
    monkeypatch.setattr(ai_responder, "set_ai_last_processed_to_latest", lambda: None)

    responder = ai_responder.CatalogResponder()
    monkeypatch.setattr(responder, "_embed_texts", _dummy_embeddings)

    stats = responder.ingest_text(str(text_path), source_name="Catalogo TXT")

    assert stats["chunks"] >= 1
    assert responder._metadata
    assert responder._metadata[0]["backend"] == "text"
    assert responder._metadata[0]["source"] == "Catalogo TXT"


def test_ingest_text_with_pdf_images_uses_product_lookup_for_missing_entities(tmp_path, monkeypatch):
    from services import ai_responder

    media_root = tmp_path / "media"
    catalog_root = media_root / "catalogos"
    pages_dir = media_root / "pages"
    base_index = tmp_path / "index" / "catalog"
    for path in (media_root, catalog_root, pages_dir, base_index.parent):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ai_responder.Config, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(ai_responder.Config, "CATALOG_UPLOAD_DIR", str(catalog_root))
    monkeypatch.setattr(ai_responder.Config, "AI_PAGE_IMAGE_DIR", str(pages_dir))
    monkeypatch.setattr(ai_responder.Config, "AI_VECTOR_STORE_PATH", str(base_index))

    monkeypatch.setattr(ai_responder, "update_ai_catalog_metadata", lambda stats: None)
    monkeypatch.setattr(ai_responder, "set_ai_last_processed_to_latest", lambda: None)
    monkeypatch.setattr(ai_responder, "build_catalog_index", lambda *args, **kwargs: {})

    CatalogResponder = ai_responder.CatalogResponder
    CatalogResponder._instance = None
    responder = CatalogResponder()

    catalog_id = "fakehash"
    images_dir = catalog_root / catalog_id / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    pdf_metadata = []
    entity_map = {
        1: "Habitación Pino",
        2: "Habitación Eucalipto",
        4: "Cabaña Cóndor",
        5: "Cabaña Mamaquilla",
        7: "Cabaña Tunúpa",
    }
    for page in range(1, 9):
        rel_path = os.path.join("catalogos", catalog_id, "images", f"page_{page:04d}.png")
        pdf_metadata.append(
            {
                "page": page,
                "chunk": 1,
                "text": f"Página {page}",
                "image": rel_path,
                "image_url": None,
                "entities": [entity_map[page]] if page in entity_map else [],
            }
        )

    responder._load_pdf_metadata = lambda _pdf_path, _source: pdf_metadata
    responder._compute_pdf_hash = lambda _pdf_path: catalog_id

    captured: Dict[str, object] = {}

    def fake_commit(metadata, chunks):
        captured["metadata"] = metadata
        captured["chunks"] = chunks
        return {"chunks": len(metadata)}

    responder._commit_ingest = fake_commit

    def fake_get_image_for_product(name, catalog_id_arg, min_score=0.85):
        lookup = {
            "cabaña inti": 3,
            "cabana inti": 3,
            "cabaña taypi": 6,
            "cabana taypi": 6,
        }
        normalized = name.strip().lower()
        page = lookup.get(normalized)
        if not page:
            return {"ok": False, "reason": "NO_MATCH"}
        image_path = images_dir / f"page_{page:04d}.png"
        image_path.touch()
        return {
            "ok": True,
            "image_path": str(image_path),
            "page": page,
        }

    monkeypatch.setattr(ai_responder, "get_image_for_product", fake_get_image_for_product)

    pdf_path = tmp_path / "catalogo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    text_lines = []
    for idx in range(12):
        text_lines.append(f"SECCIÓN GENERAL {idx}")
        text_lines.append("")
    text_lines.extend(
        [
            "SECCIÓN: FICHAS DE PRODUCTO",
            "",
            "PRODUCTO: Cabaña Inti",
            "Tipo: Cabaña de lujo",
            "Hoja: 3",
            "Observaciones: vista a la montaña",
            "",
            "PRODUCTO: Cabaña Taypi",
            "Tipo: Cabaña familiar",
            "Hoja: 6",
            "Observaciones: ideal para grupos",
        ]
    )
    text_path = tmp_path / "catalogo.txt"
    text_path.write_text("\n".join(text_lines), encoding="utf-8")

    monkeypatch.setattr(ai_responder, "get_known_entity_names", lambda: [])

    responder.ingest_text_with_pdf_images(str(text_path), str(pdf_path), source_name="Catalogo combinado")

    assert "metadata" in captured

    inti_entry = next(m for m in captured["metadata"] if "Cabaña Inti" in m["text"])
    taypi_entry = next(m for m in captured["metadata"] if "Cabaña Taypi" in m["text"])

    assert inti_entry["page"] == 3
    assert inti_entry["image"].endswith("page_0003.png")

    assert taypi_entry["page"] == 6
    assert taypi_entry["image"].endswith("page_0006.png")
