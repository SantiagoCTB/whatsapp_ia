"""Herramientas para indexar catálogos PDF y recuperar imágenes por producto."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from config import Config
from services.normalize_text import normalize_text

try:  # pragma: no cover - import opcional para OCR
    from pytesseract import image_to_string
except Exception:  # pragma: no cover - pytesseract opcional
    image_to_string = None  # type: ignore[assignment]


@dataclass
class CatalogProduct:
    """Representa un producto detectado en el catálogo."""

    name: str
    aliases: Sequence[str]


@dataclass
class PageMatch:
    """Resultado de la búsqueda de coincidencias en una página del PDF."""

    page: Optional[int]
    score: float
    alias: Optional[str]


def _read_text_file(path: str) -> str:
    """Lee ``path`` intentando varias codificaciones comunes."""

    last_error: Optional[Exception] = None
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as fh:
                return fh.read()
        except UnicodeDecodeError as exc:
            last_error = exc
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def _iter_lines(text: str) -> Iterable[str]:
    for raw_line in text.splitlines():
        yield raw_line.rstrip("\n\r")


def _apply_alias_replacements(value: str) -> List[str]:
    """Genera variantes básicas para ``value`` reemplazando acentos conocidos."""

    replacements = {
        "cabaña": "cabana",
        "cabana": "cabaña",
        "habitación": "habitacion",
        "habitacion": "habitación",
    }
    variants = {value}
    for src, dst in replacements.items():
        if src in value:
            variants.add(value.replace(src, dst))
    return list(variants)


def _build_aliases(name: str) -> List[str]:
    """Construye la lista de alias de búsqueda para ``name``."""

    alias_candidates = set()
    stripped = (name or "").strip()
    if not stripped:
        return []

    alias_candidates.add(stripped)
    alias_candidates.add(stripped.lower())
    alias_candidates.add(normalize_text(stripped))

    for alias in list(alias_candidates):
        for variant in _apply_alias_replacements(alias):
            alias_candidates.add(variant)
            alias_candidates.add(variant.lower())
            alias_candidates.add(normalize_text(variant))

    cleaned = sorted({alias.strip() for alias in alias_candidates if alias.strip()})
    return cleaned


def extract_catalog_products(text_path: str) -> List[CatalogProduct]:
    """Devuelve los productos encontrados en ``text_path``.

    La extracción comienza después de la línea que contiene
    ``FICHAS DE PRODUCTO`` y cada producto debe iniciar con ``PRODUCTO:``.
    """

    text_content = _read_text_file(text_path)
    if not text_content.strip():
        raise ValueError("El archivo de catálogo no contiene texto utilizable.")

    products: List[CatalogProduct] = []
    in_products_section = False
    for raw_line in _iter_lines(text_content):
        normalized_line = raw_line.strip()
        upper_line = normalized_line.upper()
        if not in_products_section:
            if "FICHAS DE PRODUCTO" in upper_line:
                in_products_section = True
            continue

        if not normalized_line:
            continue

        if upper_line.startswith("PRODUCTO:"):
            name = normalized_line.split(":", 1)[1].strip()
            if not name:
                continue
            products.append(CatalogProduct(name=name, aliases=_build_aliases(name)))
    if not products:
        raise ValueError("No se encontraron productos en la sección FICHAS DE PRODUCTO.")
    return products


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _extract_text_with_pdfium(pdf_path: str) -> List[str]:
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # pragma: no cover - dependencia opcional
        raise RuntimeError("pypdfium2 es requerido para procesar el PDF.") from exc

    texts: List[str] = []
    doc = pdfium.PdfDocument(pdf_path)
    try:
        for page_index in range(len(doc)):
            page_text = ""
            page = doc.get_page(page_index)
            try:
                textpage = page.get_textpage()
                try:
                    page_text = textpage.get_text_range()
                finally:
                    textpage.close()

                if not page_text and image_to_string is not None:
                    # Fallback OCR puntual por página.
                    bitmap = page.render(scale=Config.AI_PAGE_IMAGE_SCALE)
                    try:
                        pil_image = bitmap.to_pil()
                        try:
                            page_text = image_to_string(
                                pil_image, lang=Config.AI_OCR_LANG or "spa"
                            )
                        finally:
                            if hasattr(pil_image, "close"):
                                pil_image.close()  # type: ignore[call-arg]
                    except Exception:
                        page_text = ""
                    finally:
                        bitmap.close()
            finally:
                page.close()
            texts.append(page_text or "")
    finally:
        doc.close()
    return texts


def _prepare_page_index(pdf_path: str) -> List[Dict[str, object]]:
    texts = _extract_text_with_pdfium(pdf_path)
    page_index: List[Dict[str, object]] = []
    for idx, text in enumerate(texts, start=1):
        lower_text = (text or "").lower()
        normalized_text = normalize_text(text)
        normalized_lines = [normalize_text(line) for line in (text or "").splitlines()]
        page_index.append(
            {
                "page": idx,
                "text": text or "",
                "lower": lower_text,
                "normalized": normalized_text,
                "normalized_lines": normalized_lines,
            }
        )
    return page_index


def _score_page_against_alias(page_info: Dict[str, object], alias: str) -> float:
    page_text_lower = page_info.get("lower", "")
    page_text_normalized = page_info.get("normalized", "")
    alias_lower = alias.lower()
    alias_normalized = normalize_text(alias)

    if alias_lower and alias_lower in page_text_lower:
        return 1.0
    if alias_normalized and alias_normalized in page_text_normalized:
        return 0.95

    best = _similarity(alias_lower, page_text_lower)
    best = max(best, _similarity(alias_normalized, page_text_normalized))
    for line in page_info.get("normalized_lines", []):
        best = max(best, _similarity(alias_normalized, line))
        if best >= 0.95:
            break
    return best


def _find_best_page_for_product(
    page_index: Sequence[Dict[str, object]],
    product: CatalogProduct,
) -> PageMatch:
    best_score = 0.0
    best_page: Optional[int] = None
    best_alias: Optional[str] = None

    for alias in product.aliases:
        for page_info in page_index:
            score = _score_page_against_alias(page_info, alias)
            if score > best_score:
                best_score = score
                best_page = int(page_info["page"])
                best_alias = alias
            if best_score >= 1.0:
                return PageMatch(page=best_page, score=best_score, alias=best_alias)
    return PageMatch(page=best_page, score=best_score, alias=best_alias)


def _render_page_image(pdf_path: str, page_number: int, output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        import pypdfium2 as pdfium
    except Exception as exc:  # pragma: no cover - dependencia opcional
        raise RuntimeError("pypdfium2 es requerido para renderizar imágenes del PDF.") from exc

    doc = pdfium.PdfDocument(pdf_path)
    try:
        if page_number < 1 or page_number > len(doc):
            raise ValueError(f"Número de página fuera de rango: {page_number}")
        page = doc.get_page(page_number - 1)
        try:
            bitmap = page.render(scale=Config.AI_PAGE_IMAGE_SCALE)
            try:
                image = bitmap.to_pil()
                image.save(output_path, format="PNG")
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()


def build_catalog_index(
    text_path: str,
    pdf_path: str,
    catalog_id: str,
    *,
    min_score: float = 0.85,
) -> Dict[str, Dict[str, object]]:
    """Genera el índice de productos y lo persiste en disco."""

    catalog_dir = os.path.join(Config.CATALOG_UPLOAD_DIR, catalog_id)
    images_dir = os.path.join(catalog_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    products = extract_catalog_products(text_path)
    page_index = _prepare_page_index(pdf_path)

    rendered_pages: Dict[int, str] = {}
    catalog_index: Dict[str, Dict[str, object]] = {}

    for product in products:
        match = _find_best_page_for_product(page_index, product)
        if match.page is not None and match.score >= min_score:
            page_number = match.page
            if page_number not in rendered_pages:
                image_name = f"page_{page_number:04d}.png"
                image_path = os.path.join(images_dir, image_name)
                try:
                    _render_page_image(pdf_path, page_number, image_path)
                except Exception:
                    logging.exception("No se pudo renderizar la página %s del PDF", page_number)
                    image_path = ""
                rendered_pages[page_number] = image_path
            image_path = rendered_pages.get(page_number, "")
        else:
            page_number = None
            image_path = ""

        catalog_index[product.name] = {
            "page": page_number,
            "aliases": list(product.aliases),
            "match_score": match.score,
            "alias_match": match.alias,
            "image_path": os.path.relpath(image_path, catalog_dir) if image_path else "",
        }

    index_path = os.path.join(catalog_dir, "catalog_index.json")
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(catalog_index, fh, ensure_ascii=False, indent=2)

    return catalog_index


def _load_catalog_index(catalog_id: str) -> Tuple[str, Dict[str, Dict[str, object]]]:
    catalog_dir = os.path.join(Config.CATALOG_UPLOAD_DIR, catalog_id)
    index_path = os.path.join(catalog_dir, "catalog_index.json")
    if not os.path.exists(index_path):
        raise FileNotFoundError(f"No existe catalog_index.json para {catalog_id}")
    with open(index_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("El índice de catálogo tiene un formato inválido.")
    return catalog_dir, data


def _score_query_against_aliases(query: str, aliases: Iterable[str]) -> Tuple[float, Optional[str]]:
    normalized_query = normalize_text(query)
    best_score = 0.0
    best_alias: Optional[str] = None
    for alias in aliases:
        alias_normalized = normalize_text(alias)
        score = _similarity(normalized_query, alias_normalized)
        if score > best_score:
            best_score = score
            best_alias = alias
            if best_score >= 1.0:
                break
    return best_score, best_alias


def get_image_for_product(
    name: str,
    catalog_id: str,
    *,
    min_score: float = 0.85,
) -> Dict[str, object]:
    """Devuelve la ruta de imagen asociada a ``name`` dentro del ``catalog_id``."""

    if not name:
        return {"ok": False, "reason": "NO_MATCH"}

    try:
        catalog_dir, index = _load_catalog_index(catalog_id)
    except FileNotFoundError:
        return {"ok": False, "reason": "NO_CATALOG"}
    except Exception as exc:
        logging.exception("No se pudo cargar el índice del catálogo %s", catalog_id)
        return {"ok": False, "reason": "INVALID_INDEX", "detail": str(exc)}

    normalized_query = normalize_text(name)
    best_match_name: Optional[str] = None
    best_match_score = 0.0
    best_match_alias: Optional[str] = None

    for product_name, data in index.items():
        aliases = data.get("aliases") or []
        if not isinstance(aliases, list):
            continue
        candidate_aliases = list(aliases) + [product_name]
        score, matched_alias = _score_query_against_aliases(normalized_query, candidate_aliases)
        if score > best_match_score:
            best_match_score = score
            best_match_name = product_name
            best_match_alias = matched_alias
        if best_match_score >= 1.0:
            break

    if not best_match_name or best_match_score < min_score:
        return {"ok": False, "reason": "NO_MATCH"}

    product_data = index.get(best_match_name, {})
    page_number = product_data.get("page")
    image_rel_path = product_data.get("image_path") or ""
    if image_rel_path:
        image_path = os.path.join(catalog_dir, image_rel_path)
    elif isinstance(page_number, int):
        image_name = f"page_{page_number:04d}.png"
        image_path = os.path.join(catalog_dir, "images", image_name)
    else:
        image_path = ""

    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "reason": "NO_IMAGE"}

    return {
        "ok": True,
        "product": best_match_name,
        "alias": best_match_alias,
        "score": best_match_score,
        "image_path": image_path,
        "page": page_number,
    }
