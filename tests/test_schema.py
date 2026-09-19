"""Testes do esquema do indice.

Cobrem os tres comportamentos que o resto do sistema assume: o FTS e'
alimentado por trigger e ignora acento, o KNN vetorial funciona em int8,
e apagar um documento leva junto tudo que deriva dele.
"""

from __future__ import annotations

import random

import pytest

from rag.db import (
    connect,
    quantize_int8,
    serialize_f32,
    serialize_int8,
    transaction,
    truncate_and_normalize,
)
from rag.migrations import migrate


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.sqlite")
    migrate(connection, verbose=False)
    yield connection
    connection.close()


def random_unit_vector(dims: int = 768) -> list[float]:
    vec = [random.gauss(0, 1) for _ in range(dims)]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec]


def insert_chunk(conn, content: str, vector: list[float], locator: str = "## Secao"):
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO documents (source_path, doc_type, content_hash) VALUES (?,?,?)",
            (f"/tmp/{content[:10]}.md", "markdown", "hash"),
        )
        doc_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO chunks (document_id, ordinal, content, locator) VALUES (?,?,?,?)",
            (doc_id, 0, content, locator),
        )
        chunk_id = cur.lastrowid

        small = truncate_and_normalize(vector, 256)
        # IMPORTANTE: sem vec_int8() o sqlite-vec le o BLOB como float32
        # e recusa a insercao.
        conn.execute(
            "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, vec_int8(?))",
            (chunk_id, serialize_int8(quantize_int8(small))),
        )
        conn.execute(
            "INSERT INTO chunk_vectors_full (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, serialize_f32(vector)),
        )
    return doc_id, chunk_id


def test_fts_ignora_acento(conn):
    insert_chunk(conn, "orçamento anual da fábrica", random_unit_vector())
    rows = conn.execute(
        "SELECT c.content FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
        "WHERE chunks_fts MATCH 'orcamento'"
    ).fetchall()
    assert len(rows) == 1


def test_knn_encontra_o_proprio_vetor(conn):
    vector = random_unit_vector()
    _, chunk_id = insert_chunk(conn, "documento alvo", vector)
    for i in range(5):
        insert_chunk(conn, f"ruido {i}", random_unit_vector())

    query = serialize_int8(quantize_int8(truncate_and_normalize(vector, 256)))
    rows = conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec "
        "WHERE embedding MATCH vec_int8(?) AND k = 3",
        (query,),
    ).fetchall()

    assert rows[0]["chunk_id"] == chunk_id
    assert rows[0]["distance"] < 0.01


def test_delete_em_cascata(conn):
    doc_id, _ = insert_chunk(conn, "para apagar", random_unit_vector())
    with transaction(conn):
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunk_vectors_full").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0] == 0
    # chunks_vec e' tabela virtual e nao tem chave estrangeira: so o
    # trigger da migracao 2 a mantem em sincronia.
    assert conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 0


def test_rowid_reaproveitado_nao_colide_com_vetor_morto(conn):
    """Reproduz o bug que quebrou a ingestao apos um `rag forget`."""
    doc_id, _ = insert_chunk(conn, "primeiro documento", random_unit_vector())
    with transaction(conn):
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    # Sem a limpeza do vetor, este insert falharia com UNIQUE constraint
    # porque o SQLite reaproveita o rowid do chunk apagado.
    insert_chunk(conn, "segundo documento", random_unit_vector())

    assert conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0] == 1


def test_muitos_ciclos_de_insercao_e_remocao(conn):
    """Varias passadas de indexacao sobre o mesmo caminho."""
    for _ in range(5):
        doc_id, _ = insert_chunk(conn, "documento reindexado", random_unit_vector())
        with transaction(conn):
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    insert_chunk(conn, "documento final", random_unit_vector())

    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    vetores = conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0]
    cheios = conn.execute("SELECT count(*) FROM chunk_vectors_full").fetchone()[0]
    assert chunks == vetores == cheios == 1


def test_sensitivity_rejeita_valor_invalido(conn):
    with pytest.raises(Exception):
        with transaction(conn):
            conn.execute(
                "INSERT INTO documents (source_path, doc_type, content_hash, sensitivity) "
                "VALUES (?,?,?,?)",
                ("/tmp/x.md", "markdown", "h", "publico"),
            )


def test_truncamento_preserva_norma(conn):
    vector = random_unit_vector()
    small = truncate_and_normalize(vector, 256)
    norm = sum(v * v for v in small) ** 0.5
    assert abs(norm - 1.0) < 1e-6
