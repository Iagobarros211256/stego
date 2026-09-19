"""Parsers: arquivo do disco -> Document.

Um parser declara as extensoes que atende e como ler. O registro
resolve qual usar. Acrescentar PDF ou conversas exportadas e' registrar
mais um; o indexador nao muda.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Protocol

from ..documents import Document, detect_sensitivity, hash_content, modified_at


class Parser(Protocol):
    extensions: tuple[str, ...]
    doc_type: str

    def parse(self, path: Path) -> Document: ...


# --- Markdown e texto -------------------------------------------------------

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
FIRST_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)


class MarkdownParser:
    extensions = (".md", ".markdown", ".txt")
    doc_type = "markdown"

    def parse(self, path: Path) -> Document:
        raw = path.read_text(encoding="utf-8", errors="replace")
        body, meta = self._strip_frontmatter(raw)

        return Document(
            source_path=str(path.resolve()),
            doc_type=self.doc_type,
            content=body,
            content_hash=hash_content(raw),
            modified_at=modified_at(path),
            sensitivity=detect_sensitivity(path),
            title=self._title(body, meta, path),
            extra=meta,
        )

    def _strip_frontmatter(self, raw: str) -> tuple[str, dict]:
        """Separa o frontmatter YAML do corpo.

        Sem dependencia de YAML: le so pares `chave: valor` de primeiro
        nivel, que e' o que interessa aqui (titulo, tags). Estrutura
        aninhada e' ignorada em vez de quebrar a ingestao.
        """
        match = FRONTMATTER.match(raw)
        if not match:
            return raw, {}

        meta = {}
        for line in match.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "-", "\t")):
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip("\"'")

        return raw[match.end() :], meta

    def _title(self, body: str, meta: dict, path: Path) -> str:
        if title := meta.get("title"):
            return title
        if match := FIRST_H1.search(body):
            return match.group(1).strip()
        return path.stem


# --- Registro ---------------------------------------------------------------

PARSERS: list[Parser] = [MarkdownParser()]

_BY_EXTENSION = {ext: parser for parser in PARSERS for ext in parser.extensions}

# Diretorios que nunca entram no corpus: controle de versao, caches,
# dependencias de terceiros e artefatos de build. A documentacao de uma
# biblioteca que voce instalou nao e' acervo pessoal.
SKIP_DIRS = {
    # controle de versao e editores
    ".git", ".svn", ".hg", ".idea", ".vscode", ".vs",
    # caches e ambientes
    "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".cache", ".gradle", ".m2", ".tox",
    # dependencias de terceiros
    "node_modules", "vendor", "site-packages", "bower_components",
    "Pods", "third_party", "thirdparty", "external", "externals",
    # artefatos de build
    "target", "build", "dist", "out", "bin", "obj", ".next", ".nuxt",
    # Unity e engines
    "Library", "Temp", "Logs", "UserSettings", "ProjectSettings",
    "Packages", "PackageCache", "Builds", "MemoryCaptures",
    "Plugins", "TextMesh Pro", "Standard Assets", "Editor Default Resources",
    # outros
    "coverage", "htmlcov", ".terraform",
}

# Arquivos que quase nunca sao acervo pessoal: licencas, changelogs de
# dependencia, arquivos gerados por ferramenta.
SKIP_FILENAME_PATTERNS = (
    "license", "licence", "copying", "notice", "authors", "contributors",
    "changelog", "third party notices", "code_of_conduct", "codeofconduct",
    "unityadditionalfile", "projectversion", "libraryformatversion",
    "lastscenemanagersetup", "ofl", "attribution", "linebreaking",
    "readme-zh", "readme.zh",
)

# Arquivo minusculo raramente rende chunk util; arquivo gigante costuma
# ser dump ou log, nao texto escrito por uma pessoa.
MIN_FILE_BYTES = 200
MAX_FILE_BYTES = 5 * 1024 * 1024


def _looks_like_boilerplate(name: str) -> bool:
    baixo = name.lower()
    return any(padrao in baixo for padrao in SKIP_FILENAME_PATTERNS)


def load_ignore_patterns(root: Path) -> list[str]:
    """Le `.ragignore` na raiz: um glob por linha, `#` comenta."""
    arquivo = root / ".ragignore"
    if not arquivo.is_file():
        return []
    padroes = []
    for linha in arquivo.read_text(encoding="utf-8", errors="replace").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#"):
            padroes.append(linha)
    return padroes


def parser_for(path: Path) -> Parser | None:
    return _BY_EXTENSION.get(path.suffix.lower())


def supported_extensions() -> set[str]:
    return set(_BY_EXTENSION)


@dataclass
class Discovery:
    """Resultado da varredura, com o motivo de cada descarte."""

    files: list[Path] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)

    def report(self) -> str:
        if not self.skipped:
            return ""
        partes = [f"{n} {motivo}" for motivo, n in self.skipped.most_common()]
        return "ignorados: " + ", ".join(partes)


def discover_detailed(
    root: Path,
    extra_ignores: Sequence[str] = (),
    include_boilerplate: bool = False,
) -> Discovery:
    """Varre `root` contabilizando o que entrou e o que foi descartado."""
    resultado = Discovery()
    padroes = [*load_ignore_patterns(root), *extra_ignores]

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        if SKIP_DIRS & set(path.parts):
            resultado.skipped["diretorio excluido"] += 1
            continue

        if any(part.startswith(".") for part in path.parts):
            resultado.skipped["oculto"] += 1
            continue

        if parser_for(path) is None:
            resultado.skipped["extensao nao suportada"] += 1
            continue

        relativo = path.relative_to(root)
        if any(
            fnmatch(str(relativo), padrao) or fnmatch(path.name, padrao)
            for padrao in padroes
        ):
            resultado.skipped["ragignore"] += 1
            continue

        if not include_boilerplate and _looks_like_boilerplate(path.name):
            resultado.skipped["licenca/changelog"] += 1
            continue

        try:
            tamanho = path.stat().st_size
        except OSError:
            resultado.skipped["ilegivel"] += 1
            continue

        if tamanho < MIN_FILE_BYTES:
            resultado.skipped["pequeno demais"] += 1
            continue
        if tamanho > MAX_FILE_BYTES:
            resultado.skipped["grande demais"] += 1
            continue

        resultado.files.append(path)

    return resultado


def discover(root: Path, **kwargs) -> list[Path]:
    return discover_detailed(root, **kwargs).files
