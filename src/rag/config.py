"""Configuracao central.

Tudo que e' ajustavel vive aqui, com sobrescrita por variavel de ambiente.
Os valores padrao vem direto dos ADRs do PRD.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Config:
    # --- Indice (ADR-002) ---
    db_path: Path = Path(_env("RAG_DB", "~/.local/share/rag-pessoal/index.sqlite")).expanduser()

    # --- Modelos (ADR-003) ---
    ollama_host: str = _env("RAG_OLLAMA_HOST", "http://127.0.0.1:11434")
    embed_model: str = _env("RAG_EMBED_MODEL", "nomic-embed-text")
    generate_model: str = _env("RAG_GENERATE_MODEL", "llama3.1:8b")
    context_length: int = _env_int("RAG_CONTEXT_LENGTH", 4096)

    # --- Embeddings (ADR-004) ---
    # Dimensao nativa do nomic-embed-text.
    embed_dims_full: int = 768
    # Truncamento Matryoshka para o indice de busca.
    embed_dims_indexed: int = _env_int("RAG_EMBED_DIMS", 256)

    # --- Busca (RF-03) ---
    candidates_per_branch: int = _env_int("RAG_CANDIDATES", 50)
    rescore_top_n: int = _env_int("RAG_RESCORE", 20)
    context_chunks: int = _env_int("RAG_CONTEXT_CHUNKS", 5)
    # Constante do Reciprocal Rank Fusion. 60 e' o valor do paper original.
    rrf_k: int = _env_int("RAG_RRF_K", 60)

    # --- Ingestao ---
    embed_batch_size: int = _env_int("RAG_EMBED_BATCH", 32)

    # --- Servidor (RNF-01) ---
    host: str = "127.0.0.1"
    port: int = _env_int("RAG_PORT", 8080)


CONFIG = Config()
