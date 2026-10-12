import os
import sys
import json
import time
import logging
from pathlib import Path

# Ensure project root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
venv_site = ROOT_DIR / ".venv" / "Lib" / "site-packages"
if venv_site.exists() and str(venv_site) not in sys.path:
    sys.path.insert(0, str(venv_site))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("recover_doc2")

from backend.app.config import settings
from backend.app.database import SessionLocal, init_db
from backend.app.models.document import Document
from backend.app.models.chunk import DocumentChunk
from backend.app.services.document_service import document_service
from backend.app.services.embedding_service import embedding_service

TARGET_FILES = [
    "ah.pdf",
    "BR245_EN_Col23_CO_A4.pdf",
    "cg.pdf",
    "pdf 1.pdf",
    "SAP BRIM AI Architect.pdf",
    "SAP_BRIM_Architect_Knowledge.md",
]

EXCLUDE_FILES = [
    "SAP BRIM AI Architect (1).pdf",
]

TARGET_CHUNK_SIZE = 600
CHUNK_OVERLAP = 200

def run_recovery():
    init_db()
    db = SessionLocal()

    doc2_dir = ROOT_DIR / "knowledge_base" / "doc2"
    if not doc2_dir.exists():
        logger.error(f"Target directory {doc2_dir} does not exist!")
        return

    logger.info("============================================================")
    logger.info("Starting SAP BRIM doc2 Ingestion Recovery")
    logger.info(f"Target directory: {doc2_dir}")
    logger.info(f"Chunk size: {TARGET_CHUNK_SIZE} chars, Overlap: {CHUNK_OVERLAP} chars")
    logger.info(f"Target files: {TARGET_FILES}")
    logger.info(f"Excluded files: {EXCLUDE_FILES}")
    logger.info("============================================================")

    # Pre-load FastEmbed model for 384-dim dual embedding
    fe_model = None
    try:
        from fastembed import TextEmbedding
        fe_model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
        logger.info("FastEmbed model initialized for 384-dim vector column.")
    except Exception as fe_err:
        logger.warning(f"FastEmbed not loaded: {fe_err}")

    results = []
    start_total_time = time.time()

    for filename in TARGET_FILES:
        file_path = doc2_dir / filename
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            results.append({"filename": filename, "status": "failed", "error": "file_not_found"})
            continue

        file_hash = document_service.compute_file_hash(file_path)
        doc_info = document_service.get_document_info(filename)

        logger.info(f"\n--- Processing: '{filename}' ---")
        t0 = time.time()

        # Step 1: Extract pages/sections
        pages = document_service.extract_document_pages(file_path)
        page_count = len(pages)
        logger.info(f"Extracted {page_count} pages/sections from '{filename}'.")

        # Step 2: Chunk all pages into memory with target_chunk_size=600 and chunk_overlap=200
        all_chunk_dicts = []
        for page_data in pages:
            chunks = document_service.chunk_page_text(
                text=page_data["text"],
                page_number=page_data["page_number"],
                document_name=filename,
                active_section=page_data.get("active_section", "Overview"),
                target_chunk_size=TARGET_CHUNK_SIZE,
                chunk_overlap=CHUNK_OVERLAP,
            )
            all_chunk_dicts.extend(chunks)

        total_extracted_chunks = len(all_chunk_dicts)
        logger.info(f"Generated {total_extracted_chunks} chunks (size={TARGET_CHUNK_SIZE}, overlap={CHUNK_OVERLAP}).")

        # Step 3: Safety check - ensure replacement chunks exist before mutating DB
        if total_extracted_chunks == 0:
            logger.error(f"No chunks extracted from '{filename}'. Aborting mutation for this file.")
            results.append({"filename": filename, "status": "failed", "error": "zero_chunks_extracted"})
            continue

        # Step 4: Handle database document record and atomic chunk replacement
        existing_doc = db.query(Document).filter(Document.filename == filename).first()
        if existing_doc:
            deleted_count = db.query(DocumentChunk).filter(DocumentChunk.document_id == existing_doc.id).delete()
            existing_doc.title = doc_info["title"]
            existing_doc.file_hash = file_hash
            existing_doc.page_count = page_count
            db.commit()
            doc_record = existing_doc
            logger.info(f"Updated document record ID {doc_record.id}. Purged {deleted_count} previous chunks safely.")
        else:
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
            logger.info(f"Created new document record ID {doc_record.id}.")

        # Step 5: Batch embed and insert into document_chunks
        batch_size = 100
        total_inserted = 0

        for start_idx in range(0, total_extracted_chunks, batch_size):
            chunk_batch = all_chunk_dicts[start_idx : start_idx + batch_size]
            texts_to_embed = [c["chunk_text"] for c in chunk_batch]

            # 1536-dim OpenAI embedding
            embeddings_1536 = embedding_service.embed_batch(texts_to_embed)

            # 384-dim FastEmbed embedding
            embs_384 = None
            if fe_model:
                try:
                    embs_384 = [list(e) for e in fe_model.embed(texts_to_embed)]
                except Exception as err:
                    logger.debug(f"FastEmbed batch error: {err}")
                    embs_384 = None

            for local_idx, chunk_data in enumerate(chunk_batch):
                global_idx = start_idx + local_idx
                vec_1536 = embeddings_1536[local_idx]
                emb_384 = embs_384[local_idx] if embs_384 else ([0.0] * 384)

                chunk_record = DocumentChunk(
                    document_id=doc_record.id,
                    document_name=filename,
                    chunk_index=global_idx,
                    page_number=chunk_data["page_number"],
                    section=chunk_data["section"],
                    chunk_text=chunk_data["chunk_text"],
                    metadata_json=json.dumps(chunk_data["metadata"]),
                    embedding=emb_384,
                    embedding_new=vec_1536,
                )
                db.add(chunk_record)
                total_inserted += 1

            db.commit()
            logger.info(f"  Inserted batch [{start_idx + len(chunk_batch)}/{total_extracted_chunks}] chunks into DB.")

        t_elapsed = time.time() - t0
        logger.info(f"Successfully recovered '{filename}': {page_count} pages, {total_inserted} chunks in {t_elapsed:.1f}s.")
        results.append({
            "filename": filename,
            "status": "success",
            "pages": page_count,
            "chunks": total_inserted,
            "time_sec": round(t_elapsed, 1),
        })

    total_time = time.time() - start_total_time
    logger.info("\n============================================================")
    logger.info(f"Recovery Pipeline Completed in {total_time:.1f} seconds")
    logger.info("============================================================")
    for r in results:
        logger.info(f"  {r['filename']}: status={r['status']} | pages={r.get('pages', '-')} | chunks={r.get('chunks', '-')} | time={r.get('time_sec', '-')}s")

    # Final DB Verification
    all_docs = db.query(Document).order_by(Document.filename).all()
    all_chunks_count = db.query(DocumentChunk).count()
    null_1536 = db.query(DocumentChunk).filter(DocumentChunk.embedding_new == None).count()
    null_384 = db.query(DocumentChunk).filter(DocumentChunk.embedding == None).count()

    print("\n--- FINAL DATABASE TOTALS ---")
    print(f"Total documents: {len(all_docs)}")
    print(f"Total chunks: {all_chunks_count}")
    print(f"Chunks with null 1536-dim embedding: {null_1536}")
    print(f"Chunks with null 384-dim embedding: {null_384}")

    db.close()

if __name__ == "__main__":
    run_recovery()
