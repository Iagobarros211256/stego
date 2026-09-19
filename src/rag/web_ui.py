"""Interface web, embutida como string.

Sem arquivos estaticos, sem CDN, sem fonte externa: a pagina nao faz
nenhuma requisicao para fora da maquina (RNF-01). As familias
tipograficas sao as do sistema.
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acervo</title>
<style>
  :root {
    --papel: #eef0ef;
    --papel-fundo: #e3e6e5;
    --tinta: #16232b;
    --tinta-fraca: #5b6b73;
    --linha: #c9cfce;
    --marca: #256b63;
    --restrito: #8a5a12;
    --serif: Georgia, "Liberation Serif", "Times New Roman", serif;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: var(--papel-fundo);
    color: var(--tinta);
    font-family: var(--sans);
    line-height: 1.5;
  }

  .folha {
    max-width: 46rem;
    margin: 0 auto;
    padding: 2rem 1.25rem 6rem;
  }

  header { padding: 1.5rem 0 2rem; }

  h1 {
    font-family: var(--serif);
    font-size: 1.5rem;
    font-weight: 400;
    margin: 0 0 .25rem;
    letter-spacing: .01em;
  }

  .sub { color: var(--tinta-fraca); font-size: .875rem; margin: 0; }

  /* --- busca --- */

  form { position: relative; }

  .campo {
    display: flex;
    align-items: stretch;
    background: var(--papel);
    border: 1px solid var(--linha);
    border-radius: 2px;
  }

  .campo:focus-within {
    border-color: var(--marca);
    box-shadow: 0 0 0 3px rgba(37,107,99,.15);
  }

  input[type=text] {
    flex: 1;
    border: 0;
    background: none;
    padding: .9rem 1rem;
    font-family: var(--serif);
    font-size: 1.0625rem;
    color: var(--tinta);
    min-width: 0;
  }

  input[type=text]:focus { outline: none; }

  button {
    border: 0;
    border-left: 1px solid var(--linha);
    background: none;
    color: var(--marca);
    font: inherit;
    font-size: .875rem;
    padding: 0 1.15rem;
    cursor: pointer;
  }

  button:hover:not(:disabled) { background: rgba(37,107,99,.08); }
  button:disabled { color: var(--tinta-fraca); cursor: default; }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--marca); outline-offset: 2px; }

  .opcoes {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
    margin-top: .6rem;
    font-size: .8125rem;
    color: var(--tinta-fraca);
    flex-wrap: wrap;
  }

  .alternador { display: flex; align-items: center; gap: .45rem; cursor: pointer; }
  .alternador input { accent-color: var(--restrito); }
  .alternador.ligado { color: var(--restrito); }

  /* --- resposta --- */

  .resposta {
    font-family: var(--serif);
    font-size: 1.125rem;
    line-height: 1.65;
    margin: 2.5rem 0 0;
    white-space: pre-wrap;
    min-height: 1.65em;
  }

  .resposta:empty { display: none; }

  .cursor::after {
    content: "";
    display: inline-block;
    width: .5em;
    height: 1.05em;
    background: var(--marca);
    vertical-align: -.15em;
    animation: piscar 1.1s steps(2) infinite;
  }

  @keyframes piscar { 50% { opacity: 0; } }

  .recusa { color: var(--tinta-fraca); font-style: italic; }

  /* --- fontes --- */

  .fontes { margin-top: 2.5rem; border-top: 1px solid var(--linha); }

  .fontes h2 {
    font-size: .8125rem;
    font-weight: 600;
    color: var(--tinta-fraca);
    margin: 1.25rem 0 .75rem;
  }

  ol { list-style: none; margin: 0; padding: 0; counter-reset: fonte; }

  li {
    counter-increment: fonte;
    display: grid;
    grid-template-columns: 2rem 1fr;
    gap: .25rem 0;
    padding: .9rem 0;
    border-bottom: 1px solid var(--linha);
  }

  li::before {
    content: "[" counter(fonte) "]";
    color: var(--marca);
    font-size: .8125rem;
    font-variant-numeric: tabular-nums;
    padding-top: .12rem;
  }

  .titulo { font-weight: 600; font-size: .9375rem; }
  .titulo .tag { color: var(--restrito); font-weight: 400; font-size: .8125rem; }

  .caminho {
    grid-column: 2;
    font-size: .75rem;
    color: var(--tinta-fraca);
    word-break: break-all;
  }

  .trecho {
    grid-column: 2;
    font-family: var(--serif);
    font-size: .9375rem;
    line-height: 1.55;
    color: var(--tinta-fraca);
    margin-top: .4rem;
    max-height: 4.7em;
    overflow: hidden;
    cursor: pointer;
  }

  .trecho.aberto { max-height: none; }

  .sinais {
    grid-column: 2;
    margin-top: .4rem;
    font-size: .6875rem;
    color: var(--tinta-fraca);
    font-variant-numeric: tabular-nums;
  }

  /* --- rodape --- */

  .medidas {
    margin-top: 1.5rem;
    font-size: .75rem;
    color: var(--tinta-fraca);
    font-variant-numeric: tabular-nums;
  }

  .erro {
    margin-top: 2rem;
    padding: .9rem 1rem;
    border-left: 3px solid var(--restrito);
    background: var(--papel);
    font-size: .875rem;
  }

  .vazio { margin-top: 3rem; color: var(--tinta-fraca); font-size: .9375rem; }
  .vazio p { margin: 0 0 .5rem; }
  code {
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    font-size: .8125em;
    background: var(--papel);
    padding: .1em .35em;
  }

  @media (prefers-reduced-motion: reduce) {
    .cursor::after { animation: none; }
  }
</style>
</head>
<body>
<div class="folha">

  <header>
    <h1>Acervo</h1>
    <p class="sub" id="resumo">carregando...</p>
  </header>

  <form id="form">
    <div class="campo">
      <input type="text" id="pergunta" autocomplete="off" autofocus
             placeholder="O que voce quer encontrar?">
      <button type="submit" id="enviar">Buscar</button>
    </div>
    <div class="opcoes">
      <label class="alternador" id="rotulo-restrito">
        <input type="checkbox" id="restrito">
        Incluir documentos restritos
      </label>
      <span id="modelo"></span>
    </div>
  </form>

  <div id="erro" class="erro" hidden></div>
  <div id="resposta" class="resposta"></div>

  <section id="fontes" class="fontes" hidden>
    <h2>Trechos usados</h2>
    <ol id="lista"></ol>
    <p class="medidas" id="medidas"></p>
  </section>

  <div id="vazio" class="vazio" hidden></div>

</div>

<script>
const form = document.getElementById('form');
const campo = document.getElementById('pergunta');
const enviar = document.getElementById('enviar');
const restrito = document.getElementById('restrito');
const rotuloRestrito = document.getElementById('rotulo-restrito');
const respostaEl = document.getElementById('resposta');
const fontesEl = document.getElementById('fontes');
const listaEl = document.getElementById('lista');
const medidasEl = document.getElementById('medidas');
const erroEl = document.getElementById('erro');
const vazioEl = document.getElementById('vazio');

let fonte = null;

restrito.addEventListener('change', () => {
  rotuloRestrito.classList.toggle('ligado', restrito.checked);
});

fetch('/api/stats').then(r => r.json()).then(d => {
  const restritos = d.restricted ? `, ${d.restricted} restritos` : '';
  document.getElementById('resumo').textContent =
    `${d.documents} documentos, ${d.chunks} trechos${restritos}`;
  document.getElementById('modelo').textContent = d.model;
  if (d.documents === 0) {
    vazioEl.hidden = false;
    vazioEl.innerHTML =
      '<p>O indice esta vazio.</p>' +
      '<p>Indexe uma pasta com <code>rag index ~/suas-notas</code> ' +
      'e recarregue esta pagina.</p>';
  }
}).catch(() => {});

form.addEventListener('submit', e => {
  e.preventDefault();
  const pergunta = campo.value.trim();
  if (!pergunta) return;

  if (fonte) fonte.close();
  respostaEl.textContent = '';
  respostaEl.classList.add('cursor');
  respostaEl.classList.remove('recusa');
  listaEl.innerHTML = '';
  fontesEl.hidden = true;
  erroEl.hidden = true;
  vazioEl.hidden = true;
  enviar.disabled = true;
  enviar.textContent = 'Buscando';

  const url = `/api/ask?q=${encodeURIComponent(pergunta)}` +
              `&restricted=${restrito.checked ? 1 : 0}`;
  fonte = new EventSource(url);

  fonte.addEventListener('hits', ev => {
    const hits = JSON.parse(ev.data);
    enviar.textContent = 'Lendo';
    if (!hits.length) return;

    fontesEl.hidden = false;
    for (const h of hits) {
      const li = document.createElement('li');

      const titulo = document.createElement('div');
      titulo.className = 'titulo';
      titulo.textContent = h.citation;
      if (h.sensitivity === 'restricted') {
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = ' restrito';
        titulo.appendChild(tag);
      }

      const caminho = document.createElement('div');
      caminho.className = 'caminho';
      caminho.textContent = h.source_path;

      const trecho = document.createElement('div');
      trecho.className = 'trecho';
      trecho.textContent = h.content;
      trecho.title = 'clique para expandir';
      trecho.addEventListener('click', () => trecho.classList.toggle('aberto'));

      const sinais = document.createElement('div');
      sinais.className = 'sinais';
      const origem = [];
      if (h.vec_rank) origem.push(`semantica #${h.vec_rank}`);
      if (h.fts_rank) origem.push(`textual #${h.fts_rank}`);
      sinais.textContent = `distancia ${h.distance} · ${origem.join(' + ')}`;

      li.append(titulo, caminho, trecho, sinais);
      listaEl.appendChild(li);
    }
  });

  fonte.addEventListener('token', ev => {
    respostaEl.textContent += JSON.parse(ev.data);
  });

  fonte.addEventListener('done', ev => {
    const d = JSON.parse(ev.data);
    respostaEl.classList.remove('cursor');
    if (d.refused) respostaEl.classList.add('recusa');

    const partes = Object.entries(d.timings).map(([k, v]) => `${k} ${v}ms`);
    if (d.tokens_per_second) partes.push(`${d.tokens_per_second} tok/s`);
    medidasEl.textContent = partes.join('  ·  ');

    fonte.close();
    fonte = null;
    enviar.disabled = false;
    enviar.textContent = 'Buscar';
  });

  fonte.addEventListener('error', ev => {
    respostaEl.classList.remove('cursor');
    erroEl.hidden = false;
    erroEl.textContent = ev.data
      ? JSON.parse(ev.data)
      : 'A conexao caiu. Verifique se o Ollama esta rodando.';
    if (fonte) { fonte.close(); fonte = null; }
    enviar.disabled = false;
    enviar.textContent = 'Buscar';
  });
});
</script>
</body>
</html>
"""
