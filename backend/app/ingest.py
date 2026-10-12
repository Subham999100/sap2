import sys
from backend.app.ingestion.ingest import run_ingestion, logger

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    logger.info(f"Starting Developer Ingestion Pipeline{' for ' + target if target else ''}...")
    res = run_ingestion(target_filename=target)
    logger.info(f"Ingestion finished: {res}")
