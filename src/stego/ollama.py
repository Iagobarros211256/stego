"""Cliente do Ollama.

Duas operacoes, so: embeddar em lote e gerar com streaming. A API
compatavel com OpenAI existe, mas a nativa expoe o lote de embeddings
de forma mais direta, e o ADR-001 ja garante que trocar de backend
(llama-server) mantem as duas rotas.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import httpx

from .config import CONFIG


class OllamaError(RuntimeError):
    """Falha de comunicacao ou de modelo."""


@dataclass
class GenerationStats:
    """Numeros que o Ollama devolve no fim de uma geracao."""

    eval_count: int
    eval_duration_ns: int
    prompt_eval_count: int
    load_duration_ns: int

    @property
    def tokens_per_second(self) -> float:
        if not self.eval_duration_ns:
            return 0.0
        return self.eval_count / (self.eval_duration_ns / 1e9)


class OllamaClient:
    def __init__(self, host: str | None = None, timeout: float = 300.0):
        self.host = (host or CONFIG.ollama_host).rstrip("/")
        # Timeout generoso na leitura: a primeira carga do modelo leva
        # alguns segundos e a geracao completa pode levar dezenas.
        self._client = httpx.Client(
            base_url=self.host,
            timeout=httpx.Timeout(timeout, connect=5.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- Saude -----------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            self._client.get("/api/tags", timeout=5.0).raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def installed_models(self) -> list[str]:
        try:
            response = self._client.get("/api/tags", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"nao consegui listar modelos: {exc}") from exc
        return [m["name"] for m in response.json().get("models", [])]

    def loaded_models(self) -> list[dict]:
        """Modelos residentes agora, com tamanho e se estao na GPU."""
        try:
            response = self._client.get("/api/ps", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"nao consegui consultar /api/ps: {exc}") from exc
        return response.json().get("models", [])

    # --- Embeddings ------------------------------------------------------

    def embed(self, texts: Sequence[str], model: str | None = None) -> list[list[float]]:
        """Embedda uma lista de textos numa unica chamada.

        O endpoint aceita lote nativo, o que evita o custo de ida e volta
        por chunk. Devolve os vetores em dimensao cheia (768); o
        truncamento do ADR-004 e' responsabilidade de quem indexa.
        """
        if not texts:
            return []

        payload = {"model": model or CONFIG.embed_model, "input": list(texts)}
        try:
            response = self._client.post("/api/embed", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"embed falhou ({exc.response.status_code}): {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"embed falhou: {exc}") from exc

        embeddings = response.json().get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            raise OllamaError(
                f"esperava {len(texts)} vetores, recebi {len(embeddings or [])}"
            )
        return embeddings

    def embed_one(self, text: str, model: str | None = None) -> list[float]:
        return self.embed([text], model=model)[0]

    def embed_batched(
        self,
        texts: Sequence[str],
        batch_size: int | None = None,
        model: str | None = None,
    ) -> Iterator[tuple[int, list[float]]]:
        """Embedda em lotes, rendendo (indice, vetor) conforme sai.

        Gerador de proposito: quem indexa faz commit incremental e o pico
        de memoria nao acompanha o tamanho do corpus (RNF-02).
        """
        size = batch_size or CONFIG.embed_batch_size
        for start in range(0, len(texts), size):
            window = texts[start : start + size]
            for offset, vector in enumerate(self.embed(window, model=model)):
                yield start + offset, vector

    # --- Geracao ---------------------------------------------------------

    def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        num_ctx: int | None = None,
    ) -> Iterator[str | GenerationStats]:
        """Gera com streaming.

        Rende pedacos de texto conforme chegam e, no fim, um
        GenerationStats. Temperatura baixa por padrao: a tarefa e'
        sintetizar trechos recuperados, nao inventar (RF-04).
        """
        payload = {
            "model": model or CONFIG.generate_model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx or CONFIG.context_length,
            },
        }
        if system:
            payload["system"] = system

        try:
            with self._client.stream("POST", "/api/generate", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)

                    if error := chunk.get("error"):
                        raise OllamaError(f"geracao falhou: {error}")

                    if piece := chunk.get("response"):
                        yield piece

                    if chunk.get("done"):
                        yield GenerationStats(
                            eval_count=chunk.get("eval_count", 0),
                            eval_duration_ns=chunk.get("eval_duration", 0),
                            prompt_eval_count=chunk.get("prompt_eval_count", 0),
                            load_duration_ns=chunk.get("load_duration", 0),
                        )
        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"geracao falhou ({exc.response.status_code}): {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"geracao falhou: {exc}") from exc

    def generate(self, prompt: str, **kwargs) -> tuple[str, GenerationStats | None]:
        """Versao nao-streaming, para scripts e testes."""
        pieces: list[str] = []
        stats: GenerationStats | None = None
        for item in self.generate_stream(prompt, **kwargs):
            if isinstance(item, GenerationStats):
                stats = item
            else:
                pieces.append(item)
        return "".join(pieces), stats
