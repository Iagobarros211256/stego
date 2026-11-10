# Backlog — Uso futuro do Strix | Stego — RAG local

**ID:** STEGO-STRIX-001  
**Prioridade:** P2 — média  
**Responsável/condição:** Projeto próprio

## Para que usar

Investigar segurança da API local, SSE, acesso a arquivos e integração entre recuperação de documentos e execução de comandos.

## Cenários de segurança propostos (não são falhas confirmadas)

- Acesso indevido à API GET /api/ask e permissões de leitura.
- Vazamento de caminhos, trechos privados, logs e documentos indexados.
- Abuso de parâmetros HTTP/SSE ou integrações que executem comandos.
- Prompt injection como trilha complementar com avaliação dedicada, sem presumir cobertura total do Strix.

## Como usar (plano futuro)

1. Criar corpus totalmente sintético no SQLite/FTS5/sqlite-vec e usar instância Ollama de teste.
2. Isolar o serviço em loopback e verificar quais portas e arquivos o agente consegue acessar.
3. Testar entradas e rotas permitidas sem indexar documentos reais.
4. Documentar os achados e criar testes de controle de acesso e limites.

## Restrições específicas

Não compartilhar conteúdo da base RAG pessoal ou dados do disco; nenhum scan irrestrito do sistema hospedeiro.

## Critérios de aceite para um piloto futuro

- [ ] Proprietários/responsáveis aprovaram o escopo, os endpoints, contas e limites.
- [ ] Ambiente de teste isolado e com restauração documentada.
- [ ] Dados e credenciais sintéticos; revisão de privacidade do provedor LLM.
- [ ] Configuração, custo e tempo máximos definidos.
- [ ] Execução piloto registrada com versão do Strix, alvos, logs e evidências.
- [ ] Achados classificados em **confirmado**, **não reproduzido** ou **fora do escopo**.
- [ ] Correções e testes de regressão revisados antes de eventual uso recorrente.
- [ ] Decisão de prosseguir, limitar ou descartar documentada.

## Situação e governança

- **Status:** PENDENTE — avaliação futura; não instalado, não integrado e não executado neste projeto.
- **Data de registro:** 2026-11-09.
- **Ferramenta:** [Strix (usestrix/strix)](https://github.com/usestrix/strix), pentesting automatizado com agentes de IA.
- **Natureza do registro:** proposta técnica, não requisito implementado ou teste concluído.
- **Autorização:** testar somente código, serviços e ambientes próprios ou com autorização explícita dos responsáveis, delimitando por escrito o escopo.
- **Ambiente:** preferir laboratório isolado e reversível, nunca produção, com usuários, documentos e dados sintéticos.
- **Privacidade e custos:** revisar o código, dependências, permissões, política de envio de conteúdo ao provedor LLM, retenção, custos, limites de execução e logs antes de configurar qualquer chave.
- **Impactos:** o Strix pode enviar requisições com efeitos colaterais e tentar explorações; aplicar rate limit, backups/snapshots e rollback.
- **Validação:** separar hipótese/sinal de vulnerabilidade de evidência confirmada; reproduzir e corrigir manualmente antes de declarar resultado.
- **Operações:** este registro **não** autoriza scans, instalação, alteração de repositórios, push/pull, CI, deploy, acesso a dados reais ou alteração de bancos.
