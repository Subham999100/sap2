
import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Set

import numpy as np

from backend.app.config import settings

logger = logging.getLogger(__name__)


STOP_WORDS = {
    "what", "is", "the", "a", "an", "how", "to", "in", "of", "and",
    "for", "with", "does", "explain", "describe", "can", "you", "tell",
    "me", "about", "which", "are", "it", "this", "that", "from",
    "into", "through", "between", "using", "where", "when", "why",
}

TECHNICAL_IDENTIFIERS = {
    # T-Codes
    "fpl9", "fpe1", "fpe2", "fpe3", "fp04", "fpy1", "fpva", "fpvb",
    "fpo1", "fp06", "fp07", "fp08", "fp40", "spro", "pfcg", "sm30",
    "fkkbix", "fkkbixbit_mon", "fkkbix_bit_mon", "fkkbixcit_mon", "fkkbix_cit_mon",
    # Core SAP BRIM tables
    "dfkkop", "dfkkko", "dfkkinv_doc", "dfkkbip", "fkkvkp", "cdhdr", "cdpos",
    # Core SAP BRIM components & acronyms
    "som", "cc", "ci", "fi-ca", "fica", "rar", "sd", "s4hana", "btp",
    # Domain concepts & objects
    "pob", "odi", "bit", "cit", "rap", "daas",
}

NORMALIZED_TECHNICAL_IDENTIFIERS = {
    re.sub(r"[-_/]", "", t.lower()) for t in TECHNICAL_IDENTIFIERS
}

DOMAIN_PRODUCT_NAMES = {
    "ariba", "concur", "fieldglass", "hybris", "c4c",
    "qualtrics", "celonis",
}

TOKEN_PATTERN = re.compile(r"(?:s/4hana|[a-z0-9]+(?:[_-][a-z0-9]+)*)")


class RerankingService:
    """
    Lightweight heuristic scoring and candidate deduplication service.
    ColBERT neural reranking has been completely removed.
    """
    def __init__(self):
        pass

    @staticmethod
    def _normalize_token(text: str) -> str:
        """Strip punctuation/separators for robust identifier matching."""
        return re.sub(r"[-_/]", "", (text or "").lower())

    @staticmethod
    def _tokens(text: str) -> List[str]:
        """Tokenize text while preserving technical identifiers."""
        return TOKEN_PATTERN.findall((text or "").lower())


    @classmethod
    def _heuristic_features(
        cls,
        query: str,
        candidate: Dict[str, Any],
    ) -> Dict[str, float]:
        query_lower = query.lower()
        query_tokens = set(cls._tokens(query))
        candidate_text = candidate.get("chunk_text") or ""
        text_lower = candidate_text.lower()
        text_tokens = set(cls._tokens(candidate_text))
        section_lower = (
            candidate.get("section") or ""
        ).lower()
        document_lower = (
            candidate.get("document_name") or ""
        ).lower()

        raw_query_tokens = cls._tokens(query)
        norm_query_tokens = [cls._normalize_token(t) for t in raw_query_tokens]
        norm_text_tokens = {cls._normalize_token(t) for t in text_tokens}

        # Exact or normalized identifier matching preserves technical names and handles format differences (e.g. fkkbix_bit_mon vs fkkbixbit_mon)
        identifier_matches = sum(
            1
            for t, norm_t in zip(raw_query_tokens, norm_query_tokens)
            if (t in TECHNICAL_IDENTIFIERS or norm_t in NORMALIZED_TECHNICAL_IDENTIFIERS)
            and (t in text_tokens or norm_t in norm_text_tokens)
        )

        # Count matching query terms without relying on substring matches.
        significant_tokens = {
            token
            for token in query_tokens
            if token not in STOP_WORDS and len(token) > 2
        }

        norm_section_tokens = {
            cls._normalize_token(t)
            for t in cls._tokens(section_lower)
        }

        section_matches = sum(
            1
            for token in significant_tokens
            if token in set(cls._tokens(section_lower))
            or cls._normalize_token(token) in norm_section_tokens
        )

        content_matches = sum(
            1
            for token in significant_tokens
            if token in text_tokens
            or cls._normalize_token(token) in norm_text_tokens
        )

        coverage = (
            content_matches / len(significant_tokens)
            if significant_tokens
            else 0.0
        )

        section_boost = min(section_matches * 0.04, 0.12)

        # Match adjacent meaningful query terms, rather than counting every
        # adjacent word pair in the original question.
        query_terms = [
            token
            for token in cls._tokens(query)
            if token not in STOP_WORDS
        ]

        ngram_matches = 0
        for index in range(len(query_terms) - 1):
            phrase = (
                f"{query_terms[index]} {query_terms[index + 1]}"
            )
            if phrase in text_lower:
                ngram_matches += 1

        ngram_boost = min(ngram_matches * 0.025, 0.10)

        direct_boost = 0.0

        if re.search(
            r"\b(?:is defined as|refers to|represents|consists of)\b",
            text_lower,
        ):
            direct_boost += 0.025

        if re.search(
            r"\b(?:step\s+\d+|prerequisites|procedure|process flow|"
            r"transaction code)\b",
            text_lower,
        ):
            direct_boost += 0.025

        direct_boost += min(identifier_matches * 0.07, 0.18)

        # Multi-concept co-occurrence boost: when a query asks across distinct technical identifiers
        # or domain concepts, reward chunks that genuinely discuss multiple required concepts together
        distinct_query_tech = {
            norm_t for t, norm_t in zip(raw_query_tokens, norm_query_tokens)
            if (t in TECHNICAL_IDENTIFIERS or norm_t in NORMALIZED_TECHNICAL_IDENTIFIERS)
        }
        if len(distinct_query_tech) >= 2:
            matched_tech = sum(1 for norm_t in distinct_query_tech if norm_t in norm_text_tokens)
            if matched_tech >= 2:
                direct_boost += 0.05

        noise_penalty = 0.0

        if "catalog" in document_lower or "press-catalog" in document_lower:
            noise_penalty += 0.12

        if text_lower.count(".....") > 3:
            noise_penalty += 0.10

        return {
            "section_boost": section_boost,
            "ngram_boost": ngram_boost,
            "direct_boost": direct_boost,
            "noise_penalty": noise_penalty,
            "query_coverage": coverage,
            "identifier_matches": float(identifier_matches),
        }

    @staticmethod
    def _deduplicate_candidates(
        candidates: List[Dict[str, Any]],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """
        Suppresses identical or near-duplicate chunks from duplicate source uploads
        (e.g., dual uploads of the SAP BRIM Press Book), ensuring distinct technical evidence
        chunks are retained within top_n.
        """
        selected: List[Dict[str, Any]] = []
        seen_fingerprints: Set[str] = set()

        for cand in candidates:
            text = (cand.get("chunk_text") or "").strip()
            # Normalize leading 200 characters to alphanumeric fingerprint
            fp = re.sub(r"\W+", "", text[:200].lower())
            if fp and fp in seen_fingerprints:
                continue
            if fp:
                seen_fingerprints.add(fp)
            selected.append(cand)
            if len(selected) >= top_n:
                break

        # Fallback to satisfy top_n if duplicate suppression left fewer items than top_n
        if len(selected) < top_n and len(selected) < len(candidates):
            selected_ids = {c.get("chunk_id") for c in selected}
            for cand in candidates:
                if cand.get("chunk_id") not in selected_ids:
                    selected.append(cand)
                    if len(selected) >= top_n:
                        break

        return selected

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_n: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Rank retrieved chunks using neural relevance plus bounded,
        interpretable evidence features.

        Preserves candidate metadata and returns:
            (ranked_chunks, evidence_sufficient)

        Important: the returned score is a ranking score, not a calibrated
        probability that the answer is factually supported.
        """
        if not candidates:
            logger.debug("Reranker received no candidates.")
            return [], False

        n = max(1, top_n or settings.FINAL_TOP_K)
        thresh = (
            threshold
            if threshold is not None
            else settings.SIMILARITY_THRESHOLD
        )

        query_tokens = set(self._tokens(query))
        significant_query_tokens = {
            token
            for token in query_tokens
            if token not in STOP_WORDS and len(token) > 2
        }

        scored_candidates: List[Dict[str, Any]] = []

        # RRF scores are small by design. Normalize them relative
        # to the current batch so heuristics can contribute without
        # saturating all candidates at 1.0.
        finite_base_scores = [
            float(
                c.get("rrf_score", c.get("score", 0.0)) or 0.0
            )
            for c in candidates
        ]
        finite_base_scores = [
            value for value in finite_base_scores
            if np.isfinite(value)
        ]

        if finite_base_scores:
            low = min(finite_base_scores)
            high = max(finite_base_scores)
            spread = high - low

        for candidate in candidates:
            item = dict(candidate)

            raw_base_score = candidate.get(
                "rrf_score",
                candidate.get("score", 0.0),
            )

            try:
                base_score = float(raw_base_score or 0.0)
            except (TypeError, ValueError):
                base_score = 0.0

            if not np.isfinite(base_score):
                base_score = 0.0

            if finite_base_scores:
                base_normalized = (
                    (base_score - low) / spread
                    if spread > 1e-12
                    else 0.5
                )
            else:
                base_normalized = 0.0

            features = self._heuristic_features(query, candidate)

            combined_score = (
                0.55 * base_normalized
                + 0.10 * features["query_coverage"]
                + features["section_boost"]
                + features["ngram_boost"]
                + features["direct_boost"]
                - features["noise_penalty"]
            )
            method = "hybrid_rrf"

            bounded_score = max(0.0, min(1.0, float(combined_score)))
            item["rerank_score"] = round(bounded_score, 6)
            item["score"] = item["rerank_score"]
            item["retrieval_method"] = method
            item["rrf_score"] = base_score
            item["query_coverage"] = round(
                features["query_coverage"], 4
            )
            item["identifier_matches"] = int(
                features["identifier_matches"]
            )

            scored_candidates.append(item)

        # Stable tie-breaking: use the original retrieval score after the
        # rerank score, then preserve original order as the final tie-break.
        scored_candidates.sort(
            key=lambda item: (
                item["rerank_score"],
                item["rrf_score"],
            ),
            reverse=True,
        )

        top_chunks = self._deduplicate_candidates(scored_candidates, n)

        if not top_chunks:
            return [], False

        top_score = top_chunks[0]["rerank_score"]

        logger.debug(
            "Reranking complete: selected %d/%d candidates "
            "(method=%s, top scores=%s)",
            len(top_chunks),
            len(candidates),
            top_chunks[0]["retrieval_method"],
            [item["rerank_score"] for item in top_chunks],
        )

        # Retain the existing threshold contract. Since scores are now
        # batch-relative ranking values rather than probabilities, validate
        # threshold compatibility against settings before relying on it.
        if top_score < thresh:
            return top_chunks, False

        combined_text = " ".join(
            (item.get("chunk_text") or "").lower()
            for item in top_chunks
        )
        combined_tokens = set(self._tokens(combined_text))

        for token in significant_query_tokens:
            if token in DOMAIN_PRODUCT_NAMES and token not in combined_tokens:
                return top_chunks, False

        return top_chunks, True


reranking_service = RerankingService()
