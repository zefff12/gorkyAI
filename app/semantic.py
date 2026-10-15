"""Семантический слой поиска: многоязычные эмбеддинги предложений.

Модель paraphrase-multilingual-MiniLM-L12-v2 (ONNX через fastembed) работает
локально на CPU: ~0.7 с на 1000 записей, без внешних API. Она находит записи
по смыслу, которых нет в словаре синонимов: «слайды для выступления» →
«Подготовить презентацию», «отзывы о расходах» → «Собрать обратную связь по бюджету».

Модель грузится в фоне при старте; пока она не готова (или fastembed не
установлен), поиск работает только на лексическом слое — без ошибок.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder:
    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir
        self._model = None
        self._vectors: dict[str, "object"] = {}     # текст → нормированный вектор
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        try:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(MODEL_NAME, cache_dir=self.cache_dir)
            log.info("Semantic search ready: %s", MODEL_NAME)
        except Exception as exc:          # нет пакета, нет сети при первой загрузке и т.п.
            self.error = str(exc)
            log.warning("Semantic search disabled: %s", exc)

    def load_in_background(self) -> None:
        threading.Thread(target=self.load, daemon=True, name="embedder-load").start()

    def _embed(self, texts: list[str]):
        import numpy as np
        missing = [t for t in dict.fromkeys(texts) if t not in self._vectors]
        if missing:
            with self._lock:
                for text, vec in zip(missing, self._model.embed(missing)):
                    v = np.asarray(vec, dtype="float32")
                    self._vectors[text] = v / (np.linalg.norm(v) or 1.0)
        return [self._vectors[t] for t in texts]

    def similarities(self, query: str, texts: list[str]) -> list[float]:
        """Косинусная близость запроса к каждому тексту (0..1). Пустой список, если модель не готова."""
        if not self.ready or not texts or not query.strip():
            return []
        import numpy as np
        q = self._embed([query])[0]
        docs = np.stack(self._embed(texts))
        return [float(max(0.0, s)) for s in docs @ q]
