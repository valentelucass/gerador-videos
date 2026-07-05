# SynthReel Assets

Esta pasta guarda assets persistentes do projeto. A limpeza automatica do
SynthReel nao apaga nada daqui.

## `background_music/`

Musicas de fundo disponiveis para o motor escolher aleatoriamente a cada video.

Regras futuras de uso:

- Escolher uma faixa aleatoria por render.
- Baixar bem o volume da trilha para nao disputar com a narracao.
- Volume atual de referencia na mixagem: `0.10`.
- Cortar ou repetir a musica ate a duracao final do video.
- Fazer fade in e fade out curto no inicio e no fim.
- Misturar a trilha no audio final depois da narracao principal.
- Manter `amix` sem normalizacao dinamica para a voz nao subir quando outros sons terminam.
- Permitir render sem musica quando a execucao pedir.

Formatos aceitos:

- `.mp3`
- `.wav`
- `.m4a`

## `transitions/`

Transicoes visuais em formato de video, normalmente com audio embutido.
Elas nao sao apenas efeitos sonoros.

Exemplos atuais:

- `FILM/`
- `FILM CLUTTER/`

Regras futuras de uso:

- Escolher uma transicao aleatoria entre os cortes das cenas.
- Aplicar a transicao por cima do video principal como overlay.
- Preservar e mixar o audio embutido da transicao no corte, sempre em volume baixo.
- Volume atual de referencia do audio embutido: `0.22`.
- Ajustar a transicao para cobrir o ponto de corte sem deslocar a narracao.
- Usar preferencialmente assets curtos, de impacto, com alpha/chroma/blend quando o arquivo permitir.
- Usar janelas curtas, abaixo de meio segundo sempre que possivel, para nao poluir os cortes.
- Permitir render sem transicoes quando a execucao pedir.

Formatos aceitos:

- `.mp4`
- `.mov`
- `.webm`

## Importante

`src/workspace/temp/` e `src/workspace/output/` sao descartaveis.
`src/workspace/assets/` e `src/workspace/voice_refs/` sao persistentes.
