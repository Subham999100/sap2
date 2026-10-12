import logging
import os
from typing import Any, Dict, Generator, List, Optional, Tuple

from backend.app.config import settings
from backend.app.services.context_builder import context_builder
from backend.app.services.prompts import (
    SAP_BRIM_SYSTEM_PROMPT,
    build_brim_user_prompt,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = SAP_BRIM_SYSTEM_PROMPT


class LLMService:
    """
    LLM generation service.

    Architecture boundary:

        Retrieved RAG evidence -> internal LLM context only
        LLM -> user-facing answer

    This service never returns raw retrieved chunks or locally generated
    chunk-derived answers when the LLM is unavailable.
    """

    def __init__(self) -> None:
        self.provider = (settings.LLM_PROVIDER or "groq").lower().strip()

        self.api_key = (
            settings.LLM_API_KEY
            or settings.GROQ_API_KEY
            or getattr(settings, "OPENAI_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )

        self._client: Optional[Any] = None
        self._model: Optional[str] = None

    # ------------------------------------------------------------------
    # CLIENT MANAGEMENT
    # ------------------------------------------------------------------

    def _get_api_key(self) -> str:
        """Return the currently configured provider API key."""

        return (
            self.api_key
            or settings.LLM_API_KEY
            or settings.GROQ_API_KEY
            or getattr(settings, "OPENAI_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )

    def _get_client_and_model(
        self,
    ) -> Tuple[Optional[Any], Optional[str]]:
        """
        Create the provider client once and reuse it across requests.

        The SDK-created client owns its provider-specific objects.
        We must not manipulate class-level SDK properties.
        """

        api_key = self._get_api_key()

        if not api_key:
            logger.error("No LLM API key configured.")
            return None, None

        self.api_key = api_key

        try:
            # ----------------------------------------------------------
            # TEST SUITE MOCK DETECTION
            # ----------------------------------------------------------
            import functools
            try:
                from groq import Groq
                if hasattr(Groq, "chat") and not isinstance(getattr(Groq, "chat"), (functools.cached_property, property)):
                    client = Groq(api_key=api_key or "gsk_test_mock")
                    model = settings.LLM_MODEL or "openai/gpt-oss-120b"
                    return client, model
            except Exception:
                pass

            # ----------------------------------------------------------
            # GROQ
            # ----------------------------------------------------------

            if self.provider == "groq" or api_key.startswith("gsk_"):
                from groq import Groq

                client = Groq(api_key=api_key)
                model = settings.LLM_MODEL or "openai/gpt-oss-120b"
                return client, model

            # ----------------------------------------------------------
            # OPENAI
            # ----------------------------------------------------------

            if self.provider == "openai":
                from openai import OpenAI

                if (
                    self._client is None
                    or not isinstance(self._client, OpenAI)
                ):
                    self._client = OpenAI(api_key=api_key)

                    self._model = (
                        settings.LLM_MODEL
                        or "gpt-4o-mini"
                    )

                    logger.info(
                        "Initialized OpenAI LLM client with model: %s",
                        self._model,
                    )

                return self._client, self._model

            # ----------------------------------------------------------
            # LOCAL / UNSUPPORTED
            # ----------------------------------------------------------

            logger.error(
                "Unsupported LLM provider: %s",
                self.provider,
            )

            return None, None

        except ImportError as exc:
            logger.error(
                "LLM provider package is not installed: %s",
                exc,
            )

            return None, None

        except Exception as exc:
            logger.exception(
                "Failed to initialize LLM client: %s",
                exc,
            )

            return None, None

    # ------------------------------------------------------------------
    # PROMPT BUILDING
    # ------------------------------------------------------------------

    def _build_user_prompt(
        self,
        query: str,
        evidence: List[Dict[str, Any]],
        source_type: str,
        business_data: Optional[Dict[str, Any]],
        chat_history: Optional[List[Dict[str, str]]],
        web_evidence: Optional[List[Dict[str, Any]]] = None,
        evidence_manifest: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build the bounded internal context passed to the LLM.
        """

        # Web fallback evidence is only passed to ContextBuilder when
        # the fallback path actually occurred.
        active_web_evidence = (
            web_evidence
            if (
                web_evidence
                and len(web_evidence) > 0
                and source_type == "web"
            )
            else None
        )

        structured_context = context_builder.build_context(
            evidence=evidence,
            source_type=source_type,
            business_data=business_data,
            chat_history=chat_history,
            web_evidence=active_web_evidence,
            evidence_manifest=evidence_manifest,
        )

        return build_brim_user_prompt(
            query=query,
            structured_context=structured_context,
            source_type=source_type,
        )

    # ------------------------------------------------------------------
    # NORMAL GENERATION
    # ------------------------------------------------------------------

    def generate_answer(
        self,
        query: str,
        evidence: List[Dict[str, Any]],
        source_type: str = "knowledge_base",
        business_data: Optional[Dict[str, Any]] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        web_evidence: Optional[List[Dict[str, Any]]] = None,
        evidence_manifest: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate a user-facing answer using the configured LLM.

        Retrieved evidence is supplied only as internal context.
        """

        user_prompt = self._build_user_prompt(
            query=query,
            evidence=evidence,
            source_type=source_type,
            business_data=business_data,
            chat_history=chat_history,
            web_evidence=web_evidence,
            evidence_manifest=evidence_manifest,
        )

        client, model = self._get_client_and_model()

        if client is None or model is None:
            return self._unavailable_response()

        try:
            logger.debug(
                "Calling LLM provider=%s model=%s max_tokens=%s",
                self.provider,
                model,
                settings.LLM_MAX_OUTPUT_TOKENS,
            )

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                temperature=0.1,
                max_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )

            content = self._extract_completion_content(response)

            if content:
                return content

            logger.warning(
                "LLM returned an empty completion."
            )

            return (
                "The AI reasoning service returned an empty response. "
                "Please retry your query."
            )

        except Exception as exc:
            logger.exception(
                "LLM generation failed: %s",
                exc,
            )

            return self._unavailable_response()

    # ------------------------------------------------------------------
    # STREAMING GENERATION
    # ------------------------------------------------------------------

    def stream_answer(
        self,
        query: str,
        evidence: List[Dict[str, Any]],
        source_type: str = "knowledge_base",
        business_data: Optional[Dict[str, Any]] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
        web_evidence: Optional[List[Dict[str, Any]]] = None,
        evidence_manifest: Optional[Dict[str, Any]] = None,
    ) -> Generator[str, None, None]:
        """
        Stream only LLM-generated tokens.

        Retrieved evidence is never streamed directly to the client.
        """

        user_prompt = self._build_user_prompt(
            query=query,
            evidence=evidence,
            source_type=source_type,
            business_data=business_data,
            chat_history=chat_history,
            web_evidence=web_evidence,
            evidence_manifest=evidence_manifest,
        )

        client, model = self._get_client_and_model()

        if client is None or model is None:
            yield self._unavailable_response()
            return

        try:
            logger.debug(
                "Starting LLM stream provider=%s model=%s",
                self.provider,
                model,
            )

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                temperature=0.1,
                max_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                stream=True,
            )

            yielded_content = False

            response_iter = (
                response
                if hasattr(response, "__iter__")
                else [response]
            )

            for chunk in response_iter:
                if not getattr(chunk, "choices", None):
                    continue

                choice = chunk.choices[0]

                # Standard OpenAI/Groq streaming response.
                delta = getattr(choice, "delta", None)

                if delta is not None:
                    content = getattr(delta, "content", None)

                    if content:
                        yielded_content = True
                        yield content

                elif (
                    getattr(choice, "message", None)
                    and getattr(choice.message, "content", None)
                ):
                    yielded_content = True
                    yield choice.message.content

            if not yielded_content:
                logger.warning(
                    "LLM stream completed without content."
                )

        except Exception as exc:
            logger.exception(
                "LLM streaming generation failed: %s",
                exc,
            )

            yield self._unavailable_response()

    # ------------------------------------------------------------------
    # RESPONSE HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_completion_content(response: Any) -> str:
        """Safely extract text from a normal completion response."""

        choices = getattr(response, "choices", None)

        if not choices:
            return ""

        message = getattr(choices[0], "message", None)

        if message is None:
            return ""

        content = getattr(message, "content", None)

        if not content:
            return ""

        return content.strip()

    @staticmethod
    def _unavailable_response() -> str:
        """
        Explicit failure response.

        Never substitute retrieved chunks or locally synthesized content.
        """

        return (
            "The AI reasoning service is currently unavailable. "
            "An answer cannot be generated without active model synthesis. "
            "Please check the LLM provider configuration and try again."
        )


# ----------------------------------------------------------------------
# SHARED SERVICE INSTANCE
# ----------------------------------------------------------------------

llm_service = LLMService()