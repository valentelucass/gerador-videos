# Automação de animação

Projeto independente do renderizador horizontal. Ele lê somente as imagens já aprovadas em `assets/images/`, a partir de um manifesto criado pela API local; não altera essas mídias, o roteiro ou o workspace de renderização.

## Preparação

```powershell
cd automation
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install firefox
Copy-Item .env.example .env
```

1. Defina `PLATFORM_URL` em `.env`.
2. Envie as imagens em **Mídias das cenas** no painel. O comando **Animar IA** usa exatamente essas mídias, sem cópia e sem pasta paralela.
3. Escreva o prompt fixo em `prompts/animate.md`.
4. Use **Animar IA** no header. O Firefox abre uma nova guia visível para mostrar todo o processo.

Para reutilizar um perfil Firefox já autenticado, defina `FIREFOX_PROFILE_DIR` no `.env`. Na primeira execução a automação cria um perfil dedicado em `browser-profile/firefox-authenticated/`, descartando locks, caches e abas restauradas. Os próximos disparos usam esse perfil, portanto um login manual feito na janela do robô permanece salvo e o Firefox normal pode continuar aberto.

O painel inicia a automação como `python -m automation.main`, evitando conflito entre o arquivo `selectors.py` do projeto e o módulo padrão do Python.

## Ajuste obrigatório da interface beta

Os textos conhecidos já estão em `selectors.py`, usando role, texto e CSS simples. Como a plataforma não foi inspecionada neste repositório, faça um teste de uma imagem e ajuste os locators desse arquivo se os rótulos forem diferentes.

Se o card não mostrar o nome do arquivo, configure `IMAGE_CARD_SELECTOR` no `.env` com o seletor CSS estável do card. O fluxo usa o nome do arquivo para reencontrar a imagem depois de um refresh; não confie em posição/índice.

## Marcação de clique

Com `HEADLESS=false`, cada clique mostra um contorno vermelho pulsante e um ponto central sobre o alvo real antes da ação. Deixe `SHOW_CLICK_HIGHLIGHT=true` para auditoria visual e ajuste `CLICK_HIGHLIGHT_DURATION_MS` se quiser uma marcação mais lenta ou rápida.

## Recuperação

- Sucesso somente é registrado após a mensagem `Animação concluída!`.
- O robô cria apenas um projeto Vibes. Ele envia grupos consecutivos de até 12 imagens nesse mesmo projeto; após cada toast de sucesso, atualiza a página e confere as miniaturas antes de abrir o próximo upload.
- O upload só avança depois do clique em **Upload/Carregar** e do toast de confirmação do Vibes. Um input interno com contador vazio não é considerado falha.
- Erro explícito na animação preserva o checkpoint da mesma imagem, atualiza o editor e repete somente essa imagem. Nas primeiras falhas espera `ERROR_RETRY_DELAY_SECONDS` (5 s por padrão); depois de `REPEATED_ERROR_THRESHOLD` falhas consecutivas, ativa uma pausa de proteção de `REPEATED_ERROR_WAIT_SECONDS` (300 s por padrão) antes de tentar de novo. Isso evita martelar a plataforma quando ela está saturada.
- Se ela ainda falhar em `MAX_GENERATION_ERRORS_PER_ROUND` tentativas (5 por padrão), é marcada como **adiada** e o robô continua a lista. Ao terminar as demais, ele aguarda `DEFERRED_ROUND_WAIT_SECONDS` e executa nova rodada contendo somente as adiadas. Após `MAX_DEFERRED_ROUNDS` ciclos completos (3 por padrão) ainda com falha, marca as restantes como `failed_final` e encerra para revisão humana.
- Antes de abrir o navegador, cópias binárias idênticas são detectadas por SHA-256 e ignoradas; elas não são enviadas nem animadas. Ao encerrar, a auditoria registra o total de sucessos, vídeos já existentes, duplicatas ignoradas e erros finais.
- Rate limit aguarda `RATE_LIMIT_WAIT_SECONDS` e tenta de novo.
- `state/checkpoint.json` permite reiniciar o processo sem reenviar imagens já concluídas.

Para diagnóstico, `logs/automation.log` descreve ações e exceções; `logs/events.jsonl` registra cada transição, clique, refresh, retry e resultado em formato estruturado; `state/run_state.json` mostra o último ponto seguro do robô.

Pare com `Ctrl+C` quando necessário. Não apague `state/` para retomar; apague-o apenas se quiser reprocessar imagens concluídas.
