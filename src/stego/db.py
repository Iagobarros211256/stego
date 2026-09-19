"""Conexao com o indice.

Um unico arquivo SQLite carrega a extensao sqlite-vec e guarda tudo:
documentos, chunks, indice textual e indice vetorial (ADR-002).
"""

from __future__ import annotations

import sqlite3
import struct
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

import sqlite_vec

from .config import CONFIG


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Abre a conexao com sqlite-vec carregado e pragmas de performance."""
    path = db_path or CONFIG.db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    # WAL: leitor (servidor) e escritor (indexador) convivem sem se bloquear.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # Cache modesto de proposito: a RAM e' o recurso escasso (RNF-02).
    conn.execute("PRAGMA cache_size = -32000")  # 32 MB
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Commit no sucesso, rollback em qualquer excecao."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# --- Serializacao de vetores ------------------------------------------------
# sqlite-vec espera bytes crus. float32 para os vetores cheios (rescore),
# int8 para o indice de busca (ADR-004).


def serialize_f32(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def deserialize_f32(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def serialize_int8(vector: Sequence[int]) -> bytes:
    return struct.pack(f"{len(vector)}b", *vector)


def quantize_int8(vector: Sequence[float]) -> list[int]:
    """Quantiza para int8 assumindo entrada normalizada em [-1, 1].

    O nomic-embed-text devolve vetores normalizados, entao a escala fixa
    de 127 basta e dispensa guardar parametros de quantizacao por vetor.
    """
    out = []
    for value in vector:
        scaled = round(value * 127)
        out.append(max(-128, min(127, scaled)))
    return out


def truncate_and_normalize(vector: Sequence[float], dims: int) -> list[float]:
    """Truncamento Matryoshka seguido de renormalizacao.

    Cortar dimensoes quebra a norma unitaria; sem renormalizar, a
    similaridade de cosseno fica distorcida.
    """
    head = list(vector[:dims])
    norm = sum(v * v for v in head) ** 0.5
    if norm == 0:
        return head
    return [v / norm for v in head]
