import os
import sys
import logging
from pathlib import Path
from typing import Dict, Any, List
from sqlalchemy.orm import Session

# Ensure project root is on sys.path if invoked directly
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.models.document import Document
from backend.app.models.chunk import DocumentChunk
from backend.app.services.document_service import document_service
from backend.app.services.embedding_service import embedding_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ingestion")

def run_ingestion(
    db: Session = None,
    target_filename: str = None,
    doc_dir: Path = None,
    target_chunk_size: int = 1200,
    chunk_overlap: int = 200,
    force_reingest: bool = False,
) -> List[Dict[str, Any]]:
    should_close_db = False
    if db is None:
        init_db()
        db = SessionLocal()
        should_close_db = True

    results = []
    active_dir = Path(doc_dir) if doc_dir else settings.DOCUMENTS_DIR
    if not active_dir.exists():
        logger.warning(f"Documents directory does not exist: {active_dir}. Creating it.")
        active_dir.mkdir(parents=True, exist_ok=True)
        return results

    doc_files = sorted(list(active_dir.glob("*.pdf")) + list(active_dir.glob("*.md")))
    logger.info(f"Discovered {len(doc_files)} documents in {active_dir}")

    if target_filename:
        doc_files = [p for p in doc_files if p.name == target_filename]
        logger.info(f"Targeting specific document '{target_filename}': {len(doc_files)} matched.")

    # Initialize FastEmbed model for 384-dim vector column if available
    fe_model = None
    try:
        from fastembed import TextEmbedding
        fe_model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
    except Exception as fe_err:
        logger.debug(f"FastEmbed not loaded for dual-embedding: {fe_err}")

    for doc_path in doc_files:
        filename = doc_path.name
        file_hash = document_service.compute_file_hash(doc_path)

        # Idempotency check: check if document already exists with same filename and hash AND has chunks
        existing_doc = db.query(Document).filter(Document.filename == filename).first()
        if existing_doc and existing_doc.file_hash == file_hash and not force_reingest:
            existing_chunks_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == existing_doc.id).count()
            if existing_chunks_count > 0:
                logger.info(f"Skipping '{filename}': already ingested with identical checksum ({existing_chunks_count} chunks).")
                results.append({
                    "filename": filename,
                    "status": "skipped",
                    "reason": "already_up_to_date",
                    "chunks": existing_chunks_count
                })
                continue
            else:
                logger.warning(f"Document '{filename}' exists in database but has 0 chunks. Re-ingesting chunks...")

        # Duplicate detection: check if byte-identical file is already ingested under a different name
        duplicate_hash_doc = db.query(Document).filter(Document.file_hash == file_hash).first()
        if duplicate_hash_doc and duplicate_hash_doc.filename != filename:
            dup_chunks_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == duplicate_hash_doc.id).count()
            if dup_chunks_count > 0:
                logger.info(f"Skipping duplicate file '{filename}': byte-identical to already ingested '{duplicate_hash_doc.filename}' (hash: {file_hash[:12]}...).")
                results.append({
                    "filename": filename,
                    "status": "skipped",
                    "reason": f"duplicate_of_{duplicate_hash_doc.filename}",
                    "chunks": 0
                })
                continue

        logger.info(f"Processing document: '{filename}' (chunk_size={target_chunk_size}, overlap={chunk_overlap}) ...")
        pages = document_service.extract_document_pages(doc_path)
        page_count = len(pages)

        # If document already exists, remove old chunks and refresh metadata
        if existing_doc:
            logger.info(f"Updating/re-populating existing document record for '{filename}'.")
            db.query(DocumentChunk).filter(DocumentChunk.document_id == existing_doc.id).delete()
            doc_info = document_service.get_document_info(filename)
            existing_doc.title = doc_info["title"]
            existing_doc.file_hash = file_hash
            existing_doc.page_count = page_count
            db.commit()
            doc_record = existing_doc
        else:
            doc_info = document_service.get_document_info(filename)
            doc_record = Document(
                filename=filename,
                title=doc_info["title"],
                file_hash=file_hash,
                version="1.0",
                page_count=page_count
            )
            db.add(doc_record)
            db.commit()
            db.refresh(doc_record)

        # Process chunks
        total_chunks = 0
        all_chunk_dicts = []
        for page_data in pages:
            page_num = page_data["page_number"]
            page_text = page_data["text"]
            active_section = page_data.get("active_section", "Overview")

            chunks = document_service.chunk_page_text(
                text=page_text,
                page_number=page_num,
                document_name=filename,
                active_section=active_section,
                target_chunk_size=target_chunk_size,
                chunk_overlap=chunk_overlap,
            )
            all_chunk_dicts.extend(chunks)

        # Batch embed chunk texts in blocks of 100 for memory safety on large PDFs
        if all_chunk_dicts:
            import json
            batch_size = 100
            for start_idx in range(0, len(all_chunk_dicts), batch_size):
                chunk_batch = all_chunk_dicts[start_idx : start_idx + batch_size]
                texts_to_embed = [c["chunk_text"] for c in chunk_batch]
                embeddings = embedding_service.embed_batch(texts_to_embed)

                embs_384 = None
                if fe_model:
                    try:
                        embs_384 = [list(e) for e in fe_model.embed(texts_to_embed)]
                    except Exception as err:
                        logger.debug(f"FastEmbed batch encoding error: {err}")
                        embs_384 = None

                for local_idx, chunk_data in enumerate(chunk_batch):
                    global_idx = start_idx + local_idx
                    vec = embeddings[local_idx]
                    if len(vec) == 1536:
                        emb_384 = embs_384[local_idx] if embs_384 else ([0.0] * 384)
                        emb_1536 = vec
                    else:
                        emb_384 = vec
                        emb_1536 = None

                    chunk_record = DocumentChunk(
                        document_id=doc_record.id,
                        document_name=filename,
                        chunk_index=global_idx,
                        page_number=chunk_data["page_number"],
                        section=chunk_data["section"],
                        chunk_text=chunk_data["chunk_text"],
                        metadata_json=json.dumps(chunk_data["metadata"]),
                        embedding=emb_384,
                        embedding_new=emb_1536,
                    )
                    db.add(chunk_record)
                    total_chunks += 1

                db.commit()
        logger.info(f"Successfully ingested '{filename}': {page_count} pages, {total_chunks} chunks.")
        results.append({
            "filename": filename,
            "status": "ingested",
            "page_count": page_count,
            "chunks": total_chunks
        })

    if should_close_db:
        db.close()

    return results

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ingest documents into SAP Knowledge Assistant")
    parser.add_argument("--target", type=str, default=None, help="Specific file to ingest")
    parser.add_argument("--dir", type=str, default=None, help="Directory containing documents")
    parser.add_argument("--chunk-size", type=int, default=1200, help="Target chunk character size")
    parser.add_argument("--chunk-overlap", type=int, default=200, help="Chunk character overlap")
    args = parser.parse_args()

    doc_dir_path = Path(args.dir) if args.dir else None
    logger.info(f"Starting Developer Ingestion Pipeline for dir={doc_dir_path or settings.DOCUMENTS_DIR}, target={args.target}, chunk_size={args.chunk_size}, overlap={args.chunk_overlap}...")
    res = run_ingestion(
        target_filename=args.target,
        doc_dir=doc_dir_path,
        target_chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    logger.info(f"Ingestion finished: {res}")
