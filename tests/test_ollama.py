"""Testes do cliente Ollama.

Usam transporte simulado do httpx: rodam sem o Ollama de pe' e cobrem
o que importa — lote, streaming, e erro virando OllamaError legivel.
"""

from __future__ import annotations

import json

import httpx
import pytest

from stego.ollama import GenerationStats, OllamaClient, OllamaError


def client_with(handler) -> OllamaClient:
    client = OllamaClient(host="http://fake")
    client._client = httpx.Client(
        base_url="http://fake",
        transport=httpx.MockTransport(handler),
    )
    return client


def test_embed_em_lote_devolve_um_vetor_por_texto():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/embed"
        assert isinstance(body["input"], list)
        return httpx.Response(
            200, json={"embeddings": [[0.1] * 768 for _ in body["input"]]}
        )

    with client_with(handler) as client:
        vectors = client.embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert len(vectors[0]) == 768


def test_embed_vazio_nao_chama_a_api():
    def handler(request):
        raise AssertionError("nao deveria ter chamado")

    with client_with(handler) as client:
        assert client.embed([]) == []


def test_embed_batched_respeita_o_tamanho_do_lote():
    lotes = []

    def handler(request):
        body = json.loads(request.content)
        lotes.append(len(body["input"]))
        return httpx.Response(
            200, json={"embeddings": [[0.5] * 768 for _ in body["input"]]}
        )

    with client_with(handler) as client:
        resultado = list(client.embed_batched(["t"] * 7, batch_size=3))

    assert lotes == [3, 3, 1]
    assert [i for i, _ in resultado] == list(range(7))


def test_embed_detecta_contagem_divergente():
    def handler(request):
        return httpx.Response(200, json={"embeddings": [[0.1] * 768]})

    with client_with(handler) as client:
        with pytest.raises(OllamaError, match="esperava 3 vetores"):
            client.embed(["a", "b", "c"])


def test_erro_http_vira_ollama_error():
    def handler(request):
        return httpx.Response(404, text="model not found")

    with client_with(handler) as client:
        with pytest.raises(OllamaError, match="404"):
            client.embed(["a"])


def test_generate_stream_rende_texto_e_depois_stats():
    linhas = [
        {"response": "Uma "},
        {"response": "resposta."},
        {
            "done": True,
            "eval_count": 50,
            "eval_duration": 1_800_000_000,
            "prompt_eval_count": 22,
            "load_duration": 4_500_000_000,
        },
    ]

    def handler(request):
        corpo = "\n".join(json.dumps(linha) for linha in linhas)
        return httpx.Response(200, text=corpo)

    with client_with(handler) as client:
        itens = list(client.generate_stream("pergunta"))

    assert itens[:2] == ["Uma ", "resposta."]
    assert isinstance(itens[-1], GenerationStats)
    assert itens[-1].tokens_per_second == pytest.approx(27.8, abs=0.1)


def test_generate_junta_os_pedacos():
    linhas = [
        {"response": "olá"},
        {"response": " mundo"},
        {"done": True, "eval_count": 2, "eval_duration": 100_000_000},
    ]

    def handler(request):
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in linhas))

    with client_with(handler) as client:
        texto, stats = client.generate("oi")

    assert texto == "olá mundo"
    assert stats.eval_count == 2


def test_erro_no_meio_do_stream():
    linhas = [{"response": "come"}, {"error": "out of memory"}]

    def handler(request):
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in linhas))

    with client_with(handler) as client:
        with pytest.raises(OllamaError, match="out of memory"):
            list(client.generate_stream("pergunta"))


def test_opcoes_de_geracao_vao_no_payload():
    capturado = {}

    def handler(request):
        capturado.update(json.loads(request.content))
        return httpx.Response(200, text=json.dumps({"done": True}))

    with client_with(handler) as client:
        list(client.generate_stream("p", system="seja breve", num_ctx=4096))

    assert capturado["system"] == "seja breve"
    assert capturado["options"]["num_ctx"] == 4096
    assert capturado["options"]["temperature"] == 0.1
    assert capturado["stream"] is True
