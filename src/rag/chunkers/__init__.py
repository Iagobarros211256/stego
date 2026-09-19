"""Chunkers: Document -> lista de Chunks.

Uma estrategia por tipo de documento (ADR-005). Janela deslizante de N
tokens corta no meio da frase e da tabela; a estrutura do documento e'
a melhor fronteira disponivel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..documents import Document

# Alvos de tamanho, em caracteres. ~4 caracteres por token em portugues.
MIN_CHUNK_CHARS = 200
MAX_CHUNK_CHARS = 2400

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)
FENCE = re.compile(r"^```", re.MULTILINE)


@dataclass
class Chunk:
    content: str
    locator: str
    ordinal: int

    @property
    def token_estimate(self) -> int:
        return len(self.content) // 4


def _mask_fenced_code(text: str) -> set[int]:
    """Posicoes de inicio de linha que estao dentro de bloco de codigo.

    Sem isso, um comentario `# TODO` dentro de um bloco Python vira
    heading e parte o chunk no lugar errado.
    """
    inside = set()
    open_at = None
    for match in FENCE.finditer(text):
        if open_at is None:
            open_at = match.start()
        else:
            inside.add((open_at, match.end()))
            open_at = None
    if open_at is not None:
        inside.add((open_at, len(text)))

    positions = set()
    for start, end in inside:
        positions.update(range(start, end))
    return positions


class MarkdownChunker:
    """Quebra por heading, prefixando cada chunk com seu caminho.

    O prefixo importa: um trecho como "usar porta 8080" e' ambiguo
    sozinho, mas "Deploy > Configuracao: usar porta 8080" carrega o
    contexto no proprio texto embeddado.
    """

    doc_type = "markdown"

    def chunk(self, document: Document) -> list[Chunk]:
        text = document.content.strip()
        if not text:
            return []

        sections = self._split_by_heading(text)
        sections = self._merge_small(sections)

        chunks: list[Chunk] = []
        for path_parts, body in sections:
            locator = " > ".join(path_parts) if path_parts else "(inicio)"
            prefix = f"{locator}\n\n" if path_parts else ""

            for piece in self._split_large(body):
                chunks.append(
                    Chunk(
                        content=f"{prefix}{piece}".strip(),
                        locator=locator,
                        ordinal=len(chunks),
                    )
                )
        return chunks

    def _split_by_heading(self, text: str) -> list[tuple[list[str], str]]:
        """Devolve (caminho_de_headings, corpo) por secao."""
        masked = _mask_fenced_code(text)
        headings = [m for m in HEADING.finditer(text) if m.start() not in masked]

        if not headings:
            return [([], text)]

        sections: list[tuple[list[str], str]] = []
        stack: list[tuple[int, str]] = []

        preamble = text[: headings[0].start()].strip()
        if preamble:
            sections.append(([], preamble))

        for index, match in enumerate(headings):
            level = len(match.group(1))
            title = match.group(2).strip()

            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))

            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = text[match.end() : end].strip()

            # Heading sem corpo (so agrupa sub-secoes) nao vira chunk.
            if body:
                sections.append(([t for _, t in stack], body))

        return sections

    def _merge_small(
        self, sections: list[tuple[list[str], str]]
    ) -> list[tuple[list[str], str]]:
        """Funde secoes curtas demais com a seguinte.

        Um heading com duas linhas embaixo nao rende embedding util
        sozinho, e polui o indice com ruido de alta similaridade.
        """
        merged: list[tuple[list[str], str]] = []
        pending: tuple[list[str], str] | None = None

        for path, body in sections:
            if pending:
                path_prev, body_prev = pending
                body = f"{body_prev}\n\n{body}"
                path = path_prev or path
                pending = None

            if len(body) < MIN_CHUNK_CHARS:
                pending = (path, body)
            else:
                merged.append((path, body))

        if pending:
            if merged:
                path, body = merged[-1]
                merged[-1] = (path, f"{body}\n\n{pending[1]}")
            else:
                merged.append(pending)

        return merged

    def _split_large(self, body: str) -> list[str]:
        """Parte secoes grandes em limites de paragrafo."""
        if len(body) <= MAX_CHUNK_CHARS:
            return [body]

        pieces: list[str] = []
        current: list[str] = []
        size = 0

        for paragraph in body.split("\n\n"):
            para_size = len(paragraph) + 2
            if size + para_size > MAX_CHUNK_CHARS and current:
                pieces.append("\n\n".join(current))
                current, size = [], 0
            current.append(paragraph)
            size += para_size

        if current:
            pieces.append("\n\n".join(current))
        return pieces


CHUNKERS = {chunker.doc_type: chunker for chunker in [MarkdownChunker()]}


def chunker_for(doc_type: str):
    return CHUNKERS.get(doc_type)
