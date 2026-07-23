# SynthReel Manual da Arquitetura Paralela (HITL)

Este documento define as regras de negocio estritas do SynthReel. O projeto agora possui duas esteiras isoladas: a esteira Vertical de lotes curtos e a esteira Horizontal de videos longos para YouTube. Nenhuma automacao deve misturar contratos, assets, caminhos, TTS ou regras de renderizacao entre elas.

## 1. Arquitetura Paralela Obrigatoria

- **Esteira Vertical:** o contrato permanece reservado, mas nao ha motor vertical executavel nesta versao do repositorio. Nenhum fluxo horizontal deve tentar suprir essa ausencia.
- **Esteira Horizontal:** o motor ativo e `backend/src/core/horizontal_renderer.py`, acionado pela API em `backend/src/main.py`, com saida em `workspace/lotes_horizontais/`. O alvo e video YouTube 16:9 em 1920x1080, com narrativa longa e sound design persistente.
- Os motores sao independentes. Modulos como `tts_clonador.py`, `pipeline.py` e regras de grid vertical nao devem ser adaptados silenciosamente para a horizontal.
- A horizontal deve usar seus proprios modulos dedicados, como `tts_neural.py` e `layout_factory.py`, sem alterar o comportamento legado da vertical.

## 2. Contratos da Esteira Vertical

- A inteligencia artificial local foi descontinuada. A entrada vertical exige um arquivo `lote.json` injetado externamente em `entradas_lotes/`.
- A voz clonada local opera em velocidade alta, por volta de 220 palavras por minuto.
- O motor vertical nao confia apenas em numero de cenas; ele valida a contagem de palavras do JSON da nuvem:
  - **Versao Longa (TikTok/Kwai):** obrigatorio ter mais de 60 segundos de audio real. O JSON deve fornecer no minimo 230 palavras.
  - **Versao Curta (Shorts/Reels):** obrigatorio ter mais de 40 segundos de audio real. O JSON deve fornecer no minimo 160 palavras.
- A esteira vertical nao gera texto de contingencia. Se o contrato do JSON for quebrado na entrada, o preparo deve rejeitar o arquivo e abortar.

## 3. Ingestao e Curadoria Vertical

- A tag de busca `busca` vinda do JSON vertical deve ser literal e em ingles.
- Enquanto a esteira vertical nao possuir motor proprio, ela nao deve acionar Pexels nem compartilhar a ingestao horizontal.
- Regra de ingestao desbalanceada:
  - Cena 1 (gancho): tentar baixar de 3 a 4 videos portrait exclusivos.
  - Cenas 2 em diante: baixar de 1 a 2 videos.
- A curadoria HITL vertical confia integralmente em `workspace/lotes_preparados/`. O usuario pode apagar videos ruins e repor arquivos com a mesma nomenclatura, mantendo o `metadata.json` intacto.

## 4. Contrato de Tempo Horizontal

- A esteira horizontal e voltada a videos longos, normalmente acima de 5 minutos, com narracao em paragrafos e cadencia documental.
- O motor acustico horizontal deve usar exclusivamente `TTSNeuralEngine` em `src/core/tts_neural.py`, baseado em `edge-tts`, com rate pausado de `-10%` e respeito a pontuacao.
- O campo `idioma` do JSON horizontal aceita tanto a sigla quanto o locale: `pt`/`pt-BR`, `pl`/`pl-PL`, `hr`/`hr-HR`, `en`/`en-US`, `es`/`es-ES` e `de`/`de-DE`.
- O pipeline roteia essas entradas automaticamente para `pt-BR-AntonioNeural`, `pl-PL-MarekNeural`, `hr-HR-SreckoNeural`, `en-US-GuyNeural`, `es-ES-AlvaroNeural` e `de-DE-ConradNeural`, respectivamente.
- A sintese horizontal nao depende de clonagem de voz local; o foco e clareza, pausas, respiracao narrativa e sustentacao de paragrafo.
- O JSON horizontal deve trazer cenas com informacoes explicitas de layout, como `template_id`, `fonte_midia` e `prompt_ou_busca`.
- Para retencao no YouTube, cada cena horizontal deve ter de 15 a 20 palavras e duracao acustica tipica de 3 a 7 segundos. Qualquer cena calculada pelo Whisper acima de 9,0 segundos deve abortar antes do FFmpeg com erro explicito.

## 5. Templates Baseados em Estado

- O motor horizontal nao deve "chutar" filtros de FFmpeg. Ele recebe do JSON um `template_id` de 1 a 11 e delega a composicao visual para `LayoutFactory`.
- `LayoutFactory` apenas retorna a string exata de `-filter_complex`; ela nao executa subprocessos e nao baixa arquivos.
- Todos os layouts horizontais trabalham em tela fixa 1920x1080.
- Cada input visual deve passar por `scale` com `force_original_aspect_ratio`, `crop` no tamanho exato da caixa e `overlay` em coordenadas X/Y rigidas.
- Inputs fisicos de imagem (`.jpg`, `.jpeg`, `.png`) devem receber `zoompan` centralizado e continuo antes do scale/crop final da caixa. Inputs de video e overlays persistentes, como a seta, nao recebem Ken Burns.
- As posicoes dos elementos sao parte do contrato do template. Se o JSON pedir um template invalido ou midias insuficientes, a renderizacao deve falhar de forma clara.

## 6. Ingestao Hibrida Horizontal

- A API horizontal cria um workspace isolado por job em `workspace/lotes_horizontais/` e preserva o manifesto da renderizacao.
- Quando `fonte_midia` for `pexels`, o script deve chamar o Pexels com `orientation=landscape` e salvar a midia da cena na pasta do tema.
- Quando `fonte_midia` for `ia`, o script nao pode gerar imagem sozinho. Ele deve criar um arquivo `cena_XX_PROMPT_IA.txt` ou `cena_XX_A_PROMPT_IA.txt` com o valor de `prompt_ou_busca`.
- A criacao visual por IA e uma etapa humana fora do pipeline. Antes do render pela API horizontal, o usuario deve colocar fisicamente o JPG/PNG/MP4 correspondente no workspace do job.
- Templates com multiplas midias, como 3, 5, 7, 9 e 10, devem gerar slots separados (`A`, `B`, `C`) para downloads ou prompts, por exemplo `cena_05_A_pexels.mp4` e `cena_05_B_PROMPT_IA.txt`.
- A trava HITL horizontal exige que os prompts TXT sejam substituidos ou acompanhados pelos assets visuais reais antes da renderizacao.

## 7. Tratamento Visual e Integridade

- A API do Pexels pode mentir sobre orientacao. Motores de renderizacao devem usar `ffprobe` local para ler o header real antes de aplicar cortes, escalas ou composicoes.
- Na vertical:
  - Video 9:16 real -> fullscreen.
  - Video 16:9 real -> grid 1x3 com fundo borrado.
  - Foto/estatico -> Ken Burns.
- Na horizontal, a geometria vem do `template_id`. O renderer deve validar se todos os arquivos esperados existem, respeitar 1920x1080 e aplicar o `filter_complex` da `LayoutFactory`.

## 8. Legendas e Sincronia

- O Whisper atua exclusivamente como mapeador acustico de time-code.
- O texto exibido na legenda deve vir do `metadata.json` original, nao de texto alucinado pelo Whisper.
- A vertical usa legendas virais com blocos de no maximo 2 palavras, fonte amarela forte, borda preta e safe zone central.
- A horizontal pode usar uma abordagem mais documental, mas ainda deve preservar a ortografia oficial do JSON e alinhar timestamps por similaridade normalizada quando houver transcricao.

## 9. Sound Design e Assets Persistentes

- Assets persistentes nao devem ser apagados por limpeza de workspace.
- A vertical mantem a regra de mixagem sem normalizacao dinamica: `amix=normalize=0` e volumes rigidos para voz, trilha e transicoes.
- A horizontal deve usar a base dedicada em `workspace/assets/horizontal/`:
  - `trilhas/`
  - `overlays/`
  - `fundos_estaticos/`
- As transicoes audiovisuais horizontais ficam exclusivamente em subpastas de `overlays/`. Cada clipe sorteavel deve conter video e audio sincronizados no mesmo arquivo; clipes silenciosos nao entram no pool.
- `setup_assets_horizontal.py` deve criar essas pastas e auditar `fundo_documentario.mp3`, `seta_apontamento.png` e a presenca das colecoes de transicoes dentro de `overlays/`.
- A mixagem horizontal deve usar `sidechaincompress` matematicamente: a voz/narracao deve atuar como sidechain para reduzir a trilha de fundo quando houver fala, em vez de apenas somar tudo em volume fixo.
- `overlays/seta_apontamento.png` e um asset estatico de composicao; as subpastas de `overlays/` armazenam os cortes audiovisuais. Nenhum desses arquivos e midia de cena baixada do Pexels.

## 10. Regra Geral de Segurança

- Nenhum script deve criar texto narrativo de emergencia para mascarar JSON incompleto.
- Nenhuma etapa deve misturar `workspace/lotes_preparados/` com `workspace/lotes_horizontais/`.
- Nenhuma etapa horizontal deve depender da curadoria vertical, e nenhuma etapa vertical deve depender dos templates horizontais.
- Se uma dependencia critica estiver ausente, o script deve falhar com erro claro e preservar os arquivos ja preparados para curadoria humana.
