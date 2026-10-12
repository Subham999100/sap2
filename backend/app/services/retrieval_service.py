import re
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, Float, cast

from backend.app.config import settings
from backend.app.models.chunk import DocumentChunk
from backend.app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "what", "is", "the", "a", "an", "how", "to", "in", "of", "and", "for",
    "with", "does", "do", "explain", "describe", "can", "you", "tell", "me",
    "about", "design", "first", "per", "and", "or", "as", "at", "by", "from",
    "this", "that", "these", "those", "have", "has", "had", "which", "are", "be",
    "between", "when", "within", "under", "over", "into"
}

# Conversational instruction and question-framing words to exclude from technical lexical queries
CONVERSATIONAL_WORDS: Set[str] = {
    "explain", "describe", "investigate", "investigation", "investigating",
    "distinguish", "distinguishing", "difference", "differences", "compare", "comparison",
    "discrepancy", "discrepancies", "claim", "claims", "claimed",
    "shows", "show", "showing", "already", "possible", "confirmed",
    "causes", "cause", "evidence", "across", "would", "could", "should",
    "please", "help", "steps", "step", "approach", "identify", "identifying",
    "determine", "outline", "detail", "details", "check", "verify", "finding", "findings"
}

ALL_STOP_WORDS: Set[str] = STOP_WORDS | CONVERSATIONAL_WORDS

# Authoritative SAP BRIM Acronym and Terminology Taxonomy
SAP_TAXONOMY: Dict[str, List[str]] = {
    "som": ["subscription order management", "provider contract", "subscription order"],
    "cc": ["convergent charging", "charge plan", "core tool", "charging tree"],
    "ci": ["convergent invoicing", "billable item", "consumption item", "invoicing document"],
    "fi-ca": ["contract accounts receivable and payable", "contract account", "fica"],
    "fica": ["contract accounts receivable and payable", "fi-ca", "fpl9", "contract account"],
    "rar": ["revenue accounting and reporting", "performance obligation", "pob", "contract reversal"],
    "bit": ["billable item", "billable items", "convergent invoicing"],
    "cit": ["consumption item", "consumption items", "unrated item"],
    "odi": ["order distribution infrastructure", "order distribution"],
    "daas": ["device as a service"],
    "pob": ["performance obligation", "revenue accounting"],
    "fpl9": ["account display", "account balance", "line items", "contract account display"],
    "fpe1": ["post document", "contract account document post"],
    "fpe2": ["change document"],
    "fpe3": ["display document"],
    "fp04": ["clear document"],
    "fpy1": ["payment run"],
    "fpva": ["dunning proposal run", "dunning run", "fica dunning"],
    "fpvb": ["dunning activity run", "dunning run", "fica dunning"],
    "fpo1": ["open items list", "contract account open items"],
    "fp06": ["installment plan", "installment plan change"],
    "fp08": ["document reversal", "reverse document", "fica document reversal"],
    "fp40": ["interest calculation run"],
    "dfkkop": ["open items", "contract account line items", "clearing", "subledger line items"],
    "dfkkko": ["fi-ca document header", "document header", "posting header"],
    "dfkkinv_doc": ["invoicing document", "convergent invoicing document", "ci document"],
    "dfkkbip": ["billable item package", "bit package", "billable items"],
    "cdhdr": ["change document header", "audit trail", "migration change document"],
    "cdpos": ["change document items", "change positions", "audit items"],
    "fkkbix_bit_mon": ["billable item monitor", "billable item monitoring", "bit monitor", "convergent invoicing"],
    "fkkbixbit_mon": ["billable item monitor", "billable item monitoring", "bit monitor", "convergent invoicing"],
    "fkkbix_cit_mon": ["consumption item monitor", "consumption item monitoring", "cit monitor", "convergent invoicing"],
    "fkkbixcit_mon": ["consumption item monitor", "consumption item monitoring", "cit monitor", "convergent invoicing"],
    "spro": ["customizing", "img", "implementation guide", "sap reference img"],
    "sm30": ["table maintenance", "view maintenance"],
    "pfcg": ["role maintenance", "authorization"],
    "dunning": ["dunning proposal", "dunning procedure", "fpva", "fpvb", "dunning notice"],
    "allowance": ["allowance plan", "allowance logic", "convergent charging", "core tool"],
    "provider contract": ["subscription contract", "som", "contract account"],
    "clearing": ["clearing document", "clearing restriction", "clearing reason", "fp04", "partial clearing"],
    "payment lot": ["incoming payment", "payment lot processing", "clarification list", "bank statement"],
    "payment allocation": ["payment lot", "incoming payment", "clarification list", "bank statement", "on account payment"],
    "account balance": ["fpl9", "account display", "open items", "cleared items", "line item display"],
    "invoice status": ["open item", "clearing status", "clearing restriction", "subledger document"],
    "open receivable": ["open item", "fpo1", "fpl9", "due date", "subledger document"],
    "open items": ["open item", "open receivable", "fpo1", "fpl9", "subledger document"],
    "price plan": ["charging tree", "pricing logic", "rate component"],
    "price table": ["lookup table", "tier table", "matrix table"]
}


class RetrievalService:

    @staticmethod
    def preprocess_query(query: str) -> str:
        """Normalizes whitespace while strictly preserving SAP hyphens, slashes, and technical names."""
        cleaned = re.sub(r"[ \t]+", " ", query).strip()
        return cleaned

    @classmethod
    def contextualize_query_with_history(
        cls,
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """
        Resolves anaphoric references (e.g. "what about clearing?", "that document", "it")
        using recent conversation history while strictly preserving all original query tokens
        and avoiding the fabrication of unrequested facets.
        """
        if not chat_history or not query:
            return query

        clean_q = query.strip()
        q_lower = clean_q.lower()

        is_followup = bool(re.search(
            r"\b(?:what about|how about|why did that|why did it|can we reverse|can it be|"
            r"that issue|that document|that invoice|that account|the previous|it|this|reverse that)\b",
            q_lower
        ))
        has_anchor = any(
            comp in q_lower for comp in ("som", "cc", "ci", "fica", "fi-ca", "rar", "odi", "sap", "brim")
        )
        if not is_followup and (len(clean_q.split()) > 5 or has_anchor):
            return clean_q

        # Extract entities from the last assistant/user turn
        recent_turns = chat_history[-2:]
        context_text = " ".join(t.get("content", "") for t in recent_turns)

        extracted_entities = []
        # Business identifiers (INV-1042, BILL-3001, CA-2001, CUST-1001)
        biz_ids = re.findall(r"\b(?:inv|bill|evt|ca|cust)-\d+\b", context_text, re.IGNORECASE)
        for bid in biz_ids:
            bid_up = bid.upper()
            if bid_up not in extracted_entities:
                extracted_entities.append(bid_up)

        # Technical codes & objects
        tcodes = re.findall(r"\b(?:fpl9|fpe[1-3]|fp04|fpy1|fpva|fpvb|fpo1|fp08|fkkbix[a-z0-9_]*)\b", context_text, re.IGNORECASE)
        for tc in tcodes:
            tc_up = tc.upper()
            if tc_up not in extracted_entities:
                extracted_entities.append(tc_up)

        # Core BRIM components
        comps = [c for c in ("FI-CA", "SOM", "CC", "CI", "RAR", "ODI") if c.lower() in context_text.lower()]
        for comp in comps:
            if comp not in extracted_entities:
                extracted_entities.append(comp)

        if extracted_entities:
            # Context anchor: take top 2 distinctive entities
            anchor = " ".join(extracted_entities[:2])
            # Only prepend anchor if not already mentioned in query
            if not any(e.lower() in q_lower for e in extracted_entities[:2]):
                return f"{anchor} {clean_q}"

        return clean_q



    @classmethod
    def analyze_query(cls, query: str) -> Dict[str, Any]:
        """
        Query understanding that extracts:
        1. SAP acronyms, products, and technical objects.
        2. Focused expansion terms for lexical retrieval.
        3. Multi-part facets/sub-queries for compound questions, with targeted diagnostic sequences.
        """
        cleaned = cls.preprocess_query(query)
        lower = cleaned.lower()

        # 1. Identify SAP terms & expansions
        detected_terms: Set[str] = set()
        expansion_terms: List[str] = []

        for term, expansions in SAP_TAXONOMY.items():
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if re.search(pattern, lower):
                detected_terms.add(term)
                expansion_terms.extend(expansions)

        # 2. Extract multi-part sub-facets for multi-part questions
        facets: List[str] = []

        # Technical domain keywords for identifying valid SAP BRIM diagnostic investigation areas
        diag_keywords = [
            "invoice", "invoicing", "receivable", "receivables", "payment", "payments",
            "allocation", "clearing", "balance", "balances", "item", "items",
            "posting", "postings", "lot", "order", "contract", "billing", "charging",
            "subledger", "som", "cc", "ci", "fi-ca", "fica", "rar", "odi",
            "allowance", "pricing", "proration", "reconciliation", "reconcile",
            "tax", "rate", "rating", "re-rating", "rerating", "gl", "duplicate",
            "validity", "lifecycle", "counter", "counters", "recovery", "reversal",
            "reversals", "consumption", "financial"
        ]

        # Extract diagnostic listings introduced by 'across', 'between', 'investigate', 'trace ... across', etc.
        stage_matches = list(re.finditer(r"\b(?:across|between|investigate|check|areas?|stages?|trace(?:[^\w]+.*?)?across)\s+([^.?!]+)", lower))
        best_clean_stages: List[str] = []

        for sm in stage_matches:
            stage_text = sm.group(1).strip()

            # Separate domain stage enumerations from subsequent analytical goals / questions
            # e.g., "... and configuration validity, to distinguish confirmed root causes ... verify whether downstream ..."
            transition_pattern = (
                r"(?:,\s*|\s+)(?:to\s+(?:distinguish|determine|identify|verify|check|find|isolate|confirm|ascertain|outline)"
                r"|verify\s+whether|determine\s+whether|in\s+order\s+to|how\s+would\s+you|outline\s+a)\b"
            )
            parts = re.split(transition_pattern, stage_text, flags=re.IGNORECASE)

            candidate_stages: List[str] = []

            # Part 0 contains the comma-delimited enumeration of system stages/domains
            if parts:
                domain_stages = re.split(r",\s*(?:and\s+)?|\band\s+(?=[a-z])", parts[0])
                for s in domain_stages:
                    s_clean = re.sub(r"^(?:across|between|and|or|check|investigate)\s+", "", s.strip(), flags=re.IGNORECASE)
                    s_clean = re.sub(r"[,\-;:.]+$", "", s_clean).strip()
                    if len(s_clean) >= 3 and any(dc in s_clean for dc in diag_keywords):
                        candidate_stages.append(s_clean)

            # Subsequent parts contain analytical tasks/objectives
            for task_part in parts[1:]:
                task_items = re.split(r",\s*(?:and\s+)?|\band\s+(?=[a-z])", task_part)
                for t in task_items:
                    t_clean = re.sub(r"^(?:to|and|or|whether|how|would|you|a|an|the)\s+", "", t.strip(), flags=re.IGNORECASE)
                    t_clean = re.sub(r"[,\-;:.]+$", "", t_clean).strip()
                    # Filter out purely procedural meta-instructions
                    if any(meta in t_clean for meta in ["confirmed root cause", "secondary symptom", "possible cause", "hypothes"]):
                        continue
                    if len(t_clean) >= 4 and (any(dc in t_clean for dc in diag_keywords) or any(comp in t_clean for comp in ("som", "cc", "ci", "fica", "fi-ca"))):
                        # Trim secondary descriptive clauses (e.g. "... without creating duplicate billable items")
                        t_clean = re.split(r"\b(?:without|before|after|in\s+order)\b", t_clean, flags=re.IGNORECASE)[0].strip()
                        if 4 <= len(t_clean) <= 65 and t_clean not in candidate_stages:
                            candidate_stages.append(t_clean)

            if len(candidate_stages) > len(best_clean_stages):
                best_clean_stages = candidate_stages

        if len(best_clean_stages) >= 2:
            detected_comps = {t.lower() for t in detected_terms if t.lower() in {"fi-ca", "fica", "som", "cc", "ci", "rar", "odi"}}
            anchor_prefix = next(iter(detected_comps)).upper() if len(detected_comps) == 1 else ""
            for st in best_clean_stages:
                st_clean = st.strip()
                st_norm = st_clean.lower().replace("-", "")
                if st_norm in {"som", "cc", "ci", "fica", "rar", "odi"}:
                    facet_q = st_clean.upper()
                elif anchor_prefix and not any(comp in st_norm for comp in {"som", "cc", "ci", "fica", "rar", "odi"}):
                    facet_q = f"{anchor_prefix} {st_clean}".strip()
                else:
                    facet_q = st_clean
                if facet_q not in facets:
                    facets.append(facet_q)

        # Check for explicit multi-part conjunctions, questions, and clauses
        clauses = re.split(
            r"(?:\?\s*|\b\d+[\.\)]\s+|,\s*(?:and|what|how|which)\b|;\s*|\band what\b|\band how\b|\bwhat role does it play\b)",
            cleaned,
            flags=re.IGNORECASE
        )
        if len(clauses) > 1 and len(facets) < 2:
            for clause in clauses:
                cl = clause.strip()
                if len(cl) > 8 and cl not in facets:
                    facets.append(cl)

        # Check for multi-component interaction questions (e.g. between CC, CI, and FI-CA)
        if len(facets) < 2:
            detected_comps = [t for t in detected_terms if t in {"som", "cc", "ci", "fi-ca", "fica", "rar", "odi"}]
            if len(detected_comps) >= 2 and any(w in lower for w in ["flow", "integrate", "integration", "across", "between", "role", "roles", "difference", "differences", "interact", "interaction", "component", "components", "architecture"]):
                for comp in detected_comps:
                    comp_name = comp.upper()
                    if comp_name not in facets:
                        facets.append(comp_name)

        # Check for complex scenario requirement components (e.g. ₹499 base, 100 GB, weekday/weekend)
        if "design" in lower or ("₹" in query or "gb" in lower or "discount" in lower):
            scenario_parts = re.split(r"[,;]\s*", cleaned)
            for part in scenario_parts:
                p_clean = part.strip()
                if len(p_clean) > 8 and p_clean not in facets:
                    facets.append(p_clean)

        max_facets = getattr(settings, "MAX_RETRIEVAL_FACETS", 14)
        return {
            "cleaned_query": cleaned,
            "detected_terms": list(detected_terms),
            "expansion_terms": list(dict.fromkeys(expansion_terms)),
            "facets": facets[:max_facets] if facets else []
        }

    def _extract_lexical_tokens(self, query: str) -> List[str]:
        """Extract clean alphanumeric tokens safe for PostgreSQL to_tsquery, supporting compound identifiers."""
        raw_tokens = re.findall(r"\b[a-zA-Z0-9\-_/]+\b", query.lower())
        clean_tokens = []
        for t in raw_tokens:
            parts = re.findall(r"[a-zA-Z0-9]+", t)
            if len(parts) > 1:
                joined = "".join(parts)
                if joined not in ALL_STOP_WORDS and len(joined) > 1 and joined not in clean_tokens:
                    clean_tokens.append(joined)
                for p in parts:
                    if p not in ALL_STOP_WORDS and len(p) > 1 and p not in clean_tokens:
                        clean_tokens.append(p)
            else:
                sanitized = t.replace("-", "").replace("/", "").replace("_", "")
                if sanitized not in ALL_STOP_WORDS and len(sanitized) > 1 and sanitized not in clean_tokens:
                    clean_tokens.append(sanitized)
        return clean_tokens

    def _build_focused_tsquery(self, query: str, expansions: List[str]) -> Optional[str]:
        """
        Builds a deterministic, focused PostgreSQL tsquery combining
        core query terms with distinctive SAP taxonomy expansions.
        Avoids position-based truncation (tokens[:5]) while preventing overly restrictive AND
        or indiscriminate OR flooding.
        """
        tokens = self._extract_lexical_tokens(query)
        if not tokens:
            return None

        # Filter out 1-2 char fragments unless they are recognized taxonomy keys
        distinct_terms = [t for t in tokens if len(t) > 2 or t in SAP_TAXONOMY]
        if not distinct_terms:
            distinct_terms = tokens

        # Extract clean, distinctive expansion tokens from taxonomy
        exp_tokens = []
        for exp in expansions[:6]:
            for t in re.findall(r"\b[a-zA-Z0-9]+\b", exp.lower()):
                if t not in ALL_STOP_WORDS and len(t) > 2 and t not in distinct_terms and t not in exp_tokens:
                    exp_tokens.append(t)

        # For very concise queries (<= 2 terms), conjoin terms with AND and expansion OR
        if len(distinct_terms) <= 2:
            primary_expr = " & ".join(distinct_terms)
            if exp_tokens:
                exp_or = " | ".join(exp_tokens[:4])
                return f"({primary_expr}) | ({exp_or})"
            return primary_expr

        # For multi-concept queries (>= 3 terms), identify all recognized SAP taxonomy terms
        # or technical codes/identifiers to serve as anchors.
        # If multiple anchors exist (e.g. 'cc', 'ci', 'provider contract'), conjoining anchors with OR
        # ensures chunks discussing ANY of the target components can match when paired with other concepts,
        # preventing chunks for secondary components from being starved out by a single anchor requirement.
        anchors = [
            t for t in distinct_terms
            if t in SAP_TAXONOMY or re.search(r"\d|_", t) or t in {"som", "cc", "ci", "fica", "rar", "odi", "daas"}
        ]
        if not anchors:
            anchors = [distinct_terms[0]]

        anchor_expr = " | ".join(anchors[:3])
        remaining_terms = [t for t in distinct_terms if t not in anchors[:3]]
        all_concepts = remaining_terms + [e for e in exp_tokens if e not in remaining_terms and e not in anchors[:3]]

        if all_concepts:
            concept_or = " | ".join(all_concepts[:12])
            return f"({anchor_expr}) & ({concept_or})"

        return anchor_expr

    def hybrid_search(
        self,
        db: Session,
        query: str,
        top_k: Optional[int] = None,
        document_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid retrieval using PostgreSQL vector and full‑text search with RRF fusion.

        Retrieves candidate pool (default settings.RRF_CANDIDATE_K = 30) for downstream reranking.
        Incorporates query understanding, focused acronym expansion, and multi-part facet retrieval.
        """
        k = top_k or settings.RRF_CANDIDATE_K
        analysis = self.analyze_query(query)
        processed_query = analysis["cleaned_query"]

        logger.debug(f"Starting hybrid search for query: '{processed_query[:60]}...' (candidate_k={k})")

        if not processed_query:
            logger.debug("Empty query provided to hybrid_search; returning empty results.")
            return []

        # 1️⃣ Compute query embedding via embedding service
        query_vector = embedding_service.embed_text(processed_query)
        query_dim = len(query_vector)

        # 2️⃣ Vector search (high‑recall) – fetch candidates on matching embedding column
        vec_hits: List[DocumentChunk] = []
        if db.bind.dialect.name == "postgresql":
            if query_dim == 1536:
                vector_col = DocumentChunk.embedding_new
                col_name = "embedding_new (1536-dim)"
            elif query_dim == 384:
                vector_col = DocumentChunk.embedding
                col_name = "embedding (384-dim)"
            else:
                raise ValueError(
                    f"Query embedding dimension {query_dim} does not match any configured database column (expected 1536 or 384)"
                )
            logger.debug(f"Using vector column {col_name} for query dimension {query_dim}")

            vec_q = db.query(DocumentChunk).filter(vector_col.isnot(None))
            if document_filter:
                vec_q = vec_q.filter(DocumentChunk.document_name == document_filter)
            vec_q = (
                vec_q.order_by(vector_col.op("<=>")(query_vector))
                .limit(k * 2)
            )
            vec_hits = vec_q.all()

        logger.debug(f"Vector search retrieved {len(vec_hits)} candidate chunks")

        # 3️⃣ Lexical search using GIN full‑text index with focused expansion
        lex_hits: List[Tuple[DocumentChunk, float]] = []
        if db.bind.dialect.name == "postgresql":
            # 3a. Strict full-text search first
            lex_q = db.query(DocumentChunk, func.ts_rank_cd(
                func.to_tsvector('english', DocumentChunk.chunk_text),
                func.plainto_tsquery('english', processed_query)
            ).label('lex_score'))
            if document_filter:
                lex_q = lex_q.filter(DocumentChunk.document_name == document_filter)
            lex_q = (
                lex_q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.plainto_tsquery('english', processed_query)))
                .order_by(desc('lex_score'))
                .limit(k * 2)
            )
            lex_hits = [(c, s) for c, s in lex_q.all()]

            # 3b. Focused expansion search using OR / cover density
            focused_tsquery = self._build_focused_tsquery(processed_query, analysis["expansion_terms"])
            if focused_tsquery and len(lex_hits) < 15:
                try:
                    exp_q = db.query(DocumentChunk, func.ts_rank_cd(
                        func.to_tsvector('english', DocumentChunk.chunk_text),
                        func.to_tsquery('english', focused_tsquery)
                    ).label('lex_score'))
                    if document_filter:
                        exp_q = exp_q.filter(DocumentChunk.document_name == document_filter)
                    exp_q = (
                        exp_q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.to_tsquery('english', focused_tsquery)))
                        .order_by(desc('lex_score'))
                        .limit(k * 2)
                    )
                    exp_results = [(c, s) for c, s in exp_q.all()]
                    seen_lex_ids = {c.id for c, _ in lex_hits}
                    for c, s in exp_results:
                        if c.id not in seen_lex_ids:
                            lex_hits.append((c, s))
                            seen_lex_ids.add(c.id)
                except Exception as err:
                    logger.debug(f"Focused expansion tsquery error: {err}")

            # 3c. Fallback OR query if still insufficient candidates
            if len(lex_hits) < 10:
                tokens = self._extract_lexical_tokens(processed_query)
                if tokens:
                    distinct = [t for t in tokens if len(t) > 2 or t in SAP_TAXONOMY]
                    or_expr = " | ".join((distinct or tokens)[:10])
                    try:
                        fallback_q = db.query(DocumentChunk, func.ts_rank_cd(
                            func.to_tsvector('english', DocumentChunk.chunk_text),
                            func.to_tsquery('english', or_expr)
                        ).label('lex_score'))
                        if document_filter:
                            fallback_q = fallback_q.filter(DocumentChunk.document_name == document_filter)
                        fallback_q = (
                            fallback_q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.to_tsquery('english', or_expr)))
                            .order_by(desc('lex_score'))
                            .limit(k * 2)
                        )
                        fallback_hits = [(c, s) for c, s in fallback_q.all()]
                        seen_ids = {c.id for c, _ in lex_hits}
                        for c, s in fallback_hits:
                            if c.id not in seen_ids:
                                lex_hits.append((c, s))
                                seen_ids.add(c.id)
                        lex_hits = lex_hits[:k * 2]
                    except Exception as err:
                        logger.debug(f"Fallback tsquery search error: {err}")

        logger.debug(f"Lexical search retrieved {len(lex_hits)} candidate chunks")

        # 4️⃣ Reciprocal Rank Fusion (RRF)
        def rrf_fusion(vec_hits: List[DocumentChunk], lex_hits: List[tuple], denom: int = 60) -> List[Tuple[DocumentChunk, float]]:
            scores: Dict[str, float] = {}
            for rank, chunk in enumerate(vec_hits, start=1):
                scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (denom + rank)
            for rank, (chunk, _) in enumerate(lex_hits, start=1):
                scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (denom + rank)
            # Map IDs to chunks from both sources
            id_to_chunk: Dict[str, DocumentChunk] = {c.id: c for c in vec_hits}
            id_to_chunk.update({c.id: c for c, _ in lex_hits})
            # Select top-k by RRF score
            top_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
            fused = [(id_to_chunk[_id], score) for _id, score in top_items]
            return fused

        fused_with_scores = rrf_fusion(vec_hits, lex_hits, denom=60)
        logger.debug(f"RRF fusion produced {len(fused_with_scores)} candidates")

        # Normalize RRF scores relative to the highest RRF score in the current fused result set
        max_rrf_score = max((s for _, s in fused_with_scores), default=0.0)

        # 5️⃣ Build result dictionaries preserving chunk_index and all metadata
        results: List[Dict[str, Any]] = []
        for chunk, rrf_score in fused_with_scores:
            normalized_score = (rrf_score / max_rrf_score) if max_rrf_score > 0 else 0.0
            results.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "chunk_index": getattr(chunk, "chunk_index", 0),
                "page_number": chunk.page_number,
                "section": chunk.section or f"Page {chunk.page_number}",
                "chunk_text": chunk.chunk_text,
                "vector_score": None,
                "keyword_score": None,
                "rrf_score": rrf_score,
                "score": normalized_score,
                "rerank_score": normalized_score,
                "retrieval_method": "hybrid_rrf",
            })
        return results

    def vector_search(
        self,
        db: Session,
        query: str,
        top_k: Optional[int] = None,
        document_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Vector-only search using cosine similarity on the matching embedding column."""
        k = top_k or settings.TOP_K
        processed_query = self.preprocess_query(query)
        if not processed_query or db.bind.dialect.name != "postgresql":
            return []

        query_vector = embedding_service.embed_text(processed_query)
        query_dim = len(query_vector)
        if query_dim == 1536:
            vector_col = DocumentChunk.embedding_new
        elif query_dim == 384:
            vector_col = DocumentChunk.embedding
        else:
            raise ValueError(
                f"Query embedding dimension {query_dim} does not match any configured database column (expected 1536 or 384)"
            )

        dist_expr = cast(vector_col.op("<=>")(query_vector), Float)
        q = (
            db.query(DocumentChunk, dist_expr.label("distance"))
            .filter(vector_col.isnot(None))
        )
        if document_filter:
            q = q.filter(DocumentChunk.document_name == document_filter)
        q = q.order_by(dist_expr).limit(k)

        results = []
        for chunk, dist in q.all():
            sim = max(0.0, 1.0 - float(dist)) if dist is not None else 0.0
            results.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "chunk_index": getattr(chunk, "chunk_index", 0),
                "page_number": chunk.page_number,
                "section": chunk.section or f"Page {chunk.page_number}",
                "chunk_text": chunk.chunk_text,
                "score": sim,
                "rerank_score": sim,
                "vector_score": sim,
                "keyword_score": None,
                "rrf_score": None,
                "retrieval_method": "vector",
            })
        return results

    def lexical_search(
        self,
        db: Session,
        query: str,
        top_k: Optional[int] = None,
        document_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lexical-only full-text search using PostgreSQL GIN index and ts_rank_cd."""
        k = top_k or settings.TOP_K
        processed_query = self.preprocess_query(query)
        if not processed_query or db.bind.dialect.name != "postgresql":
            return []

        q = db.query(DocumentChunk, func.ts_rank_cd(
            func.to_tsvector('english', DocumentChunk.chunk_text),
            func.plainto_tsquery('english', processed_query)
        ).label('lex_score'))
        if document_filter:
            q = q.filter(DocumentChunk.document_name == document_filter)
        q = (
            q.filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.plainto_tsquery('english', processed_query)))
            .order_by(desc('lex_score'))
            .limit(k)
        )
        hits = q.all()
        results = []
        max_score = max((s for _, s in hits), default=0.0)
        for chunk, score in hits:
            norm_score = (score / max_score) if max_score > 0 else 0.0
            results.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_name": chunk.document_name,
                "chunk_index": getattr(chunk, "chunk_index", 0),
                "page_number": chunk.page_number,
                "section": chunk.section or f"Page {chunk.page_number}",
                "chunk_text": chunk.chunk_text,
                "score": norm_score,
                "vector_score": None,
                "keyword_score": float(score),
                "rrf_score": None,
                "retrieval_method": "lexical",
            })
        return results

    def fetch_adjacent_chunks(
        self,
        db: Session,
        chunks: List[Dict[str, Any]],
        max_adjacent: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Retrieves adjacent sibling chunks (predecessor or successor) from the same document
        when needed to preserve procedural steps, prerequisites, tables, and multi-chunk continuity.
        """
        if not chunks or db.bind.dialect.name != "postgresql":
            return chunks

        existing_ids = {c["chunk_id"] for c in chunks}
        adjacent_chunks: List[Dict[str, Any]] = []

        # Only evaluate top direct evidence chunks for adjacent extension
        for c in chunks[:max_adjacent]:
            doc_id = c.get("document_id")
            c_idx = c.get("chunk_index")
            text = c.get("chunk_text", "")
            score = float(c.get("rerank_score") or c.get("score") or 0.0)

            if doc_id is None or c_idx is None or score < 0.40:
                continue

            # Check if chunk contains procedural steps, prerequisites, or table breaks
            has_procedure = bool(re.search(
                r"(?:Step\s+\d+|Procedure\b|Prerequisites\b|1\.\s+[A-Z]|2\.\s+[A-Z]|Next\b|Following\b|Below\b)",
                text, re.IGNORECASE
            ))
            has_table = bool(re.search(r"(?:\||\bTable\b|\bField\b|\bParameter\b)", text, re.IGNORECASE))
            has_mid_sentence = bool(text.rstrip().endswith((",", ";", ":", "-", "and", "or", "with", "the")))

            # If procedural or incomplete, look up successor (chunk_index + 1)
            if has_procedure or has_table or has_mid_sentence:
                next_chunk = (
                    db.query(DocumentChunk)
                    .filter(DocumentChunk.document_id == doc_id, DocumentChunk.chunk_index == c_idx + 1)
                    .first()
                )
                if next_chunk and next_chunk.id not in existing_ids:
                    adjacent_chunks.append({
                        "chunk_id": next_chunk.id,
                        "document_id": next_chunk.document_id,
                        "document_name": next_chunk.document_name,
                        "chunk_index": next_chunk.chunk_index,
                        "page_number": next_chunk.page_number,
                        "section": next_chunk.section or f"Page {next_chunk.page_number}",
                        "chunk_text": next_chunk.chunk_text,
                        "rerank_score": round(score * 0.94, 4),
                        "score": round(score * 0.94, 4),
                        "is_adjacent": True,
                        "parent_chunk_id": c["chunk_id"],
                        "retrieval_method": "adjacent_extension",
                    })
                    existing_ids.add(next_chunk.id)

            # If prerequisites or start of procedure, look up predecessor (chunk_index - 1)
            if ("step 2" in text.lower() or "step 3" in text.lower() or "prerequisite" in text.lower()) and c_idx > 0:
                prev_chunk = (
                    db.query(DocumentChunk)
                    .filter(DocumentChunk.document_id == doc_id, DocumentChunk.chunk_index == c_idx - 1)
                    .first()
                )
                if prev_chunk and prev_chunk.id not in existing_ids:
                    adjacent_chunks.append({
                        "chunk_id": prev_chunk.id,
                        "document_id": prev_chunk.document_id,
                        "document_name": prev_chunk.document_name,
                        "chunk_index": prev_chunk.chunk_index,
                        "page_number": prev_chunk.page_number,
                        "section": prev_chunk.section or f"Page {prev_chunk.page_number}",
                        "chunk_text": prev_chunk.chunk_text,
                        "rerank_score": round(score * 0.92, 4),
                        "score": round(score * 0.92, 4),
                        "is_adjacent": True,
                        "parent_chunk_id": c["chunk_id"],
                        "retrieval_method": "adjacent_extension",
                    })
                    existing_ids.add(prev_chunk.id)

        if adjacent_chunks:
            logger.debug(f"Retrieved {len(adjacent_chunks)} adjacent sibling chunks to preserve continuity")
            combined = list(chunks) + adjacent_chunks
            return combined

        return chunks

    @staticmethod
    def is_table_of_contents_fragment(chunk_text: str, section: str = "") -> bool:
        """
        Detects whether a chunk is primarily a Table of Contents (TOC) fragment
        characterized by dot leaders (e.g. '.......... 12'), chapter listings, or TOC headers.
        TOC fragments provide no substantive procedural or technical evidence.
        """
        if not chunk_text:
            return False
        text = chunk_text.strip()
        sec = (section or "").lower()
        if any(header in sec for header in ("table of contents", "contents", "inhaltsverzeichnis")):
            return True

        # Detect dot leaders (e.g. ................ 15)
        dot_leaders = len(re.findall(r"\.{4,}\s*\d+", text))
        if dot_leaders >= 2:
            return True
        if len(re.findall(r"\.{3,}", text)) >= 4:
            return True

        # Detect multiple lines ending in page numbers with trailing dots or large spacing
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        toc_lines = sum(
            1 for line in lines
            if re.search(r"(?:\.{2,}|\s{4,})\s*\d+\s*$", line) or re.match(r"^\s*(?:\d+[\.\)]|[A-Z][\.\)]|\bChapter\b|\bSection\b).*\b\d+\s*$", line)
        )
        if len(lines) >= 3 and (toc_lines / len(lines)) >= 0.50:
            return True

        return False

    @classmethod
    def _evaluate_facet_relevance(
        cls,
        facet: str,
        candidate: Dict[str, Any],
        min_rerank_threshold: float = 0.35,
    ) -> Tuple[bool, float]:
        """
        Evaluates genuine semantic and technical relevance of a candidate chunk to a specific sub-facet.
        Returns (is_relevant, facet_relevance_score).

        Strictly prevents superficial matches: a chunk containing merely an isolated component acronym
        like 'CI', 'CC', or 'FI-CA' without substantive concept overlap is marked non-relevant.
        """
        chunk_text = (candidate.get("chunk_text") or "").lower()
        section_text = (candidate.get("section") or "").lower()
        full_content = f"{section_text} {chunk_text}"
        candidate_score = float(candidate.get("score") or candidate.get("rrf_score") or 0.0)

        # Baseline quality cutoff: low-relevance or noise chunks are rejected unless evaluated on concept coverage
        if candidate_score < 0.15:
            return False, 0.0

        # Reject obvious table of contents fragments from facet evidence selection
        if cls.is_table_of_contents_fragment(candidate.get("chunk_text") or "", candidate.get("section") or ""):
            return False, 0.0

        f_lower = facet.lower()
        # Normalize slashes separating components (e.g. CI/FI-CA -> CI FI-CA) while preserving S/4HANA
        f_normalized = re.sub(r"(?<!s)/|/(?!4hana)", " ", f_lower)
        raw_tokens = re.findall(r"\b[a-zA-Z0-9\-_]+\b", f_normalized)
        facet_tokens = [t for t in raw_tokens if t not in ALL_STOP_WORDS and len(t) > 1]

        COMPONENT_ACRONYMS = {"som", "cc", "ci", "fi-ca", "fica", "rar", "odi", "brim", "sap"}
        NORMALIZED_COMPONENTS = {"som", "cc", "ci", "fica", "rar", "odi", "brim", "sap"}

        anchor_components = [
            t for t in facet_tokens
            if t in COMPONENT_ACRONYMS or re.sub(r"[-_/]", "", t) in NORMALIZED_COMPONENTS
        ]
        substantive_tokens = [
            t for t in facet_tokens
            if t not in COMPONENT_ACRONYMS and re.sub(r"[-_/]", "", t) not in NORMALIZED_COMPONENTS
        ]

        # Extract domain expansions from taxonomy matching the facet using whole-word boundary matching
        # to avoid false substring matches (e.g. 'cc' matching inside 'account balance', 'som' inside 'some')
        domain_expansions: Set[str] = set()
        for tax_key, tax_vals in SAP_TAXONOMY.items():
            tax_pattern = rf"(?<![a-z0-9]){re.escape(tax_key)}(?![a-z0-9])"
            if re.search(tax_pattern, f_lower):
                domain_expansions.update(tax_vals)
            for tv in tax_vals:
                tv_pattern = rf"(?<![a-z0-9]){re.escape(tv)}(?![a-z0-9])"
                if re.search(tv_pattern, f_lower):
                    domain_expansions.add(tax_key)
                    domain_expansions.update(tax_vals)

        def contains_term(target_text: str, term: str) -> bool:
            term_clean = term.lower()
            pattern = rf"(?<![a-z0-9]){re.escape(term_clean)}(?![a-z0-9])"
            if re.search(pattern, target_text):
                return True
            norm_term = re.sub(r"[-_/]", "", term_clean)
            if len(norm_term) > 2:
                norm_pat = rf"(?<![a-z0-9]){re.escape(norm_term)}(?![a-z0-9])"
                if re.search(norm_pat, target_text):
                    return True
            # Morphological stem matching for words > 4 characters to catch singular/plural and verb forms
            if len(term_clean) > 4:
                stem = re.sub(r"(?:ing|tion|tions|ed|es|s)$", "", term_clean)
                if len(stem) >= 4:
                    stem_pat = rf"(?<![a-z0-9]){re.escape(stem)}(?:ing|ed|es|s|tion|tions|e)?(?![a-z0-9])"
                    if re.search(stem_pat, target_text):
                        return True
            return False

        matched_substantive = [t for t in substantive_tokens if contains_term(full_content, t)]
        matched_expansions = [exp for exp in domain_expansions if contains_term(full_content, exp)]

        # Check component anchor match (e.g. if facet is specifically about FI-CA or SOM)
        anchor_match = (
            any(contains_term(full_content, ac) for ac in anchor_components)
            if anchor_components
            else True
        )

        # CRITICAL RELEVANCE ENFORCEMENT:
        # If substantive tokens exist, the chunk MUST match substantive tokens or domain expansions.
        # Superficial matches (e.g. solitary 'CI' or 'FI-CA' in boilerplate text) are strictly rejected.
        if substantive_tokens:
            if not matched_substantive and not matched_expansions:
                return False, 0.0
            substantive_ratio = len(matched_substantive) / max(1, len(substantive_tokens))
            expansion_signal = min(1.0, len(matched_expansions) * 0.25)
            concept_coverage = max(substantive_ratio, expansion_signal)
        else:
            # Facet is purely component-named (e.g. 'CC' or 'Convergent Charging')
            if not matched_expansions and not any(contains_term(full_content, ac) for ac in anchor_components):
                return False, 0.0
            concept_coverage = min(1.0, len(matched_expansions) * 0.35 + (0.5 if anchor_match else 0.0))

        section_match = (
            any(contains_term(section_text, t) for t in substantive_tokens)
            or any(contains_term(section_text, exp) for exp in domain_expansions)
        )

        facet_relevance = (
            0.45 * candidate_score
            + 0.30 * concept_coverage
            + 0.15 * (1.0 if anchor_match else 0.0)
            + 0.10 * (1.0 if section_match else 0.0)
        )

        if (facet_relevance >= 0.35 and concept_coverage >= 0.20) or concept_coverage >= 0.40:
            return True, round(facet_relevance, 4)
        return False, 0.0

    @classmethod
    def _stratified_facet_selection(
        cls,
        facets: List[str],
        candidates: List[Dict[str, Any]],
        target_k: int = 8,
        threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Stratified sub-facet evidence selection for complex multi-component queries:
        1. Evaluates genuine semantic and technical relevance of candidate chunks to each facet.
        2. Reserves the best relevant reranked chunk for each detected facet that has valid evidence.
           If a facet has no relevant evidence, records it in the evidence manifest as an unsupported gap.
        3. Fills remaining budget up to target_k with globally highest-ranked complementary chunks.
        4. Deduplicates strictly by stable chunk_id and content fingerprint to eliminate near-duplicate passages.
        5. Preserves exact technical identifiers and metadata for citation compatibility.
        6. Compiles a structured evidence manifest tracking supported vs. unsupported investigation areas.
        """
        if not candidates:
            return []

        def content_fp(text: str) -> str:
            return re.sub(r"\W+", "", (text or "")[:180].lower())

        selected: List[Dict[str, Any]] = []
        selected_ids: Set[str] = set()
        selected_fps: Set[str] = set()

        supported_facets: Dict[str, Dict[str, Any]] = {}
        unsupported_facets: List[str] = []

        # Step 1: For each detected facet, find and reserve the best genuinely relevant chunk
        max_per_facet = 2 if len(facets) == 2 and target_k >= 6 else 1

        for facet in facets:
            valid_for_facet: List[Tuple[Dict[str, Any], float]] = []
            for c in candidates:
                is_valid, f_score = cls._evaluate_facet_relevance(facet, c)
                if is_valid:
                    valid_for_facet.append((c, f_score))

            if not valid_for_facet:
                # No relevant evidence found for this facet: record missing facet for manifest
                unsupported_facets.append(facet)
                continue

            valid_for_facet.sort(
                key=lambda item: (
                    item[1],
                    float(item[0].get("score") or item[0].get("rrf_score") or 0.0)
                ),
                reverse=True
            )

            facet_taken = 0
            taken_cids: List[str] = []
            for cand, f_score in valid_for_facet:
                cid = cand.get("chunk_id")
                fp = content_fp(cand.get("chunk_text") or "")
                if cid and cid in selected_ids:
                    taken_cids.append(cid)
                    continue
                if fp and len(fp) >= 40 and fp in selected_fps:
                    continue

                selected.append(cand)
                if cid:
                    selected_ids.add(cid)
                    taken_cids.append(cid)
                if fp and len(fp) >= 40:
                    selected_fps.add(fp)

                facet_taken += 1
                if facet_taken >= max_per_facet or len(selected) >= target_k:
                    break

            if taken_cids:
                best_cand, best_score = valid_for_facet[0]
                supported_facets[facet] = {
                    "chunk_id": taken_cids[0],
                    "document_name": best_cand.get("document_name"),
                    "section": best_cand.get("section"),
                    "score": round(best_score, 4),
                }
            else:
                unsupported_facets.append(facet)

        # Step 2: Fill remaining slots up to target_k with globally highest-ranked complementary chunks
        if len(selected) < target_k:
            for cand in candidates:
                cid = cand.get("chunk_id")
                # Deprioritize/skip TOC fragments in complementary slot filling when substantive chunks exist
                if cls.is_table_of_contents_fragment(cand.get("chunk_text") or "", cand.get("section") or ""):
                    continue
                fp = content_fp(cand.get("chunk_text") or "")
                if cid and cid in selected_ids:
                    continue
                if fp and len(fp) >= 40 and fp in selected_fps:
                    continue

                selected.append(cand)
                if cid:
                    selected_ids.add(cid)
                if fp and len(fp) >= 40:
                    selected_fps.add(fp)

                if len(selected) >= target_k:
                    break

        # Step 3: Re-sort selected chunks by global RRF score descending for citation & grounding alignment
        selected.sort(
            key=lambda c: (
                float(c.get("score", 0.0) or 0.0),
                float(c.get("rrf_score", 0.0) or 0.0)
            ),
            reverse=True
        )

        # Step 4: Attach evidence manifest to all selected chunks
        manifest = {
            "total_facets": len(facets),
            "supported_facets": supported_facets,
            "unsupported_facets": unsupported_facets,
            "coverage_ratio": round(len(supported_facets) / max(1, len(facets)), 4),
        }
        for chunk in selected:
            chunk["evidence_manifest"] = manifest

        return selected

    def retrieve_and_rerank(
        self,
        db: Session,
        query: str,
        candidate_k: Optional[int] = None,
        final_k: Optional[int] = None,
        threshold: Optional[float] = None,
        document_filter: Optional[str] = None,
        enable_rerank: bool = False,
        enable_adjacent: bool = False,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Streamlined hybrid RAG retrieval pipeline without neural reranking:
        Stage 1: Vector Search + Lexical Search with SAP Taxonomical Expansion -> RRF Fusion (Candidate Pool)
        Stage 2: Sub-facet candidate enrichment (Vector + Lexical)
        Stage 3: Stratified facet-aware candidate selection -> Final dynamic Top-K Chunks + Evidence Manifest
        Stage 4: Adjacent-chunk retrieval preserving procedural steps, tables and dependencies.
        """
        r_k = candidate_k or settings.RRF_CANDIDATE_K
        f_k = final_k or settings.FINAL_TOP_K

        # Contextualize query with conversation history for anaphoric follow-up inquiries
        search_query = self.contextualize_query_with_history(query, chat_history) if chat_history else query

        # Stage 1: Hybrid candidate retrieval via RRF
        candidates = self.hybrid_search(
            db=db,
            query=search_query,
            top_k=r_k,
            document_filter=document_filter
        )

        # Multi-part facet candidate enrichment: Ensure candidates for distinct sub-questions reach selection
        analysis = self.analyze_query(search_query)
        facets = analysis.get("facets", [])
        is_multi_facet = bool(facets and len(facets) > 1)

        if is_multi_facet:
            seen_ids = {c["chunk_id"] for c in candidates}
            facet_slots = max(3, min(5, (r_k // (len(facets) + 1))))
            facet_candidates: List[Dict[str, Any]] = []
            for facet in facets:
                f_hits = self.vector_search(db=db, query=facet, top_k=facet_slots, document_filter=document_filter)
                for fc in f_hits:
                    if fc["chunk_id"] not in seen_ids:
                        seen_ids.add(fc["chunk_id"])
                        facet_candidates.append(fc)

                # If facet mentions a technical identifier, table, or T-code, also check lexical candidates
                f_tokens = re.findall(r"\b(?:fpl9|fpe[1-3]|fp04|fpy1|fpva|fpvb|fpo1|fp08|sm30|spro|fkkbix[a-z0-9_]*|dfkk[a-z0-9_]*|cdhdr|cdpos)\b", facet.lower())
                if f_tokens and db.bind.dialect.name == "postgresql":
                    try:
                        lex_expr = " | ".join(f_tokens)
                        lex_facet_q = (
                            db.query(
                                DocumentChunk,
                                func.ts_rank_cd(
                                    func.to_tsvector('english', DocumentChunk.chunk_text),
                                    func.to_tsquery('english', lex_expr)
                                ).label('lex_score')
                            )
                            .filter(func.to_tsvector('english', DocumentChunk.chunk_text).op('@@')(func.to_tsquery('english', lex_expr)))
                        )
                        if document_filter:
                            lex_facet_q = lex_facet_q.filter(DocumentChunk.document_name == document_filter)
                        lex_facet_q = lex_facet_q.order_by(desc('lex_score')).limit(facet_slots)
                        lex_facet_results = lex_facet_q.all()
                        max_lex_score = max((float(s) for _, s in lex_facet_results), default=0.0)

                        for fc, s in lex_facet_results:
                            if fc.id not in seen_ids:
                                seen_ids.add(fc.id)
                                raw_s = float(s) if s is not None else 0.0
                                norm_score = round((raw_s / max_lex_score * 0.45) if max_lex_score > 0 else 0.20, 4)
                                facet_candidates.append({
                                    "chunk_id": fc.id,
                                    "document_id": fc.document_id,
                                    "document_name": fc.document_name,
                                    "chunk_index": getattr(fc, "chunk_index", 0),
                                    "page_number": fc.page_number,
                                    "section": fc.section or f"Page {fc.page_number}",
                                    "chunk_text": fc.chunk_text,
                                    "vector_score": None,
                                    "keyword_score": raw_s,
                                    "rrf_score": round(norm_score * 0.1, 4),
                                    "score": norm_score,
                                    "rerank_score": norm_score,
                                    "retrieval_method": "facet_lexical",
                                })
                    except Exception as lex_f_err:
                        logger.debug(f"Sub-facet lexical search error: {lex_f_err}")

            if facet_candidates:
                candidates = candidates + facet_candidates

        if not candidates:
            return [], False

        # Dynamic Top-K calculation:
        # Simple queries (len(facets) <= 1): preserve existing Top-K behavior (f_k).
        # Complex multi-facet queries (len(facets) > 1): dynamically select up to 12 chunks to preserve cross-domain coverage.
        if is_multi_facet:
            if final_k is not None and final_k <= 2:
                effective_k = final_k
            else:
                effective_k = min(12, max(f_k, len(facets) + 2))
        else:
            effective_k = f_k

        # Stage 2: Facet-aware candidate selection
        thresh = threshold if threshold is not None else settings.SIMILARITY_THRESHOLD
        has_sufficient = bool(candidates and (candidates[0].get("score", 0.0) >= thresh))

        if is_multi_facet and candidates:
            top_chunks = self._stratified_facet_selection(
                facets=facets,
                candidates=candidates,
                target_k=effective_k,
                threshold=threshold
            )
        else:
            top_chunks = candidates[:effective_k]
            single_facet = facets[0] if facets else analysis.get("cleaned_query", query)
            if top_chunks and single_facet:
                best_chunk = None
                best_f_score = 0.0
                for c in top_chunks:
                    is_valid, f_score = self._evaluate_facet_relevance(single_facet, c)
                    if is_valid and f_score > best_f_score:
                        best_f_score = f_score
                        best_chunk = c

                if best_chunk:
                    manifest = {
                        "total_facets": 1,
                        "supported_facets": {
                            single_facet: {
                                "chunk_id": best_chunk.get("chunk_id"),
                                "document_name": best_chunk.get("document_name"),
                                "section": best_chunk.get("section"),
                                "score": round(best_f_score, 4),
                            }
                        },
                        "unsupported_facets": [],
                        "coverage_ratio": 1.0,
                    }
                else:
                    manifest = {
                        "total_facets": 1,
                        "supported_facets": {},
                        "unsupported_facets": [single_facet],
                        "coverage_ratio": 0.0,
                    }
                for c in top_chunks:
                    c["evidence_manifest"] = manifest

        # Stage 3: Adjacent-chunk retrieval when needed to preserve procedural steps and tables
        if enable_adjacent and top_chunks and has_sufficient:
            enriched_chunks = self.fetch_adjacent_chunks(db=db, chunks=top_chunks, max_adjacent=2)
            if top_chunks and "evidence_manifest" in top_chunks[0]:
                for ec in enriched_chunks:
                    ec["evidence_manifest"] = top_chunks[0]["evidence_manifest"]
            return enriched_chunks, has_sufficient

        return top_chunks, has_sufficient


retrieval_service = RetrievalService()
