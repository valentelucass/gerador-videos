# Roteiro JSON — guia rápido

Este arquivo serve como instrução para outra LLM criar roteiros que o gerador aceita.

## Regras

- Um JSON representa uma única versão linguística de um vídeo.
- `language` é o locale da narração, por exemplo `pt-BR`, `en-US` ou `es-ES`.
- `narrator_gender` aceita somente `male` ou `female`.
- Escreva a fala em `blocks[].text`; ela é a fonte oficial do áudio.
- Cada bloco possui uma ou mais `scenes`. Planeje uma cena a cada dois segundos de fala.
- Cada imagem deve durar de 1 a 3 segundos. Depois do TTS, o sistema verifica se há imagens suficientes.
- A imagem é enviada depois pelo painel e deve ter o mesmo nome indicado em `image`.
- Não inclua resolução, FPS, codec, nome de saída ou música no roteiro. A música é escolhida no painel.
- `background_animation` é opcional e anima somente a foto de fundo. Aceita `movimento_sutil`, `movimento_lateral`, `pulsacao` e `none`. O padrão é `movimento_sutil`.

## Cena visual

`visual` descreve a imagem que será gerada no Google Flow. Preencha sempre:

- `subject`: quem ou o que aparece;
- `action`: o que acontece;
- `setting`: cenário, fundo e atmosfera;
- `framing`: enquadramento e posição na imagem;
- `details`: elementos importantes, iluminação e restrições.

O painel transforma esses campos em um prompt para o Google Flow. Não peça textos, legendas, logotipos ou marcas dentro da imagem.

## Transições

- Entrada: `zoom_in`, `from_left`, `from_right` ou `none`.
- Saída: `to_left`, `to_right` ou `none`.
- Velocidade: `fast`, `normal` ou `slow`.

## Sons

`sounds.transition` é uma lista explícita de efeitos para a saída desta cena.
Não há seleção automática, frequência fixa ou padrão de "um clique a cada N
cenas". Deixe a lista vazia quando a troca não pedir som. Um clique pode ser
combinado com um whoosh quando o significado da cena justificar os dois.

IDs disponíveis: `whoosh_fast`, `whoosh_cinematic`, `whoosh_soft`, `click`,
`wrong_answer`, `camera_shutter`, `cash_register`, `crumpled_paper`,
`new_idea`, `boxing_bell`, `paper_flip`, `shutter_click`, `bottle_cork`,
`celebration` e `writing`. `sounds.context` é opcional e usa um destes IDs em
um acontecimento específico da cena, com `at` igual a `start`, `middle` ou
`end`.

## Anotação sincronizada

Use `annotation` somente em ideias que ganham clareza como uma anotação rápida.
Ela abre um blur forte no vídeo, mostra texto muito grande e toca
`keyboard-typing-5997.mp3` no mesmo instante. Escreva no máximo duas linhas de
até 32 caracteres; o texto deve resumir ou enfatizar a fala, não precisa ser
uma transcrição literal. Prefira ganchos curtos com reticências, contraste,
pergunta ou virada, por exemplo `"SEM LUZ DO SOL..."` ou `"MAS COMO?"`.
Não use anotação nos primeiros 10 segundos; depois disso, escolha poucas cenas
em posições narrativas irregulares. Ela desaparece um segundo após a digitação
terminar, sem duração fixa no JSON.

## Exemplo

```json
{
  "title": "O maior crocodilo do mundo",
  "language": "pt-BR",
  "narrator_gender": "male",
  "background": "white",
  "background_animation": "movimento_sutil",
  "blocks": [
    {
      "id": "block_01",
      "text": "É o maior réptil vivo.",
      "scenes": [
        {
          "id": "scene_01",
          "image": "block_01_scene_01.png",
          "visual": {
            "subject": "Um crocodilo-de-água-salgada enorme",
            "action": "abre a boca de frente para a câmera",
            "setting": "fundo claro e limpo, clima documental",
            "framing": "plano médio centralizado",
            "details": "escamas detalhadas, luz suave, sem texto e sem logotipos"
          },
          "transition": {
            "in": "zoom_in",
            "out": "to_right",
            "speed": "normal"
          },
          "sounds": {
            "transition": ["whoosh_fast"],
            "context": null
          },
          "annotation": {
            "lines": ["PREDADOR", "DE EMBOSCADA..."],
            "at": "start"
          }
        }
      ]
    }
  ]
}
```
