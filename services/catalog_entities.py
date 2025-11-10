"""Utility helpers for well-known catalog entity names."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Set

from services.normalize_text import normalize_text

# Generic words that appear across many product names and should not be used
# on their own to identify a specific entity.
_GENERIC_ENTITY_TOKENS: Set[str] = {
    "cabana",
    "habitacion",
    "habitaciones",
    "suite",
}

_KNOWN_ENTITY_NAMES: Sequence[str] = (
    "Cabaña Cóndor",
    "Cabaña Mamaquilla",
    "Cabaña Tunúpa",
    "Cabaña Taypi",
    "Cabaña Inti",
    "Habitación Eucalipto",
    "Habitación Pino",
)


def _build_entity_index() -> List[Dict[str, object]]:
    index: List[Dict[str, object]] = []
    for name in _KNOWN_ENTITY_NAMES:
        normalized = normalize_text(name)
        tokens = set(normalized.split())
        keywords = {tok for tok in tokens if tok not in _GENERIC_ENTITY_TOKENS}
        if not keywords:
            keywords = set(tokens)
        index.append(
            {
                "name": name,
                "normalized": normalized,
                "tokens": tokens,
                "keywords": keywords,
            }
        )
    return index


_ENTITY_INDEX: Sequence[Dict[str, object]] = tuple(_build_entity_index())


def get_known_entity_names() -> List[str]:
    """Return the canonical catalog entity names."""

    return list(_KNOWN_ENTITY_NAMES)


def iter_entity_index() -> Sequence[Dict[str, object]]:
    """Return read-only entity index information."""

    return _ENTITY_INDEX


def find_entities_in_text(text: str) -> List[Dict[str, object]]:
    """Identify catalog entities mentioned in ``text``.

    The match is accent-insensitive and ignores generic tokens like ``cabaña``.
    """

    normalized = normalize_text(text or "")
    if not normalized:
        return []

    tokens = set(normalized.split())
    if not tokens:
        return []

    matches: List[Dict[str, object]] = []
    for entity in _ENTITY_INDEX:
        normalized_name = entity["normalized"]
        keywords: Set[str] = entity["keywords"]  # type: ignore[assignment]
        entity_tokens: Set[str] = entity["tokens"]  # type: ignore[assignment]

        if normalized_name and normalized_name in normalized:
            matches.append(entity)
            continue
        if keywords and keywords.issubset(tokens):
            matches.append(entity)
            continue
        if entity_tokens and entity_tokens.issubset(tokens):
            matches.append(entity)
            continue
        if keywords and tokens & keywords:
            matches.append(entity)
    return matches


def _score_text_against_entity(text: str, entity: Dict[str, object]) -> int:
    normalized = normalize_text(text or "")
    if not normalized:
        return 0

    tokens = set(normalized.split())
    if not tokens:
        return 0

    normalized_name = entity["normalized"]
    keywords: Set[str] = entity["keywords"]  # type: ignore[assignment]
    entity_tokens: Set[str] = entity["tokens"]  # type: ignore[assignment]

    score = 0
    if normalized_name and normalized_name in normalized:
        score = max(score, 3)
    if keywords and keywords.issubset(tokens):
        score = max(score, 3)
    if entity_tokens and entity_tokens.issubset(tokens):
        score = max(score, 2)
    if keywords and tokens & keywords:
        score = max(score, 2)
    return score


def score_fields_against_entities(
    fields: Iterable[str],
    entities: Sequence[Dict[str, object]],
) -> int:
    """Return a relevance score for ``fields`` towards the given ``entities``.

    The score is the maximum match across all fields and entities.
    """

    best = 0
    for field in fields:
        if not field:
            continue
        for entity in entities:
            best = max(best, _score_text_against_entity(field, entity))
            if best >= 3:
                return best
    return best


def collect_normalized_tokens(*texts: str) -> Set[str]:
    """Normalize the provided texts and return the combined token set."""

    combined: Set[str] = set()
    for text in texts:
        normalized = normalize_text(text or "")
        if not normalized:
            continue
        combined.update(normalized.split())
    return combined
