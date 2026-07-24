# SynthReel

Gerador local de vídeos documentais horizontais para YouTube. O SynthReel recebe um roteiro JSON estruturado, associa uma imagem a cada trecho da narração, cria a voz com Edge TTS e compõe um vídeo 16:9 em 1920×1080 com cartões, fullscreen, transições, trilha, efeitos e anotações sincronizadas.

> Estado atual: a esteira **horizontal** é a implementação ativa. A esteira vertical continua reservada por contrato, mas não possui motor executável neste repositório. Os dois fluxos não devem ser misturados.

## O que o projeto faz

- Gera narração neural pelo Edge TTS, em cadência documental (`-10%`).
- Aceita roteiros em `pt-BR`, `pl-PL`, `hr-HR`, `en-US`, `es-ES` e `de-DE`, incluindo as vozes permitidas para cada locale.
- Usa os time-codes de palavras retornados pelo TTS para sincronizar fala, imagem, anotações e efeitos — não estima a duração só pela quantidade de palavras.
- Monta imagens estáticas com zoom suave e B-roll horizontal do Pexels, em fullscreen ou cartões sobre um fundo animado.
- Aplica transições, efeitos sonoros, trilha em loop e ducking por `sidechaincompress` enquanto há narração.
- Valida IDs, uma cena por bloco, a sequência de imagens e os assets antes de iniciar o FFmpeg.
- Processa uma renderização pesada por vez e mantém o andamento, eventos e log técnico por trabalho.

## Arquitetura

```text
Roteiro JSON + imagens + fundo + trilha
                │
                ▼
       Painel React / Vite (porta 5173)
                │ HTTP
                ▼
       API FastAPI (porta 8000)
                │
   validação → associação de imagens → fila exclusiva
                │
                ▼
 Edge TTS + time-codes → compositor FFmpeg segmentado
                │
                ▼
 workspace/lotes_horizontais/finalizados/<titulo>-<job>.mp4
```

O backend é autônomo: a API chama diretamente o renderizador em `backend/src/core/horizontal_renderer.py`; não há scripts externos de renderização na cadeia da API.

## Requisitos

- Windows, PowerShell, Python 3.11+ e Node.js 20+ recomendados.
- `ffmpeg` e `ffprobe` instalados e acessíveis no `PATH`.
- Acesso à internet durante a síntese: o Edge TTS usa o serviço de voz da Microsoft.
- Espaço livre: por padrão, a API exige pelo menos **8 GiB** livres antes de aceitar um render.

Confirme as dependências principais:

```powershell
python --version
node --version
ffmpeg -version
ffprobe -version
```

Se os binários não estiverem no `PATH`, defina seus caminhos antes de iniciar:

```powershell
$env:FFMPEG_BIN = "C:\caminho\para\ffmpeg.exe"
$env:FFPROBE_BIN = "C:\caminho\para\ffprobe.exe"
```

## Início rápido

Na raiz do projeto, instale as dependências uma única vez:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

cd ..\frontend
npm install
```

Depois, para iniciar o painel e a API juntos:

```powershell
cd frontend
npm start
```

Abra o endereço informado pelo Vite, normalmente `http://localhost:5173`.

O iniciador sobe o backend em `http://127.0.0.1:8000`, espera a API responder e então inicia o painel. Ele encerra processos anteriores nas portas 8000 e 5173; não execute um segundo `npm start` ao mesmo tempo. Use `Ctrl+C` no mesmo terminal para parar ambos.

Para desenvolvimento independente do backend:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn src.main:app --reload --port 8000
```

## Fluxo de produção no painel

1. Cole ou importe o roteiro JSON.
2. Valide o roteiro; o painel pode medir a duração real da narração antes do render.
3. Gere os prompts visuais, se necessário, e crie/obtenha as imagens 16:9.
4. Clique em **Buscar B-roll** para revisar vídeos Pexels horizontais nas cenas `video_generico`; aprove uma opção ou altere a busca em inglês e tente novamente.
5. Envie as imagens das cenas `imagem`. O painel também permite corrigir o vínculo manualmente por cena.
6. Escolha ou envie o fundo; selecione o movimento do fundo e a trilha.
7. Clique em **Gerar vídeo** e acompanhe a fila/progresso.
8. Ao terminar, abra o MP4 publicado pelo link do painel.

O exemplo completo [`roteiro.json`](./roteiro.json) pode ser importado como referência. O prompt para pedir novos roteiros a outra IA está em [`backend/PROMPT_JSON_ROTEIRO.md`](./backend/PROMPT_JSON_ROTEIRO.md).

## Contrato do roteiro JSON

Cada `block` contém exatamente uma `scene`. Isso mantém uma unidade editorial: um trecho de narração, uma imagem e uma composição visual sincronizados.

```json
{
  "title": "Título específico do vídeo",
  "language": "pt-BR",
  "narrator_gender": "male",
  "voice": "pt-BR-AntonioNeural",
  "background": "black",
  "background_animation": "movimento_sutil",
  "blocks": [
    {
      "id": "block_01",
      "text": "Uma frase curta e natural para esta cena documental.",
      "scenes": [
        {
          "id": "scene_01",
          "image_id": 1,
          "asset_key": "specific-english-visual-key",
          "image": "cena_01.png",
          "visual": {
            "subject": "assunto visível específico",
            "action": "ação concreta",
            "setting": "local e atmosfera",
            "framing": "enquadramento documental horizontal 16:9",
            "details": "detalhes visuais, sem texto, logotipos ou marca-d'água"
          },
          "transition": {
            "in": "zoom_in",
            "out": "to_right",
            "speed": "normal"
          },
          "sounds": {
            "transition": ["whoosh_soft"],
            "context": { "type": "click", "at": "start" }
          },
          "annotation": {
            "lines": ["NOTA CURTA"],
            "at": "middle",
            "emoji": "💡"
          }
        }
      ]
    }
  ]
}
```

Regras importantes:

- `id` de blocos e cenas deve ser único; `image_id` deve ser sequencial, começando em 1.
- `image`, IDs e vínculos aceitam somente nomes de arquivo, nunca caminhos.
- `asset_key` é uma chave visual curta, única, em inglês e separada por hífens. Ela ajuda a associar imagens com nomes gerados por IA.
- `transition.in: "zoom_in"` produz fullscreen; `from_left`, `from_right` e `none` produzem cartão sobre o fundo. As saídas aceitas são `to_left`, `to_right` e `none`.
- Os efeitos disponíveis são `whoosh_fast`, `whoosh_cinematic`, `whoosh_soft`, `click`, `wrong_answer`, `camera_shutter`, `cash_register`, `crumpled_paper`, `new_idea`, `boxing_bell`, `paper_flip`, `shutter_click`, `bottle_cork`, `celebration` e `writing`.
- `annotation` é opcional e aceita uma ou duas linhas, com até 32 caracteres por linha.
- A voz é parte do roteiro. A API não aceita substituí-la no pedido de renderização.

Para boa retenção, planeje geralmente 10–12 cenas por minuto, cerca de 8–12 palavras por cena e 3–7 segundos de áudio. A validação acústica aborta a renderização se uma cena ultrapassar **9 segundos**; o limite total da narração é 20 minutos.

## Imagens, B-roll e associação sem depender da ordem de envio

As imagens das cenas ficam em `assets/images/`. Use PNG, JPG, JPEG ou WEBP. Há três formas de associação, nesta ordem:

1. O arquivo tem o prefixo do ID, por exemplo `5 - arqueologo-laboratorio.jpeg`.
2. O arquivo tem nome descritivo e o backend compara o nome com `asset_key` e o `visual` da cena.
3. O operador escolhe explicitamente o arquivo no seletor da cena no painel.

A ordem de upload nunca é usada como critério. Isso reduz associações erradas quando uma ferramenta de imagem salva arquivos com nomes próprios. O endpoint de prompts também fornece um prompt visual e um nome sugerido por cena.

Cenas `video_generico` usam MP4 em `assets/videos/`. O botão **Buscar B-roll** consulta o Pexels com `orientation=landscape`, apresenta prévias para curadoria humana e só baixa o vídeo aprovado. O painel mostra o texto original da cena e permite pedir a versão em português quando o roteiro estiver em outro idioma. **Abrir pasta dos vídeos** abre exatamente essa pasta local no Explorador de Arquivos.

## Áudio, visual e sincronização

O renderizador trabalha em 1920×1080 a 24 fps. Imagens recebem composição com corte proporcional e movimento suave; o fundo pode usar `none`, `movimento_sutil`, `movimento_lateral` ou `pulsacao`.

A narração é sintetizada uma única vez e o Edge TTS entrega os limites temporais de cada palavra. Esses limites são alinhados ao texto oficial do JSON para calcular cada cena. Se os time-codes não puderem ser alinhados ou a cena ficar longa demais, o trabalho falha antes da composição final com uma mensagem clara.

O render mantém o som documental por meio de masterização da voz, efeitos declarados no roteiro, trilha escolhida em loop e compressão sidechain para reduzir a música durante a fala. CTAs com anotação podem ganhar uma curta pausa de exibição, sem iniciar uma nova imagem antes de a chamada terminar.

## Pastas e arquivos

```text
backend/
  src/main.py                     API, fila, jobs e endpoints
  src/models.py                   contrato Pydantic do roteiro
  src/services.py                 validação, catálogo e associação semântica
  src/core/tts_neural.py          Edge TTS e catálogo de vozes
  src/core/horizontal_renderer.py compositor FFmpeg horizontal
  PROMPT_JSON_ROTEIRO.md          prompt canônico para gerar roteiro
frontend/
  src/main.tsx                    painel React
  scripts/start.mjs               iniciador conjunto da API e Vite
assets/images/                    imagens de cenas enviadas
assets/videos/                    B-roll Pexels aprovado por cena
fundos/                           fundos estáticos selecionáveis
music/                            trilhas selecionáveis
sound/                            efeitos sonoros do roteiro
workspace/lotes_horizontais/      jobs temporários e entregas
  finalizados/                    MP4s publicados
```

`workspace/`, `assets/images/` e `assets/videos/` são ignorados pelo Git, pois guardam conteúdo local e resultados. Após sucesso, o job temporário é limpo e um manifesto resumido é arquivado em `workspace/lotes_horizontais/finalizados/.jobs/`. Em falhas, o backend preserva diagnóstico como `manifest.json`, `render.log`, `events.jsonl`, roteiro e time-codes; os intermediários são removidos.

## API local

| Método | Rota | Finalidade |
| --- | --- | --- |
| `GET` | `/api/catalog` | Lista imagens, fundos, músicas e sons disponíveis. |
| `POST` | `/api/validate` | Valida roteiro e vínculos; pode medir o timing real. |
| `POST` | `/api/prompts` | Gera prompt visual e nome sugerido para cada cena. |
| `POST` | `/api/images` | Envia imagens de cena. |
| `POST` | `/api/pexels/candidates` | Busca alternativas horizontais para as cenas de B-roll. |
| `POST` | `/api/pexels/download` | Baixa a opção Pexels aprovada para a cena escolhida. |
| `POST` | `/api/pexels/open-folder` | Abre a pasta local de B-roll no sistema operacional. |
| `POST` | `/api/translate` | Traduz uma narração para português sob demanda. |
| `POST` | `/api/backgrounds` | Envia fundos selecionáveis. |
| `POST` | `/api/render` | Cria um job e o coloca na fila exclusiva. |
| `GET` | `/api/jobs/{job_id}` | Consulta status, etapa, progresso e URL de saída. |
| `GET` | `/api/jobs/{job_id}/log` | Lê o log técnico de um job em andamento/falho. |
| `GET` | `/api/jobs/{job_id}/events` | Lê o histórico JSONL do job. |
| `GET` | `/api/voices` | Lista vozes disponíveis e prévias existentes. |
| `POST` | `/api/voice-previews` | Gera em segundo plano as prévias de voz ausentes. |

Arquivos estáticos são publicados em `/assets/...`; os vídeos finais ficam disponíveis em `/outputs/<arquivo>.mp4` enquanto a API está em execução.

## Configuração operacional

| Variável | Padrão | Uso |
| --- | --- | --- |
| `FFMPEG_BIN` | `ffmpeg` | Executável do FFmpeg. |
| `FFPROBE_BIN` | `ffprobe` | Executável do FFprobe. |
| `SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB` | `8` | Reserva mínima de disco, em GiB, antes de iniciar um job. |

Exemplo para reduzir temporariamente a reserva em uma máquina de teste:

```powershell
$env:SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB = "4"
cd frontend
npm start
```

## Diagnóstico rápido

- **O painel não abre:** verifique se `npm install` foi executado em `frontend/` e se as portas 5173/8000 não estão bloqueadas.
- **O backend não inicia:** ative o ambiente virtual e instale `backend/requirements.txt`; confirme `python -m uvicorn backend.src.main:app --port 8000` a partir da raiz.
- **Falha de FFmpeg/FFprobe:** instale os dois binários ou configure `FFMPEG_BIN` e `FFPROBE_BIN`.
- **Imagem pendente:** envie uma imagem com prefixo `image_id`, use um nome descritivo compatível com o brief ou selecione-a manualmente no painel.
- **Cena acima de 9 segundos:** divida o texto em duas cenas e forneça uma imagem para cada uma.
- **Espaço insuficiente:** libere espaço em disco ou ajuste conscientemente `SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB`.
- **Render falhou:** abra o link de log no painel ou consulte `/api/jobs/<job_id>/log`; a pasta do job falho permanece no workspace para investigação.

## Limites e princípios do projeto

- Não há geração automática de narrativa de contingência: roteiro incompleto deve ser corrigido na origem.
- O fluxo horizontal não usa clonagem de voz local, motor vertical, grid vertical nem `workspace/lotes_preparados/`.
- A renderização aceita imagens estáticas e B-roll horizontal aprovado. A geração das imagens e a aprovação dos vídeos continuam sendo etapas humanas antes do render.
- Não inicie vários renders para tentar acelerar o processo: a fila serial é intencional para proteger memória e estabilidade do FFmpeg.
