# stego

Base de conhecimento pessoal: indexa seus documentos locais e responde
perguntas em linguagem natural, citando o trecho de origem. Roda
inteiramente offline — nenhum dado sai da maquina.

Construido para hardware modesto: uma AMD RX 580 de 8 GB via Vulkan,
sem ROCm e sem CUDA.

## Como funciona

Cada pergunta dispara duas buscas sobre o mesmo indice SQLite: uma
vetorial (semantica, para parafrase) e uma textual por BM25 (literal,
para numeros e nomes proprios). Os resultados sao fundidos por
Reciprocal Rank Fusion e os finalistas reordenados com os vetores em
precisao cheia. Os cinco melhores trechos vao para um modelo local, que
responde apenas com base neles — e recusa quando nao encontra.

## Seguranca

O indice contem o texto integral do que voce indexar. Leia o
[SECURITY.md](SECURITY.md) antes de apontar para pastas com documentos
sensiveis, e rode `--dry-run` antes de qualquer indexacao nova.

## Requisitos

- Python 3.12+
- Ollama rodando em `127.0.0.1:11434` com `llama3.1:8b` e `nomic-embed-text`

## Instalacao

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Uso

```bash
stego check                 # valida ollama, modelos e GPU
stego init-db               # cria ou migra o indice
stego index ~/notas --dry-run   # ve o que entraria, sem indexar
stego index ~/notas             # indexa um diretorio (idempotente)
stego forget ~/notas            # remove do indice o que veio dali
stego search "qual porta?"  # so os trechos, sem gerar resposta
stego ask "qual porta?"     # resposta ancorada, no terminal
stego serve                 # interface web em 127.0.0.1:8080
stego status                # estado atual do indice
```

## Interface

`stego serve` sobe a pagina em `http://127.0.0.1:8080`. A resposta chega
por streaming e os trechos usados aparecem numerados abaixo, com o
caminho do arquivo e de qual metade da busca cada um veio.

A pagina nao faz nenhuma requisicao para fora da maquina: sem CDN, sem
fonte externa, sem telemetria.

## Resposta ancorada

O modelo recebe apenas os trechos recuperados e a instrucao de
responder a partir deles, citando `[numero]`. Quando os trechos nao
contem a resposta, ele recusa em vez de completar com conhecimento
proprio.

## Busca

Cada consulta roda duas buscas e funde os resultados por Reciprocal
Rank Fusion: vetorial (semantica, para parafrase) e FTS5/BM25 (literal,
para numeros e nomes proprios). Os finalistas sao reordenados com os
vetores em precisao cheia.

`stego search -v` mostra de qual metade cada trecho veio e o tempo de
cada etapa.

## Cuidado com segredos

O indexador le qualquer `.md` ou `.txt` que encontrar, inclusive chaves,
tokens e codigos de recuperacao. Use `.ragignore` ou `--exclude` para
mante-los fora, e prefira apontar o indice para pastas de escrita, nao
para a pasta de downloads.

## Corpus

`stego index` percorre o diretorio recursivamente e reindexa apenas o que
mudou desde a ultima passada. Arquivos que sumiram do disco saem do
indice.

**Rode `--dry-run` primeiro.** Diretorios de projeto trazem muito ruido:
documentacao de dependencia, licencas, arquivos gerados. O que fica de
fora por padrao:

- caches, ambientes virtuais, artefatos de build
- dependencias de terceiros (`node_modules`, `vendor`, `site-packages`)
- pastas de engine (`Library`, `PackageCache`, `ProjectSettings`)
- licencas, changelogs e arquivos gerados por ferramenta
- arquivos com menos de 200 bytes ou mais de 5 MB

Para regras proprias, crie um `.ragignore` na raiz do diretorio (um glob
por linha) ou use `--exclude GLOB`, repetivel.

Documentos sob diretorios listados em `STEGO_RESTRICTED_DIRS`
(padrao: `financeiro,financas,documentos-pessoais,pessoal`) entram
marcados como restritos e ficam fora das buscas por padrao.

## Configuracao

Tudo por variavel de ambiente, com padroes em `src/rag/config.py`:

| Variavel | Padrao |
|---|---|
| `STEGO_DB` | `~/.local/share/stego/index.sqlite` |
| `STEGO_OLLAMA_HOST` | `http://127.0.0.1:11434` |
| `STEGO_GENERATE_MODEL` | `llama3.1:8b` |
| `STEGO_EMBED_MODEL` | `nomic-embed-text` |
| `STEGO_EMBED_DIMS` | `256` |
| `STEGO_RESTRICTED_DIRS` | `financeiro,financas,documentos-pessoais,pessoal` |
