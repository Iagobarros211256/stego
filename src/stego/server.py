"""Servidor web local (RF-06).

Escuta apenas em 127.0.0.1, sem dependencia externa: a biblioteca
padrao cobre HTTP e SSE, e cada dependencia a menos e' memoria que o
modelo pode usar (RNF-02).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .answer import Answer, ask_stream
from .config import CONFIG
from .db import connect
from .ollama import OllamaClient
from .search import Hit
from .web_ui import INDEX_HTML

# Conexao por thread: objetos sqlite3 nao atravessam threads.
_local = threading.local()


def _conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = connect()
    return _local.conn


def _hit_to_dict(hit: Hit) -> dict:
    return {
        "chunk_id": hit.chunk_id,
        "content": hit.content,
        "locator": hit.locator,
        "source_path": hit.source_path,
        "citation": hit.citation,
        "sensitivity": hit.sensitivity,
        "distance": round(hit.distance, 4) if hit.distance is not None else None,
        "vec_rank": hit.vec_rank,
        "fts_rank": hit.fts_rank,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "stego"

    def log_message(self, fmt, *args):  # silencia o log padrao
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        rota = urlparse(self.path)

        if rota.path == "/":
            self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
            return

        if rota.path == "/api/stats":
            conn = _conn()
            dados = {
                "documents": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
                "chunks": conn.execute("SELECT count(*) FROM chunks").fetchone()[0],
                "restricted": conn.execute(
                    "SELECT count(*) FROM documents WHERE sensitivity='restricted'"
                ).fetchone()[0],
                "model": CONFIG.generate_model,
            }
            self._send(200, json.dumps(dados).encode(), "application/json")
            return

        if rota.path == "/api/history":
            rows = _conn().execute(
                "SELECT question, asked_at FROM query_log ORDER BY id DESC LIMIT 20"
            ).fetchall()
            dados = [{"question": r["question"], "at": r["asked_at"]} for r in rows]
            self._send(200, json.dumps(dados).encode(), "application/json")
            return

        if rota.path == "/api/ask":
            self._stream_answer(parse_qs(rota.query))
            return

        self._send(404, b"nao encontrado", "text/plain; charset=utf-8")

    def _stream_answer(self, params: dict) -> None:
        pergunta = (params.get("q") or [""])[0].strip()
        restrito = (params.get("restricted") or ["0"])[0] == "1"

        if not pergunta:
            self._send(400, b"pergunta vazia", "text/plain; charset=utf-8")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def evento(tipo: str, dados) -> None:
            bloco = f"event: {tipo}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"
            self.wfile.write(bloco.encode("utf-8"))
            self.wfile.flush()

        try:
            with OllamaClient() as client:
                for item in ask_stream(
                    _conn(), pergunta, client, include_restricted=restrito
                ):
                    if isinstance(item, list):
                        evento("hits", [_hit_to_dict(h) for h in item])
                    elif isinstance(item, Answer):
                        evento(
                            "done",
                            {
                                "timings": {
                                    k: round(v * 1000) for k, v in item.timings.items()
                                },
                                "tokens_per_second": (
                                    round(item.stats.tokens_per_second, 1)
                                    if item.stats
                                    else None
                                ),
                                "refused": item.refused,
                            },
                        )
                    else:
                        evento("token", item)
        except BrokenPipeError:
            pass  # aba fechada no meio da geracao
        except Exception as exc:  # noqa: BLE001
            try:
                evento("error", str(exc))
            except Exception:  # noqa: BLE001
                pass


def serve(host: str | None = None, port: int | None = None) -> None:
    endereco = (host or CONFIG.host, port or CONFIG.port)
    servidor = ThreadingHTTPServer(endereco, Handler)
    servidor.daemon_threads = True

    print(f"servindo em http://{endereco[0]}:{endereco[1]}")
    print("Ctrl+C para parar")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nparando")
    finally:
        servidor.server_close()
