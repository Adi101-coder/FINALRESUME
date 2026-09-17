from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path

import faiss
import numpy as np

from app.config import settings

logger = logging.getLogger("app.embeddings")

DIM = 384

MODEL_LOAD_TIMEOUT_SEC = 15


class EmbeddingService:
    """Candidate knowledge base using FAISS. Uses sentence-transformers when available."""

    def __init__(self) -> None:
        self._model = None
        self._use_fallback = settings.use_embedding_fallback
        self._index: faiss.IndexFlatIP | None = None
        self._id_map: dict[int, int] = {}
        self._meta_path = settings.vector_index_path / "metadata.json"
        self._index_path = settings.vector_index_path / "index.faiss"

    def _try_load_model(self):
        logger.info("Loading embedding model: %s", settings.embedding_model)
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(settings.embedding_model)

    @property
    def model(self):
        if self._use_fallback:
            return None
        if self._model is None:
            try:
                result = [None]
                exc_holder = [None]
                def _load():
                    try:
                        result[0] = self._try_load_model()
                    except Exception as e:
                        exc_holder[0] = e
                t = threading.Thread(target=_load, daemon=True)
                t.start()
                t.join(timeout=MODEL_LOAD_TIMEOUT_SEC)
                if t.is_alive():
                    logger.warning(
                        "Embedding model load timed out after %ds, using fallback",
                        MODEL_LOAD_TIMEOUT_SEC,
                    )
                    self._use_fallback = True
                elif exc_holder[0]:
                    logger.warning("Embedding model unavailable, using fallback: %s", exc_holder[0])
                    self._use_fallback = True
                else:
                    self._model = result[0]
                    logger.info("Embedding model loaded")
            except Exception as exc:
                logger.warning("Embedding model load failed, using fallback: %s", exc)
                self._use_fallback = True
        return self._model

    def _load(self) -> None:
        if self._index is not None:
            return
        dim = self.model.get_sentence_embedding_dimension() if self.model else DIM
        if self._index_path.exists():
            logger.debug("Loading FAISS index from disk")
            self._index = faiss.read_index(str(self._index_path))
            if self._meta_path.exists():
                self._id_map = {
                    int(k): int(v) for k, v in json.loads(self._meta_path.read_text()).items()
                }
        else:
            logger.debug("Creating new FAISS index (dim=%s)", dim)
            self._index = faiss.IndexFlatIP(dim)

    def _save(self) -> None:
        if self._index is None:
            return
        faiss.write_index(self._index, str(self._index_path))
        self._meta_path.write_text(json.dumps({str(k): v for k, v in self._id_map.items()}))

    def _fallback_embed(self, text: str) -> np.ndarray:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        vector = np.zeros(DIM, dtype=np.float32)
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            for i in range(DIM):
                vector[i] += (digest[i % len(digest)] - 128) / 128.0
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector /= norm
        return vector.reshape(1, -1)

    def embed_text(self, text: str) -> np.ndarray:
        if self.model:
            logger.debug("Embedding text (%d chars) via model", len(text))
            vector = self.model.encode([text], normalize_embeddings=True)
            return np.array(vector, dtype=np.float32)
        logger.debug("Embedding text (%d chars) via hash fallback", len(text))
        return self._fallback_embed(text)

    def upsert_candidate(self, candidate_id: int, text: str) -> int:
        self._load()
        vector = self.embed_text(text)
        faiss_id = len(self._id_map)
        self._index.add(vector)
        self._id_map[faiss_id] = candidate_id
        self._save()
        logger.info("FAISS upsert: candidate_id=%s faiss_id=%s", candidate_id, faiss_id)
        return faiss_id

    def search(self, query: str, top_k: int = 5) -> list[tuple[int, float]]:
        self._load()
        if not self._id_map:
            return []
        vector = self.embed_text(query)
        scores, indices = self._index.search(vector, min(top_k, len(self._id_map)))
        results: list[tuple[int, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            candidate_id = self._id_map.get(int(idx))
            if candidate_id is not None:
                results.append((candidate_id, float(score)))
        return results


def build_profile_document(profile: dict) -> str:
    parts = [
        profile.get("name", ""),
        profile.get("summary", ""),
        "Skills: " + ", ".join(profile.get("skills", [])),
        "Keywords: " + ", ".join(profile.get("keywords", [])),
    ]
    for exp in profile.get("experience", []):
        parts.append(
            f"{exp.get('role', '')} at {exp.get('company', '')}: "
            + " ".join(exp.get("bullets", []))
        )
    for project in profile.get("projects", []):
        parts.append(
            f"Project {project.get('name', '')}: {project.get('description', '')} "
            + ", ".join(project.get("technologies", []))
        )
    return "\n".join(p for p in parts if p.strip())
