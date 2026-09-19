"""Interface de linha de comando."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CONFIG
from .db import connect, transaction
from .migrations import MIGRATIONS, current_version, migrate


def cmd_init_db(args: argparse.Namespace) -> int:
    print(f"indice: {CONFIG.db_path}")
    conn = connect()
    before = current_version(conn)
    after = migrate(conn)
    if before == after:
        print(f"ja na versao {after}, nada a fazer")
    else:
        print(f"esquema: v{before} -> v{after}")
    conn.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if not CONFIG.db_path.exists():
        print(f"indice nao existe ainda: {CONFIG.db_path}")
        print("rode: rag init-db")
        return 1

    conn = connect()
    version = current_version(conn)
    latest = MIGRATIONS[-1][0]

    size_mb = CONFIG.db_path.stat().st_size / 1_048_576
    docs = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    restricted = conn.execute(
        "SELECT count(*) FROM documents WHERE sensitivity = 'restricted'"
    ).fetchone()[0]

    print(f"indice:      {CONFIG.db_path}")
    print(f"tamanho:     {size_mb:.1f} MB")
    print(f"esquema:     v{version}" + ("" if version == latest else f" (ultima: v{latest})"))
    print(f"documentos:  {docs}  ({restricted} restritos)")
    print(f"chunks:      {chunks}")

    if docs:
        print("\npor tipo:")
        rows = conn.execute(
            "SELECT doc_type, count(*) AS n FROM documents GROUP BY doc_type ORDER BY n DESC"
        ).fetchall()
        for row in rows:
            print(f"  {row['doc_type']:<14} {row['n']}")

    # Integridade: as tres tabelas derivadas devem ter a mesma contagem
    # de chunks. Divergencia significa indice inconsistente.
    vetores = conn.execute("SELECT count(*) FROM chunks_vec").fetchone()[0]
    cheios = conn.execute("SELECT count(*) FROM chunk_vectors_full").fetchone()[0]
    if not (chunks == vetores == cheios):
        print(f"\nINCONSISTENCIA: {chunks} chunks, {vetores} vetores indexados, "
              f"{cheios} vetores cheios")
        print("reconstrua o indice: rag forget <dir> e rag index <dir>")

    conn.close()
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Valida a stack de ponta a ponta: servico, modelos, GPU, embeddings."""
    from .ollama import OllamaClient, OllamaError

    problemas = []

    with OllamaClient() as client:
        print(f"ollama:      {client.host}")
        if not client.is_alive():
            print("  ERRO: sem resposta. Rode: systemctl start ollama")
            return 1
        print("  servico no ar")

        instalados = client.installed_models()
        for papel, modelo in (("geracao", CONFIG.generate_model), ("embed", CONFIG.embed_model)):
            achou = any(m == modelo or m.startswith(f"{modelo}:") for m in instalados)
            print(f"  {papel:<9} {modelo}  {'ok' if achou else 'AUSENTE'}")
            if not achou:
                problemas.append(f"ollama pull {modelo}")

        if problemas:
            print("\nfaltando:")
            for cmd in problemas:
                print(f"  {cmd}")
            return 1

        # Embedding real: confirma dimensao e que o truncamento cabe.
        try:
            vector = client.embed_one("teste de sanidade")
        except OllamaError as exc:
            print(f"  ERRO no embed: {exc}")
            return 1

        print(f"\nembeddings:  {len(vector)} dims -> {CONFIG.embed_dims_indexed} indexadas")
        if len(vector) != CONFIG.embed_dims_full:
            print(f"  AVISO: esperava {CONFIG.embed_dims_full} dims")
        if CONFIG.embed_dims_indexed > len(vector):
            print("  ERRO: RAG_EMBED_DIMS maior que a dimensao do modelo")
            return 1

        # Geracao curta: mede tok/s e confirma que foi para a GPU.
        print("\ngerando...", flush=True)
        _, stats = client.generate("Responda apenas: ok", num_ctx=512)
        if stats:
            print(f"  {stats.tokens_per_second:.1f} tok/s  ({stats.eval_count} tokens)")

        print("\nresidentes:")
        for m in client.loaded_models():
            tam = m.get("size", 0) / 1_073_741_824
            vram = m.get("size_vram", 0) / 1_073_741_824
            pct = f"{vram / tam * 100:.0f}% GPU" if tam else "?"
            print(f"  {m.get('name', '?'):<26} {tam:.1f} GB  {pct}")

    print(f"\nindice:      {CONFIG.db_path}"
          f"  {'ok' if CONFIG.db_path.exists() else 'nao criado (rag init-db)'}")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    import time

    from .indexer import index_directory
    from .ollama import OllamaClient

    root = Path(args.directory).expanduser()
    if not root.is_dir():
        print(f"nao e' um diretorio: {root}")
        return 1

    # Dry-run antes de qualquer trabalho: num diretorio grande, saber o
    # que seria indexado vale mais do que descobrir no meio da ingestao.
    from .parsers import discover_detailed

    varredura = discover_detailed(
        root,
        extra_ignores=tuple(args.exclude or ()),
        include_boilerplate=args.include_boilerplate,
    )

    if args.dry_run:
        print(f"{root}\n")
        print(f"{len(varredura.files)} arquivo(s) entrariam no indice")
        if varredura.skipped:
            print(varredura.report())
        if varredura.files:
            print("\namostra:")
            for caminho in varredura.files[:20]:
                print(f"  {caminho.relative_to(root)}")
            if len(varredura.files) > 20:
                print(f"  ... e mais {len(varredura.files) - 20}")
        return 0

    if not varredura.files:
        print(f"nada a indexar em {root}")
        if varredura.skipped:
            print(varredura.report())
        return 0

    if not CONFIG.db_path.exists():
        print("indice nao existe. Rode: rag init-db")
        return 1

    conn = connect()
    inicio = time.monotonic()

    def progresso(acao: str, caminho: str) -> None:
        if not args.quiet:
            marca = {"novo": "+", "atualizado": "~", "erro": "!"}.get(acao, " ")
            print(f"  {marca} {Path(caminho).name}")

    with OllamaClient() as client:
        if not client.is_alive():
            print("ollama fora do ar. Rode: systemctl start ollama")
            conn.close()
            return 1

        print(f"indexando {root} ({len(varredura.files)} arquivos)")
        stats = index_directory(
            conn,
            root,
            client,
            prune=not args.no_prune,
            on_progress=progresso,
            extra_ignores=tuple(args.exclude or ()),
            include_boilerplate=args.include_boilerplate,
        )

    decorrido = time.monotonic() - inicio
    print(f"\n{stats.resumo()}  em {decorrido:.1f}s")

    if stats.erros:
        print(f"\n{len(stats.erros)} erro(s):")
        for caminho, motivo in stats.erros[:10]:
            print(f"  {Path(caminho).name}: {motivo}")

    conn.close()
    return 1 if stats.erros else 0


def cmd_search(args: argparse.Namespace) -> int:
    from .ollama import OllamaClient
    from .search import search

    if not CONFIG.db_path.exists():
        print("indice nao existe. Rode: rag init-db")
        return 1

    conn = connect()
    with OllamaClient() as client:
        if not client.is_alive():
            print("ollama fora do ar. Rode: systemctl start ollama")
            conn.close()
            return 1

        resultado = search(
            conn,
            args.question,
            client,
            limit=args.limit,
            include_restricted=args.restricted,
        )

    if not resultado.hits:
        print("nenhum trecho encontrado")
        if not args.restricted:
            print("(documentos restritos ficam de fora; use --restricted)")
        conn.close()
        return 0

    for posicao, hit in enumerate(resultado.hits, start=1):
        origem = []
        if hit.vec_rank:
            origem.append(f"vec#{hit.vec_rank}")
        if hit.fts_rank:
            origem.append(f"fts#{hit.fts_rank}")
        marca = " restrito" if hit.sensitivity == "restricted" else ""

        print(f"\n[{posicao}] {hit.citation}{marca}")
        print(f"    dist {hit.distance:.4f}  rrf {hit.score:.4f}  {'+'.join(origem)}")
        corpo = " ".join(hit.content.split())
        print(f"    {corpo[:240]}{'...' if len(corpo) > 240 else ''}")

    if args.verbose:
        total = sum(resultado.timings.values())
        etapas = "  ".join(f"{k} {v * 1000:.0f}ms" for k, v in resultado.timings.items())
        print(f"\ncandidatos: {resultado.vec_found} vetorial, {resultado.fts_found} textual")
        print(f"tempo: {etapas}  | total {total * 1000:.0f}ms")

    conn.close()
    return 0


def cmd_forget(args: argparse.Namespace) -> int:
    """Remove do indice tudo que veio de um diretorio.

    O indice e' cache derivado do corpus (RNF-04): apagar nunca perde
    dado original, so obriga a reindexar.
    """
    root = Path(args.directory).expanduser().resolve()
    conn = connect()

    total = conn.execute(
        "SELECT count(*) FROM documents WHERE source_path LIKE ?", (f"{root}/%",)
    ).fetchone()[0]

    if not total:
        print(f"nada indexado sob {root}")
        conn.close()
        return 0

    if not args.yes:
        resposta = input(f"remover {total} documento(s) sob {root}? [s/N] ")
        if resposta.strip().lower() not in {"s", "sim", "y", "yes"}:
            print("cancelado")
            conn.close()
            return 0

    with transaction(conn):
        conn.execute("DELETE FROM documents WHERE source_path LIKE ?", (f"{root}/%",))

    print(f"{total} documento(s) removidos")
    conn.close()
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .answer import ask_stream, Answer
    from .ollama import OllamaClient
    from .search import Hit

    if not CONFIG.db_path.exists():
        print("indice nao existe. Rode: rag init-db")
        return 1

    conn = connect()
    with OllamaClient() as client:
        if not client.is_alive():
            print("ollama fora do ar. Rode: systemctl start ollama")
            conn.close()
            return 1

        for item in ask_stream(
            conn, args.question, client, include_restricted=args.restricted
        ):
            if isinstance(item, list):
                if not item:
                    continue
                print("trechos:")
                for numero, hit in enumerate(item, start=1):
                    marca = " [restrito]" if hit.sensitivity == "restricted" else ""
                    print(f"  [{numero}] {hit.citation}{marca}")
                print()
            elif isinstance(item, Answer):
                print()
                if args.verbose:
                    etapas = "  ".join(
                        f"{k} {v * 1000:.0f}ms" for k, v in item.timings.items()
                    )
                    print(f"\n{etapas}")
                    if item.stats:
                        print(f"{item.stats.tokens_per_second:.1f} tok/s")
            else:
                print(item, end="", flush=True)

    conn.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    if not CONFIG.db_path.exists():
        print("indice nao existe. Rode: rag init-db")
        return 1

    serve(port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag",
        description="Base de conhecimento pessoal, local e consultavel.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-db", help="cria ou migra o indice")
    p_init.set_defaults(func=cmd_init_db)

    p_status = sub.add_parser("status", help="mostra o estado do indice")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="valida ollama, modelos e GPU")
    p_check.set_defaults(func=cmd_check)

    p_index = sub.add_parser("index", help="indexa um diretorio do corpus")
    p_index.add_argument("directory", help="diretorio a indexar, recursivamente")
    p_index.add_argument("--no-prune", action="store_true",
                         help="nao remove do indice arquivos que sumiram do disco")
    p_index.add_argument("-q", "--quiet", action="store_true", help="so o resumo final")
    p_index.add_argument("--dry-run", action="store_true",
                         help="mostra o que seria indexado, sem indexar")
    p_index.add_argument("--exclude", action="append", metavar="GLOB",
                         help="padrao a ignorar (repetivel)")
    p_index.add_argument("--include-boilerplate", action="store_true",
                         help="nao descarta licencas e changelogs")
    p_index.set_defaults(func=cmd_index)

    p_forget = sub.add_parser("forget", help="remove do indice tudo sob um diretorio")
    p_forget.add_argument("directory")
    p_forget.add_argument("-y", "--yes", action="store_true", help="nao pergunta")
    p_forget.set_defaults(func=cmd_forget)

    p_search = sub.add_parser("search", help="busca trechos no indice")
    p_search.add_argument("question", help="pergunta em linguagem natural")
    p_search.add_argument("-n", "--limit", type=int, default=CONFIG.context_chunks)
    p_search.add_argument("--restricted", action="store_true",
                          help="inclui documentos restritos")
    p_search.add_argument("-v", "--verbose", action="store_true",
                          help="mostra tempos e contagem de candidatos")
    p_search.set_defaults(func=cmd_search)

    p_ask = sub.add_parser("ask", help="pergunta e recebe resposta ancorada")
    p_ask.add_argument("question")
    p_ask.add_argument("--restricted", action="store_true")
    p_ask.add_argument("-v", "--verbose", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    p_serve = sub.add_parser("serve", help="sobe a interface web local")
    p_serve.add_argument("-p", "--port", type=int, default=CONFIG.port)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
