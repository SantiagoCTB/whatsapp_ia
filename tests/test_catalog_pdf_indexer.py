import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import catalog_pdf_indexer


CATALOG_TEXT = """NATURAL MALLKU — CATÁLOGO DE ALOJAMIENTOS\nVersión: 2025-11-10\n\nSECCIÓN: POLÍTICAS Y DATOS GENERALES\nUbicación: Frente al Parque de las Aguas, a 40 minutos de Medellín.\n\nSECCIÓN: FICHAS DE PRODUCTO\n\nPRODUCTO: Habitación Pino\nTipo: Habitación\nHoja: 4\nTarifa base por noche (2 personas): $350.000\n\nPRODUCTO: Habitación Eucalipto\nTipo: Habitación\nHoja: 2\nTarifa base por noche (2 personas): $430.000\n"""


def test_extract_catalog_products_includes_page_hint(tmp_path):
    text_path = tmp_path / "catalogo.txt"
    text_path.write_text(CATALOG_TEXT, encoding="utf-8")

    products = catalog_pdf_indexer.extract_catalog_products(str(text_path))

    assert [product.name for product in products] == [
        "Habitación Pino",
        "Habitación Eucalipto",
    ]
    assert [product.page_hint for product in products] == [4, 2]


def test_build_catalog_index_uses_page_hint_when_match_missing(tmp_path, monkeypatch):
    text_path = tmp_path / "catalogo.txt"
    text_path.write_text(CATALOG_TEXT, encoding="utf-8")

    pdf_path = tmp_path / "catalogo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    uploads_dir = tmp_path / "uploads"
    monkeypatch.setattr(catalog_pdf_indexer.Config, "CATALOG_UPLOAD_DIR", str(uploads_dir))

    monkeypatch.setattr(
        catalog_pdf_indexer,
        "_prepare_page_index",
        lambda _pdf_path: [],
    )

    def fake_render(_pdf_path: str, page_number: int, output_path: str) -> None:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as fh:
            fh.write(b"image")

    monkeypatch.setattr(catalog_pdf_indexer, "_render_page_image", fake_render)

    catalog_id = "mallku"
    index = catalog_pdf_indexer.build_catalog_index(
        str(text_path), str(pdf_path), catalog_id, min_score=0.99
    )

    product_data = index["Habitación Pino"]
    assert product_data["page"] == 4
    assert product_data["image_path"] == "images/page_0004.png"

    saved_index_path = uploads_dir / catalog_id / "catalog_index.json"
    assert saved_index_path.exists()

    with saved_index_path.open("r", encoding="utf-8") as fh:
        saved_data = json.load(fh)

    assert saved_data["Habitación Eucalipto"]["page"] == 2
