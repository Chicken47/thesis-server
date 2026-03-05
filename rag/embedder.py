"""
Custom ChromaDB embedding function that uses Ollama's nomic-embed-text model.
This avoids loading PyTorch/sentence-transformers separately — Ollama is already running.
"""

import ollama
from chromadb import EmbeddingFunction, Embeddings

EMBED_MODEL = "nomic-embed-text"


class OllamaEmbeddingFunction(EmbeddingFunction):
    """ChromaDB-compatible embedding function backed by Ollama."""

    def __init__(self, model: str = EMBED_MODEL):
        self.model = model

    def __call__(self, input: list[str]) -> Embeddings:
        embeddings = []
        for text in input:
            response = ollama.embeddings(model=self.model, prompt=text)
            embeddings.append(response["embedding"])
        return embeddings
