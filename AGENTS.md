# SynthReel Agents Manual

Este documento define as regras de negócio do motor Text-First do SynthReel.
Ele deve orientar as implementações de roteiro, sincronização, busca de mídias e edição final.

## 1. Contratos de Retenção e Tempo (CRÍTICO)

- A voz clonada opera em velocidade alta (~220 palavras por minuto).
- O motor não confia apenas em "número de cenas". Ele exige contagem rígida de palavras.
- **Versão Longa (TikTok/Kwai):** OBRIGATÓRIO ter mais de 60 segundos de áudio real. O JSON do LLM DEVE gerar no mínimo 230 palavras.
- **Versão Curta (Shorts/Reels):** OBRIGATÓRIO ter mais de 40 segundos de áudio real. O JSON do LLM DEVE gerar no mínimo 160 palavras.
- **Trava de Segurança:** Se a inferência retornar palavras abaixo do contrato, o pipeline DEVE abortar com exceção (`raise`). É proibido usar fallback de roteiro genérico que comprometa a retenção e a monetização.

## 2. Lógica de Busca e Seleção Visual (Pexels)

- A tag de busca gerada pelo LLM DEVE ser literal e concreta (ex: "astronaut walking", "old map"). Conceitos abstratos ("mystery", "dark mood") são proibidos.
- Ordem de tentativa:
  1. Teste 1 (Trava RAM): o ID da mídia não pode estar na lista de mídias já usadas no vídeo atual.
  2. Teste 2: buscar vídeo vertical (9:16).
  3. Teste 3: buscar vídeo horizontal (16:9).
  4. Teste 4: buscar foto como fallback final.
- **Trava de Qualidade:** Se o Pexels falhar em encontrar mídias contextuais em mais de 15% das cenas (acionando textura local preta genérica), o pipeline deve abortar.

## 3. Matemática de Mídias e Ritmo (Atos)

- Cortes base: a cada 2.0s a 3.0s.
- Ato 1 (Gancho): ritmo frenético, 1.0s a 1.5s por corte.
- Ato 2 (Desenvolvimento): 2.0s a 3.0s por corte.
- Ato 3 (Clímax): acelerado nos 10s finais.
- **B-Roll Break:** Se o áudio da cena passar de 3.0s, o motor divide o tempo da cena EXATAMENTE NA METADE (em 2 segmentos iguais, e não em múltiplos picotes) e insere um vídeo secundário contextual.

## 4. Tratamento Visual (FFmpeg)

- Vídeo 9:16 -> Fullscreen puro.
- Vídeo 16:9 -> Grid 1x3 com `boxblur` no fundo e vídeo centralizado.
- Foto -> Filtro Ken Burns com zoom in/out panorâmico contínuo.

## 5. Legendas Virais e Sincronia Anti-Alucinação

- As legendas devem ser queimadas no vídeo final em formato ASS.
- Usar no máximo 2 palavras por bloco de legenda.
- Posição: centro da tela (Safe Zone para Shorts/Reels/TikTok).
- Estilo: fonte grande, amarela, negrito, com borda preta grossa e sombra preta.
- O texto exibido DEVE vir EXCLUSIVAMENTE do roteiro oficial gerado pelo LLM para garantir o PT-BR impecável e a acentuação.
- O algoritmo deve alinhar os timestamps do Whisper ao texto do roteiro usando similaridade normalizada (ex: `SequenceMatcher`). Palavras intrusas "alucinadas" pelo Whisper devem ser puladas matematicamente para não deslocar os tempos da legenda oficial.

## 6. Assets Persistentes e Mixagem de Áudio

- `src/workspace/assets/background_music/`: banco de músicas de fundo. Deve ser subdividido por pastas de "vibe" ou nicho (ex: `dark`, `epic`, `lofi`) para garantir coesão temática.
- Volumes de referência configuráveis: narração em `1.18` a `1.22`, música de fundo subida para `0.18`, transições subidas para `0.35`.
- A mixagem final NÃO PODE normalizar dinamicamente o volume; é OBRIGATÓRIO o uso de `amix=normalize=0` para a voz não oscilar quando trilhas ou transições entram e saem.
- `src/workspace/assets/transitions/`: overlays audiovisuais. O áudio da transição atua apenas na região do corte.
- As pastas de `assets` e `voice_refs/` são persistentes e isoladas da rotina de limpeza de diretórios (`workspace_cleaner`).