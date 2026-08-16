# Automação Google Flow em sublotes

Esta esteira é independente da automação Vibes e não lê `roteiro.json`. Ela
lê apenas um TXT com prompts de imagem e animação, sempre em grupos de cinco.

## Formato obrigatório do TXT

```text
[[SCENE 01]]
IMAGE: 16:9 editorial illustration of an orange cat pulling away from a hand.
ANIMATION: Preserve the exact flat 2D illustration. The cat slowly leans back; subtle blink only; no camera movement; no audio; no text.
[[/SCENE]]
```

Repita o bloco para cada cena. O motor rejeita TXT sem `IMAGE:` ou
`ANIMATION:`; ele nunca inventa prompts faltantes.

## Preparação única

1. Instale as dependências já usadas pela automação: `pip install -r automation/requirements.txt` e `playwright install chromium`.
2. Clique em **Abrir Chrome Flow**. O painel abre a home do Flow em um perfil dedicado com depuração remota.
3. Faça login se necessário, escolha manualmente o projeto/chat certo e clique em **Ativar Flow**.
3. Antes de gastar créditos, rode com um TXT de cinco cenas e ajuste os seletores conforme a interface da conta. O Flow não oferece um contrato público de seletores; os atributos devem ser verificados no navegador real.

```powershell
```dotenv
FLOW_BROWSER=chromium
FLOW_CDP_URL=http://127.0.0.1:9222
FLOW_CARD_SELECTOR=[data-testid*='asset']
FLOW_CARD_ID_ATTRIBUTE=data-id
FLOW_CHAT_SELECTOR=textarea
FLOW_SEND_SELECTOR=button[aria-label*='Send']
FLOW_ANIMATE_SELECTOR=button:has-text('Animate')
FLOW_ANIMATION_PROMPT_SELECTOR=textarea
FLOW_ANIMATION_SEND_SELECTOR=button[aria-label*='Generate']
FLOW_DOWNLOAD_SELECTOR=button[aria-label*='Download']
FLOW_DELETE_SELECTOR=button[aria-label*='Delete']
FLOW_CONFIRM_DELETE_SELECTOR=button:has-text('Delete')
FLOW_ERROR_SELECTOR=text=/generation failed|falha ao gerar/i
FLOW_MAX_ATTEMPTS_PER_ITEM=3
```

Execute:

```powershell
python -m automation.google_flow.main --txt .\lote_01_flow.txt
```

Comece copiando `lote_flow.template.txt` e complete os blocos até a cena 25
(ou 75). A automação divide a lista em sublotes de cinco automaticamente.

O `flow_checkpoint.json` associa cada cena ao ID estável do card do Flow. A
imagem só é apagada do projeto depois de o MP4 correspondente ser baixado e
validado por `ffprobe`. Caso um vídeo falhe, somente ele é repetido; as fontes e os itens
concluídos permanecem intactos.
