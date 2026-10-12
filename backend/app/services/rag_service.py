import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple, Generator
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models.chat import Chat
from backend.app.models.message import Message
from backend.app.models.citation import Citation
from backend.app.models.web_source import WebSource
from backend.app.services.sap_classifier import sap_classifier
from backend.app.services.retrieval_service import retrieval_service
from backend.app.services.grounding_service import grounding_service
from backend.app.services.web_search_service import web_search_service
from backend.app.services.llm_service import llm_service

logger = logging.getLogger(__name__)


class RAGService:
    """
    Authoritative, unified RAG execution service.
    Orchestrates:
    1. SAP domain classification
    2. Multi-turn conversation context retrieval
    3. Hybrid retrieval (vector + keyword via RRF)
    4. Evidence sufficiency & grounding check
    5. Context preparation & Web fallback
    6. LLM answer synthesis (strictly LLM-only output)
    7. Claim-to-citation alignment & grounding evaluation
    8. Unified database persistence across standard and streaming queries
    """

    @staticmethod
    def get_recent_chat_history(db: Session, chat_id: Optional[str], limit: int = 6) -> List[Dict[str, str]]:
        """Retrieves recent conversation turns for multi-turn conversational grounding."""
        if not chat_id:
            return []
        try:
            messages = (
                db.query(Message)
                .filter(Message.chat_id == chat_id)
                .order_by(Message.created_at.asc(), Message.id.asc())
                .all()
            )
            history = []
            for m in messages:
                if m.role in ("user", "assistant") and m.content:
                    history.append({"role": m.role, "content": m.content})
            return history[-limit:]
        except Exception as e:
            logger.warning(f"Could not retrieve chat history for {chat_id}: {e}")
            return []

    def prepare_context_and_sources(
        self,
        db: Session,
        query: str,
        chat_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes domain classification, retrieval, reranking, and evidence gathering.
        Returns a structured preparation state ready for answer generation.
        """
        # STEP 1: Conversation Context (Multi-turn support)
        chat_history = self.get_recent_chat_history(db, chat_id, limit=6)

        # STEP 2: Domain Restriction Validation (with conversation context awareness)
        is_sap, reason = sap_classifier.classify(query, chat_history=chat_history)
        if not is_sap:
            return {
                "status": "refusal",
                "answer": "I can only help with SAP and SAP-related topics.",
                "source_type": "refusal",
                "grounding_score": 0.0,
                "citations": [],
                "web_sources": [],
                "evidence": [],
                "chat_history": chat_history
            }

        # STEP 3: Private Knowledge Base Hybrid Retrieval + Adjacent Chunk Extension
        top_chunks, has_sufficient_evidence = retrieval_service.retrieve_and_rerank(
            db=db,
            query=query,
            candidate_k=settings.RRF_CANDIDATE_K,
            final_k=settings.FINAL_TOP_K,
            threshold=settings.SIMILARITY_THRESHOLD,
            enable_adjacent=True,
            chat_history=chat_history,
        )

        grounding_score = grounding_service.calculate_evidence_grounding_score(
            query,
            top_chunks,
            has_sufficient_evidence=has_sufficient_evidence,
            threshold=settings.SIMILARITY_THRESHOLD
        )

        # STEP 4: Evidence Sufficiency Evaluation
        manifest = top_chunks[0].get("evidence_manifest") if top_chunks else None
        is_partial = bool(manifest and manifest.get("unsupported_facets") and manifest.get("supported_facets"))

        # CASE 1: SUFFICIENT GROUNDING (grounding_score >= SIMILARITY_THRESHOLD)
        if top_chunks and grounding_score >= settings.SIMILARITY_THRESHOLD:
            # Candidate citations for preliminary preview
            candidate_citations = [
                {
                    "document": c["document_name"],
                    "page": c["page_number"],
                    "section": c["section"],
                    "score": c.get("score", c.get("rerank_score", 0.0)),
                    "snippet": c["chunk_text"][:280] + ("..." if len(c["chunk_text"]) > 280 else "")
                }
                for c in top_chunks
            ]
            return {
                "status": "ready",
                "source_type": "knowledge_base",
                "evidence": top_chunks,
                "internal_evidence": top_chunks,
                "web_evidence": None,
                "candidate_citations": candidate_citations,
                "candidate_web_sources": [],
                "chat_history": chat_history,
                "grounding_score": grounding_score,
                "evidence_sufficiency_score": grounding_score,
                "web_attempted": False,
                "evidence_manifest": manifest,
                "is_partial_evidence": is_partial
            }

        # CASE 2: INSUFFICIENT GROUNDING (grounding_score < SIMILARITY_THRESHOLD)
        # STEP 5: Authoritative Web Fallback
        if settings.WEB_FALLBACK_ENABLED:
            web_results = web_search_service.search_sap_authoritative(query)
            if web_results:
                candidate_web_sources = [
                    {
                        "title": w["title"],
                        "url": w["url"],
                        "domain": w["domain"],
                        "snippet": w["snippet"]
                    }
                    for w in web_results[:3]
                ]
                return {
                    "status": "ready",
                    "source_type": "web",
                    "evidence": top_chunks,
                    "internal_evidence": top_chunks,
                    "web_evidence": web_results,
                    "candidate_citations": [],
                    "candidate_web_sources": candidate_web_sources,
                    "chat_history": chat_history,
                    "grounding_score": grounding_score,
                    "evidence_sufficiency_score": grounding_score,
                    "web_attempted": True,
                    "evidence_manifest": manifest,
                    "is_partial_evidence": is_partial
                }

        # STEP 6: Insufficient Information Everywhere
        # If web fallback produced no results, but internal evidence has positive relevance
        # (partial evidence with top_score >= 0.40 and not a foreign product), allow partial evidence synthesis.
        # This enables the LLM to answer supported parts and explicitly identify missing evidence/gaps.
        # If no relevant chunks or foreign product, return refusal.
        query_words_lower = set(re.findall(r"\b[a-z0-9\/\-_]+\b", query.lower()))
        is_foreign_product = any(
            p in query_words_lower for p in ("ariba", "concur", "fieldglass", "hybris", "c4c", "qualtrics", "celonis", "salesforce")
        )
        top_score = float(top_chunks[0].get("rerank_score", top_chunks[0].get("score", 0.0))) if top_chunks else 0.0
        if top_chunks and (grounding_score > 0.0 or top_score >= 0.40) and not is_foreign_product:
            candidate_citations = [
                {
                    "document": c["document_name"],
                    "page": c["page_number"],
                    "section": c["section"],
                    "score": c.get("score", c.get("rerank_score", 0.0)),
                    "snippet": c["chunk_text"][:280] + ("..." if len(c["chunk_text"]) > 280 else "")
                }
                for c in top_chunks
            ]
            partial_score = (
                grounding_score
                if 0.0 < grounding_score < settings.SIMILARITY_THRESHOLD
                else round(min(settings.SIMILARITY_THRESHOLD - 0.05, top_score * 0.50), 4)
            )
            return {
                "status": "ready",
                "source_type": "knowledge_base",
                "evidence": top_chunks,
                "internal_evidence": top_chunks,
                "web_evidence": None,
                "candidate_citations": candidate_citations,
                "candidate_web_sources": [],
                "chat_history": chat_history,
                "grounding_score": partial_score,
                "evidence_sufficiency_score": partial_score,
                "web_attempted": settings.WEB_FALLBACK_ENABLED,
                "evidence_manifest": manifest,
                "is_partial_evidence": True
            }

        return {
            "status": "insufficient",
            "answer": "The information is not available in the provided knowledge base or fallback web evidence.",
            "source_type": "knowledge_base",
            "grounding_score": 0.0,
            "evidence_sufficiency_score": 0.0,
            "citations": [],
            "web_sources": [],
            "evidence": top_chunks,
            "internal_evidence": top_chunks,
            "web_evidence": None,
            "chat_history": chat_history,
            "web_attempted": settings.WEB_FALLBACK_ENABLED,
            "evidence_manifest": manifest,
            "is_partial_evidence": False
        }

    def _persist_interaction(
        self,
        db: Session,
        chat_id: Optional[str],
        query: str,
        answer: str,
        source_type: str,
        grounding_score: float,
        citations: List[Dict[str, Any]],
        web_sources: List[Dict[str, Any]]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Persists user message, assistant message, citations, and web sources consistently.
        Returns (assistant_message_id, chat_id).
        """
        if not chat_id:
            return None, None

        chat = db.query(Chat).filter(Chat.id == chat_id).first()
        if not chat:
            return None, None

        # Check if user message already logged to avoid double insertion
        last_msg = (
            db.query(Message)
            .filter(Message.chat_id == chat_id)
            .order_by(Message.created_at.desc())
            .first()
        )
        if not last_msg or last_msg.role != "user" or last_msg.content.strip() != query.strip():
            user_msg = Message(
                chat_id=chat_id,
                role="user",
                content=query.strip()
            )
            db.add(user_msg)
            # Auto-title chat on first turn
            if chat.title in ("New SAP Chat", "New Chat", "", None):
                words = query.strip().split()
                chat.title = " ".join(words[:6]) + ("..." if len(words) > 6 else "")

        # Create assistant message
        assistant_msg = Message(
            chat_id=chat_id,
            role="assistant",
            content=answer,
            source_type=source_type,
            grounding_score=grounding_score
        )
        db.add(assistant_msg)
        chat.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        # Persist citations
        if citations:
            for c in citations:
                db.add(Citation(
                    message_id=assistant_msg.id,
                    source_type="knowledge_base",
                    document_id=str(c.get("document_id") or ("doc-" + str(c.get("document", "sap"))))[:36],
                    document_name=c.get("document", "Unknown SAP Document"),
                    chunk_id=str(c.get("chunk_id", "chunk-ref"))[:36],
                    page_number=c.get("page", 1),
                    section=c.get("section"),
                    similarity_score=float(c.get("score", 0.0)),
                    citation_text=c.get("snippet", "")
                ))
            db.commit()

        # Persist web sources
        if web_sources:
            for w in web_sources:
                db.add(WebSource(
                    message_id=assistant_msg.id,
                    url=w.get("url", ""),
                    title=w.get("title", ""),
                    domain=w.get("domain", ""),
                    snippet=w.get("snippet", "")
                ))
            db.commit()

        db.refresh(assistant_msg)
        return assistant_msg.id, chat_id

    def process_query(
        self,
        db: Session,
        query: str,
        chat_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes the synchronous RAG pipeline with full persistence and citation alignment.
        """
        prep = self.prepare_context_and_sources(db, query, chat_id)

        # Refusal or Insufficient Evidence
        if prep["status"] in ("refusal", "insufficient"):
            msg_id, c_id = self._persist_interaction(
                db=db,
                chat_id=chat_id,
                query=query,
                answer=prep["answer"],
                source_type=prep["source_type"],
                grounding_score=prep["grounding_score"],
                citations=prep["citations"],
                web_sources=prep["web_sources"]
            )
            return {
                "answer": prep["answer"],
                "source_type": prep["source_type"],
                "evidence_sufficiency_score": prep["grounding_score"],
                "answer_grounding_score": prep["grounding_score"],
                "grounding_score": prep["grounding_score"],
                "citations": prep["citations"],
                "web_sources": prep["web_sources"],
                "chat_id": c_id,
                "message_id": msg_id,
                "evidence_manifest": prep.get("evidence_manifest"),
                "is_partial_evidence": prep.get("is_partial_evidence", False)
            }

        # Knowledge Base or Web Evidence Synthesis
        answer = llm_service.generate_answer(
            query=query,
            evidence=prep.get("internal_evidence", prep["evidence"]),
            source_type=prep["source_type"],
            chat_history=prep["chat_history"],
            web_evidence=prep.get("web_evidence"),
            evidence_manifest=prep.get("evidence_manifest"),
        )

        # Priority 1: Adaptive Fallback
        # If internal KB generation produced explicit refusal, allow one fallback attempt
        # when Tavily is enabled and has not already been attempted.
        is_kb_refusal = (
            prep["source_type"] == "knowledge_base"
            and grounding_service.is_refusal(answer)
        )
        if is_kb_refusal and settings.WEB_FALLBACK_ENABLED and not prep.get("web_attempted", False):
            logger.info("Internal answer generation produced explicit refusal. Attempting adaptive Tavily fallback.")
            prep["web_attempted"] = True
            web_results = web_search_service.search_sap_authoritative(query)
            if web_results:
                prep["source_type"] = "web"
                prep["web_evidence"] = web_results
                answer = llm_service.generate_answer(
                    query=query,
                    evidence=prep.get("internal_evidence", prep["evidence"]),
                    source_type="web",
                    chat_history=prep["chat_history"],
                    web_evidence=web_results,
                    evidence_manifest=prep.get("evidence_manifest"),
                )

        if prep["source_type"] == "knowledge_base":
            aligned_citations = grounding_service.align_citations(answer, prep["evidence"])
            final_web_sources = []
            grounding = grounding_service.evaluate_grounding(
                query, answer, prep["evidence"], source_type="knowledge_base"
            )
        else:
            aligned_citations = grounding_service.align_citations(
                answer, prep.get("internal_evidence", []), min_top_k=0
            )
            final_web_sources = grounding_service.align_web_sources(
                answer,
                prep.get("web_evidence", []),
                internal_chunks_count=len(prep.get("internal_evidence", []))
            )
            web_chunk_reps = [
                {
                    "chunk_text": f"{w.get('title', '')} {w.get('snippet', '')}",
                    "score": float(w.get("score", 0.85)),
                    "rerank_score": float(w.get("score", 0.85))
                }
                for w in (final_web_sources or prep.get("web_evidence", []))
            ]
            combined_eval_evidence = prep.get("internal_evidence", []) + web_chunk_reps
            grounding = grounding_service.evaluate_grounding(
                query, answer, combined_eval_evidence, source_type="web"
            )

        msg_id, c_id = self._persist_interaction(
            db=db,
            chat_id=chat_id,
            query=query,
            answer=answer,
            source_type=prep["source_type"],
            grounding_score=grounding,
            citations=aligned_citations,
            web_sources=final_web_sources
        )

        # Determine final source type and grounding for UI
        final_source_type = prep["source_type"]
        answer_grounding_score = grounding
        if grounding_service.is_refusal(answer):
            final_source_type = "refusal"
            answer_grounding_score = 0.0
        return {
            "answer": answer,
            "source_type": final_source_type,
            "evidence_sufficiency_score": prep["grounding_score"],
            "answer_grounding_score": answer_grounding_score,
            "grounding_score": answer_grounding_score,
            "citations": aligned_citations,
            "web_sources": final_web_sources,
            "chat_id": c_id,
            "message_id": msg_id,
            "evidence_manifest": prep.get("evidence_manifest"),
            "is_partial_evidence": prep.get("is_partial_evidence", False)
        }

    def stream_query(
        self,
        db: Session,
        query: str,
        chat_id: str,
        user_id: Optional[str] = None
    ) -> Generator[str, None, None]:
        """
        Executes genuine incremental provider streaming over SSE with consistent persistence.
        Supports conservative evidence sufficiency and adaptive fallback.
        """
        prep = self.prepare_context_and_sources(db, query, chat_id)

        # Handle non-LLM immediate terminal outcomes
        if prep["status"] in ("refusal", "insufficient"):
            msg_id, _ = self._persist_interaction(
                db=db,
                chat_id=chat_id,
                query=query,
                answer=prep["answer"],
                source_type=prep["source_type"],
                grounding_score=prep["grounding_score"],
                citations=prep["citations"],
                web_sources=prep["web_sources"]
            )
            # SSE Metadata
            meta_payload = {
                "message_id": msg_id,
                "source_type": prep["source_type"],
                "evidence_sufficiency_score": prep.get("evidence_sufficiency_score", prep.get("grounding_score", 0.0)),
                "answer_grounding_score": prep["grounding_score"],
                "grounding_score": prep["grounding_score"],
                "is_preliminary": False,
                "citations": prep["citations"],
                "web_sources": prep["web_sources"],
                "evidence_manifest": prep.get("evidence_manifest"),
                "is_partial_evidence": prep.get("is_partial_evidence", False)
            }
            yield f"event: metadata\ndata: {json.dumps(meta_payload)}\n\n"
            yield f"event: token\ndata: {json.dumps({'token': prep['answer']})}\n\n"
            yield f"event: done\ndata: {json.dumps({'status': 'complete'})}\n\n"
            return

        # Adaptive Fallback Support for streaming KB generation
        if prep["source_type"] == "knowledge_base" and settings.WEB_FALLBACK_ENABLED and not prep.get("web_attempted", False):
            refusal_prefix = "the information is not available in the provided knowledge base"
            buffered_tokens: List[str] = []
            is_refusal_matching = True
            metadata_emitted = False
            tokens_accumulated: List[str] = []

            for token in llm_service.stream_answer(
                query=query,
                evidence=prep.get("internal_evidence", prep["evidence"]),
                source_type="knowledge_base",
                chat_history=prep["chat_history"],
                web_evidence=None,
                evidence_manifest=prep.get("evidence_manifest"),
            ):
                tokens_accumulated.append(token)
                if is_refusal_matching:
                    buffered_tokens.append(token)
                    current_buf_text = "".join(buffered_tokens).lower().strip()
                    cleaned_buf = re.sub(r"[^\w\s]", "", current_buf_text)
                    cleaned_target = re.sub(r"[^\w\s]", "", refusal_prefix)

                    if cleaned_target.startswith(cleaned_buf):
                        if len(cleaned_buf) >= len(cleaned_target):
                            break
                        continue
                    else:
                        is_refusal_matching = False
                        if not metadata_emitted:
                            pre_score = prep.get("evidence_sufficiency_score", prep.get("grounding_score", 0.0))
                            preliminary_meta = {
                                "message_id": None,
                                "source_type": "knowledge_base",
                                "evidence_sufficiency_score": pre_score,
                                "grounding_score": pre_score,
                                "is_preliminary": True,
                                "citations": prep.get("candidate_citations", []),
                                "web_sources": [],
                                "evidence_manifest": prep.get("evidence_manifest"),
                                "is_partial_evidence": prep.get("is_partial_evidence", False)
                            }
                            yield f"event: metadata\ndata: {json.dumps(preliminary_meta)}\n\n"
                            metadata_emitted = True
                        for b_tok in buffered_tokens:
                            yield f"event: token\ndata: {json.dumps({'token': b_tok})}\n\n"
                        buffered_tokens = []
                else:
                    yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

            accumulated_answer = "".join(tokens_accumulated).strip()
            # If explicit refusal was generated, perform adaptive Tavily fallback
            if grounding_service.is_refusal(accumulated_answer):
                logger.info("Streamed internal generation produced explicit refusal. Attempting adaptive Tavily fallback.")
                prep["web_attempted"] = True
                web_results = web_search_service.search_sap_authoritative(query)
                if web_results:
                    candidate_web_sources = [
                        {
                            "title": w["title"],
                            "url": w["url"],
                            "domain": w["domain"],
                            "snippet": w["snippet"]
                        }
                        for w in web_results[:3]
                    ]
                    web_meta = {
                        "message_id": None,
                        "source_type": "web",
                        "evidence_sufficiency_score": 0.0,
                        "grounding_score": 0.0,
                        "is_preliminary": True,
                        "citations": [],
                        "web_sources": candidate_web_sources,
                        "evidence_manifest": prep.get("evidence_manifest"),
                        "is_partial_evidence": prep.get("is_partial_evidence", False)
                    }
                    yield f"event: metadata\ndata: {json.dumps(web_meta)}\n\n"

                    web_tokens: List[str] = []
                    for token in llm_service.stream_answer(
                        query=query,
                        evidence=prep.get("internal_evidence", prep["evidence"]),
                        source_type="web",
                        chat_history=prep["chat_history"],
                        web_evidence=web_results,
                        evidence_manifest=prep.get("evidence_manifest"),
                    ):
                        web_tokens.append(token)
                        yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

                    full_answer = "".join(web_tokens).strip()
                    if not full_answer:
                        full_answer = "The AI reasoning service did not return content. Please retry your query."

                    aligned_citations = grounding_service.align_citations(
                        full_answer, prep.get("internal_evidence", []), min_top_k=0
                    )
                    final_web_sources = grounding_service.align_web_sources(
                        full_answer,
                        web_results,
                        internal_chunks_count=len(prep.get("internal_evidence", []))
                    )
                    web_chunk_reps = [
                        {
                            "chunk_text": f"{w.get('title', '')} {w.get('snippet', '')}",
                            "score": float(w.get("score", 0.85)),
                            "rerank_score": float(w.get("score", 0.85))
                        }
                        for w in (final_web_sources or web_results)
                    ]
                    combined_eval_evidence = prep.get("internal_evidence", []) + web_chunk_reps
                    grounding = grounding_service.evaluate_grounding(
                        query, full_answer, combined_eval_evidence, source_type="web"
                    )

                    msg_id, _ = self._persist_interaction(
                        db=db,
                        chat_id=chat_id,
                        query=query,
                        answer=full_answer,
                        source_type="web",
                        grounding_score=grounding,
                        citations=aligned_citations,
                        web_sources=final_web_sources
                    )

                    final_meta = {
                        "message_id": msg_id,
                        "source_type": "web",
                        "evidence_sufficiency_score": 0.0,
                        "answer_grounding_score": grounding,
                        "grounding_score": grounding,
                        "is_preliminary": False,
                        "citations": aligned_citations,
                        "web_sources": final_web_sources,
                        "evidence_manifest": prep.get("evidence_manifest"),
                        "is_partial_evidence": prep.get("is_partial_evidence", False)
                    }
                    yield f"event: metadata\ndata: {json.dumps(final_meta)}\n\n"
                    yield f"event: done\ndata: {json.dumps({'status': 'complete'})}\n\n"
                    return
                else:
                    # Strict refusal: neither source supported an answer
                    refusal_answer = "The information is not available in the provided knowledge base."
                    msg_id, _ = self._persist_interaction(
                        db=db,
                        chat_id=chat_id,
                        query=query,
                        answer=refusal_answer,
                        source_type="knowledge_base",
                        grounding_score=0.0,
                        citations=[],
                        web_sources=[]
                    )
                    final_meta = {
                        "message_id": msg_id,
                        "source_type": "knowledge_base",
                        "evidence_sufficiency_score": 0.0,
                        "answer_grounding_score": 0.0,
                        "grounding_score": 0.0,
                        "is_preliminary": False,
                        "citations": [],
                        "web_sources": [],
                        "evidence_manifest": prep.get("evidence_manifest"),
                        "is_partial_evidence": prep.get("is_partial_evidence", False)
                    }
                    yield f"event: metadata\ndata: {json.dumps(final_meta)}\n\n"
                    yield f"event: token\ndata: {json.dumps({'token': refusal_answer})}\n\n"
                    yield f"event: done\ndata: {json.dumps({'status': 'complete'})}\n\n"
                    return

            # Non-refusal confirmed KB answer
            if buffered_tokens:
                if not metadata_emitted:
                    pre_score = prep.get("evidence_sufficiency_score", prep.get("grounding_score", 0.0))
                    preliminary_meta = {
                        "message_id": None,
                        "source_type": "knowledge_base",
                        "evidence_sufficiency_score": pre_score,
                        "grounding_score": pre_score,
                        "is_preliminary": True,
                        "citations": prep.get("candidate_citations", []),
                        "web_sources": [],
                        "evidence_manifest": prep.get("evidence_manifest"),
                        "is_partial_evidence": prep.get("is_partial_evidence", False)
                    }
                    yield f"event: metadata\ndata: {json.dumps(preliminary_meta)}\n\n"
                    metadata_emitted = True
                for b_tok in buffered_tokens:
                    yield f"event: token\ndata: {json.dumps({'token': b_tok})}\n\n"
                buffered_tokens = []

            full_answer = accumulated_answer
            aligned_citations = grounding_service.align_citations(full_answer, prep["evidence"])
            final_web_sources = []
            grounding = grounding_service.evaluate_grounding(
                query, full_answer, prep["evidence"], source_type="knowledge_base"
            )

            final_source_type = "knowledge_base"
            final_grounding = grounding
            if grounding_service.is_refusal(full_answer):
                final_source_type = "refusal"
                final_grounding = 0.0

            msg_id, _ = self._persist_interaction(
                db=db,
                chat_id=chat_id,
                query=query,
                answer=full_answer,
                source_type=final_source_type,
                grounding_score=final_grounding,
                citations=aligned_citations,
                web_sources=final_web_sources
            )

            final_meta = {
                "message_id": msg_id,
                "source_type": final_source_type,
                "evidence_sufficiency_score": prep.get("evidence_sufficiency_score", prep.get("grounding_score", 0.0)),
                "answer_grounding_score": final_grounding,
                "grounding_score": final_grounding,
                "is_preliminary": False,
                "citations": aligned_citations,
                "web_sources": final_web_sources,
                "evidence_manifest": prep.get("evidence_manifest"),
                "is_partial_evidence": prep.get("is_partial_evidence", False)
            }
            yield f"event: metadata\ndata: {json.dumps(final_meta)}\n\n"
            yield f"event: done\ndata: {json.dumps({'status': 'complete'})}\n\n"
            return

        # Standard streaming (web source or when adaptive fallback is disabled)
        pre_score = prep.get("evidence_sufficiency_score", prep.get("grounding_score", 0.0))
        preliminary_meta = {
            "message_id": None,
            "source_type": prep["source_type"],
            "evidence_sufficiency_score": pre_score,
            "grounding_score": pre_score,
            "is_preliminary": True,
            "citations": prep.get("candidate_citations", []),
            "web_sources": prep.get("candidate_web_sources", []),
            "evidence_manifest": prep.get("evidence_manifest"),
            "is_partial_evidence": prep.get("is_partial_evidence", False)
        }
        yield f"event: metadata\ndata: {json.dumps(preliminary_meta)}\n\n"

        # Stream incremental tokens from LLM provider
        tokens_accumulated = []
        for token in llm_service.stream_answer(
            query=query,
            evidence=prep.get("internal_evidence", prep["evidence"]),
            source_type=prep["source_type"],
            chat_history=prep["chat_history"],
            web_evidence=prep.get("web_evidence"),
            evidence_manifest=prep.get("evidence_manifest"),
        ):
            tokens_accumulated.append(token)
            yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"

        full_answer = "".join(tokens_accumulated).strip()
        if not full_answer:
            full_answer = "The AI reasoning service did not return content. Please retry your query."

        # Post-generation grounding and citation verification
        if prep["source_type"] == "knowledge_base":
            aligned_citations = grounding_service.align_citations(full_answer, prep["evidence"])
            final_web_sources = []
            grounding = grounding_service.evaluate_grounding(
                query, full_answer, prep["evidence"], source_type="knowledge_base"
            )
        else:
            aligned_citations = grounding_service.align_citations(
                full_answer, prep.get("internal_evidence", []), min_top_k=0
            )
            final_web_sources = grounding_service.align_web_sources(
                full_answer,
                prep.get("web_evidence", []),
                internal_chunks_count=len(prep.get("internal_evidence", []))
            )
            web_chunk_reps = [
                {
                    "chunk_text": f"{w.get('title', '')} {w.get('snippet', '')}",
                    "score": float(w.get("score", 0.85)),
                    "rerank_score": float(w.get("score", 0.85))
                }
                for w in (final_web_sources or prep.get("web_evidence", []))
            ]
            combined_eval_evidence = prep.get("internal_evidence", []) + web_chunk_reps
            grounding = grounding_service.evaluate_grounding(
                query, full_answer, combined_eval_evidence, source_type="web"
            )

        # Consistent database persistence
        final_source_type = prep["source_type"]
        final_grounding = grounding
        if grounding_service.is_refusal(full_answer):
            final_source_type = "refusal"
            final_grounding = 0.0

        msg_id, _ = self._persist_interaction(
            db=db,
            chat_id=chat_id,
            query=query,
            answer=full_answer,
            source_type=final_source_type,
            grounding_score=final_grounding,
            citations=aligned_citations,
            web_sources=final_web_sources
        )

        # Final metadata event with confirmed message ID and aligned citations
        final_meta = {
            "message_id": msg_id,
            "source_type": final_source_type,
            "evidence_sufficiency_score": prep.get("evidence_sufficiency_score", prep.get("grounding_score", 0.0)),
            "answer_grounding_score": final_grounding,
            "grounding_score": final_grounding,
            "is_preliminary": False,
            "citations": aligned_citations,
            "web_sources": final_web_sources,
            "evidence_manifest": prep.get("evidence_manifest"),
            "is_partial_evidence": prep.get("is_partial_evidence", False)
        }
        yield f"event: metadata\ndata: {json.dumps(final_meta)}\n\n"
        yield f"event: done\ndata: {json.dumps({'status': 'complete'})}\n\n"


rag_service = RAGService()
