import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
venv_site = ROOT_DIR / ".venv" / "Lib" / "site-packages"
if venv_site.exists() and str(venv_site) not in sys.path:
    sys.path.insert(0, str(venv_site))

from backend.app.ingestion.ingest import run_ingestion
from backend.app.database import SessionLocal
from backend.app.models.document import Document
from backend.app.models.chunk import DocumentChunk

def main():
    doc2_dir = ROOT_DIR / "knowledge_base" / "doc2"
    print(f"============================================================")
    print(f"Ingesting documents from: {doc2_dir}")
    print(f"Parameters: chunk_size=600, overlap=200")
    print(f"============================================================")

    start_time = time.time()
    results = run_ingestion(
        doc_dir=doc2_dir,
        target_chunk_size=600,
        chunk_overlap=200,
    )
    elapsed = time.time() - start_time

    print(f"\n============================================================")
    print(f"Ingestion Completed in {elapsed:.1f} seconds")
    print(f"============================================================")
    for r in results:
        print(f"  {r['filename']}: status={r.get('status')} | pages={r.get('page_count', '-')} | chunks={r.get('chunks', '-')}")

    # Database summary
    db = SessionLocal()
    total_docs = db.query(Document).count()
    total_chunks = db.query(DocumentChunk).count()
    print(f"\nDatabase Totals:")
    print(f"  Total Documents in DB: {total_docs}")
    print(f"  Total Chunks in DB: {total_chunks}")
    db.close()

if __name__ == "__main__":
    main()
