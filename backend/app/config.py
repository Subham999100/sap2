import os
from pathlib import Path
from typing import Any
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # App
    PROJECT_NAME: str = "SAP Knowledge Assistant"
    API_V1_STR: str = "/api"
    DEBUG: bool = False

    # DATABASE_URL points to PostgreSQL. SQLite fallback removed for strict PostgreSQL usage.
    # DATABASE_URL is set via environment variable or defaults to the local PostgreSQL instance.
    # Example: postgresql://user:pass@localhost:5432/dbname
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5434/sap_assistant"
    )

    # Environment & Security Enforcement
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()

    # JWT Security
    JWT_SECRET: str = os.getenv("JWT_SECRET", "sap-super-secret-jwt-key-2026-production-ready")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # Admin Key for Developer Ingestion
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "sap-admin-dev-secret-key-999")

    # LLM Settings
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")  # openai, groq, or local
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    LLM_MAX_OUTPUT_TOKENS: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "4000"))

    # Embedding & RAG parameters
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local")  # "local" or "openai"
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    # Default dimension: 384 for local, 1536 for OpenAI provider
    EMBEDDING_DIM: int = int(os.getenv(
        "EMBEDDING_DIM",
        "1536" if os.getenv("EMBEDDING_PROVIDER", "local") == "openai" else "384",
    ))
    TOP_K: int = int(os.getenv("TOP_K", "30"))
    RRF_CANDIDATE_K: int = int(os.getenv("RRF_CANDIDATE_K", os.getenv("TOP_K", "30")))
    FINAL_TOP_K: int = int(os.getenv("FINAL_TOP_K", "5"))
    MAX_RETRIEVAL_FACETS: int = int(os.getenv("MAX_RETRIEVAL_FACETS", "14"))
    SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.60"))
    GROUNDING_THRESHOLD: float = float(os.getenv("GROUNDING_THRESHOLD", "0.65"))

    # Web Fallback
    WEB_FALLBACK_ENABLED: bool = os.getenv("WEB_FALLBACK_ENABLED", "True").lower() in ("true", "1")
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", os.getenv("WEB_SEARCH_API_KEY", ""))
    WEB_SEARCH_API_KEY: str = os.getenv("WEB_SEARCH_API_KEY", os.getenv("TAVILY_API_KEY", ""))

    # Canonical Knowledge Base Path (at repo root if exists, otherwise backend)
    DOCUMENTS_DIR: Path = (ROOT_DIR / "knowledge_base" / "documents") if (ROOT_DIR / "knowledge_base" / "documents").exists() else (BASE_DIR / "knowledge_base" / "documents")

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / "backend" / ".env"),
        extra="allow"
    )

    def model_post_init(self, __context: Any) -> None:
        # Configuration Security: Fail closed in production if insecure fallback secrets are used
        if self.ENVIRONMENT in ("production", "prod"):
            insecure_defaults = {
                "sap-super-secret-jwt-key-2026-production-ready",
                "sap-admin-dev-secret-key-999",
                "secret",
                "changeme",
            }
            if not self.JWT_SECRET or self.JWT_SECRET in insecure_defaults:
                raise ValueError("JWT_SECRET must be configured with a secure non-default secret in production.")
            if not self.ADMIN_API_KEY or self.ADMIN_API_KEY in insecure_defaults:
                raise ValueError("ADMIN_API_KEY must be configured with a secure non-default secret in production.")

settings = Settings()

