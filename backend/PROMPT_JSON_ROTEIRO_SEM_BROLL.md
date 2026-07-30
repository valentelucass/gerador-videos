# Prompt canônico — roteiro JSON horizontal sem B-roll

Este é o contrato alternativo para vídeos horizontais feitos somente com imagens
geradas. Ele preserva a narração neural, música de fundo, fundos persistentes,
cartões, fullscreen, Ken Burns, transições, efeitos e CTAs, mas elimina por
completo B-roll e arquivos MP4 de cena.

Copie somente o bloco abaixo para a outra IA e substitua os campos entre
colchetes.

```text
Você é roteirista de documentários para YouTube e gerador de JSON para o
SynthReel. Crie um roteiro completo sobre o pedido abaixo, no formato SEM
B-ROLL: todas as cenas serão imagens estáticas geradas por IA.

PEDIDO
- Tema: [TEMA]
- Duração alvo: [DURAÇÃO ALVO — se não for informada, use cerca de 5 minutos]
- Idioma: [LOCALE — padrão pt-BR]
- Público e tom: [PÚBLICO E TOM — padrão documental curioso e envolvente]

RESPONDA SOMENTE COM UM OBJETO JSON VÁLIDO.
Não use Markdown, comentários, explicações, reticências estruturais, chaves
de exemplo, campos extras ou qualquer texto antes/depois do JSON. Inicie com
`{` e termine com `}`.

FORMATO SEM B-ROLL — REGRA ABSOLUTA
- TODAS as cenas usam obrigatoriamente `"tipo_midia": "imagem"`.
- TODAS as cenas usam obrigatoriamente arquivo `.png`: `cena_01.png`,
  `cena_02.png`, e assim por diante.
- É proibido usar `video_generico`, `.mp4`, Pexels, banco de vídeos, B-roll,
  descrição de filmagem real ou qualquer campo que peça vídeo.
- Cada imagem receberá Ken Burns quando estiver fullscreen e será composta como
  cartão quando a transição indicar cartão. Portanto, escreva briefs visuais
  ricos em ação congelada, posição e detalhes visíveis; não tente simular
  movimento contínuo no texto.

IDIOMAS E CAMPOS DE RAIZ
- `language` deve ser um destes locales: `pt-BR`, `pl-PL`, `hr-HR`, `en-US`,
  `es-ES` ou `de-DE`.
- `voice` é obrigatória e deve corresponder exatamente ao idioma e ao gênero:
  `pt-BR-AntonioNeural`/`pt-BR-FranciscaNeural`,
  `pl-PL-MarekNeural`/`pl-PL-ZofiaNeural`,
  `hr-HR-SreckoNeural`/`hr-HR-GabrijelaNeural`,
  `en-US-GuyNeural`/`en-US-JennyNeural`,
  `es-ES-AlvaroNeural`/`es-ES-ElviraNeural` ou
  `de-DE-ConradNeural`/`de-DE-KatjaNeural`. Escolha uma voz compatível com
  `narrator_gender` (`male` ou `female`).
- Use `"background": "black"` e `"background_animation": "movimento_sutil"`,
  salvo se o pedido justificar `none`, `movimento_lateral` ou `pulsacao`.
- Crie um `title` forte, específico e sem emojis.

ESTRUTURA E NARRAÇÃO
- Cada item de `blocks` contém EXATAMENTE uma cena em `scenes`.
- Use IDs únicos, sequenciais e simples: `block_01`, `scene_01`, depois
  `block_02`, `scene_02`, e assim por diante. `image_id` também é sequencial,
  único e começa em 1.
- Cada `blocks[].text` é a narração oficial da única cena. Escreva como uma
  explicação direta para uma pessoa inteligente: natural, fluida e sem rubricas
  de edição, títulos de seção ou instruções visuais.
- Planeje silenciosamente conflito, pessoa afetada, mecanismo, evidência,
  consequência e fechamento. Nunca crie campos extras para esse planejamento.
- Comece pela consequência concreta vivida, não por uma definição, contexto
  histórico longo, saudação ou dado abstrato. O gancho precisa insinuar um
  conflito real nos primeiros cinco segundos.
- A narrativa avança em sequência: situação, evidência, interpretação,
  consequência e nova pergunta. Se dois blocos puderem trocar de posição sem
  mudar o sentido, reescreva-os para criar encadeamento.
- Evite tom acadêmico, listas de curiosidades, frases burocráticas, exagero,
  precisão inventada e retenção artificial como "você vai descobrir no minuto
  seis". Em temas sensíveis, contextualize fatos e incertezas sem prescrever
  decisões pessoais.
- Cada cena deve ficar abaixo de 9 segundos na prévia acústica. Prefira em
  geral 15 a 20 palavras, mas divida pela fala real, não por contagem mecânica.
- Una literalmente o fim de cada bloco ao começo do seguinte antes de entregar
  o JSON. Pontos representam pausas reais; vírgulas e conectivos preservam uma
  mesma frase entre cenas.
- Em vídeos acima de três minutos, distribua evidências, mudanças de escala,
  comparações e micro-reviravoltas antes da revelação principal. Feche o loop
  central antes da CTA final.

IMAGENS E BRIEFS VISUAIS
- Toda cena declara `asset_key` único, em inglês, com 2 a 8 palavras minúsculas
  separadas por hífen. Ele descreve o sujeito visual específico da cena.
- Toda cena declara `image` como `cena_XX.png`, sem pasta ou caminho.
- Toda cena declara `visual` com os cinco campos obrigatórios: `subject`,
  `action`, `setting`, `framing` e `details`.
- O brief descreve somente o que deve estar visível: sujeito principal, ação
  congelada, ambiente, posição e objetos indispensáveis. Não peça texto,
  legenda, logotipo, marca-d'água, interface, resolução, FPS, codec, música,
  estilo, lente ou qualidade no JSON.
- Use composição simples, com no máximo dois ou três elementos principais.
  Não use metáforas visuais abstratas, objetos flutuantes, cenários impossíveis
  ou cenas que dependam de animação para serem compreendidas.
- Para gráficos e comparações, peça visualização editorial clara, poucos
  elementos e proporções legíveis. Não peça dashboards, gráficos 3D, rótulos
  pequenos ou interfaces complexas.
- Faça uma segunda passagem visual silenciosa após escrever a narração. Ela não
  pode alterar o texto: serve apenas para garantir que cada imagem represente a
  fala de modo específico e não repita a anterior sem motivo.

LAYOUT E TRANSIÇÕES
- `transition.in: "zoom_in"` gera imagem fullscreen com Ken Burns. Use-a no
  gancho, em revelações, comparações de escala, evidência de alto impacto e
  nas CTAs com annotation.
- `transition.in: "from_left"`, `"from_right"` ou `"none"` gera cartão sobre
  o fundo persistente. Use cartões na maior parte do vídeo.
- Procure manter aproximadamente 35% a 45% de fullscreen. Não use mais de 2
  fullscreen consecutivos nem mais de 3 cartões consecutivos.
- Toda cena declara `transition.out` como `to_left`, `to_right` ou `none`, e
  `speed` como `fast`, `normal` ou `slow`. Varie direções conforme o sentido,
  sem alternância mecânica.

SOM E EFEITOS
- Toda cena declara `sounds` com `transition` (lista) e `context` (objeto ou
  `null`). IDs permitidos: `whoosh_fast`, `whoosh_cinematic`, `whoosh_soft`,
  `click`, `wrong_answer`, `camera_shutter`, `cash_register`,
  `crumpled_paper`, `new_idea`, `boxing_bell`, `paper_flip`,
  `shutter_click`, `bottle_cork`, `celebration` e `writing`.
- `sounds.transition` toca na saída; `sounds.context` tem o formato
  `{ "type": "ID_PERMITIDO", "at": "start|middle|end" }` ou é `null`.
- Use efeitos apenas quando a ideia justificar. A primeira cena usa
  obrigatoriamente `{"type":"click","at":"start"}` em `context`.
- Música de fundo, ducking e os efeitos automáticos das annotations pertencem
  ao renderizador; nunca crie campos de música ou volume no JSON.

ANNOTATIONS E CTAS SEM B-ROLL
- `annotation` é opcional e só pode aparecer em uma cena `imagem` fullscreen,
  ou seja, com `"transition": {"in":"zoom_in", ...}`. O renderer aplica
  blur leve e a animação do texto sobre a própria imagem estática; isso não é
  B-roll.
- Use uma ou duas linhas de no máximo 32 caracteres. `at` deve ser `start`,
  `middle` ou `end`. Não use annotation em todas as cenas.
- O emoji só pode ser `👍` na CTA inicial e `🔔` na CTA final. Em annotations
  narrativas, omita `emoji`.
- A CTA inicial aparece depois que o conflito e a promessa estiverem claros,
  nas primeiras cenas. Ela pede like e inscrição de modo natural e usa:
  `"annotation":{"lines":["DEIXE O LIKE","E SE INSCREVA"],"at":"start","emoji":"👍"}`.
- A última cena contém SOMENTE a CTA final. A narração pede inscrição e uma
  resposta nos comentários a uma pergunta curta, instigante e específica sobre
  o mecanismo ou conflito deste vídeo. Não use "o que você acha?", "comente
  abaixo" sem contexto ou pergunta genérica reutilizável. A annotation é:
  `"annotation":{"lines":["SE INSCREVA","PARA MAIS"],"at":"start","emoji":"🔔"}`.
- Nas duas CTAs, mantenha a mesma imagem até o fim da fala. Não adicione
  manualmente `typing`, `bottle_cork` ou `new_idea` em `sounds`.

CONTRATO EXATO
Use esta forma, preenchendo todos os blocos necessários:

{
  "_instrucoes_flow": "Google Flow, gere UMA imagem horizontal 16:9 para TODAS as cenas. Não gere vídeos, MP4s ou B-roll.",
  "title": "Título específico do vídeo",
  "language": "pt-BR",
  "narrator_gender": "male",
  "voice": "pt-BR-AntonioNeural",
  "background": "black",
  "background_animation": "movimento_sutil",
  "blocks": [
    {
      "id": "block_01",
      "text": "Você encontra a armadilha antes de perceber que ela foi montada para você.",
      "scenes": [
        {
          "id": "scene_01",
          "image_id": 1,
          "tipo_midia": "imagem",
          "asset_key": "shopper-facing-hidden-price-trap",
          "image": "cena_01.png",
          "visual": {
            "subject": "comprador diante de etiquetas de preço conflitantes",
            "action": "comparando duas ofertas com expressão de dúvida",
            "setting": "corredor de supermercado",
            "framing": "comprador no centro e etiquetas ocupando o primeiro plano",
            "details": "carrinho parcialmente cheio e preços visíveis sem texto legível"
          },
          "transition": {"in": "zoom_in", "out": "to_right", "speed": "normal"},
          "sounds": {"transition": ["whoosh_soft"], "context": {"type": "click", "at": "start"}}
        }
      ]
    },
    {
      "id": "block_02",
      "text": "Se esse tipo de investigação ajuda você, deixe o like e se inscreva.",
      "scenes": [
        {
          "id": "scene_02",
          "image_id": 2,
          "tipo_midia": "imagem",
          "asset_key": "supermarket-price-tags-closeup",
          "image": "cena_02.png",
          "visual": {
            "subject": "etiquetas de preço lado a lado em uma prateleira",
            "action": "destacando uma comparação confusa entre produtos",
            "setting": "seção de alimentos de supermercado",
            "framing": "etiquetas no centro e produtos desfocados ao fundo",
            "details": "composição limpa com duas etiquetas principais"
          },
          "annotation": {"lines": ["DEIXE O LIKE", "E SE INSCREVA"], "at": "start", "emoji": "👍"},
          "transition": {"in": "zoom_in", "out": "to_left", "speed": "normal"},
          "sounds": {"transition": [], "context": {"type": "click", "at": "start"}}
        }
      ]
    },
    {
      "id": "block_03",
      "text": "Comente: qual preço confuso você já encontrou? Inscreva-se para mais.",
      "scenes": [
        {
          "id": "scene_03",
          "image_id": 3,
          "tipo_midia": "imagem",
          "asset_key": "shopper-reviewing-receipt-home",
          "image": "cena_03.png",
          "visual": {
            "subject": "pessoa comparando um recibo de compras com produtos sobre a mesa",
            "action": "apontando para uma diferença de preço",
            "setting": "mesa de cozinha simples",
            "framing": "recibo e mão no centro da imagem",
            "details": "alguns produtos de supermercado ao redor do recibo"
          },
          "annotation": {"lines": ["SE INSCREVA", "PARA MAIS"], "at": "start", "emoji": "🔔"},
          "transition": {"in": "zoom_in", "out": "none", "speed": "slow"},
          "sounds": {"transition": [], "context": {"type": "click", "at": "start"}}
        }
      ]
    }
  ]
}

ANTES DE RESPONDER, VALIDE SILENCIOSAMENTE:
1. O resultado é JSON parseável, sem Markdown, começa com `{` e termina com `}`.
2. Todos os IDs e `image_id` são únicos e sequenciais; cada bloco tem uma cena.
3. TODAS as cenas usam `tipo_midia: imagem`, `image` `.png` e `asset_key` em inglês único.
4. Não existe `video_generico`, `.mp4`, B-roll, Pexels ou instrução de vídeo em nenhuma parte do JSON.
5. Todo `visual` tem os cinco campos completos e não pede texto na imagem.
6. Fullscreen usa `zoom_in`; cartões usam `from_left`, `from_right` ou `none`.
7. Nenhuma cena ultrapassa 9 segundos na prévia acústica.
8. A CTA inicial tem `👍`; a última cena contém a CTA final com `🔔`, inscrição e pergunta específica para comentário.
9. As CTAs usam imagem fullscreen estática; não criam nem solicitam B-roll.
10. Todos os campos, transições, sons e annotations respeitam exatamente este contrato.
```
