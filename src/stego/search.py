"""Busca hibrida: vetorial + textual, fundidas por RRF (RF-03).

Semantica sozinha erra em numeros e nomes proprios; BM25 sozinho erra
em parafrase. Rodar as duas e fundir sai mais barato do que tentar
fazer uma delas cobrir os dois casos.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field

from .config import CONFIG
from .db import quantize_int8, serialize_f32, serialize_int8, truncate_and_normalize
from .ollama import OllamaClient

# Tokens que o FTS5 trataria como operador se passassem crus.
FTS_TOKEN = re.compile(r"[\w\u00C0-\u024F]+", re.UNICODE)
STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "das", "dos", "em", "no", "na", "nos",
    "nas", "um", "uma", "para", "por", "com", "que", "se", "os", "as", "ao",
    "qual", "quais", "quando", "onde", "como", "quanto", "quanta", "meu",
    "minha", "meus", "minhas", "eu", "foi", "era", "sao", "e'",
}


@dataclass
class Hit:
    chunk_id: int
    content: str
    locator: str
    source_path: str
    title: str | None
    doc_type: str
    sensitivity: str
    score: float = 0.0
    vec_rank: int | None = None
    fts_rank: int | None = None
    distance: float | None = None

    @property
    def citation(self) -> str:
        nome = self.title or self.source_path.rsplit("/", 1)[-1]
        if not self.locator:
            return nome
        # Em Markdown o titulo costuma ser o primeiro H1, e o caminho de
        # headings tambem comeca nele: "Deploy — Deploy > Config" repetiria.
        # O " > " no teste evita confundir "Deploy" com "Deployment > ...".
        if self.locator == nome or self.locator.startswith(f"{nome} > "):
            return self.locator
        return f"{nome} — {self.locator}"


@dataclass
class SearchResult:
    hits: list[Hit]
    timings: dict[str, float] = field(default_factory=dict)
    vec_found: int = 0
    fts_found: int = 0


def build_fts_query(question: str) -> str:
    """Transforma a pergunta numa consulta FTS5 segura.

    Aspas em cada termo: sem isso, pontuacao e palavras como OR ou NEAR
    viram operador e a consulta falha ou retorna lixo.
    """
    tokens = [t.lower() for t in FTS_TOKEN.findall(question)]
    uteis = [t for t in tokens if t not in STOPWORDS and len(t) > 1]
    if not uteis:
        uteis = tokens
    if not uteis:
        return ""
    return " OR ".join(f'"{t}"' for t in uteis)


def _sensitivity_clause(include_restricted: bool) -> str:
    # Filtro na consulta, nunca como pos-filtro: pos-filtrar envenena a
    # lista de candidatos com documentos que serao descartados.
    return "" if include_restricted else " AND d.sensitivity = 'normal'"


def vector_search(
    conn: sqlite3.Connection,
    query_vector: list[float],
    limit: int,
    include_restricted: bool,
) -> list[tuple[int, float]]:
    reduced = truncate_and_normalize(query_vector, CONFIG.embed_dims_indexed)
    blob = serialize_int8(quantize_int8(reduced))

    # KNN do sqlite-vec nao aceita JOIN na mesma consulta, entao busca-se
    # com folga e filtra-se depois pelos ids.
    folga = limit * 4 if not include_restricted else limit
    rows = conn.execute(
        "SELECT chunk_id, distance FROM chunks_vec "
        "WHERE embedding MATCH vec_int8(?) AND k = ?",
        (blob, folga),
    ).fetchall()

    if not rows:
        return []

    ids = [r["chunk_id"] for r in rows]
    marcadores = ",".join("?" * len(ids))
    permitidos = {
        r["id"]
        for r in conn.execute(
            f"SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id "
            f"WHERE c.id IN ({marcadores})" + _sensitivity_clause(include_restricted),
            ids,
        ).fetchall()
    }

    saida = [(r["chunk_id"], r["distance"]) for r in rows if r["chunk_id"] in permitidos]
    return saida[:limit]


def text_search(
    conn: sqlite3.Connection, question: str, limit: int, include_restricted: bool
) -> list[tuple[int, float]]:
    fts_query = build_fts_query(question)
    if not fts_query:
        return []

    try:
        rows = conn.execute(
            f"""
            SELECT c.id AS chunk_id, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE chunks_fts MATCH ?{_sensitivity_clause(include_restricted)}
            ORDER BY score
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # Consulta malformada nao deve derrubar a busca: a metade
        # vetorial ainda responde.
        return []

    return [(r["chunk_id"], r["score"]) for r in rows]


def reciprocal_rank_fusion(
    vec_hits: list[tuple[int, float]],
    fts_hits: list[tuple[int, float]],
    k: int | None = None,
) -> dict[int, tuple[float, int | None, int | None]]:
    """Funde duas listas ordenadas usando so a posicao.

    RRF dispensa calibrar pesos entre escalas incomparaveis — distancia
    de cosseno e BM25 nao vivem no mesmo mundo, mas rank 1 e rank 1 sim.
    """
    constante = k if k is not None else CONFIG.rrf_k
    scores: dict[int, list] = {}

    for posicao, (chunk_id, _) in enumerate(vec_hits, start=1):
        entrada = scores.setdefault(chunk_id, [0.0, None, None])
        entrada[0] += 1.0 / (constante + posicao)
        entrada[1] = posicao

    for posicao, (chunk_id, _) in enumerate(fts_hits, start=1):
        entrada = scores.setdefault(chunk_id, [0.0, None, None])
        entrada[0] += 1.0 / (constante + posicao)
        entrada[2] = posicao

    return {cid: (v[0], v[1], v[2]) for cid, v in scores.items()}


def rescore(
    conn: sqlite3.Connection, chunk_ids: list[int], query_vector: list[float]
) -> dict[int, float]:
    """Distancia de cosseno com os vetores em precisao cheia.

    O indice usa int8 truncado por custo; o ranking final nao precisa
    pagar essa aproximacao, ja que sao poucos finalistas.
    """
    if not chunk_ids:
        return {}

    marcadores = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"""
        SELECT chunk_id, vec_distance_cosine(embedding, ?) AS distance
        FROM chunk_vectors_full
        WHERE chunk_id IN ({marcadores})
        """,
        [serialize_f32(query_vector), *chunk_ids],
    ).fetchall()

    return {r["chunk_id"]: r["distance"] for r in rows}


def hydrate(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, Hit]:
    if not chunk_ids:
        return {}

    marcadores = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"""
        SELECT c.id, c.content, c.locator,
               d.source_path, d.title, d.doc_type, d.sensitivity
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.id IN ({marcadores})
        """,
        chunk_ids,
    ).fetchall()

    return {
        r["id"]: Hit(
            chunk_id=r["id"],
            content=r["content"],
            locator=r["locator"] or "",
            source_path=r["source_path"],
            title=r["title"],
            doc_type=r["doc_type"],
            sensitivity=r["sensitivity"],
        )
        for r in rows
    }


def search(
    conn: sqlite3.Connection,
    question: str,
    client: OllamaClient,
    limit: int | None = None,
    include_restricted: bool = False,
) -> SearchResult:
    """Busca hibrida completa: embed, duas buscas, fusao, rescore."""
    limite = limit or CONFIG.context_chunks
    timings: dict[str, float] = {}

    marco = time.monotonic()
    query_vector = client.embed_one(question)
    timings["embed"] = time.monotonic() - marco

    marco = time.monotonic()
    vec_hits = vector_search(
        conn, query_vector, CONFIG.candidates_per_branch, include_restricted
    )
    timings["vetorial"] = time.monotonic() - marco

    marco = time.monotonic()
    fts_hits = text_search(
        conn, question, CONFIG.candidates_per_branch, include_restricted
    )
    timings["textual"] = time.monotonic() - marco

    marco = time.monotonic()
    fundidos = reciprocal_rank_fusion(vec_hits, fts_hits)
    ordenados = sorted(fundidos.items(), key=lambda item: -item[1][0])
    finalistas = [cid for cid, _ in ordenados[: CONFIG.rescore_top_n]]

    distancias = rescore(conn, finalistas, query_vector)
    timings["fusao_rescore"] = time.monotonic() - marco

    marco = time.monotonic()
    hits_por_id = hydrate(conn, finalistas)
    timings["hidratacao"] = time.monotonic() - marco

    hits: list[Hit] = []
    for chunk_id in finalistas:
        hit = hits_por_id.get(chunk_id)
        if hit is None:
            continue
        rrf, vec_rank, fts_rank = fundidos[chunk_id]
        hit.score = rrf
        hit.vec_rank = vec_rank
        hit.fts_rank = fts_rank
        hit.distance = distancias.get(chunk_id)
        hits.append(hit)

    # Ordem final pela distancia real; RRF ja fez o trabalho de
    # selecionar quem merece ser rescoreado.
    hits.sort(key=lambda h: (h.distance if h.distance is not None else 9.9))

    return SearchResult(
        hits=hits[:limite],
        timings=timings,
        vec_found=len(vec_hits),
        fts_found=len(fts_hits),
    )
