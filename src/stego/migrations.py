"""Migracoes versionadas do esquema.

Cada migracao e' um par (versao, SQL). A versao aplicada fica em
`PRAGMA user_version`, entao nao e' preciso tabela de controle.
Migracoes sao append-only: nunca edite uma ja publicada, acrescente outra.
"""

from __future__ import annotations

import sqlite3

from .config import CONFIG
from .db import transaction

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        f"""
        -- Um documento = um arquivo do corpus.
        CREATE TABLE documents (
            id            INTEGER PRIMARY KEY,
            source_path   TEXT NOT NULL UNIQUE,
            doc_type      TEXT NOT NULL,
            content_hash  TEXT NOT NULL,
            sensitivity   TEXT NOT NULL DEFAULT 'normal'
                              CHECK (sensitivity IN ('normal', 'restricted')),
            modified_at   TEXT,
            indexed_at    TEXT NOT NULL DEFAULT (datetime('now')),
            title         TEXT,
            extra         TEXT  -- JSON livre por tipo de documento
        );

        -- content_hash decide se o arquivo precisa ser reprocessado (RF-01).
        CREATE INDEX idx_documents_hash ON documents(content_hash);
        CREATE INDEX idx_documents_sensitivity ON documents(sensitivity);

        -- Um chunk = uma unidade recuperavel, com sua posicao de origem.
        CREATE TABLE chunks (
            id           INTEGER PRIMARY KEY,
            document_id  INTEGER NOT NULL
                             REFERENCES documents(id) ON DELETE CASCADE,
            ordinal      INTEGER NOT NULL,
            content      TEXT NOT NULL,
            locator      TEXT,        -- "p. 12", "## Instalacao", "turno 47"
            token_est    INTEGER,
            UNIQUE (document_id, ordinal)
        );

        CREATE INDEX idx_chunks_document ON chunks(document_id);

        -- Busca textual (RF-03). remove_diacritics 2: "orcamento" acha
        -- "orcamento" acentuado.
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            content,
            content='chunks',
            content_rowid='id',
            tokenize="unicode61 remove_diacritics 2"
        );

        -- Triggers mantem o indice FTS em sincronia com a tabela base.
        CREATE TRIGGER chunks_fts_insert AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
        END;

        CREATE TRIGGER chunks_fts_delete AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
        END;

        CREATE TRIGGER chunks_fts_update AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content)
            VALUES ('delete', old.id, old.content);
            INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
        END;

        -- Indice vetorial de busca: truncado e quantizado (ADR-004).
        CREATE VIRTUAL TABLE chunks_vec USING vec0(
            chunk_id INTEGER PRIMARY KEY,
            embedding int8[{CONFIG.embed_dims_indexed}] distance_metric=cosine
            -- Insercao/consulta exigem vec_int8(?); um BLOB cru e lido como float32.
        );

        -- Vetores cheios, fora do indice, usados so no rescore dos finalistas.
        CREATE TABLE chunk_vectors_full (
            chunk_id  INTEGER PRIMARY KEY
                          REFERENCES chunks(id) ON DELETE CASCADE,
            embedding BLOB NOT NULL
        );

        -- Observabilidade (RNF-05).
        CREATE TABLE query_log (
            id           INTEGER PRIMARY KEY,
            asked_at     TEXT NOT NULL DEFAULT (datetime('now')),
            question     TEXT NOT NULL,
            include_restricted INTEGER NOT NULL DEFAULT 0,
            chunk_ids    TEXT,    -- JSON com os ids recuperados
            timings      TEXT,    -- JSON com o tempo de cada etapa
            answer       TEXT
        );
        """,
    ),
    (
        2,
        """
        -- chunks_vec e' tabela virtual vec0 e NAO participa de chave
        -- estrangeira: a cascata de documents -> chunks nunca chegava
        -- nela, deixando vetores orfaos. Como o SQLite reaproveita
        -- rowid, um chunk novo colidia com o vetor de um chunk morto.
        DELETE FROM chunks_vec WHERE chunk_id NOT IN (SELECT id FROM chunks);

        -- Trigger em vez de FK. Dispara tambem dentro da cascata, o que
        -- torna o comportamento equivalente ao de uma tabela comum.
        CREATE TRIGGER chunks_vec_delete AFTER DELETE ON chunks BEGIN
            DELETE FROM chunks_vec WHERE chunk_id = old.id;
        END;
        """,
    ),
]


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, verbose: bool = True) -> int:
    """Aplica as migracoes pendentes. Devolve a versao final."""
    version = current_version(conn)

    for target, sql in MIGRATIONS:
        if target <= version:
            continue
        if verbose:
            print(f"  aplicando migracao {target}...")
        with transaction(conn):
            conn.executescript(sql)
            # PRAGMA nao aceita parametro vinculado.
            conn.execute(f"PRAGMA user_version = {target}")
        version = target

    return version
