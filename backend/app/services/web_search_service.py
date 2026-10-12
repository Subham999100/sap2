import logging
import re
import urllib.parse
from typing import List, Dict, Any, Optional

from tavily import TavilyClient

from backend.app.config import settings

logger = logging.getLogger(__name__)


class WebSearchService:
    """
    Controlled Web Search Service for SAP BRIM Assistant.

    Flow:
    1. Used strictly as a controlled fallback when internal knowledge base evidence is insufficient.
    2. Generates a small, bounded set of focused search queries covering missing requirements.
    3. Restricted to authoritative SAP domains (help.sap.com, community.sap.com, learning.sap.com, blogs.sap.com, sap.com).
    4. Canonicalizes URLs and deduplicates evidence while retaining the most informative snippets.
    5. Fails gracefully per query without breaking RAG execution.
    """

    ALLOWED_DOMAINS = (
        "help.sap.com",
        "community.sap.com",
        "learning.sap.com",
        "blogs.sap.com",
        "sap.com",
    )

    def __init__(self, api_key: Optional[str] = None):
        key = api_key or getattr(settings, "TAVILY_API_KEY", "") or getattr(settings, "WEB_SEARCH_API_KEY", "")
        self.api_key = key.strip() if key else ""
        self.client: Optional[TavilyClient] = None

        if self.api_key:
            try:
                self.client = TavilyClient(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Failed to initialize Tavily client: {e}")
                self.client = None

    @classmethod
    def _is_sap_source(cls, url: str) -> bool:
        """Check whether a URL belongs to an authoritative SAP domain."""
        url_lower = url.lower()
        return any(domain in url_lower for domain in cls.ALLOWED_DOMAINS)

    @staticmethod
    def _clean_text(value: Any) -> str:
        """Clean HTML and normalize whitespace."""
        if not value:
            return ""
        text = str(value)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        """
        Canonicalizes URL for accurate deduplication:
        - Strips URL fragments (#...)
        - Normalizes scheme and host to lowercase
        - Strips default ports (:80, :443)
        - Removes tracking query parameters (utm_*, ref, etc.)
        - Normalizes path (strips trailing slashes)
        """
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlsplit(url.strip())
            scheme = parsed.scheme.lower() if parsed.scheme else "https"
            netloc = parsed.netloc.lower()
            if ":80" in netloc and scheme == "http":
                netloc = netloc.replace(":80", "")
            elif ":443" in netloc and scheme == "https":
                netloc = netloc.replace(":443", "")

            path = parsed.path.rstrip("/")
            if not path:
                path = ""

            query_parts = []
            if parsed.query:
                params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                for k, v in params:
                    k_lower = k.lower()
                    if k_lower.startswith("utm_") or k_lower in {
                        "ref", "source", "fbclid", "gclid", "locale", "lang", "language", "format"
                    }:
                        continue
                    query_parts.append((k, v))
                query_parts.sort()

            new_query = urllib.parse.urlencode(query_parts) if query_parts else ""
            return urllib.parse.urlunsplit((scheme, netloc, path, new_query, ""))
        except Exception:
            return url.strip().split("#")[0].rstrip("/")

    @staticmethod
    def _is_boilerplate_snippet(snippet: str) -> bool:
        """Identify forum boilerplate or login notice snippets."""
        if not snippet:
            return False
        snip_lower = snippet.lower()
        boilerplate_phrases = (
            "you must be a registered user to add a comment",
            "please sign in to comment",
            "log in to vote",
            "javascript is required",
            "cookies must be enabled",
        )
        return any(phrase in snip_lower for phrase in boilerplate_phrases)

    @classmethod
    def generate_focused_queries(cls, query: str) -> List[str]:
        """
        Generates a small set of focused, targeted search queries derived from question requirements.

        1. Allowance product-switch scenario: searches distinct concepts covering:
           - SAP Convergent Charging, subscription/product changes, pricing configuration
           - Allowance validity, remaining balances, expiration, transfer, and proration behavior
        2. General scenarios: extracts essential entities and technical criteria into concise keyword queries.

        Always bounded to at most 3 distinct, non-empty queries.
        """
        clean = query.strip()
        if not clean:
            return []

        clean_lower = clean.lower()
        queries: List[str] = []

        # Check for the allowance product-switch / lifecycle scenario
        has_allowance = "allowance" in clean_lower
        has_switch_or_product = any(
            k in clean_lower for k in ("switch", "change", "product", "pricing", "subscription", "contract")
        )
        has_lifecycle = any(
            k in clean_lower
            for k in ("validity", "consumed", "balance", "remain", "proration", "transfer", "expire", "expiration")
        )

        if has_allowance and (has_switch_or_product or has_lifecycle):
            # Concept 1: Subscription/product changes & pricing configuration in SAP Convergent Charging
            queries.append("SAP Convergent Charging subscription product change pricing configuration allowance")
            # Concept 2: Allowance validity, remaining balance, expiration, transfer, and proration behavior
            queries.append("SAP Convergent Charging allowance validity remaining balance expiration transfer proration")
            # Concept 3: Partially consumed allowance during product switch / contract change
            queries.append("SAP Convergent Charging partially consumed allowance product switch billing period")
        else:
            # Base query formulation
            if "sap" in clean_lower:
                base_query = clean
            else:
                base_query = f"SAP {clean}"
            queries.append(base_query)

            # Generate focused keyword query by stripping conversational filler words
            tokens = re.findall(r"[a-zA-Z0-9_\-]+", clean)
            stop_words = {
                "how", "does", "what", "is", "are", "can", "could", "would", "should",
                "why", "when", "where", "which", "who", "whom", "the", "a", "an", "in",
                "on", "at", "to", "for", "of", "with", "within", "same", "different",
                "treat", "treats", "treated", "and", "or", "by", "from", "into", "about",
                "explain", "describe", "tell", "me", "show", "please"
            }
            focused_tokens = [t for t in tokens if t.lower() not in stop_words]
            if len(focused_tokens) >= 3 and len(focused_tokens) < len(tokens):
                focused_kw = " ".join(focused_tokens)
                if "sap" not in focused_kw.lower():
                    focused_kw = f"SAP {focused_kw}"
                if focused_kw.lower() != base_query.lower():
                    queries.append(focused_kw)

        # Deduplicate while preserving order, bounded to at most 3 queries
        unique_queries: List[str] = []
        seen = set()
        for q in queries:
            normalized = " ".join(q.split()).strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                unique_queries.append(normalized)
            if len(unique_queries) >= 3:
                break

        return unique_queries

    def search_sap_authoritative(
        self,
        query: str,
        max_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Executes authoritative SAP domain search across generated focused queries via Tavily.
        Deduplicates results by canonical URL, retains best snippets, and filters low-value boilerplate.
        Returns a list of clean, structured web evidence items.
        """
        if not getattr(settings, "WEB_FALLBACK_ENABLED", True):
            logger.debug("Tavily web fallback is disabled by configuration.")
            return []

        # Re-check client if key was configured later
        if not self.client and (getattr(settings, "TAVILY_API_KEY", "") or getattr(settings, "WEB_SEARCH_API_KEY", "")):
            key = getattr(settings, "TAVILY_API_KEY", "") or getattr(settings, "WEB_SEARCH_API_KEY", "")
            if key.strip():
                try:
                    self.api_key = key.strip()
                    self.client = TavilyClient(api_key=self.api_key)
                except Exception as e:
                    logger.warning(f"Failed to initialize Tavily client on-demand: {e}")
                    self.client = None

        if not self.client:
            logger.debug("Tavily client unavailable (no API key configured or initialization failed).")
            return []

        focused_queries = self.generate_focused_queries(query)
        if not focused_queries:
            return []

        deduped_results: Dict[str, Dict[str, Any]] = {}
        searched_queries = set()

        for search_query in focused_queries:
            norm_q = search_query.strip().lower()
            if norm_q in searched_queries:
                continue
            searched_queries.add(norm_q)

            try:
                logger.info(f"Executing Tavily fallback search for query: '{search_query[:80]}'")
                response = self.client.search(
                    query=search_query,
                    search_depth="advanced",
                    max_results=max_results,
                    include_answer=False,
                    include_raw_content=False,
                    include_domains=list(self.ALLOWED_DOMAINS),
                )
            except Exception as exc:
                logger.warning(f"[Tavily] Web search request failed gracefully for '{search_query[:50]}': {exc}")
                continue

            for item in response.get("results", []):
                raw_url = str(item.get("url", "")).strip()
                if not raw_url:
                    continue

                # Only accept verified SAP sources
                if not self._is_sap_source(raw_url):
                    continue

                canon_url = self._canonicalize_url(raw_url)
                if not canon_url:
                    continue

                title = self._clean_text(item.get("title", ""))
                snippet = self._clean_text(item.get("content", ""))

                if self._is_boilerplate_snippet(snippet):
                    continue

                if not title:
                    title = "SAP Official Documentation"
                if not snippet:
                    snippet = "No preview available."

                domain = "sap.com"
                for d in ("help.sap.com", "community.sap.com", "learning.sap.com", "blogs.sap.com"):
                    if d in canon_url.lower():
                        domain = d
                        break

                raw_score = item.get("score")
                score = float(raw_score) if raw_score is not None else 0.85

                # Deduplicate by canonical URL, retaining the more informative snippet
                if canon_url in deduped_results:
                    existing = deduped_results[canon_url]
                    if len(snippet) > len(existing["snippet"]):
                        existing["snippet"] = snippet
                        existing["title"] = title
                    existing["score"] = max(existing["score"], score)
                else:
                    deduped_results[canon_url] = {
                        "title": title,
                        "url": canon_url,
                        "domain": domain,
                        "snippet": snippet,
                        "score": score
                    }

        return list(deduped_results.values())[:max_results]


web_search_service = WebSearchService()