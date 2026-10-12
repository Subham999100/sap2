
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.rag import RAGQueryRequest, RAGQueryResponse
from backend.app.services.rag_service import rag_service
from backend.app.models.user import User
from backend.app.models.chat import Chat
from backend.app.security.auth import get_current_user


# Create a logger for recording errors internally.
# Avoid returning internal exception details to API clients.
logger = logging.getLogger(__name__)


# Group all RAG endpoints under the /rag prefix.
# The tags setting groups these endpoints in Swagger documentation.
router = APIRouter(
    prefix="/rag",
    tags=["RAG"]
)


@router.post(
    "/query",
    response_model=RAGQueryResponse,
    status_code=status.HTTP_200_OK
)
def query_rag(
    # FastAPI validates the incoming JSON against RAGQueryRequest.
    request: RAGQueryRequest,

    # Resolve the authenticated user through the existing auth dependency.
    current_user: User = Depends(get_current_user),

    # Obtain a database session through the application's dependency.
    db: Session = Depends(get_db)
):
    """
    Process an authenticated user's SAP BRIM question through the RAG service.

    Steps:
    1. Validate chat ownership when a chat ID is supplied.
    2. Execute the RAG pipeline.
    3. Return the result using the response schema.
    """

    # Step 1: Verify chat ownership.
    # A user must not access another user's conversation by guessing its ID.
    if request.chat_id is not None:
        chat = (
            db.query(Chat)
            .filter(
                Chat.id == request.chat_id,
                Chat.user_id == current_user.id
            )
            .first()
        )

        # Return 404 if the chat does not exist or does not belong to this user.
        # Using the same response avoids revealing whether another user's chat exists.
        if chat is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Chat not found"
            )

    # Step 2: Execute the RAG pipeline.
    try:
        result = rag_service.process_query(
            db=db,
            query=request.query,
            chat_id=request.chat_id,
            user_id=current_user.id
        )

    except HTTPException:
        # Preserve intentional HTTP errors raised by the service.
        raise

    except Exception:
        # Record the full exception and traceback in server logs.
        # Do not expose internal errors, SQL details, or other implementation
        # information to the client.
        logger.exception(
            "RAG query processing failed for user_id=%s",
            current_user.id
        )

        # Roll back any pending transaction if the session is in a failed state.
        # Transaction ownership should also be handled consistently by the service
        # and the get_db dependency.
        try:
            db.rollback()
        except Exception:
            logger.exception(
                "Database rollback failed for user_id=%s",
                current_user.id
            )

        # Return a generic error message to the client.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing your query."
        )

    # Step 3: Construct the response using the defined Pydantic schema.
    # FastAPI validates and serializes the returned object against this schema.
    return RAGQueryResponse(
        answer=result["answer"],
        source_type=result["source_type"],
        grounding_score=result["grounding_score"],
        citations=result["citations"],
        web_sources=result["web_sources"],
        chat_id=result.get("chat_id"),
        message_id=result.get("message_id")
    )