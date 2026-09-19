# Seguranca

Este sistema le documentos pessoais e os guarda em texto em um indice
local. Algumas consequencias que vale ter claras.

## O indice e' dado sensivel

`index.sqlite` contem o **texto integral** de tudo que foi indexado,
nao apenas vetores. Quem tem o arquivo tem o acervo.

- Fica em `~/.local/share/stego/` por padrao, fora do repositorio.
- Esta no `.gitignore`. Nunca versione, nunca anexe, nunca suba para
  nuvem sem cifra.
- Backup: trate como trataria os documentos originais.

## O indexador nao distingue segredo de nota

Qualquer `.md` ou `.txt` legivel entra: chaves de API, codigos de
recuperacao de conta, arquivos `.env`. Uma vez indexado, o conteudo
pode aparecer numa resposta.

Antes de indexar um diretorio novo:

```bash
stego index <dir> --dry-run
```

Mantenha um `.ragignore` na raiz de cada diretorio indexado. Use o
`.ragignore.exemplo` deste repositorio como ponto de partida.

## O servidor nao tem autenticacao

`stego serve` escuta em `127.0.0.1` e assume um unico usuario na maquina.
Nao exponha a porta: sem login, qualquer um que alcance o endereco le
todo o acervo. Nao rode atras de proxy reverso publico, nao mude o host
para `0.0.0.0`, e cuidado com encaminhamento de porta por SSH.

## Documentos restritos

Documentos sob diretorios listados em `RAG_RESTRICTED_DIRS` ficam fora
das buscas por padrao e so aparecem com o alternador ligado. Isso e'
reducao de exposicao acidental, **nao e' controle de acesso**: quem tem
o arquivo do indice le tudo, restrito ou nao.

## Rede

Nenhuma requisicao sai da maquina durante consulta ou indexacao. A
pagina nao carrega fonte, script ou estilo externo. O unico trafego e'
para o Ollama em `127.0.0.1:11434`.

Para verificar:

```bash
ss -tnp | grep -E 'rag|ollama'
```

## Reportar um problema

Este e' um projeto pessoal. Abra uma issue.
