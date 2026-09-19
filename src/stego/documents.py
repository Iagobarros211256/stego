"""Formato intermediario entre parsers e chunkers.

Todo parser devolve um Document. Acrescentar uma fonte nova ao corpus
significa escrever um parser; nada mais do pipeline muda.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Diretorios cujo conteudo e' marcado como restrito (ADR-006).
# Casamento por nome de componente do caminho, nao por substring, para
# que "financeiro" case mas "refinanceiramento" nao.
RESTRICTED_DIR_NAMES = {
    d.strip().lower()
    for d in os.environ.get(
        "STEGO_RESTRICTED_DIRS", "financeiro,financas,documentos-pessoais,pessoal"
    ).split(",")
    if d.strip()
}


@dataclass
class Document:
    source_path: str
    doc_type: str
    content: str
    content_hash: str
    modified_at: datetime | None = None
    sensitivity: str = "normal"
    title: str | None = None
    extra: dict = field(default_factory=dict)


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def detect_sensitivity(path: Path) -> str:
    """Restrito se qualquer diretorio do caminho estiver na lista."""
    parts = {part.lower() for part in path.parts[:-1]}
    return "restricted" if parts & RESTRICTED_DIR_NAMES else "normal"


def modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
