"""Testes da busca hibrida.

Embedder simulado, mas deterministico e com estrutura: textos que
compartilham palavras recebem vetores proximos, o que permite testar a
fusao sem depender do modelo real.
"""

from __future__ import annotations

import hashlib

import pytest

from stego.db import connect
from stego.indexer import index_directory
from stego.migrations import migrate
from stego.search import (
    build_fts_query,
    reciprocal_rank_fusion,
    search,
    text_search,
    vector_search,
)


class BagOfWordsEmbedder:
    """Vetor por saco de palavras: similaridade reflete vocabulario."""

    def is_alive(self):
        return True

    def _vector(self, texto: str) -> list[float]:
        vetor = [0.0] * 768
        for palavra in texto.lower().split():
            indice = int(hashlib.sha256(palavra.encode()).hexdigest()[:8], 16) % 768
            vetor[indice] += 1.0
        norma = sum(v * v for v in vetor) ** 0.5
        return [v / norma for v in vetor] if norma else [0.01] * 768

    def embed(self, texts, model=None):
        return [self._vector(t) for t in texts]

    def embed_one(self, text, model=None):
        return self._vector(text)


@pytest.fixture
def indexed(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    (corpus / "deploy.md").write_text(
        "# Deploy\n\n"
        + "O servidor de producao roda atras de nginx. " * 8
        + "\n\n## Configuracao\n\n"
        + "A aplicacao escuta na porta 8080 por padrao. " * 8
    )
    (corpus / "cozinha.md").write_text(
        "# Receita de pao\n\n"
        + "Misture farinha fermento agua e sal na tigela grande. " * 10
    )
    financeiro = corpus / "financeiro"
    financeiro.mkdir()
    (financeiro / "extrato.md").write_text(
        "# Extrato de marco\n\n"
        + "Pagamento de aluguel no valor combinado no contrato. " * 10
    )

    conn = connect(tmp_path / "idx.sqlite")
    migrate(conn, verbose=False)
    embedder = BagOfWordsEmbedder()
    index_directory(conn, corpus, embedder)

    yield conn, embedder
    conn.close()


# --- Consulta FTS -----------------------------------------------------------


def test_fts_query_remove_stopwords_e_cita_termos():
    q = build_fts_query("qual e a porta do servidor?")
    assert '"porta"' in q and '"servidor"' in q
    assert '"qual"' not in q


def test_fts_query_neutraliza_operadores():
    # OR e NEAR do usuario viram termos literais entre aspas; o unico
    # operador da consulta final e' o OR que nos inserimos.
    q = build_fts_query("nginx OR NEAR porta")
    assert q == '"nginx" OR "or" OR "near" OR "porta"'


def test_fts_query_vazia_para_entrada_sem_palavras():
    assert build_fts_query("!!! ???") == ""


def test_fts_query_nao_quebra_com_pontuacao(indexed):
    conn, _ = indexed
    # Se a consulta chegasse crua, o FTS levantaria OperationalError.
    assert text_search(conn, 'porta "8080" -- ; DROP', 10, False) is not None


# --- Buscas isoladas --------------------------------------------------------


def test_busca_textual_encontra_termo_exato(indexed):
    conn, _ = indexed
    hits = text_search(conn, "8080", 10, include_restricted=False)
    assert len(hits) > 0


def test_busca_textual_ignora_acento(indexed):
    conn, _ = indexed
    assert len(text_search(conn, "configuracao", 10, False)) > 0
    assert len(text_search(conn, "configuração", 10, False)) > 0


def test_busca_vetorial_retorna_vizinhos(indexed):
    conn, embedder = indexed
    vetor = embedder.embed_one("farinha fermento tigela")
    hits = vector_search(conn, vetor, 5, include_restricted=False)
    assert len(hits) > 0


# --- Sensibilidade ----------------------------------------------------------


def test_restrito_fica_fora_por_padrao(indexed):
    conn, embedder = indexed
    resultado = search(conn, "pagamento de aluguel contrato", embedder)
    assert all(h.sensitivity == "normal" for h in resultado.hits)


def test_restrito_aparece_quando_pedido(indexed):
    conn, embedder = indexed
    resultado = search(
        conn, "pagamento de aluguel contrato", embedder, include_restricted=True
    )
    assert any(h.sensitivity == "restricted" for h in resultado.hits)


def test_filtro_vale_para_as_duas_metades(indexed):
    conn, _ = indexed
    assert text_search(conn, "aluguel", 10, include_restricted=False) == []
    assert len(text_search(conn, "aluguel", 10, include_restricted=True)) > 0


# --- Fusao ------------------------------------------------------------------


def test_rrf_premia_quem_aparece_nas_duas_listas():
    vec = [(1, 0.1), (2, 0.2), (3, 0.3)]
    fts = [(3, -5.0), (4, -4.0)]

    fundidos = reciprocal_rank_fusion(vec, fts, k=60)
    ordem = sorted(fundidos.items(), key=lambda x: -x[1][0])

    # 3 e' rank 3 no vetorial e rank 1 no textual: soma vence o rank 1
    # isolado do vetorial.
    assert ordem[0][0] == 3


def test_rrf_registra_a_origem_de_cada_hit():
    fundidos = reciprocal_rank_fusion([(1, 0.1)], [(2, -3.0)])
    assert fundidos[1][1] == 1 and fundidos[1][2] is None
    assert fundidos[2][1] is None and fundidos[2][2] == 1


def test_rrf_lista_vazia_de_um_lado():
    fundidos = reciprocal_rank_fusion([(1, 0.1), (2, 0.2)], [])
    assert len(fundidos) == 2


# --- Busca completa ---------------------------------------------------------


def test_search_devolve_citacao_utilizavel(indexed):
    conn, embedder = indexed
    resultado = search(conn, "qual porta a aplicacao escuta", embedder)

    assert resultado.hits
    hit = resultado.hits[0]
    assert hit.citation
    assert hit.locator
    assert hit.source_path.endswith(".md")


def test_search_registra_tempos(indexed):
    conn, embedder = indexed
    resultado = search(conn, "nginx producao", embedder)
    assert {"embed", "vetorial", "textual", "fusao_rescore"} <= set(resultado.timings)


def test_search_respeita_o_limite(indexed):
    conn, embedder = indexed
    resultado = search(conn, "porta servidor nginx farinha", embedder, limit=2)
    assert len(resultado.hits) <= 2


def test_search_em_indice_vazio_nao_quebra(tmp_path):
    conn = connect(tmp_path / "vazio.sqlite")
    migrate(conn, verbose=False)
    resultado = search(conn, "qualquer coisa", BagOfWordsEmbedder())
    assert resultado.hits == []
    conn.close()


def test_rescore_atribui_distancia(indexed):
    conn, embedder = indexed
    resultado = search(conn, "porta 8080 aplicacao", embedder)
    assert all(h.distance is not None for h in resultado.hits)
    # ordenado por distancia crescente
    distancias = [h.distance for h in resultado.hits]
    assert distancias == sorted(distancias)
