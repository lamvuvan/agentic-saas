"""Product matching node — FAISS search with threshold routing and GPT-4o-mini rerank."""

from __future__ import annotations

import json
import logging

from order_agent.models import OrderEntities, ProductMatch
from order_agent.product_matcher import THRESHOLD_AUTO, THRESHOLD_RERANK
from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)


async def match_products(
    entities: OrderEntities,
    product_matcher,  # ProductMatcher instance from app.state
    trace_id: str = "",
) -> tuple[list[ProductMatch], list[str]]:
    """
    Match each extracted OrderItem against the FAISS product index.

    Returns:
        (matches, unresolved_queries)
        - matches: ProductMatch per item
        - unresolved_queries: items needing user input (ask_user status)
    """
    matches: list[ProductMatch] = []
    unresolved: list[str] = []

    for item in entities.items:
        results = product_matcher.search(item.product_query, top_k=3)

        if not results:
            matches.append(
                ProductMatch(
                    product_query=item.product_query,
                    similarity_score=0.0,
                    match_status="not_found",
                )
            )
            unresolved.append(item.product_query)
            continue

        top = results[0]

        if top.match_status == "auto":
            # High confidence — use directly
            matches.append(top)

        elif top.match_status == "rerank":
            # Medium confidence — ask GPT-4o-mini to select from top-3
            reranked = await _rerank_with_llm(item.product_query, results, trace_id)
            matches.append(reranked)

        else:
            # Low confidence — needs user clarification
            top_with_candidates = top.model_copy(update={"candidates": [
                {"id": r.matched_product_id, "name": r.matched_product_name, "score": r.similarity_score}
                for r in results if r.matched_product_id
            ]})
            matches.append(top_with_candidates)
            unresolved.append(item.product_query)

    return matches, unresolved


async def _rerank_with_llm(
    query: str,
    candidates: list[ProductMatch],
    trace_id: str = "",
) -> ProductMatch:
    """Use GPT-4o-mini to select the best candidate from top-3."""
    candidate_list = "\n".join(
        f"{i+1}. {c.matched_product_name} (similarity: {c.similarity_score:.2f})"
        for i, c in enumerate(candidates[:3])
        if c.matched_product_name
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a product matching assistant for a Vietnamese restaurant. "
                "Given a user's product query and candidate products, choose the best match. "
                'Return JSON: {"index": 0} where index is 0-based position in the candidates list. '
                "If none match well, return index: -1."
            ),
        },
        {
            "role": "user",
            "content": f"Query: '{query}'\nCandidates:\n{candidate_list}",
        },
    ]

    try:
        content, _ = await chat_completion_async(
            messages=messages,
            task_type="product_rerank",
            extra_log={"trace_id": trace_id, "query": query},
        )
        result = json.loads(content)
        idx = int(result.get("index", 0))
        if 0 <= idx < len(candidates):
            return candidates[idx].model_copy(update={"match_status": "auto"})
    except Exception as exc:
        logger.warning("rerank_failed", extra={"query": query, "error": str(exc)})

    # Fallback to first candidate
    return candidates[0]
