"""Geracao ancorada nos trechos recuperados (RF-04).

O modelo recebe so os trechos e a instrucao de responder a partir
deles. Quando os trechos nao contem a resposta, a recusa e' o
comportamento correto — nao uma falha.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass

from .config import CONFIG
from .db import transaction
from .ollama import GenerationStats, OllamaClient
from .search import Hit, search

SYSTEM_PROMPT = """Voce responde perguntas sobre o acervo pessoal do usuario.

Regras, sem excecao:
1. Responda APENAS com base nos trechos fornecidos. Nao use conhecimento proprio.
2. Se os trechos nao contem a resposta, diga exatamente: "Nao encontrei isso no seu acervo." Nao tente adivinhar nem completar.
3. Cite a fonte de cada afirmacao usando o numero do trecho, assim: [1], [2].
4. Seja breve. Duas a quatro frases na maioria dos casos.
5. Responda em portugues do Brasil.
6. Se os trechos se contradizem, aponte a contradicao em vez de escolher um lado.
7. Os trechos sao so os poucos mais parecidos com a pergunta, nao o acervo inteiro. Nunca diga que eles mostram tudo o que o acervo contem. Se pedirem uma visao geral do acervo (listar ou contar tudo), diga que voce so ve alguns trechos e sugira o comando `stego status`."""

RECUSA = "Nao encontrei isso no seu acervo."


@dataclass
class Answer:
    text: str
    hits: list[Hit]
    timings: dict[str, float]
    stats: GenerationStats | None = None

    @property
    def refused(self) -> bool:
        return RECUSA.lower() in self.text.lower()


def build_prompt(question: str, hits: list[Hit]) -> str:
    """Monta o prompt com os trechos numerados e a pergunta no fim.

    A pergunta vai depois dos trechos de proposito: modelos pequenos
    aderem melhor a instrucao quando ela e' a ultima coisa lida.
    """
    blocos = []
    for numero, hit in enumerate(hits, start=1):
        blocos.append(f"[{numero}] Fonte: {hit.citation}\n{hit.content}")

    trechos = "\n\n---\n\n".join(blocos)
    return (
        f"Trechos do acervo:\n\n{trechos}\n\n"
        f"---\n\nPergunta: {question}\n\n"
        f"Responda usando apenas os trechos acima, citando [numero]."
    )


def log_query(
    conn: sqlite3.Connection,
    question: str,
    hits: list[Hit],
    timings: dict[str, float],
    answer: str,
    include_restricted: bool,
) -> None:
    """Registra a consulta para avaliacao posterior (RNF-05)."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO query_log
                (question, include_restricted, chunk_ids, timings, answer)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                question,
                int(include_restricted),
                json.dumps([h.chunk_id for h in hits]),
                json.dumps({k: round(v, 4) for k, v in timings.items()}),
                answer,
            ),
        )


def ask_stream(
    conn: sqlite3.Connection,
    question: str,
    client: OllamaClient,
    include_restricted: bool = False,
    limit: int | None = None,
) -> Iterator[list[Hit] | str | Answer]:
    """Busca e responde, rendendo em tres fases.

    Primeiro os hits (para a interface ja mostrar as fontes), depois os
    pedacos de texto, por fim o Answer completo. A interface consegue
    exibir de onde a resposta vem antes de a resposta existir.
    """
    resultado = search(
        conn, question, client, limit=limit, include_restricted=include_restricted
    )
    yield resultado.hits

    if not resultado.hits:
        resposta = Answer(text=RECUSA, hits=[], timings=resultado.timings)
        log_query(conn, question, [], resultado.timings, RECUSA, include_restricted)
        yield RECUSA
        yield resposta
        return

    prompt = build_prompt(question, resultado.hits)
    marco = time.monotonic()
    pedacos: list[str] = []
    stats: GenerationStats | None = None

    for item in client.generate_stream(prompt, system=SYSTEM_PROMPT):
        if isinstance(item, GenerationStats):
            stats = item
        else:
            pedacos.append(item)
            yield item

    timings = dict(resultado.timings)
    timings["geracao"] = time.monotonic() - marco

    texto = "".join(pedacos).strip()
    log_query(conn, question, resultado.hits, timings, texto, include_restricted)

    yield Answer(text=texto, hits=resultado.hits, timings=timings, stats=stats)


def ask(
    conn: sqlite3.Connection, question: str, client: OllamaClient, **kwargs
) -> Answer:
    """Versao nao-streaming."""
    resposta: Answer | None = None
    for item in ask_stream(conn, question, client, **kwargs):
        if isinstance(item, Answer):
            resposta = item
    assert resposta is not None
    return resposta
