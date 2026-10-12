import logging
import hashlib


import numpy as np
from typing import List, Dict
from backend.app.config import settings

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self):
        self._model = None
        self._model_failed = False
        self.embedding_dim = settings.EMBEDDING_DIM
        self._cache: Dict[str, List[float]] = {}
        self._max_cache_size: int = 512

    def _get_model(self):
        if self._model is not None:
            return self._model
        if self._model_failed:
            return None
        # Try FastEmbed ONNX runtime first (robust, fast, no AppLocker/torch DLL block)
        try:
            from fastembed import TextEmbedding
            logger.info("Loading FastEmbed model: sentence-transformers/all-MiniLM-L6-v2")
            self._model = ("fastembed", TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2"))
            logger.info("FastEmbed ONNX model loaded successfully.")
            return self._model
        except Exception as fe_err:
            logger.info(f"FastEmbed not available ({fe_err}), trying SentenceTransformer...")

        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
            self._model = ("st", SentenceTransformer(settings.EMBEDDING_MODEL))
            logger.info("SentenceTransformer model loaded successfully.")
            return self._model
        except Exception as e:
            logger.warning(f"Could not load SentenceTransformer ({e}). Using robust fallback embedding generator.")
            self._model_failed = True
            return None

    def _fallback_embed(self, text: str, dim: int | None = None) -> List[float]:
        """
        Deterministic, normalized dense projection fallback
        ensuring cosine similarity between semantically overlapping texts is high
        even without a live network connection to HuggingFace.
        """
        d = dim or getattr(self, "embedding_dim", settings.EMBEDDING_DIM)
        vec = np.zeros(d, dtype=np.float32)
        words = text.lower().split()
        if not words:
            return vec.tolist()

        for word in words:
            # Hash word into 3 bucket dimensions with sign
            h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
            for i in range(3):
                idx = (h >> (i * 10)) % d
                sign = 1.0 if ((h >> (i * 10 + 9)) & 1) else -1.0
                vec[idx] += sign

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    def embed_text(self, text: str, provider: str | None = None, target_dim: int | None = None) -> List[float]:
        cleaned = text.strip()
        chosen_provider = provider or settings.EMBEDDING_PROVIDER
        expected_dim = target_dim or (1536 if chosen_provider == "openai" else settings.EMBEDDING_DIM)

        if not cleaned:
            return [0.0] * expected_dim

        cache_key = f"{chosen_provider}_{expected_dim}_{cleaned}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        res: List[float]

        # OpenAI provider handling
        if chosen_provider == "openai":
            try:
                from openai import OpenAI
                client = OpenAI(api_key=settings.OPENAI_API_KEY)
                response = client.embeddings.create(
                    model=settings.OPENAI_EMBEDDING_MODEL,
                    input=[cleaned],
                )
                emb = response.data[0].embedding
                emb = np.array(emb, dtype=np.float32)
                if emb.shape[0] != expected_dim:
                    raise RuntimeError(
                        f"OpenAI embedding dimension {emb.shape[0]} does not match expected {expected_dim}"
                    )
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                res = emb.tolist()
            except Exception as e:
                raise RuntimeError(f"OpenAI embedding request failed: {e}")

        # Existing local providers
        else:
            model_info = self._get_model()
            if model_info:
                tag, model = model_info
                try:
                    if tag == "fastembed":
                        emb = list(model.embed([cleaned]))[0]
                    else:
                        emb = model.encode(cleaned, convert_to_numpy=True)
                    emb = np.array(emb, dtype=np.float32)
                    if emb.shape[0] != expected_dim:
                        raise RuntimeError(
                            f"Local embedding dimension {emb.shape[0]} does not match expected EMBEDDING_DIM={expected_dim}"
                        )
                    norm = np.linalg.norm(emb)
                    if norm > 0:
                        emb = emb / norm
                    res = emb.tolist()
                except Exception as e:
                    logger.warning(f"Embedding encoding error ({tag}): {e}. Falling back.")
                    res = self._fallback_embed(cleaned, dim=expected_dim)
            else:
                res = self._fallback_embed(cleaned, dim=expected_dim)

        if len(self._cache) >= self._max_cache_size:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = res
        return res

    def embed_batch(self, texts: List[str], provider: str | None = None, target_dim: int | None = None) -> List[List[float]]:
        if not texts:
            return []
        cleaned_texts = [t.strip() for t in texts]

        # OpenAI provider handling for batch embeddings
        chosen_provider = provider or settings.EMBEDDING_PROVIDER
        if chosen_provider == "openai":
            expected_dim = target_dim or (1536 if provider == "openai" else self.embedding_dim)
            try:
                from openai import OpenAI
                client = OpenAI(api_key=settings.OPENAI_API_KEY)
                response = client.embeddings.create(
                    model=settings.OPENAI_EMBEDDING_MODEL,
                    input=cleaned_texts,
                )
                result = []
                for datum in response.data:
                    emb = np.array(datum.embedding, dtype=np.float32)
                    if emb.shape[0] != expected_dim:
                        raise RuntimeError(
                            f"OpenAI batch embedding dimension {emb.shape[0]} does not match expected {expected_dim}"
                        )
                    norm = np.linalg.norm(emb)
                    if norm > 0:
                        emb = emb / norm
                    result.append(emb.tolist())
                return result
            except Exception as e:
                raise RuntimeError(f"OpenAI batch embedding request failed: {e}")

        model_info = self._get_model()
        if model_info:
            tag, model = model_info
            try:
                if tag == "fastembed":
                    embs_gen = model.embed(cleaned_texts, batch_size=64)
                    result = []
                    for emb in embs_gen:
                        emb = np.array(emb, dtype=np.float32)
                        if emb.shape[0] != self.embedding_dim:
                            raise RuntimeError(
                                f"FastEmbed batch embedding dimension {emb.shape[0]} does not match EMBEDDING_DIM={self.embedding_dim}"
                            )
                        norm = np.linalg.norm(emb)
                        if norm > 0:
                            emb = emb / norm
                        result.append(emb.tolist())
                    return result
                else:
                    embs = model.encode(cleaned_texts, batch_size=32, convert_to_numpy=True)
                    result = []
                    for emb in embs:
                        norm = np.linalg.norm(emb)
                        if norm > 0:
                            emb = emb / norm
                        result.append(emb.tolist())
                    return result
            except Exception as e:
                logger.warning(f"Batch embedding error ({tag}): {e}. Falling back.")

        return [self._fallback_embed(t) for t in cleaned_texts]

embedding_service = EmbeddingService()
