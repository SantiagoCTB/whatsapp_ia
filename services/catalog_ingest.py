"""Gestión de trabajos en segundo plano para ingesta de catálogos."""

import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Dict, Optional

from services.ai_responder import CatalogResponder


ExecutorResult = Dict[str, object]


class _CatalogIngestState:
    """Estado compartido para la ingesta en segundo plano."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._future: Optional[Future[ExecutorResult]] = None
        self._status: Dict[str, object] = {
            "state": "idle",
            "source_name": None,
            "file_type": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "stats": None,
        }

    def get_status(self) -> Dict[str, object]:
        with self._lock:
            status = dict(self._status)
            future = self._future
        if future and future.done():
            # Garantiza que los errores se reflejen en el estado principal.
            try:
                future.result()
            except Exception:  # pragma: no cover - se registra en el callback
                pass
        return status

    def start_job(
        self,
        responder: "CatalogResponder",
        file_path: str,
        source_name: str,
        file_type: str,
    ) -> None:
        with self._lock:
            if self._future and not self._future.done():
                raise RuntimeError("Ya existe un proceso de ingesta en ejecución.")

            self._status.update(
                {
                    "state": "running",
                    "source_name": source_name,
                    "file_type": file_type,
                    "started_at": datetime.utcnow().isoformat(),
                    "finished_at": None,
                    "error": None,
                    "stats": None,
                }
            )

            future = self._executor.submit(
                self._run_ingest, responder, file_path, source_name, file_type
            )
            future.add_done_callback(
                lambda fut: self._on_done(fut, file_path, source_name, file_type)
            )
            self._future = future

    def _run_ingest(
        self,
        responder: "CatalogResponder",
        file_path: str,
        source_name: str,
        file_type: str,
    ) -> ExecutorResult:
        stats = responder.ingest_document(
            file_path, source_name=source_name, file_type=file_type
        )
        return {
            "stats": stats,
        }

    def _on_done(
        self,
        future: Future[ExecutorResult],
        file_path: str,
        source_name: str,
        file_type: str,
    ) -> None:
        error: Optional[str] = None
        stats: Optional[Dict[str, object]] = None
        try:
            result = future.result()
            stats = result.get("stats") if isinstance(result, dict) else None
        except Exception as exc:  # pragma: no cover - logging defensivo
            logging.exception("Error al procesar el catálogo %s", source_name)
            error = str(exc)
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                logging.warning(
                    "No se pudo eliminar el archivo temporal %s", file_path, exc_info=True
                )

        with self._lock:
            self._status.update(
                {
                    "state": "failed" if error else "succeeded",
                    "finished_at": datetime.utcnow().isoformat(),
                    "error": error,
                    "stats": stats,
                    "file_type": file_type,
                }
            )


_state = _CatalogIngestState()


def start_catalog_ingest(
    responder: "CatalogResponder",
    file_path: str,
    source_name: str,
    file_type: str,
) -> None:
    """Encola la ingesta del catálogo en un hilo de fondo."""
    _state.start_job(responder, file_path, source_name, file_type)


def get_catalog_ingest_status() -> Dict[str, object]:
    """Devuelve una copia del estado actual del trabajo de ingesta."""
    return _state.get_status()
