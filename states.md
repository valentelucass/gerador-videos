# Estado do projeto — SynthReel

> Documento operacional persistente. Atualize-o sempre que uma decisão de arquitetura, contrato, pendência ou marco de implementação mudar. Ele deve permitir retomar o trabalho sem depender do histórico da conversa.

## Visão geral

O SynthReel é um gerador local de vídeos documentais longos para YouTube. A implementação ativa é a **esteira horizontal**, composta por painel React/Vite, API FastAPI, narração Edge TTS e composição FFmpeg.

A esteira **vertical** permanece somente como contrato de negócio; ela não possui motor executável neste repositório. Não reutilizar, adaptar ou acoplar código da vertical à horizontal.

## Arquitetura atual

```text
frontend/                         Painel React/Vite (porta 5173)
backend/src/main.py               API FastAPI e fila serial de renderização (porta 8000)
backend/src/models.py             Contratos Pydantic do roteiro e requisições
backend/src/services.py           Validações, catálogo e associação de imagens
backend/src/core/tts_neural.py    TTS Edge com time-codes e vozes permitidas
backend/src/core/horizontal_renderer.py
                                  Composição horizontal com FFmpeg
assets/images/                    Imagens estáticas das cenas
assets/videos/                    B-roll horizontal aprovado
fundos/                           Fundos selecionáveis
music/                            Trilhas
sound/                            Efeitos sonoros
workspace/lotes_horizontais/      Jobs, diagnóstico e entregas
```

O fluxo da API é: roteiro JSON validado → associação de mídia → fila exclusiva → TTS → composição FFmpeg → MP4 em `workspace/lotes_horizontais/finalizados/`.

## Contratos e regras inegociáveis

- Renderização horizontal em 1920×1080, 16:9 e 60 fps.
- A horizontal usa apenas `TTSNeuralEngine`/`edge-tts`, com cadência `-10%`; não usa clonagem de voz local.
- Idiomas aceitos: `pt`/`pt-BR`, `pl`/`pl-PL`, `hr`/`hr-HR`, `en`/`en-US`, `es`/`es-ES`, `de`/`de-DE`, roteados para as vozes definidas em `tts_neural.py`.
- Uma cena deve ter texto curto (em geral 15–20 palavras); duração acústica acima de 9 segundos deve falhar antes do FFmpeg.
- O texto exibido deve conservar o texto oficial do JSON. Não criar narrativa de contingência.
- Cada `block` possui exatamente uma `scene`; IDs são únicos e `image_id` é sequencial a partir de 1.
- Imagens e vídeos são vinculados pelo ID/nome semântico ou por escolha manual, nunca pela ordem de upload.
- B-roll Pexels é horizontal e precisa de aprovação humana antes da composição.
- A trilha horizontal sofre ducking com `sidechaincompress` quando há narração.
- Uma única renderização pesada é executada por vez para preservar memória e estabilidade.
- Jobs com falha preservam manifesto, logs, eventos, roteiro e time-codes para diagnóstico.
- Não misturar `workspace/lotes_preparados/` (vertical) com `workspace/lotes_horizontais/` (horizontal).

### Esteira vertical — contrato reservado

- Não há motor vertical executável atualmente. A automação horizontal ou externa nunca deve tentar substituir essa ausência.
- A entrada vertical é um `lote.json` fornecido externamente em `entradas_lotes/`; não há IA local para gerar texto.
- Sem texto de contingência: contrato inválido deve ser rejeitado e os arquivos já preparados devem ser preservados.
- Versão longa (TikTok/Kwai): mínimo de 230 palavras e mais de 60 segundos reais de áudio. Versão curta (Shorts/Reels): mínimo de 160 palavras e mais de 40 segundos.
- `busca` deve ser uma tag literal em inglês. A vertical não aciona Pexels enquanto não houver motor próprio e não compartilha ingestão com a horizontal.
- A curadoria vertical ocorre em `workspace/lotes_preparados/`. O operador pode substituir mídia mantendo a mesma nomenclatura e o `metadata.json`.
- Regras futuras da vertical: vídeo 9:16 real ocupa tela; 16:9 real usa grid 1×3 com fundo borrado; imagem estática recebe Ken Burns. Legendas têm até duas palavras, amarelas, com borda preta e safe zone central.

### Esteira horizontal — layout, ingestão e integridade

- O JSON horizontal deve declarar `template_id`, `fonte_midia` e `prompt_ou_busca` por cena. Os templates válidos são de 1 a 11.
- `LayoutFactory` somente produz o `-filter_complex`; não executa processos nem baixa mídia. O renderer deve falhar claramente para template inválido ou quantidade insuficiente de mídias.
- Todo elemento visual passa por `scale` com preservação de proporção, `crop` exato da caixa e `overlay` em coordenadas rígidas. Imagem física recebe `zoompan` centralizado; vídeo e overlay persistente não.
- `fonte_midia=pexels` busca em paisagem e salva a mídia no workspace do tema. A orientação real deve ser conferida localmente com `ffprobe`, pois a API pode informar orientação incorreta.
- `fonte_midia=ia` não cria imagem automaticamente: gera o TXT do slot, como `cena_05_PROMPT_IA.txt` ou `cena_05_A_PROMPT_IA.txt`. O operador deve inserir JPG/PNG/MP4 real antes do render.
- Templates com múltiplas mídias (3, 5, 7, 9 e 10) usam slots separados `A`, `B` e `C`. A trava HITL exige assets reais para todos os slots.
- A geração de imagens por IA e a curadoria Pexels são etapas humanas externas ao pipeline.

### Legendas, tempo e som

- Whisper serve exclusivamente para mapear time-codes; nunca é fonte de texto. Legendas preservam a ortografia do JSON/`metadata.json` e podem ser alinhadas por similaridade normalizada.
- Assets persistentes não podem ser removidos em limpezas de workspace.
- A base dedicada horizontal é `workspace/assets/horizontal/`, com `trilhas/`, `overlays/` e `fundos_estaticos/`.
- `overlays/seta_apontamento.png` é um elemento estático. Subpastas de `overlays/` contêm transições audiovisuais; cada clipe utilizável deve ter vídeo e áudio no mesmo arquivo, e clipes silenciosos não entram no pool.
- `setup_assets_horizontal.py` deve criar/auditar a estrutura e verificar `fundo_documentario.mp3`, `seta_apontamento.png` e as coleções de transições.
- Vertical, quando existir, usa `amix=normalize=0` e volumes rígidos; a horizontal usa ducking matemático por `sidechaincompress`.

## Dependências e execução local

- Windows, Python 3.11+ (o projeto existente), Node.js 20+, FFmpeg e FFprobe no `PATH`.
- Backend: `backend/.venv`, dependências em `backend/requirements.txt`.
- Painel e API: executar `npm start` dentro de `frontend/`.
- Configurações relevantes: `FFMPEG_BIN`, `FFPROBE_BIN`, `SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB` e, opcionalmente, `SYNTHREEL_RENDER_CACHE`.
- Arquivos locais pesados e credenciais são ignorados pelo Git: `.env`, `workspace/`, `assets/images/` e `assets/videos/`.

## Estado de trabalho

| Área | Estado | Observação |
| --- | --- | --- |
| Motor horizontal | Ativo | API → TTS → FFmpeg implementados. |
| Motor vertical | Reservado | Não há motor executável nesta versão. |
| Painel web | Ativo | React/Vite consome a API local. |
| Automação externa de animação | Ativa | Isolada em `automation/`, acionada pelo header e executada no Firefox Playwright visível. |

## Pendências

- [x] Criar a automação Playwright em uma pasta independente `automation/`.
- [x] Integrar a automação à Biblioteca de mídias do painel; as imagens aprovadas em `assets/images/` são a única entrada física.
- [x] Criar `automation/prompts/animate.md` como fonte do prompt fixo de “Manual animate”.
- [x] Implementar configuração `.env`, log rotativo, artefatos de diagnóstico e browser com perfil persistente.
- [x] Implementar máquina de estados, `RetryManager` com backoff/jitter, refresh recuperável e checkpoint persistente por imagem.
- [x] Integrar controles da automação ao header do painel, via API local isolada (`/api/automation`).
- [~] Validar a UI real inteira do Vibes e ajustar seletores finos de cartões/resultados. A URL `https://vibes.ai/`, o perfil Firefox e o modal real de upload já foram configurados; o upload usa o seletor dinâmico disparado pela área “Click to add or drag and drop media”.
- [ ] Validar mensagens de sucesso, erro e rate limit na interface real e ajustá-las como padrões configuráveis.
- [x] Executar teste controlado simulado de upload → Manual Animate → sucesso, incluindo logs/auditoria e validação de substituição de arquivo. O teste também confirmou que um upload incompleto dispara refresh e reenvia todo o grupo de 12 arquivos.

## Próxima ação acordada

Na próxima execução manual pelo header, retomar a URL Vibes configurada e observar o primeiro ciclo da coluna esquerda: o robô deve chegar ao fim físico, animar o último item, esperar o toast verde sair, atualizar o editor e só então escolher o próximo item acima. A automação permanece isolada em `automation/` e não toca em assets, workspaces, TTS ou renderizador da esteira horizontal.

## Registro de decisões

- **2026-08-01:** automação de uma plataforma beta será construída separadamente do gerador de vídeos, priorizando resiliência, checkpoints e recuperação após refresh/rate limit.
- **2026-08-01:** o painel controla a automação somente por endpoints locais de processo; não importa Playwright no frontend e não acopla a automação à fila de renderização horizontal.
- **2026-08-01:** a automação recebe um manifesto temporário das mídias já enviadas no painel. A pasta `automation/images/` deixou de fazer parte do fluxo e não deve ser apagada automaticamente, pois pode conter arquivos locais do operador.
- **2026-08-01:** o modal atual do Vibes não expõe um input de arquivo antes do clique. A automação primeiro procura um input existente e, caso não exista, clica na área de upload dentro de `expect_file_chooser`, seleciona os 12 arquivos e só confirma quando o botão `Upload` ou `Carregar` estiver habilitado. Se algum cartão não surgir, ela salva evidências, atualiza a página e reinicia o grupo completo.
- **2026-08-01:** na primeira execução a automação cria um perfil Firefox dedicado a partir da sessão autenticada, sem disputar o lock do navegador regular; nas demais reutiliza esse perfil. Se o Vibes solicitar autenticação, ela identifica a tela, permanece aberta sem refresh e aguarda o login manual do operador antes de continuar.
- **2026-08-01:** a automação é iniciada pela API como pacote (`python -m automation.main`), evitando que `automation/selectors.py` conflite com o módulo `selectors` da biblioteca padrão em execuções diretas.
- **2026-08-01:** diagnóstico do Vibes mostrou que um input interno retornava `files.length=0` mesmo após as miniaturas do lote aparecerem. A automação deixou de validar esse input genérico: usa o `FileChooser` gerado pela área do modal, clica em `Upload`/`Carregar` quando habilitado e aguarda o toast de confirmação. O limite é por envio, portanto todos os grupos de 12 são anexados ao mesmo projeto Vibes; após cada sucesso há refresh e espera pelas miniaturas. Só depois começa a animação foto a foto. Os thumbnails são reencontrados por `img[alt]`, não por texto visível. Em erro, refresh e nova tentativa são separados por 5 s configuráveis; refresh só é permitido após erro explícito, rate limit ou timeout de confirmação.
- **2026-08-01:** o botão `Upload`/`Carregar` não constitui sucesso. A confirmação de cada grupo passou a exigir exclusivamente uma mensagem completa de êxito no toast do Vibes (o toast verde); o log e a auditoria armazenam o texto desse toast. Mensagens de erro/rate limit têm precedência sobre qualquer toast de sucesso residual. Somente depois dessa confirmação o projeto é recarregado e as miniaturas do grupo são verificadas antes do próximo lote.
- **2026-08-01:** toda transição crítica do Vibes passou a ser transacional: `Create new` só é considerado concluído quando `Upload media` aparece; `Upload media` só é concluído quando o modal de arquivos aparece. Se a página permanecer no destino anterior, a automação registra URL/origem/destino, atualiza a tela, aguarda o intervalo configurado e tenta a transição novamente. Um clique processado tardiamente é reconhecido pelo destino antes de qualquer novo clique.
- **2026-08-01:** os thumbnails reais da galeria Vibes usam `data-analytics-id="creation_gallery.thumbnail_click"` e `img alt=""`; nomes de arquivo não podem ser usados para validar upload. Depois do toast verde `Media uploaded`, a automação conta os cards da galeria, confirma o total esperado e segue para o próximo grupo de 12 no mesmo projeto. Se a galeria demorar, ela apenas atualiza/aguarda a renderização — jamais reenvia um lote cujo toast já confirmou sucesso.
- **2026-08-01:** o checkpoint passou a separar `upload confirmado` de `animação concluída`. Cada arquivo recebe assinatura, URL do projeto e marca de upload imediatamente após o toast verde. Em qualquer reinício, a automação abre o projeto salvo e agrupa somente os arquivos ainda sem upload; não usa o checkpoint de animação para decidir o que reenviar. As informações de upload são preservadas enquanto a imagem avança pelos estados de animação.
- **2026-08-01:** antes da fase de animação, cada arquivo enviado é associado ao `data-analytics-media-id` do card Vibes. A seleção posterior usa esse ID, e não posição/nome, pois a galeria move uma mídia concluída para o topo.
- **2026-08-01:** a tela fatal do Vibes (`Something went wrong!` / `An error occurred while processing your request` / botão `Try again`) é reconhecida em todas as esperas críticas. O robô registra mensagem/URL/estado, aguarda `PLATFORM_ERROR_REFRESH_DELAY_SECONDS` (5 s por padrão), atualiza a mesma página de projeto e retoma o estado corrente, sem reiniciar o fluxo nem reenviar lote confirmado.
- **2026-08-01:** se a URL de projeto salva abrir a tela `No project yet` (por exemplo, porque o projeto foi apagado), o vínculo de checkpoint é inválido. A automação limpa esse estado de projeto, volta à home e cria um projeto novo por `Create new`; daí envia todas as mídias para o novo projeto.
- **2026-08-01:** o Firefox dedicado da automação usa `BROWSER_COLOR_SCHEME=dark` por padrão. O contexto Playwright anuncia `prefers-color-scheme: dark` para o Vibes e, no Firefox, aplica preferências de tema escuro também à janela do navegador.
- **2026-08-01:** um acionamento trabalha com exatamente um projeto Vibes. Se o checkpoint da seleção atual indicar arquivos enviados para URLs diferentes, ele é considerado inconsistente (tipicamente projeto apagado/recriado) e é limpo antes do início. A automação cria um projeto único e envia novamente a seleção completa em grupos de 12; por exemplo, 50 mídias geram `12 + 12 + 12 + 12 + 2`.
- **2026-08-01:** na fase de animação, os thumbnails da coluna esquerda do editor são selecionados pelo `img[alt="nome-do-arquivo"]` dentro do botão, começando do fim da lista. O editor `Edit video` é terminal e marcado como `skipped_video`; `Edit image` segue para `Manual animate`. O sucesso real dessa fase é o toast inglês `Animation complete!`, que passa a ser reconhecido pelo checkpoint.
- **2026-08-01:** `automation/.env` aceita `RESUME_URL`. Quando preenchida por uma URL Vibes de projeto/conteúdo validada pelo operador, a automação abre essa URL, considera o upload já concluído para a seleção atual e entra diretamente na fase de animação; não recria projeto nem envia arquivos. A API expõe esse trabalho pendente e o botão `Animar IA` exige confirmação explícita de continuidade antes de disparar o processo.
- **2026-08-01:** em um editor Vibes já aberto, a ordem de animação vem da coluna esquerda, não do manifesto local. Como essa coluna é virtualizada, a automação encontra o contêiner rolável, leva-o ao fim físico e exige duas leituras estáveis antes de escolher o último thumbnail pendente; assim não confunde o último item renderizado com o último item real. Depois de cada toast `Animation complete!`, ela espera o toast encerrar e atualiza o editor antes da próxima escolha. Isso permite ao Vibes mover o vídeo concluído ao topo e mantém o processamento visual de baixo para cima sem selecionar esse vídeo recém-criado.
- **2026-08-01:** o toast real de falha do Vibes usa `Generation failed` / `Something went wrong. Please try again.`. Essas mensagens são erro recuperável: o checkpoint da mídia permanece `retrying`, o editor é atualizado e a mesma imagem é localizada novamente antes de um novo `Manual animate`; a lista lateral não avança. Retomadas por URL `/content/` aguardam o editor ficar pronto e obrigatoriamente começam pela coluna esquerda, evitando o fallback prematuro que escolhia o último arquivo do manifesto local.
- **2026-08-01:** falhas consecutivas de geração na mesma mídia também são tratadas como saturação implícita. Após três toasts de erro consecutivos, a automação faz uma pausa de proteção configurável de cinco minutos (`REPEATED_ERROR_WAIT_SECONDS=300`) e só então repete a mesma mídia; antes disso mantém a espera curta normal de cinco segundos. Esse contador é local à mídia e jamais autoriza avançar a coluna lateral.
- **2026-08-02:** a reserva agora usa cinco falhas por mídia e por rodada (`MAX_GENERATION_ERRORS_PER_ROUND=5`). Após o percurso normal, são permitidos no máximo três ciclos completos compostos somente pelas adiadas (`MAX_DEFERRED_ROUNDS=3`). Se uma delas ainda falhar, recebe `failed_final`, entra no relatório de revisão manual e a automação encerra em vez de insistir indefinidamente. Antes da execução, duplicatas binárias exatas são removidas por SHA-256; a avaliação final separa sucesso, vídeo já existente, duplicata ignorada e erro final.
- **2026-08-02:** o `.gitignore` protege dados locais e pesados: mídias de cena, a pasta legada `automation/images/`, logs/auditoria, checkpoints, screenshots, perfis Firefox, workspaces, ambientes Python/Node e caches. `automation/logs/.gitkeep` continua versionável para preservar a estrutura sem enviar logs.
- **2026-08-02:** a contagem de falhas de geração é persistida por imagem no checkpoint (`generation_error_count`). Interromper ou reabrir o navegador no meio da rodada não devolve a mídia à tentativa 1; a retomada continua, por exemplo, da falha 3 para as falhas 4 e 5 antes de adiar. Cada nova rodada da reserva zera esse contador e dá cinco chances novas àquelas mídias.
- **2026-08-02:** o orçamento de cinco falhas agora cobre tanto o toast `Generation failed` quanto travas técnicas de seleção, `Manual animate`, preenchimento e botão `Animate`. Cada etapa tem limite configurável de 90 s; ao esgotá-lo, a falha é registrada para a mesma mídia, seguida de refresh e contagem normal. Isso elimina loops internos infinitos que antes impediam o estado `deferred`/`failed_final`.
- **2026-08-02:** foi criado `media-collector/`, um protótipo Firefox local e independente para detectar nas requisições da aba apenas URLs diretas de mídia não protegida, listá-las e enviá-las à fila normal de downloads. Ele não remove marca d'água, não trata DRM nem reconstrói HLS/DASH; o teste é carregado temporariamente por `about:debugging`.
- **2026-08-02:** o `media-collector/` passou a operar somente com vídeo. Ao abrir ou acionar “Varrer página”, combina tráfego de rede com os elementos e recursos já carregados da aba, mostra contagem e resolução/dimensão conhecida ou estimada. “Baixar todos” mantém um único candidato de maior qualidade por grupo conservador de variantes diretas; streams HLS/DASH continuam apenas sinalizados, sem reconstrução.
- **2026-08-02:** a extensão Firefox recebeu ID estável, empacotador `media-collector/package.ps1` e instruções para assinatura privada (unlisted/self-distribution) no AMO. Firefox Release só preserva extensões assinadas pela Mozilla; pacotes gerados em `media-collector/dist/` são locais e ignorados pelo Git.
- **2026-08-02:** para cumprir a validação atual do AMO, o manifesto declara explicitamente `data_collection_permissions.required=["none"]`. A extensão não transmite nem armazena dados fora do navegador; por isso essa é a declaração correta para a assinatura privada.
- **2026-08-02:** a entrega horizontal foi elevada para 1080p60. O compositor agora usa a RX 7600 com AMF em `high_quality`/`quality`, perfil H.264 High, CQP 18/20/22, preanalysis, VBAQ e reforço para alto movimento. Isso aumenta substancialmente custo de GPU, tempo e espaço temporário, mas preserva melhor detalhes antes da recompressão de plataformas; fontes de 24/30 fps não recebem movimento nativo novo.
- **2026-08-02:** o prompt de animação foi ajustado para reduzir aparência artificial: movimento principal contido, duas a quatro animações ambientais coerentes e brilho/partículas sutis nas camadas já existentes. A câmera pode fazer um único movimento cinematográfico sutil (push-in, pull-back ou deriva lateral) quando ajudar a cena; pessoas podem continuar ações já implícitas. Texto, fonte, logos e elementos gráficos legíveis ficam pixel-estáveis, enquanto rostos, mãos e dedos recebem proteção estrita contra morphing e gestos novos.
