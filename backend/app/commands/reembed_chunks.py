import logging
import time
from typing import List

from backend.app.database import SessionLocal
from backend.app.services.embedding_service import embedding_service
from backend.app.models.chunk import DocumentChunk
from backend.app.config import settings

logger = logging.getLogger(__name__)

# Configuration defaults (can be overridden via environment variables)
BATCH_SIZE = int(getattr(settings, "REEMBED_BATCH_SIZE", 64))
MAX_RETRIES = int(getattr(settings, "REEMBED_MAX_RETRIES", 3))
RETRY_BACKOFF = int(getattr(settings, "REEMBED_RETRY_BACKOFF_SECONDS", 5))
TARGET_DIM = 1536  # OpenAI text-embedding-3-small dimensions


def _fetch_batch(session, last_id: int | None, limit: int) -> List[DocumentChunk]:
    """Fetch a batch of chunks that need re‑embedding using keyset pagination.

    Retrieves rows where ``embedding_new`` is NULL and whose ``id`` is greater than
    ``last_id`` (if provided). Results are ordered by ``id`` to ensure a stable
    traversal order.
    """
    query = session.query(DocumentChunk).filter(DocumentChunk.embedding_new == None)  # noqa: E711
    if last_id is not None:
        query = query.filter(DocumentChunk.id > str(last_id))
    return (
        query
        .order_by(DocumentChunk.id)
        .limit(limit)
        .all()
    )


def _is_valid_vector(vec: List[float]) -> bool:
    return isinstance(vec, list) and len(vec) == TARGET_DIM


def reembed_chunks(batch_size: int = BATCH_SIZE, dry_run: bool = False) -> dict:
    """Re‑embed all existing chunks using the OpenAI provider.

    Returns a summary dict with counts of total, processed, skipped and failed.
    """
    logger.info("Starting re‑embedding job (dry_run=%s, batch_size=%s)", dry_run, batch_size)
    session = SessionLocal()
    try:
        total = session.query(DocumentChunk).count()
        processed = 0
        skipped = 0
        failed = 0
        last_id = None
        while True:
            batch = _fetch_batch(session, last_id, batch_size)
            if not batch:
                break
            # Determine which chunks truly need embedding
            to_process = []
            for chunk in batch:
                if chunk.embedding_new is not None and _is_valid_vector(chunk.embedding_new):
                    skipped += 1
                    continue
                to_process.append(chunk)
            if not to_process:
                # Advance last_id to the highest id in this batch
                last_id = batch[-1].id
            if dry_run:
                processed += len(to_process)
                last_id = batch[-1].id
                continue

            texts = [c.chunk_text for c in to_process]
            attempt = 0
            while True:
                try:
                    vectors = embedding_service.embed_batch(texts, provider="openai", target_dim=TARGET_DIM)
                    break
                except Exception as e:
                    attempt += 1
                    if attempt > MAX_RETRIES:
                        logger.error("Batch failed after %s retries: %s", MAX_RETRIES, e)
                        failed += len(to_process)
                        vectors = []
                        break
                    logger.warning(
                        "Embedding batch error (attempt %s): %s – retrying in %s seconds",
                        attempt,
                        e,
                        RETRY_BACKOFF,
                    )
                    time.sleep(RETRY_BACKOFF)
            batch_updated = False
            for chunk, vec in zip(to_process, vectors):
                if not _is_valid_vector(vec):
                    logger.error("Invalid vector dimension for chunk %s", chunk.id)
                    failed += 1
                    continue
                if not dry_run:
                    chunk.embedding_new = vec
                    batch_updated = True
                processed += 1
            if not dry_run and batch_updated:
                session.commit()
            # Advance last_id by the highest id processed in this batch
            last_id = batch[-1].id
            logger.info("Progress: processed=%d, skipped=%d, failed=%d (total=%d)", processed, skipped, failed, total)
        summary = {
            "total_chunks": total,
            "processed": processed,
            "skipped": skipped,
            "failed": failed,
        }
        logger.info("Re‑embedding job complete: %s", summary)
        return summary
    finally:
        session.close()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Re‑embed all document chunks with OpenAI embeddings.")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Number of chunks per API request.")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing to the database.")
    args = parser.parse_args()
    res = reembed_chunks(batch_size=args.batch_size, dry_run=args.dry_run)
    print("Summary:", res)
