"""Testes da ingestao: parser, chunker e pipeline.

O embedder e' simulado — o que se testa aqui e' o pipeline, nao o
modelo. Um vetor deterministico derivado do texto basta.
"""

from __future__ import annotations

import hashlib

import pytest

from stego.chunkers import MAX_CHUNK_CHARS, MarkdownChunker
from stego.db import connect
from stego.documents import detect_sensitivity
from stego.indexer import index_directory
from stego.migrations import migrate
from stego.parsers import MarkdownParser, discover


class FakeOllama:
    """Embedder deterministico: mesmo texto, mesmo vetor."""

    def __init__(self):
        self.chamadas = 0
        self.textos_vistos = 0

    def is_alive(self):
        return True

    def embed(self, texts, model=None):
        self.chamadas += 1
        self.textos_vistos += len(texts)
        saida = []
        for texto in texts:
            digest = hashlib.sha256(texto.encode()).digest()
            vetor = [(digest[i % 32] - 128) / 128 for i in range(768)]
            norma = sum(v * v for v in vetor) ** 0.5
            saida.append([v / norma for v in vetor])
        return saida


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "idx.sqlite")
    migrate(c, verbose=False)
    yield c
    c.close()


# --- Parser -----------------------------------------------------------------


def test_parser_extrai_frontmatter_e_titulo(tmp_path):
    arquivo = tmp_path / "nota.md"
    arquivo.write_text("---\ntitle: Minha Nota\ntags: a, b\n---\n\nCorpo do texto.\n")

    doc = MarkdownParser().parse(arquivo)

    assert doc.title == "Minha Nota"
    assert doc.extra["tags"] == "a, b"
    assert doc.content.strip() == "Corpo do texto."
    assert "---" not in doc.content


def test_parser_usa_h1_quando_nao_ha_frontmatter(tmp_path):
    arquivo = tmp_path / "x.md"
    arquivo.write_text("# Titulo do H1\n\ntexto")
    assert MarkdownParser().parse(arquivo).title == "Titulo do H1"


def test_parser_cai_no_nome_do_arquivo(tmp_path):
    arquivo = tmp_path / "sem-titulo.md"
    arquivo.write_text("so texto solto")
    assert MarkdownParser().parse(arquivo).title == "sem-titulo"


def test_hash_muda_com_o_conteudo(tmp_path):
    arquivo = tmp_path / "a.md"
    arquivo.write_text("versao um")
    h1 = MarkdownParser().parse(arquivo).content_hash
    arquivo.write_text("versao dois")
    h2 = MarkdownParser().parse(arquivo).content_hash
    assert h1 != h2


# --- Sensibilidade ----------------------------------------------------------


def test_diretorio_financeiro_marca_restrito(tmp_path):
    caminho = tmp_path / "financeiro" / "extrato.md"
    assert detect_sensitivity(caminho) == "restricted"


def test_nome_parecido_nao_marca_restrito(tmp_path):
    caminho = tmp_path / "refinanceiramento" / "nota.md"
    assert detect_sensitivity(caminho) == "normal"


# --- Discovery --------------------------------------------------------------


def escrever(path, tamanho=400):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("conteudo de teste. " * (tamanho // 19))


def test_discover_ignora_git_ocultos_e_dependencias(tmp_path):
    escrever(tmp_path / ".git" / "config.md")
    escrever(tmp_path / "node_modules" / "leia.md")
    escrever(tmp_path / "Library" / "PackageCache" / "doc.md")
    escrever(tmp_path / ".oculto.md")
    escrever(tmp_path / "valido.md")
    (tmp_path / "imagem.png").write_bytes(b"x" * 400)

    achados = [p.name for p in discover(tmp_path)]
    assert achados == ["valido.md"]


def test_discover_descarta_licenca_e_changelog(tmp_path):
    escrever(tmp_path / "LICENSE.txt")
    escrever(tmp_path / "CHANGELOG.md")
    escrever(tmp_path / "Third Party Notices.md")
    escrever(tmp_path / "Assembly-CSharp.UnityAdditionalFile.txt")
    escrever(tmp_path / "minhas-notas.md")

    achados = [p.name for p in discover(tmp_path)]
    assert achados == ["minhas-notas.md"]


def test_discover_descarta_arquivo_minusculo(tmp_path):
    (tmp_path / "vazio.md").write_text("oi")
    escrever(tmp_path / "real.md")
    assert [p.name for p in discover(tmp_path)] == ["real.md"]


def test_ragignore_e_respeitado(tmp_path):
    escrever(tmp_path / "rascunho.md")
    escrever(tmp_path / "publicado.md")
    escrever(tmp_path / "temp" / "coisa.md")
    (tmp_path / ".ragignore").write_text("# comentario\nrascunho.md\ntemp/*\n")

    assert [p.name for p in discover(tmp_path)] == ["publicado.md"]


def test_discovery_reporta_motivos(tmp_path):
    from stego.parsers import discover_detailed

    escrever(tmp_path / "LICENSE.md")
    escrever(tmp_path / "node_modules" / "x.md")
    escrever(tmp_path / "bom.md")

    resultado = discover_detailed(tmp_path)

    assert [p.name for p in resultado.files] == ["bom.md"]
    assert resultado.skipped["licenca/changelog"] == 1
    assert resultado.skipped["diretorio excluido"] == 1
    assert "ignorados:" in resultado.report()


# --- Chunker ----------------------------------------------------------------


def make_doc(content: str):
    from stego.documents import Document

    return Document(
        source_path="/tmp/x.md", doc_type="markdown", content=content, content_hash="h"
    )


def test_chunk_prefixa_caminho_de_headings():
    texto = (
        "# Deploy\n\n" + "intro. " * 40 + "\n\n"
        "## Configuracao\n\n" + "usar a porta 8080. " * 20
    )
    chunks = MarkdownChunker().chunk(make_doc(texto))

    alvo = [c for c in chunks if "8080" in c.content][0]
    assert alvo.locator == "Deploy > Configuracao"
    assert alvo.content.startswith("Deploy > Configuracao")


def test_chunk_ignora_heading_dentro_de_bloco_de_codigo():
    texto = (
        "# Real\n\n" + "corpo. " * 40 + "\n\n"
        "```python\n# isto nao e um heading\nprint(1)\n```\n\n" + "mais corpo. " * 30
    )
    chunks = MarkdownChunker().chunk(make_doc(texto))
    assert all(c.locator == "Real" for c in chunks)


def test_secoes_curtas_sao_fundidas():
    texto = "\n\n".join(f"## S{i}\n\nlinha curta." for i in range(6))
    chunks = MarkdownChunker().chunk(make_doc(texto))
    assert len(chunks) < 6


def test_secao_grande_e_partida_em_paragrafos():
    grande = "\n\n".join(["paragrafo com algum conteudo. " * 15 for _ in range(12)])
    chunks = MarkdownChunker().chunk(make_doc(f"# T\n\n{grande}"))

    assert len(chunks) > 1
    assert all(len(c.content) <= MAX_CHUNK_CHARS + 200 for c in chunks)


def test_documento_vazio_nao_gera_chunk():
    assert MarkdownChunker().chunk(make_doc("   \n\n  ")) == []


# --- Pipeline ---------------------------------------------------------------


def corpus(tmp_path):
    (tmp_path / "a.md").write_text("# Alpha\n\n" + "conteudo de alpha. " * 30)
    (tmp_path / "b.md").write_text("# Beta\n\n" + "conteudo de beta. " * 30)
    sub = tmp_path / "financeiro"
    sub.mkdir()
    (sub / "c.md").write_text("# Extrato\n\n" + "valores do mes. " * 30)
    return tmp_path


def test_indexa_e_marca_sensibilidade(conn, tmp_path):
    raiz = corpus(tmp_path)
    stats = index_directory(conn, raiz, FakeOllama())

    assert stats.novos == 3
    assert stats.chunks > 0
    restritos = conn.execute(
        "SELECT count(*) FROM documents WHERE sensitivity = 'restricted'"
    ).fetchone()[0]
    assert restritos == 1


def test_segunda_passada_nao_reprocessa(conn, tmp_path):
    raiz = corpus(tmp_path)
    fake = FakeOllama()

    index_directory(conn, raiz, fake)
    chamadas_primeira = fake.chamadas

    stats = index_directory(conn, raiz, fake)

    assert stats.novos == 0
    assert stats.atualizados == 0
    assert stats.inalterados == 3
    assert fake.chamadas == chamadas_primeira  # nenhuma chamada nova


def test_arquivo_alterado_e_reindexado_sem_duplicar(conn, tmp_path):
    raiz = corpus(tmp_path)
    index_directory(conn, raiz, FakeOllama())

    (raiz / "a.md").write_text("# Alpha\n\n" + "conteudo novo e diferente. " * 30)
    stats = index_directory(conn, raiz, FakeOllama())

    assert stats.atualizados == 1
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 3
    # cascata limpou os chunks antigos
    total = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    vetores = conn.execute("SELECT count(*) FROM chunk_vectors_full").fetchone()[0]
    assert total == vetores


def test_arquivo_removido_sai_do_indice(conn, tmp_path):
    raiz = corpus(tmp_path)
    index_directory(conn, raiz, FakeOllama())

    (raiz / "b.md").unlink()
    stats = index_directory(conn, raiz, FakeOllama())

    assert stats.removidos == 1
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 2


def test_no_prune_preserva_o_ausente(conn, tmp_path):
    raiz = corpus(tmp_path)
    index_directory(conn, raiz, FakeOllama())
    (raiz / "b.md").unlink()

    stats = index_directory(conn, raiz, FakeOllama(), prune=False)

    assert stats.removidos == 0
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 3


def test_erro_de_embed_nao_derruba_a_ingestao(conn, tmp_path):
    raiz = corpus(tmp_path)

    class Quebrado(FakeOllama):
        def embed(self, texts, model=None):
            if any("beta" in t.lower() for t in texts):
                raise RuntimeError("timeout")
            return super().embed(texts)

    stats = index_directory(conn, raiz, Quebrado())

    assert stats.novos == 2
    assert len(stats.erros) == 1
    assert "timeout" in stats.erros[0][1]


def test_fts_encontra_conteudo_indexado(conn, tmp_path):
    raiz = corpus(tmp_path)
    index_directory(conn, raiz, FakeOllama())

    rows = conn.execute(
        "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'alpha'"
    ).fetchone()[0]
    assert rows > 0
