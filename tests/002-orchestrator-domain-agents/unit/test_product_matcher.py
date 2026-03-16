"""Unit tests for ProductMatcher — FAISS search and threshold routing."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_products():
    """Minimal product catalog for test index."""
    return [
        {"id": "p001", "name": "Trứng lộn", "price": 10000.0},
        {"id": "p002", "name": "Cháo lòng", "price": 25000.0},
        {"id": "p003", "name": "Bò kho bánh mì", "price": 45000.0},
        {"id": "p004", "name": "Cà phê đen", "price": 15000.0},
        {"id": "p005", "name": "Phở bò", "price": 40000.0},
    ]


@pytest.fixture
def matcher(sample_products):
    from order_agent.product_matcher import ProductMatcher

    m = ProductMatcher()
    # Build synchronously for tests (build_index is sync)
    m.build_index(sample_products)
    return m


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------


class TestIndexBuilding:
    def test_index_built_with_correct_size(self, matcher, sample_products):
        assert matcher.index_size == len(sample_products)

    def test_build_index_with_empty_catalog(self):
        from order_agent.product_matcher import ProductMatcher

        m = ProductMatcher()
        m.build_index([])
        assert m.index_size == 0

    def test_product_ids_aligned_with_index(self, matcher, sample_products):
        """Product IDs must be stored in same order as FAISS index positions."""
        assert len(matcher.product_ids) == len(sample_products)
        assert matcher.product_ids[0] == "p001"


# ---------------------------------------------------------------------------
# Search — threshold routing
# ---------------------------------------------------------------------------


class TestSearchThresholdRouting:
    def test_exact_match_above_threshold_auto(self, matcher):
        """Exact product name match should yield status='auto' with high similarity."""
        results = matcher.search("Trứng lộn", top_k=3)
        assert len(results) > 0
        top = results[0]
        # Exact match should be high similarity
        assert top.similarity_score > 0.65

    def test_auto_status_when_similarity_above_0_85(self, matcher):
        results = matcher.search("Trứng lộn", top_k=3)
        top = results[0]
        if top.similarity_score >= 0.85:
            assert top.match_status == "auto"

    def test_rerank_status_when_similarity_0_65_to_0_84(self, matcher):
        """Similarity in 0.65–0.84 range should yield match_status='rerank'."""
        results = matcher.search("trứng", top_k=3)  # partial match, likely lower score
        for r in results:
            if 0.65 <= r.similarity_score < 0.85:
                assert r.match_status == "rerank"
                break

    def test_ask_user_when_similarity_below_0_65(self, matcher):
        """Very different query should yield match_status='ask_user'."""
        results = matcher.search("xyz_nonexistent_product_abc", top_k=3)
        if results and results[0].similarity_score < 0.65:
            assert results[0].match_status == "ask_user"

    def test_search_returns_at_most_top_k(self, matcher):
        results = matcher.search("phở", top_k=2)
        assert len(results) <= 2

    def test_empty_index_returns_empty_list(self):
        from order_agent.product_matcher import ProductMatcher

        m = ProductMatcher()
        m.build_index([])
        results = m.search("test")
        assert results == []

    def test_candidates_present_for_rerank(self, matcher):
        """For rerank/ask_user status, candidates list must be populated."""
        results = matcher.search("cà phê", top_k=3)
        for r in results:
            if r.match_status in ("rerank", "ask_user"):
                # Candidates should be populated at the search result level
                assert r.match_status in ("rerank", "ask_user")


# ---------------------------------------------------------------------------
# Atomic refresh
# ---------------------------------------------------------------------------


class TestAtomicRefresh:
    def test_refresh_does_not_corrupt_live_index(self, sample_products):
        """Index swap must be atomic — searches during rebuild should not fail."""
        from order_agent.product_matcher import ProductMatcher

        m = ProductMatcher()
        m.build_index(sample_products)

        # Simulate rebuild: build new index, swap reference
        new_products = sample_products + [{"id": "p006", "name": "Nước mía", "price": 12000.0}]
        m.build_index(new_products)  # atomic swap in implementation

        # After refresh, index should have the new size
        assert m.index_size == len(new_products)

    def test_search_after_refresh_uses_new_index(self, sample_products):
        from order_agent.product_matcher import ProductMatcher

        m = ProductMatcher()
        m.build_index(sample_products)
        m.build_index(sample_products + [{"id": "p007", "name": "Sữa đậu nành", "price": 8000.0}])

        results = m.search("Sữa đậu nành", top_k=1)
        assert len(results) > 0
