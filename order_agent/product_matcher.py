"""ProductMatcher — FAISS IndexFlatIP with paraphrase-multilingual-MiniLM-L12-v2."""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

from order_agent.models import ProductMatch
from order_agent.vn_utils import normalize_text

logger = logging.getLogger(__name__)

# Similarity thresholds (research.md Decision 3)
THRESHOLD_AUTO = 0.85
THRESHOLD_RERANK = 0.65

_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


class ProductMatcher:
    """
    FAISS-backed product matcher using multilingual sentence embeddings.

    Index is built atomically: a new index is built into a temp variable
    and swapped in-place to avoid partially updating the live index.
    """

    def __init__(self) -> None:
        self._index: Any = None  # faiss.IndexFlatIP
        self._product_ids: list[str] = []
        self._product_data: list[dict[str, Any]] = []
        self._model: Any = None  # SentenceTransformer
        self._lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415

            self._model = SentenceTransformer(_MODEL_NAME)
        return self._model

    def build_index(self, products: list[dict[str, Any]]) -> None:
        """
        Build FAISS index from product list.
        Atomic: builds into temp objects, then swaps references under lock.
        Each product dict must have: id, name, price.
        """
        if not products:
            with self._lock:
                self._index = None
                self._product_ids = []
                self._product_data = []
            return

        import faiss  # noqa: PLC0415

        model = self._get_model()
        names = [normalize_text(p["name"]) for p in products]
        embeddings = model.encode(names, normalize_embeddings=True).astype(np.float32)

        dim = embeddings.shape[1]
        new_index = faiss.IndexFlatIP(dim)
        new_index.add(embeddings)

        new_ids = [p["id"] for p in products]
        new_data = list(products)

        # Atomic swap
        with self._lock:
            self._index = new_index
            self._product_ids = new_ids
            self._product_data = new_data

        logger.info("faiss_index_built", extra={"product_count": len(products)})

    def search(self, query: str, top_k: int = 3) -> list[ProductMatch]:
        """
        Search for products matching the query string.
        Returns ProductMatch list with match_status based on cosine similarity thresholds.
        """
        with self._lock:
            index = self._index
            product_ids = list(self._product_ids)
            product_data = list(self._product_data)

        if index is None or len(product_ids) == 0:
            return []

        model = self._get_model()
        normalized_query = normalize_text(query)
        query_embedding = model.encode([normalized_query], normalize_embeddings=True).astype(np.float32)

        k = min(top_k, len(product_ids))
        scores, indices = index.search(query_embedding, k)

        results: list[ProductMatch] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            product = product_data[idx]
            similarity = float(score)

            if similarity >= THRESHOLD_AUTO:
                status = "auto"
            elif similarity >= THRESHOLD_RERANK:
                status = "rerank"
            else:
                status = "ask_user"

            results.append(
                ProductMatch(
                    product_query=query,
                    matched_product_id=product["id"],
                    matched_product_name=product["name"],
                    price=product.get("price"),
                    similarity_score=max(0.0, min(1.0, similarity)),
                    match_status=status,
                    candidates=[
                        {"id": product_data[i]["id"], "name": product_data[i]["name"]}
                        for i in indices[0]
                        if i >= 0
                    ],
                )
            )

        return results

    @property
    def index_size(self) -> int:
        with self._lock:
            return len(self._product_ids)

    @property
    def product_ids(self) -> list[str]:
        with self._lock:
            return list(self._product_ids)
