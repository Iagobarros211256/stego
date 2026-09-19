"""Pipeline de indexacao.

Percorre um diretorio, decide o que mudou, e reindexa so isso. O pico
de memoria nao acompanha o tamanho do corpus: um documento por vez,
embeddings em lote, commit incremental (RNF-02).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .chunkers import chunker_for
from .config import CONFIG
from .db import quantize_int8, serialize_f32, serialize_int8, transaction, truncate_and_normalize
from .documents import Document
from .ollama import OllamaClient
from .parsers import discover_detailed, parser_for


@dataclass
class IndexStats:
    novos: int = 0
    atualizados: int = 0
    inalterados: int = 0
    removidos: int = 0
    ignorados: int = 0
    chunks: int = 0
    erros: list[tuple[str, str]] = field(default_factory=list)

    def resumo(self) -> str:
        partes = [
            f"{self.novos} novos",
            f"{self.atualizados} atualizados",
            f"{self.inalterados} inalterados",
        ]
        if self.removidos:
            partes.append(f"{self.removidos} removidos")
        return ", ".join(partes) + f" | {self.chunks} chunks"


def existing_hash(conn: sqlite3.Connection, source_path: str) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT id, content_hash FROM documents WHERE source_path = ?", (source_path,)
    ).fetchone()
    return (row["id"], row["content_hash"]) if row else None


def store_document(
    conn: sqlite3.Connection,
    document: Document,
    chunks: list,
    vectors: list[list[float]],
) -> int:
    """Grava documento, chunks e vetores numa transacao.

    O documento antigo e' apagado antes; a cascata leva chunks, vetores
    e entradas do FTS junto, entao nao ha risco de orfaos.
    """
    with transaction(conn):
        conn.execute("DELETE FROM documents WHERE source_path = ?", (document.source_path,))

        cursor = conn.execute(
            """
            INSERT INTO documents
                (source_path, doc_type, content_hash, sensitivity, modified_at, title, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.source_path,
                document.doc_type,
                document.content_hash,
                document.sensitivity,
                document.modified_at.isoformat() if document.modified_at else None,
                document.title,
                json.dumps(document.extra, ensure_ascii=False) if document.extra else None,
            ),
        )
        document_id = cursor.lastrowid

        for chunk, vector in zip(chunks, vectors, strict=True):
            cursor = conn.execute(
                """
                INSERT INTO chunks (document_id, ordinal, content, locator, token_est)
                VALUES (?, ?, ?, ?, ?)
                """,
                (document_id, chunk.ordinal, chunk.content, chunk.locator, chunk.token_estimate),
            )
            chunk_id = cursor.lastrowid

            reduced = truncate_and_normalize(vector, CONFIG.embed_dims_indexed)
            # vec_int8() e' obrigatorio: sem ele o BLOB e' lido como float32.
            conn.execute(
                "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, vec_int8(?))",
                (chunk_id, serialize_int8(quantize_int8(reduced))),
            )
            conn.execute(
                "INSERT INTO chunk_vectors_full (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, serialize_f32(vector)),
            )

    return document_id


def index_directory(
    conn: sqlite3.Connection,
    root: Path,
    client: OllamaClient,
    prune: bool = True,
    on_progress: Callable[[str, str], None] | None = None,
    extra_ignores: tuple[str, ...] = (),
    include_boilerplate: bool = False,
) -> IndexStats:
    """Indexa `root` recursivamente.

    Idempotente por hash de conteudo: rodar duas vezes seguidas nao
    reprocessa nada na segunda (RF-01).
    """
    stats = IndexStats()
    root = root.resolve()
    varredura = discover_detailed(
        root, extra_ignores=extra_ignores, include_boilerplate=include_boilerplate
    )
    paths = varredura.files
    stats.ignorados = sum(varredura.skipped.values())
    seen: set[str] = set()

    def report(acao: str, caminho: str) -> None:
        if on_progress:
            on_progress(acao, caminho)

    for path in paths:
        source_path = str(path.resolve())
        seen.add(source_path)

        parser = parser_for(path)
        if parser is None:
            continue

        try:
            document = parser.parse(path)
        except Exception as exc:  # noqa: BLE001 - um arquivo ruim nao para a ingestao
            stats.erros.append((source_path, f"parse: {exc}"))
            report("erro", source_path)
            continue

        known = existing_hash(conn, source_path)
        if known and known[1] == document.content_hash:
            stats.inalterados += 1
            continue

        chunker = chunker_for(document.doc_type)
        if chunker is None:
            stats.erros.append((source_path, f"sem chunker para {document.doc_type}"))
            continue

        chunks = chunker.chunk(document)
        if not chunks:
            stats.inalterados += 1
            continue

        try:
            # Lote unico por documento: um documento cabe na memoria,
            # o corpus inteiro nao.
            vectors = client.embed([c.content for c in chunks])
        except Exception as exc:  # noqa: BLE001
            stats.erros.append((source_path, f"embed: {exc}"))
            report("erro", source_path)
            continue

        store_document(conn, document, chunks, vectors)

        stats.chunks += len(chunks)
        if known:
            stats.atualizados += 1
            report("atualizado", source_path)
        else:
            stats.novos += 1
            report("novo", source_path)

    if prune:
        stats.removidos = prune_missing(conn, root, seen)

    return stats


def prune_missing(conn: sqlite3.Connection, root: Path, seen: set[str]) -> int:
    """Remove do indice documentos que sumiram do disco.

    Restrito a `root`: indexar um diretorio nao pode apagar o que foi
    indexado a partir de outro.
    """
    prefix = f"{root}/"
    rows = conn.execute(
        "SELECT id, source_path FROM documents WHERE source_path LIKE ?", (f"{prefix}%",)
    ).fetchall()

    orphans = [row["id"] for row in rows if row["source_path"] not in seen]
    if not orphans:
        return 0

    with transaction(conn):
        conn.executemany("DELETE FROM documents WHERE id = ?", [(i,) for i in orphans])
    return len(orphans)
