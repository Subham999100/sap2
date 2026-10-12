import re
from typing import List, Dict, Any, Tuple, Optional


class GroundingService:

    @classmethod
    def _contains_term(cls, text: str, term: str) -> bool:
        """Match a term without accidental substring matches, supporting normalized variants."""
        term_clean = term.lower()
        pattern = rf"(?<![a-z0-9]){re.escape(term_clean)}(?![a-z0-9])"
        if re.search(pattern, text.lower()):
            return True
        norm_term = re.sub(r"[-_/]", "", term_clean)
        if len(norm_term) > 2:
            norm_pattern = rf"(?<![a-z0-9]){re.escape(norm_term)}(?![a-z0-9])"
            return bool(re.search(norm_pattern, text.lower()))
        return False

    @classmethod
    def _detect_polarity_conflict(cls, stmt: str, evidence_text: str) -> bool:
        """
        Detects obvious polarity or negation conflicts where a statement contradicts
        the retrieved evidence (e.g. asserting capability when evidence says cannot/not supported,
        or stating something is prohibited/unsupported when evidence affirms it).
        """
        s_lower = stmt.lower()
        e_lower = evidence_text.lower()

        negation_markers = [
            r"\b(?:is|are|was|were)\s+not\s+(?:supported|possible|permitted|allowed|available|required)\b",
            r"\bcannot\s+(?:be|process|post|reconcile|clear|execute|bill|charge|modify)\b",
            r"\bdoes\s+not\s+(?:support|allow|require|post|contain|update|integrate)\b",
            r"\bdo\s+not\s+(?:support|allow|require|post|contain|update|integrate)\b",
            r"\bnever\s+(?:processes|posts|reconciles|clears|updates)\b",
            r"\bnot\s+supported\b",
            r"\bprohibited\b",
        ]
        affirmative_markers = [
            r"\b(?:is|are|was|were)\s+(?:supported|possible|permitted|allowed|available|required)\b",
            r"\bcan\s+(?:be|process|post|reconcile|clear|execute|bill|charge|modify)\b",
            r"\bdoes\s+(?:support|allow|require|post|contain|update|integrate)\b",
            r"\bdo\s+(?:support|allow|require|post|contain|update|integrate)\b",
            r"\bsupports\b",
            r"\bfully\s+supported\b",
        ]

        stmt_content_terms = set(re.findall(r"\b[a-z0-9\-_]{4,}\b", s_lower)) - {
            "what", "that", "this", "from", "with", "have", "more", "also", "into",
            "than", "been", "each", "were", "when", "some", "such", "only", "other",
            "which", "there", "their", "these", "those", "would", "could", "should",
            "about", "cannot", "does", "support", "supported", "possible", "allowed"
        }

        stmt_has_neg = any(re.search(pat, s_lower) for pat in negation_markers)
        if stmt_has_neg and stmt_content_terms:
            for pat in affirmative_markers:
                for match in re.finditer(pat, e_lower):
                    span_start = max(0, match.start() - 100)
                    span_end = min(len(e_lower), match.end() + 100)
                    context_window = e_lower[span_start:span_end]
                    shared = sum(1 for t in stmt_content_terms if t in context_window)
                    if shared >= 2 and not any(re.search(n_pat, context_window) for n_pat in negation_markers):
                        return True

        stmt_has_aff = any(re.search(pat, s_lower) for pat in affirmative_markers)
        if stmt_has_aff and stmt_content_terms:
            for pat in negation_markers:
                for match in re.finditer(pat, e_lower):
                    span_start = max(0, match.start() - 100)
                    span_end = min(len(e_lower), match.end() + 100)
                    context_window = e_lower[span_start:span_end]
                    shared = sum(1 for t in stmt_content_terms if t in context_window)
                    if shared >= 2:
                        return True

        # Financial subledger state opposition:
        # e.g. claiming an item is cleared when evidence states it is an open item/uncleared, or vice versa
        cleared_patterns = [
            r"\b(?:is|was|has been)\s+cleared\b",
            r"\bcleared\s+(?:item|document|in full|invoice|receivable)\b",
            r"\bclearing\s+completed\b",
        ]
        open_patterns = [
            r"\bopen\s+(?:item|receivable|invoice|amount|debit)\b",
            r"\buncleared\b",
            r"\bnot\s+cleared\b",
            r"\bunallocated\b",
            r"\bclarification\s+(?:worklist|list)\b",
        ]

        stmt_claims_cleared = any(re.search(pat, s_lower) for pat in cleared_patterns)
        stmt_claims_open = any(re.search(pat, s_lower) for pat in open_patterns)

        if stmt_claims_cleared and any(re.search(pat, e_lower) for pat in open_patterns):
            # If evidence asserts items remain open/uncleared, but statement asserts cleared
            return True
        if stmt_claims_open and any(re.search(pat, e_lower) for pat in cleared_patterns):
            if not any(re.search(pat, e_lower) for pat in open_patterns):
                return True

        return False

    @staticmethod
    def is_refusal(text: str) -> bool:
        """Determines if the text constitutes an explicit knowledge base or fallback refusal."""
        if not text:
            return False
        t = text.lower()
        return (
            "not available in the provided knowledge base" in t
            or "not available in the knowledge base" in t
            or ("does not contain" in t and "knowledge base" in t)
            or "not available in the provided knowledge base or fallback web evidence" in t
            or "i can only help with sap and sap-related topics" in t
        )

    @classmethod
    def _split_into_statements(cls, text: str) -> List[str]:
        """
        Splits text into atomic statements, diagnostic assertions, and numbered/bulleted items.
        Safely protects common abbreviations (e.g., i.e., vs., fig., no., ref., p., pp., dr., mr., ms.)
        and prevents detaching inline [SOURCE X] citations from their preceding sentences
        using standard Python re without unsupported variable-width lookbehinds.
        """
        if not text:
            return []

        # Step 0: Ensure sentences ending before an inline citation detach cleanly from subsequent sentences
        # e.g., "...in FI-CA. [SOURCE 1] Next step..." -> "...in FI-CA. [SOURCE 1]\nNext step..."
        # This keeps [SOURCE 1] attached to "...in FI-CA." while separating it from "Next step...".
        prepared = re.sub(
            r"([.!?]\s*\[SOURCE\s+\d+\])\s+(?=[A-Z0-9])",
            lambda m: m.group(1) + "\n",
            text,
            flags=re.IGNORECASE
        )

        # Step 1: Temporarily protect abbreviation periods from sentence splitting
        # Use a callable replacement to avoid template escape parsing (prevents re.error: bad escape)
        masked = re.sub(
            r"\b(e\.g|i\.e|vs|fig|no|ref|p|pp|dr|mr|ms)\.",
            lambda m: m.group(0).replace(".", "\x00"),
            prepared,
            flags=re.IGNORECASE
        )

        # Step 2: Split using fixed-width lookbehind and lookahead
        # (?<=[.!?]) is strictly fixed-width (1 char).
        # (?!\s*\[SOURCE\s+\d+\]) is a negative lookahead (variable-width is supported in lookaheads).
        raw_parts = re.split(
            r"(?<=[.!?])(?!\s*\[SOURCE\s+\d+\])\s+|\n+|(?:^|\n)\s*[-*•]\s+|(?:^|\n)\s*\d+[\.\)]\s+",
            masked,
            flags=re.IGNORECASE
        )

        # Step 3: Unmask protected abbreviations and strip whitespace
        return [
            p.replace("\x00", ".").strip()
            for p in raw_parts
            if p and p.replace("\x00", ".").strip()
        ]

    @classmethod
    def align_citations(
        cls,
        answer: str,
        chunks: List[Dict[str, Any]],
        min_top_k: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Aligns retrieved evidence chunks with claims and references in the generated answer.
        Prioritizes explicit inline [SOURCE X] citations generated by the LLM only when
        the cited passage genuinely relates to the citing statement.
        For other chunks, verifies substantive technical and conceptual support without polarity conflict.
        """
        if not chunks or not answer:
            return []

        # If the answer is an explicit refusal/unavailable statement, no citations are aligned
        if cls.is_refusal(answer):
            return []

        answer_lower = answer.lower()
        aligned = []
        aligned_indices = set()

        # Map inline [SOURCE X] citations to their citing sentence/clause
        # Avoid splitting on common abbreviations (e.g., i.e.) and avoid detaching inline [SOURCE X] citations
        raw_sentences = cls._split_into_statements(answer)
        sentence_for_source: Dict[int, List[str]] = {}
        for s in raw_sentences:
            s_nums = re.findall(r"\[SOURCE\s+(\d+)\]", s, re.IGNORECASE)
            for sn in s_nums:
                sentence_for_source.setdefault(int(sn) - 1, []).append(s)

        # Check 1: Explicit inline [SOURCE X] citations in the answer
        explicit_source_nums = re.findall(r"\[SOURCE\s+(\d+)\]", answer, re.IGNORECASE)
        for s_num_str in explicit_source_nums:
            s_idx = int(s_num_str) - 1
            if 0 <= s_idx < len(chunks) and s_idx not in aligned_indices:
                chunk = chunks[s_idx]
                chunk_text = (chunk.get("chunk_text") or "").lower()

                # Evaluate ONLY the specific sentences citing this source, not the entire answer
                citing_stmts = sentence_for_source.get(s_idx, [])
                if not citing_stmts:
                    citing_stmts = [s for s in raw_sentences if re.search(rf"\[SOURCE\s+{s_idx + 1}\]", s, re.IGNORECASE)]
                if not citing_stmts:
                    continue

                combined_citing_text = " ".join(citing_stmts).lower()

                # Check for asserted technical entities (T-codes, tables, technical objects)
                asserted_tech = re.findall(
                    r"\b(?:[a-z]{1,4}[0-9]{1,4}[a-z0-9_]*|fkk[a-z0-9_]+|dfkk[a-z0-9_]+|erpk[a-z0-9_]+|cdpos|cdhdr)\b",
                    combined_citing_text
                )
                tech_entities = [
                    t for t in asserted_tech
                    if (re.search(r"\d|_", t) or t.startswith(("dfkk", "fkk", "erpk", "cdpos", "cdhdr"))) and len(t) >= 3
                ]
                if tech_entities:
                    # If citing statement asserts technical entities, the cited source MUST contain them
                    if not all(cls._contains_term(chunk_text, tc) for tc in tech_entities):
                        continue

                citing_words = [
                    w for w in re.findall(r"\b[a-z0-9\-_/]{3,}\b", combined_citing_text)
                    if w not in {
                        "this", "that", "with", "from", "have", "more", "also", "into", "than",
                        "been", "each", "were", "when", "some", "such", "only", "other", "about",
                        "which", "there", "their", "these", "those", "would", "could", "should",
                        "the", "source", "using", "used", "system", "systems", "step", "steps",
                        "check", "verify", "process", "document", "documents", "overview", "standard",
                        "general", "following", "before", "after", "through", "across", "sap", "brim"
                    }
                ]

                has_polarity_conflict = any(cls._detect_polarity_conflict(st, chunk_text) for st in citing_stmts)
                if has_polarity_conflict:
                    continue

                term_hits = sum(1 for w in citing_words if cls._contains_term(chunk_text, w))
                term_ratio = term_hits / max(1, len(citing_words))

                # Only align if the cited chunk has substantive term overlap with citing statement and no polarity conflict
                if (term_ratio >= 0.25 or (term_hits >= 2 and term_ratio >= 0.20)):
                    rerank_score = float(chunk.get("rerank_score", chunk.get("score", 0.0)))
                    aligned.append({
                        "document": chunk.get("document_name", "Unknown SAP Document"),
                        "page": chunk.get("page_number", 1),
                        "section": chunk.get("section"),
                        "score": rerank_score,
                        "snippet": (chunk.get("chunk_text") or "")[:280] + ("..." if len(chunk.get("chunk_text") or "") > 280 else "")
                    })
                    aligned_indices.add(s_idx)

        # Check 2: Content alignment for other chunks ONLY if no explicit inline citations were found in the answer.
        # If the LLM explicitly cited sources, do not artificially attach unrelated candidate chunks.
        if not explicit_source_nums:
            for idx, chunk in enumerate(chunks):
                if idx in aligned_indices:
                    continue

                doc_name = (chunk.get("document_name") or "").lower()
                section = (chunk.get("section") or "").lower()
                chunk_text = (chunk.get("chunk_text") or "").lower()

                doc_mentioned = bool(doc_name and doc_name in answer_lower)
                section_mentioned = bool(section and len(section) > 4 and section in answer_lower)

                # Extract substantive content tokens
                chunk_tokens = set(re.findall(r"\b[a-z0-9\-_/]{3,}\b", chunk_text))
                chunk_tokens.difference_update({
                    "this", "that", "with", "from", "have", "more", "also", "into", "than",
                    "been", "each", "were", "when", "some", "such", "only", "other", "about",
                    "which", "there", "their", "these", "those", "would", "could", "should", "the"
                })
                overlap_count = sum(1 for token in chunk_tokens if token in answer_lower)
                overlap_ratio = overlap_count / max(len(chunk_tokens), 1)

                # Technical identifier & transaction code alignment
                tech_terms = {
                    "odi", "som", "rar", "bit", "cit", "fpl9", "fpe1", "fpe2", "fpe3",
                    "fp04", "fpy1", "fpva", "fpvb", "fpo1", "fp06", "fp08", "fp40",
                    "sm30", "spro", "pfcg", "pob", "daas", "fkkbix", "fkkbixbit_mon",
                    "fkkbix_bit_mon", "fkkbixcit_mon", "fkkbix_cit_mon", "dfkkop", "dfkkbip"
                }
                has_tech_match = any(
                    cls._contains_term(chunk_text, t) and cls._contains_term(answer_lower, t)
                    for t in tech_terms
                )

                rerank_score = float(chunk.get("rerank_score", chunk.get("score", 0.0)))
                # Require substantive overlap without polarity conflict
                if not cls._detect_polarity_conflict(answer, chunk_text):
                    if (overlap_ratio >= 0.25 and overlap_count >= 3) or (has_tech_match and overlap_count >= 3):
                        aligned.append({
                            "document": chunk.get("document_name", "Unknown SAP Document"),
                            "page": chunk.get("page_number", 1),
                            "section": chunk.get("section"),
                            "score": rerank_score,
                            "snippet": (chunk.get("chunk_text") or "")[:280] + ("..." if len(chunk.get("chunk_text") or "") > 280 else "")
                        })
                        aligned_indices.add(idx)

        return aligned

    @classmethod
    def align_web_sources(
        cls,
        answer: str,
        web_sources: List[Dict[str, Any]],
        min_top_k: int = 1,
        internal_chunks_count: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Aligns retrieved web fallback sources with claims and references in the generated answer.
        Supports both globally continuous source indices (SOURCE N+idx+1) and localized indices.
        Only attaches web sources that were actually referenced or whose substantive terms
        overlap with the final answer.
        """
        if not web_sources or not answer:
            return []

        # If the answer is an explicit refusal/unavailable statement, no web sources are attached
        if cls.is_refusal(answer):
            return []

        answer_lower = answer.lower()
        aligned = []
        explicit_source_nums = {int(n) for n in re.findall(r"\[source\s+(\d+)\]", answer_lower)}

        for idx, item in enumerate(web_sources):
            title = (item.get("title") or "").lower()
            domain = (item.get("domain") or "").lower()
            snippet = (item.get("snippet") or "").lower()

            # Global and local source numbering check
            global_s_num = internal_chunks_count + idx + 1
            local_s_num = idx + 1
            is_explicitly_cited = (global_s_num in explicit_source_nums) or (local_s_num in explicit_source_nums and internal_chunks_count == 0)

            # Check 1: domain or significant title tokens mentioned in the answer
            title_tokens = [w for w in re.findall(r"\b[a-z0-9\-_]{4,}\b", title) if w not in {"official", "documentation", "guide", "preview", "available"}]
            title_mentioned = any(t in answer_lower for t in title_tokens) if title_tokens else False
            domain_mentioned = bool(domain and len(domain) > 5 and domain in answer_lower)

            # Check 2: Content alignment based on distinctive term overlap from snippet
            snippet_tokens = set(re.findall(r"\b[a-z0-9\-_/]{4,}\b", snippet))
            snippet_tokens.difference_update({
                "this", "that", "with", "from", "have", "more", "also", "into", "than",
                "been", "each", "were", "when", "some", "such", "only", "other", "about",
                "which", "there", "their", "these", "those", "would", "could", "should",
                "preview", "available"
            })
            overlap_count = sum(1 for token in snippet_tokens if token in answer_lower)
            overlap_ratio = overlap_count / max(len(snippet_tokens), 1)

            # Primary web source is included only if there is meaningful claim or term support
            is_primary = bool(
                idx < min_top_k
                and len(answer) > 40
                and (overlap_count >= 1 or title_mentioned or domain_mentioned or is_explicitly_cited)
            )

            if is_explicitly_cited or title_mentioned or domain_mentioned or overlap_ratio >= 0.12 or overlap_count >= 2 or is_primary:
                aligned.append({
                    "title": item.get("title", "SAP Official Documentation"),
                    "url": item.get("url", ""),
                    "domain": item.get("domain", "sap.com"),
                    "snippet": item.get("snippet", "")
                })

        return aligned

    @classmethod
    def evaluate_grounding(
        cls,
        query: str,
        answer: str,
        chunks: List[Dict[str, Any]],
        source_type: str = "knowledge_base"
    ) -> float:
        """
        Calibrated answer grounding evaluation.
        Evaluates whether the answer's material claims and technical assertions are supported
        by the supplied evidence, rather than relying on retrieval scores or keyword overlap alone.

        Weights:
        - 0.60: Statement-level evidence support ratio (including recognized evidence gap disclosures)
        - 0.20: Inline citation fidelity ([SOURCE X] matching valid evidence passages)
        - 0.20: Retrieval relevance signal (capped so high retrieval cannot conceal hallucinations)
        """
        # Refusal = no grounding
        if source_type == "refusal":
            return 0.0

        if cls.is_refusal(answer):
            return 0.0

        # No evidence = no grounding
        if not chunks:
            return 0.0

        # ---------------------------------------------------------
        # 1. Retrieval quality signal (capped at 20% weight)
        # ---------------------------------------------------------
        rerank_scores = [
            float(c.get("rerank_score", c.get("score", 0.0)))
            for c in chunks
        ]
        top_score = max(rerank_scores) if rerank_scores else 0.0
        retrieval_relevance = min(1.0, max(0.0, top_score))

        # ---------------------------------------------------------
        # 2. Statement / Claim-level evidence support
        # ---------------------------------------------------------
        stop_words = {
            "what", "is", "the", "a", "an", "how", "to", "in", "of", "and", "for",
            "with", "does", "explain", "can", "you", "tell", "me", "about", "which",
            "are", "this", "that", "from", "have", "more", "also", "into", "than",
            "been", "each", "were", "when", "some", "such", "only", "other", "their"
        }

        # Ubiquitous SAP vocabulary that must not confer unearned grounding credit
        generic_terms = {
            "sap", "brim", "system", "systems", "data", "process", "processes",
            "step", "steps", "using", "used", "table", "tables", "status", "statuses",
            "items", "item", "order", "orders", "document", "documents", "service",
            "services", "error", "errors", "issue", "issues", "check", "verify",
            "following", "overview", "standard", "general", "across", "through",
            "investigate", "investigation", "investigating", "review", "reviewing",
            "examine", "examining", "analysis", "discrepancies", "discrepancy",
            "finding", "findings", "relevant", "applied", "determine", "identifying",
            "identify", "ensure", "approach", "handling", "action", "actions",
            "strategy", "strategies", "procedure", "procedures", "scenario",
            "remediation", "resolution", "recommend", "recommended", "root", "cause",
            "causes", "symptom", "symptoms", "timing", "configuration", "versions",
            "version", "rules", "reconciliation", "operational", "reprocess", "reprocessing"
        }

        combined_chunk_text = " ".join(
            (c.get("chunk_text", "") + " " + (c.get("section") or ""))
            for c in chunks
        ).lower()

        # Split answer into atomic statements, diagnostic assertions, and numbered/bulleted items
        # Protect abbreviations like e.g., i.e., vs., fig., no., ref., p., pp., dr., mr., ms.,
        # and prevent splitting before inline [SOURCE X] citations.
        raw_sentences = cls._split_into_statements(answer)

        substantive_statements = []
        for s in raw_sentences:
            s_clean = s.strip()
            # Ignore markdown headers, table dividers, and very short transition labels (< 5 chars)
            if s_clean.startswith(("#", "|", "---", "===")) or len(s_clean) < 5:
                continue
            if s_clean.lower() in ("here is the analysis:", "investigation findings:", "diagnostic summary:"):
                continue
            substantive_statements.append(s_clean)

        if not substantive_statements:
            # Fallback for very brief single-phrase answers
            substantive_statements = [answer]

        supported_statements_count = 0
        explicit_citations_checked = 0
        valid_citations_count = 0

        for stmt in substantive_statements:
            stmt_lower = stmt.lower()

            # Check A: Explicit evidence gap disclosure
            # An honest disclosure of missing evidence is credited as truthful.
            # However, if the statement also contains substantive factual claims or technical assertions,
            # we must verify that the asserted technical entities and substantive claims are actually grounded in the evidence.
            gap_patterns = [
                r"\[evidence gap[^\]]*\]",
                r"\(evidence gap[^\)]*\)",
                r"evidence gap\b.*",
                r"not documented (?:in the knowledge base|in the context|in retrieved evidence)\b",
                r"insufficient evidence (?:in the knowledge base|in the context)\b",
                r"cannot be established from (?:the knowledge base|the evidence)\b",
                r"knowledge base does not contain\b.*",
                r"no matching evidence\b.*"
            ]
            has_gap_mention = any(re.search(pat, stmt_lower) for pat in gap_patterns)
            if has_gap_mention:
                # Strip the gap disclosure clause to inspect if substantive technical claims remain
                stripped_stmt = stmt_lower
                for pat in gap_patterns:
                    stripped_stmt = re.sub(pat, "", stripped_stmt)
                stripped_stmt = stripped_stmt.strip(" ,.-:;")

                # Check if substantive technical entities exist in the non-gap portion
                non_gap_tech = re.findall(
                    r"\b(?:[a-z]{1,4}[0-9]{1,4}[a-z0-9_]*|fkk[a-z0-9_]+|dfkk[a-z0-9_]+|erpk[a-z0-9_]+|cdpos|cdhdr)\b",
                    stripped_stmt
                )
                non_gap_entities = [
                    t for t in non_gap_tech
                    if (re.search(r"\d|_", t) or t.startswith(("dfkk", "fkk", "erpk", "cdpos", "cdhdr"))) and len(t) >= 3
                ]
                if non_gap_entities:
                    # If the statement asserts technical entities alongside a gap disclosure,
                    # verify that those technical entities exist in the evidence.
                    if all(cls._contains_term(combined_chunk_text, tc) for tc in non_gap_entities):
                        supported_statements_count += 1
                        continue
                    else:
                        # Statement bundled an ungrounded technical assertion with a gap disclosure - do not credit!
                        continue
                else:
                    # Check if substantive non-gap content words exist (assertions bundled with gap disclosures)
                    substantive_non_gap = [
                        w for w in re.findall(r"\b[a-z0-9\-_/]{3,}\b", stripped_stmt)
                        if w not in stop_words and w not in generic_terms
                    ]
                    if len(substantive_non_gap) >= 4:
                        # Non-trivial operational assertion attached to a gap disclosure:
                        # verify that non-gap assertions are supported in evidence without conflict
                        matched_non_gap = sum(1 for w in substantive_non_gap if cls._contains_term(combined_chunk_text, w))
                        ratio_non_gap = matched_non_gap / len(substantive_non_gap)
                        has_conflict = cls._detect_polarity_conflict(stmt, combined_chunk_text)
                        if ratio_non_gap >= 0.40 and not has_conflict:
                            supported_statements_count += 1
                        continue
                    else:
                        # Pure gap disclosure (e.g. "Missing procedure in knowledge base [Evidence Gap]")
                        supported_statements_count += 1
                        continue

            # Check B: Inline citation verification ([SOURCE X])
            # Technical identifiers asserted in the statement must be extracted first
            asserted_technical = re.findall(
                r"\b(?:[a-z]{1,4}[0-9]{1,4}[a-z0-9_]*|fkk[a-z0-9_]+|dfkk[a-z0-9_]+|erpk[a-z0-9_]+|cdpos|cdhdr)\b",
                stmt_lower
            )
            technical_entities = [
                t for t in asserted_technical
                if (re.search(r"\d|_", t) or t.startswith(("dfkk", "fkk", "erpk", "cdpos", "cdhdr"))) and len(t) >= 3
            ]

            # If the statement asserts technical entities, every single one must exist in the combined evidence.
            # Hallucinated technical entities invalidate the statement completely.
            if technical_entities and not all(cls._contains_term(combined_chunk_text, tc) for tc in technical_entities):
                continue

            cited_nums = re.findall(r"\[source\s+(\d+)\]", stmt_lower)
            if cited_nums:
                explicit_citations_checked += len(cited_nums)
                stmt_has_valid_citation = False
                for c_num_str in cited_nums:
                    c_idx = int(c_num_str) - 1
                    if 0 <= c_idx < len(chunks):
                        target_chunk_text = (chunks[c_idx].get("chunk_text") or "").lower()

                        # If technical entities are asserted in this statement,
                        # this specific cited chunk MUST contain all of them to be a valid supporting source
                        if technical_entities:
                            tech_supported = all(cls._contains_term(target_chunk_text, tc) for tc in technical_entities)
                            if not tech_supported:
                                continue

                        # Check that referenced chunk contains substantive domain terms from this statement
                        # (excluding stop words, generic SAP words, and 'source' markers)
                        stmt_words = [
                            w for w in re.findall(r"\b[a-z0-9\-_/]{3,}\b", stmt_lower)
                            if w not in stop_words and w not in generic_terms and not w.startswith("source")
                        ]
                        if stmt_words:
                            term_hits = sum(1 for w in stmt_words if cls._contains_term(target_chunk_text, w))
                            term_ratio = term_hits / max(1, len(stmt_words))
                            has_conflict = cls._detect_polarity_conflict(stmt, target_chunk_text)
                            if (term_ratio >= 0.25 or (term_hits >= 2 and term_ratio >= 0.20)) and not has_conflict:
                                valid_citations_count += 1
                                stmt_has_valid_citation = True
                        elif technical_entities and not cls._detect_polarity_conflict(stmt, target_chunk_text):
                            # Statement consisted of technical entities and generic words, with verified technical entities
                            valid_citations_count += 1
                            stmt_has_valid_citation = True

                if stmt_has_valid_citation:
                    supported_statements_count += 1
                    continue

            # Check C: Technical assertion verification
            # (If technical entities were asserted, they passed the combined evidence check above)
            # If statement had technical entities but no valid inline citation, proceed to term overlap check

            # Check D: Substantive term overlap with individual retrieved chunks
            # Validate that at least ONE individual chunk supports the terms, rather than
            # allowing terms scattered across multiple unrelated documents to artificially pass.
            stmt_terms = [
                w for w in re.findall(r"\b[a-z0-9\-_/]{3,}\b", stmt_lower)
                if w not in stop_words and w not in generic_terms
            ]
            if stmt_terms:
                is_supported = False
                for target_chunk in chunks:
                    chunk_content = (
                        (target_chunk.get("chunk_text") or "") + " " + (target_chunk.get("section") or "")
                    ).lower()
                    matched_terms = sum(1 for w in stmt_terms if cls._contains_term(chunk_content, w))
                    has_conflict = cls._detect_polarity_conflict(stmt, chunk_content)
                    if has_conflict:
                        continue

                    if len(stmt_terms) <= 2:
                        if matched_terms == len(stmt_terms):
                            is_supported = True
                            break
                    else:
                        overlap_ratio = matched_terms / len(stmt_terms)
                        if overlap_ratio >= 0.40 or (overlap_ratio >= 0.30 and matched_terms >= 2):
                            is_supported = True
                            break

                if is_supported:
                    supported_statements_count += 1
            elif technical_entities:
                # Statement consisted of verified technical entities and generic words
                # Ensure the verified technical entities exist together in at least one chunk
                if any(all(cls._contains_term(c.get("chunk_text", "").lower(), tc) for tc in technical_entities) for c in chunks):
                    supported_statements_count += 1

        statement_support_ratio = supported_statements_count / max(1, len(substantive_statements))

        # ---------------------------------------------------------
        # 3. Citation fidelity signal
        # ---------------------------------------------------------
        if explicit_citations_checked > 0:
            citation_fidelity = valid_citations_count / explicit_citations_checked
        else:
            # If the answer provides zero inline [SOURCE X] citations, citation fidelity is 0.0.
            # Do NOT fall back to statement support ratio (prevents unearned high grounding scores).
            citation_fidelity = 0.0

        # ---------------------------------------------------------
        # 4. Final Calibrated Grounding Score
        # ---------------------------------------------------------
        # Separate retrieval relevance, citation validity, and factual statement support.
        # Retrieval relevance is capped so high retrieval recall cannot disguise ungrounded answers.
        bounded_relevance = min(0.60, retrieval_relevance)
        raw_score = (
            0.50 * statement_support_ratio +
            0.35 * citation_fidelity +
            0.15 * bounded_relevance
        )

        final_score = max(0.0, min(0.98, raw_score))

        # Strict citation requirement: If substantive factual statements were made without ANY
        # inline citations to the retrieved evidence, cap grounding score at 0.35.
        if explicit_citations_checked == 0 and len(substantive_statements) >= 1:
            final_score = min(0.35, final_score)

        return round(float(final_score), 2)

    SEMANTIC_CLUSTERS = {
        "allowance": {"allowance", "allowances", "quota", "quotas", "allotment", "allotments", "counter", "counters"},
        "switch": {
            "switch", "switched", "switching", "change", "changed", "changing",
            "migration", "migrate", "migrated", "transition", "transitioned",
            "modify", "modified", "modification", "replace", "replaced", "replacement",
            "upgrade", "downgrade", "convert", "conversion"
        },
        "partially_consumed": {
            "partially-consumed", "partially consumed", "partial consumption", "partial",
            "consumed", "consumption", "unconsumed", "remaining", "balance", "balances",
            "unused", "partially used", "mid-cycle", "mid-period", "carry-forward", "carry forward", "proration", "prorated"
        },
        "reversal": {
            "reversal", "reversals", "reverse", "reversed", "reversing",
            "cancel", "canceled", "cancelled", "cancellation", "back out", "void", "storno"
        },
        "tcode": {
            "t-code", "t-codes", "tcode", "tcodes", "transaction", "transaction code",
            "fpl9", "fpe1", "fpe2", "fpe3", "fp04", "fpy1", "fpva", "fpvb", "fpo1",
            "fp06", "fp08", "fp40", "sm30", "spro", "pfcg", "fkkbix_bit_mon", "fkkbixbit_mon",
            "fkkbix_cit_mon", "fkkbixcit_mon"
        },
        "budget_billing": {"budget billing", "budget-billing", "payment plan", "installment", "installments"},
        "rap": {"rap", "restful application programming", "managed", "unmanaged"},
        "rar": {"rar", "revenue accounting", "revenue accounting and reporting", "pob", "performance obligation"},
        "fica": {"fica", "contract accounts receivable", "fpe1", "fpe2", "fpe3", "fpy1", "fpl9", "subledger"},
        "fpl9": {"fpl9", "account display", "account balance", "line item", "line items", "balance display"},
        "ci": {"convergent invoicing", "billable item", "consumption item", "bit", "cit", "fkkbi", "invoicing"},
        "cc": {"convergent charging", "charging", "charge plan", "pricing", "allowance", "core tool"},
        "som": {"som", "subscription order management", "subscription order", "subscription contract", "provider contract"},
        "odi": {"odi", "order distribution", "order distribution infrastructure", "distribution step"},
        "dunning": {"dunning", "dunning procedure", "dunning run", "dunning notice", "fpva", "fpvb", "dunning level", "dunning activity"},
        "bit_monitoring": {"fkkbix_bit_mon", "fkkbixbit_mon", "bit monitor", "billable item monitor", "billable items monitor", "bit status", "fkkbixcit_mon", "cit monitor"},
        "clearing": {
            "clearing", "cleared", "clear document", "fp04", "clearing restriction",
            "clearing status", "clearing reason", "partial clearing", "clearing document",
            "clearing control", "clearing history"
        },
        "payment": {
            "payment", "payments", "payment lot", "incoming payment", "payment run",
            "fpy1", "posted payment", "on account", "clarification", "payment allocation",
            "bank statement", "payment claim"
        },
        "open_items": {
            "open item", "open items", "open receivable", "receivable", "receivables",
            "fpo1", "due date", "subledger document", "outstanding receivable"
        }
    }

    DISTINCT_PRODUCTS = {"ariba", "concur", "fieldglass", "hybris", "c4c", "qualtrics", "celonis", "salesforce"}

    STOP_WORDS = {
        "what", "is", "the", "a", "an", "how", "to", "in", "of", "and", "for", "with",
        "does", "explain", "describe", "can", "you", "tell", "me", "about", "which", "are",
        "do", "between", "when", "within", "same", "different", "from", "that", "this"
    }

    @classmethod
    def evaluate_evidence_sufficiency(
        cls,
        query: str,
        chunks: List[Dict[str, Any]],
        rerank_score: Optional[float] = None,
        threshold: float = 0.60
    ) -> Tuple[bool, str]:
        """
        Conservative evidence sufficiency evaluation.
        Verifies that retrieved chunks support the query's required entities,
        operative scenarios, and state conditions rather than relying on retrieval score alone.
        """
        actual_score = rerank_score if rerank_score is not None else (
            float(chunks[0].get("score", chunks[0].get("rrf_score", 0.0))) if chunks else 0.0
        )
        if not chunks or actual_score < threshold:
            return False, f"Evidence score ({actual_score:.4f}) below threshold ({threshold})"

        combined_text = " ".join([(c.get("chunk_text") or "") + " " + (c.get("section") or "") for c in chunks]).lower()
        query_lower = query.lower()

        # Check distinct third-party / separate products
        query_words = set(re.findall(r"\b[a-z0-9\/\-_]+\b", query_lower))
        for p in cls.DISTINCT_PRODUCTS:
            if p in query_words and p not in combined_text:
                return False, f"Required product '{p}' not found in retrieved evidence"

        # Identify semantic clusters invoked by the query
        matched_clusters = []
        for cluster_name, terms in cls.SEMANTIC_CLUSTERS.items():
            if any(re.search(rf"\b{re.escape(term)}\b", query_lower) for term in terms):
                matched_clusters.append((cluster_name, terms))

        # Check if each required semantic cluster has at least one supporting term in the evidence
        for cluster_name, terms in matched_clusters:
            has_support = any(re.search(rf"\b{re.escape(term)}\b", combined_text) for term in terms)
            if not has_support:
                return False, f"Required scenario/entity cluster '{cluster_name}' has no supporting evidence in retrieved passages"

        # Coverage of significant content words
        # Only enforce lexical coverage ratio for non-scenario queries where user has not provided specific business numbers/terms
        is_scenario_query = bool(re.search(r"\b\d+\b|[₹$€]|discount|pricing plan|plan for|telecom plan", query_lower))
        if not is_scenario_query:
            significant_query_terms = [w for w in query_words if w not in cls.STOP_WORDS and len(w) > 3]
            if significant_query_terms:
                matched_terms = sum(1 for w in significant_query_terms if w in combined_text)
                coverage_ratio = matched_terms / len(significant_query_terms)
                if coverage_ratio < 0.25:
                    return False, f"Insufficient term coverage ({coverage_ratio:.2f} < 0.25) for query requirements"

        return True, "Sufficient evidence"

    @classmethod
    def calculate_evidence_grounding_score(
        cls,
        query: str,
        chunks: List[Dict[str, Any]],
        has_sufficient_evidence: bool = True,
        threshold: float = 0.60
    ) -> float:
        """
        Calculates the internal evidence grounding score prior to LLM generation.
        Returns the top rerank score of retrieved chunks if evidence is sufficient,
        or 0.0 if evidence is insufficient or missing required entities/scenarios.
        """
        if not chunks or not has_sufficient_evidence:
            return 0.0
        top_score = float(chunks[0].get("rerank_score", chunks[0].get("score", 0.0)))
        if top_score < threshold:
            return 0.0

        is_sufficient, reason = cls.evaluate_evidence_sufficiency(
            query=query,
            chunks=chunks,
            rerank_score=top_score,
            threshold=threshold
        )
        if not is_sufficient:
            # Foreign products not present in evidence must produce 0.0 to trigger refusal
            if "Required product" in reason:
                return 0.0
            # Operative scenario missing from internal evidence produces 0.0 so web fallback is attempted
            if "Required scenario/entity cluster" in reason:
                return 0.0
            # Insufficient term coverage indicates missing critical query aspects
            if "Insufficient term coverage" in reason:
                return 0.0
            # For scenario/partial evidence questions with positive retrieval scores, modulate score strictly below threshold
            return round(min(threshold - 0.05, top_score * 0.50), 4)

        # Modulate score if an evidence manifest indicates partial facet coverage
        manifest = chunks[0].get("evidence_manifest") if chunks else None
        if manifest and manifest.get("total_facets", 0) > 1:
            cov = float(manifest.get("coverage_ratio", 1.0))
            # Proportional coverage modulation:
            # High similarity on one facet cannot conceal missing evidence for remaining facets.
            # When coverage is low, the modulated score drops below threshold, triggering
            # web fallback or structured partial evidence synthesis.
            modulated = top_score * cov
            return round(modulated, 4)

        return round(top_score, 4)



grounding_service = GroundingService()