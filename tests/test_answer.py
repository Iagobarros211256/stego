"""Testes da geracao ancorada e do servidor web."""

from __future__ import annotations

import hashlib
import json

import pytest

from stego.answer import RECUSA, Answer, ask, build_prompt, ask_stream
from stego.db import connect
from stego.indexer import index_directory
from stego.migrations import migrate
from stego.ollama import GenerationStats
from stego.search import Hit


class FakeClient:
    """Embedder por saco de palavras + gerador roteirizado."""

    def __init__(self, resposta="A porta e' 8080 [1]."):
        self.resposta = resposta
        self.prompts = []
        self.systems = []

    def is_alive(self):
        return True

    def _vector(self, texto):
        vetor = [0.0] * 768
        for palavra in texto.lower().split():
            i = int(hashlib.sha256(palavra.encode()).hexdigest()[:8], 16) % 768
            vetor[i] += 1.0
        n = sum(v * v for v in vetor) ** 0.5
        return [v / n for v in vetor] if n else [0.01] * 768

    def embed(self, texts, model=None):
        return [self._vector(t) for t in texts]

    def embed_one(self, text, model=None):
        return self._vector(text)

    def generate_stream(self, prompt, system=None, **kwargs):
        self.prompts.append(prompt)
        self.systems.append(system)
        for palavra in self.resposta.split(" "):
            yield palavra + " "
        yield GenerationStats(
            eval_count=10, eval_duration_ns=500_000_000,
            prompt_eval_count=100, load_duration_ns=0,
        )


@pytest.fixture
def indexed(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "deploy.md").write_text(
        "# Deploy\n\n## Configuracao\n\n"
        + "A aplicacao escuta na porta 8080 por padrao no servidor. " * 8
    )
    conn = connect(tmp_path / "idx.sqlite")
    migrate(conn, verbose=False)
    index_directory(conn, corpus, FakeClient())
    yield conn
    conn.close()


# --- Prompt -----------------------------------------------------------------


def test_prompt_numera_trechos_e_cita_fontes():
    hits = [
        Hit(1, "conteudo um", "## A", "/tmp/a.md", "Doc A", "markdown", "normal"),
        Hit(2, "conteudo dois", "## B", "/tmp/b.md", "Doc B", "markdown", "normal"),
    ]
    prompt = build_prompt("pergunta?", hits)

    assert "[1] Fonte: Doc A — ## A" in prompt
    assert "[2] Fonte: Doc B — ## B" in prompt
    # a pergunta vem depois dos trechos
    assert prompt.index("Pergunta:") > prompt.index("conteudo dois")


def test_prompt_nao_vaza_caminho_absoluto_na_citacao():
    hit = Hit(1, "x", "## A", "/home/user/secreto/a.md", "Doc A", "markdown", "normal")
    assert "/home/user" not in build_prompt("q", [hit])


# --- Geracao ----------------------------------------------------------------


def test_ask_passa_o_system_prompt(indexed):
    cliente = FakeClient()
    ask(indexed, "qual a porta?", cliente)

    system = cliente.systems[0]
    assert "APENAS com base nos trechos" in system
    assert RECUSA in system


def test_ask_devolve_texto_e_hits(indexed):
    resposta = ask(indexed, "qual a porta da aplicacao?", FakeClient())
    assert resposta.text
    assert resposta.hits
    assert not resposta.refused


def test_indice_vazio_recusa_sem_chamar_o_modelo(tmp_path):
    conn = connect(tmp_path / "vazio.sqlite")
    migrate(conn, verbose=False)
    cliente = FakeClient()

    resposta = ask(conn, "qualquer coisa", cliente)

    assert resposta.refused
    assert resposta.text == RECUSA
    assert cliente.prompts == []  # nao gastou geracao a toa
    conn.close()


def test_recusa_do_modelo_e_detectada(indexed):
    resposta = ask(indexed, "pergunta", FakeClient(resposta=RECUSA))
    assert resposta.refused


def test_stream_entrega_hits_antes_dos_tokens(indexed):
    ordem = []
    for item in ask_stream(indexed, "qual a porta?", FakeClient()):
        if isinstance(item, list):
            ordem.append("hits")
        elif isinstance(item, Answer):
            ordem.append("answer")
        else:
            ordem.append("token")

    assert ordem[0] == "hits"
    assert ordem[-1] == "answer"
    assert "token" in ordem


# --- Log --------------------------------------------------------------------


def test_consulta_e_registrada(indexed):
    ask(indexed, "qual a porta?", FakeClient())

    row = indexed.execute(
        "SELECT question, chunk_ids, timings, answer FROM query_log"
    ).fetchone()

    assert row["question"] == "qual a porta?"
    assert json.loads(row["chunk_ids"])
    assert "embed" in json.loads(row["timings"])
    assert row["answer"]


def test_recusa_tambem_e_registrada(tmp_path):
    conn = connect(tmp_path / "v.sqlite")
    migrate(conn, verbose=False)
    ask(conn, "nada", FakeClient())
    assert conn.execute("SELECT count(*) FROM query_log").fetchone()[0] == 1
    conn.close()


# --- Interface --------------------------------------------------------------


def test_html_nao_faz_requisicao_externa():
    from stego.web_ui import INDEX_HTML

    # RNF-01: a pagina nao pode buscar fonte, script ou estilo de fora.
    for proibido in ("http://", "https://", "//fonts.", "cdn."):
        assert proibido not in INDEX_HTML


def test_html_tem_os_elementos_que_o_js_procura():
    from stego.web_ui import INDEX_HTML

    for elemento_id in (
        "pergunta", "enviar", "restrito", "resposta", "fontes", "lista",
        "medidas", "erro", "vazio", "resumo", "modelo",
    ):
        assert f'id="{elemento_id}"' in INDEX_HTML
