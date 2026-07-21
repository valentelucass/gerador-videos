"""Monta o roteiro de cinco minutos e o guia de imagens para curadoria humana."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "roteiro_animais_abissais_assustadores_5min.json"


BLOCKS = [
    (
        "Nas profundezas do oceano vivem animais abissais tão estranhos que parecem pesadelos vivos. Antes de descer até esse mundo sem Sol, deixe o like e se inscreva no canal. Agora, prepare-se para encarar os habitantes mais assustadores do fundo do mar.",
        [
            ("Abismo oceânico sem fim", "engole o último feixe de luz", "água preta com partículas suspensas", "plano geral monumental"),
            ("Silhuetas de criaturas abissais", "surgem por instantes da escuridão", "fundo oceânico negro", "plano amplo em contraluz"),
            ("Submarino minúsculo", "desce em direção a uma fenda impossível", "paredes rochosas gigantes", "plano aberto lateral"),
            ("Faróis do submersível", "recortam uma nuvem de sedimento", "abismo azul-preto", "câmera subjetiva frontal"),
            ("Olho desconhecido no escuro", "pisca perto da lente", "água sem luz solar", "close-up extremo"),
        ],
    ),
    (
        "Abaixo de mil metros, a pressão aumenta a cada mergulho e o frio devora qualquer calor. Ali, a escuridão não é um cenário: é uma regra. Para sobreviver, cada animal precisou transformar o próprio corpo em uma ferramenta de caça ou fuga.",
        [
            ("Medidor de profundidade de um submarino", "marca uma descida vertiginosa", "cabine escura iluminada em azul", "close-up dramático"),
            ("Parede submarina colossal", "desaparece no vazio", "rochas negras e partículas", "plano geral vertical"),
            ("Mergulhador em silhueta", "parece minúsculo diante do abismo", "luz de lanterna fraca", "plano aberto"),
            ("Corrente fria e sedimento", "varrem o fundo como fumaça", "planície abissal", "plano baixo cinematográfico"),
            ("Pequena criatura translúcida", "recolhe o corpo para sobreviver", "escuridão azul profunda", "macro lateral"),
        ],
    ),
    (
        "O peixe-pescador talvez seja o rosto mais cruel desse mundo. Seu corpo parece saído de uma lenda de terror, mas sua estratégia é precisa: uma luz balança diante da cabeça, convidando uma presa distraída para perto de dentes enormes.",
        [
            ("Peixe-pescador abissal gigantesco", "flutua imóvel antes de atacar", "água negra absoluta", "close-up frontal ameaçador"),
            ("Isca bioluminescente", "pulsa como uma estrela solitária", "vazio azul-escuro", "macro extremo"),
            ("Dentes transparentes do peixe-pescador", "brilham sob a própria isca", "escuridão total", "macro frontal"),
            ("Pequeno peixe inocente", "nada em direção à luz", "partículas luminosas no abismo", "plano médio lateral"),
            ("Sombra do predador", "cresce atrás da presa", "fundo preto azulado", "câmera baixa de suspense"),
        ],
    ),
    (
        "Quando a distância desaparece, a boca se abre num instante. Não há perseguição longa, nem aviso. No abismo, desperdiçar energia pode ser fatal. Por isso, muitos predadores esperam no escuro até que a própria curiosidade da vítima faça todo o trabalho.",
        [
            ("Boca do peixe-pescador", "se abre de forma explosiva", "água turva iluminada em azul", "close-up congelado"),
            ("Presa em fuga", "some diante de uma fileira de dentes", "abismo escuro", "plano lateral rápido"),
            ("Mandíbula articulada", "avança além do esperado", "fundo sem luz", "macro dramático"),
            ("Nuvem de partículas", "explode após o ataque", "coluna d água negra", "plano fechado"),
            ("Peixe-pescador saciado", "retorna à imobilidade", "escuridão silenciosa", "plano médio frontal"),
        ],
    ),
    (
        "O peixe-víbora leva essa aparência ainda mais longe. Seus dentes são longos demais para caber na boca de um animal comum, e seu corpo escuro quase desaparece na água. Só a luz azul do próprio organismo denuncia que ele está ali.",
        [
            ("Peixe-víbora com dentes enormes", "encara a câmera sem piscar", "oceano negro", "close-up frontal"),
            ("Fileira de presas finas", "ultrapassa os lábios fechados", "luz azul mínima", "macro extremo"),
            ("Corpo alongado do peixe-víbora", "some na escuridão", "partículas bioluminescentes", "plano diagonal"),
            ("Fotóforos azuis", "pulsam pelo ventre do predador", "abismo profundo", "macro lateral"),
            ("Peixe-víbora em silhueta", "surge atrás de uma luz fraca", "vazio submarino", "plano aberto de terror"),
        ],
    ),
    (
        "Outro caçador parece ter sido construído apenas com dentes: o peixe-dragão. Ele usa uma luz vermelha que quase nenhum outro animal consegue enxergar. É como caçar com uma lanterna secreta, invisível para quem está prestes a ser encontrado.",
        [
            ("Peixe-dragão negro", "revela a boca repleta de agulhas", "abismo sem luz", "macro frontal"),
            ("Luz vermelha discreta", "corta o azul escuro", "água profunda", "close-up abstrato"),
            ("Peixe-dragão caçando", "desliza sem mover as nadadeiras", "fundo preto", "plano lateral baixo"),
            ("Olho de presa pequena", "reflete uma luz que não entende", "coluna d água escura", "macro de suspense"),
            ("Predador com mandíbula aberta", "emerge do lado da lente", "escuridão azul", "câmera subjetiva"),
        ],
    ),
    (
        "Nem todo monstro do fundo do mar ataca com rapidez. A enguia-pelicano parece frágil até abrir uma boca tão grande que seu corpo inteiro vira uma rede. Quando alimento é raro, engolir qualquer oportunidade pode ser a única regra.",
        [
            ("Enguia-pelicano de boca fechada", "flutua como uma sombra fina", "abismo azul-preto", "plano médio lateral"),
            ("Enguia-pelicano", "abre a bolsa da boca de forma colossal", "água escura", "plano frontal exagerado"),
            ("Mandíbula em forma de rede", "engole uma nuvem de pequenos peixes", "partículas suspensas", "plano amplo"),
            ("Corpo elástico da enguia", "se dobra no vazio", "fundo totalmente negro", "plano diagonal"),
            ("Cauda luminosa da enguia", "desaparece ao longe", "profundidade infinita", "plano geral"),
        ],
    ),
    (
        "Há ainda criaturas que não precisam de dentes para causar desconforto. O isópode-gigante parece um parente monstruoso do tatuzinho de jardim. No fundo do oceano, porém, ele cresce até o tamanho de um cachorro e espera por restos que caem do alto.",
        [
            ("Isópode-gigante enorme", "caminha lentamente sobre o lodo", "planície abissal", "plano baixo"),
            ("Carapaça segmentada", "reflete a luz do submersível", "sedimento escuro", "macro lateral"),
            ("Isópode ao lado de um robô", "revela uma escala absurda", "fundo marinho preto", "plano aberto"),
            ("Patas articuladas", "movem-se entre restos no solo", "luz azul fria", "close-up detalhado"),
            ("Grupo de isópodes", "cerca uma carcaça distante", "abismo silencioso", "plano amplo sombrio"),
        ],
    ),
    (
        "O tubarão-cobra carrega uma aparência antiga demais para parecer real. Seu corpo serpentino, os dentes em fileiras e o ataque repentino lembram um fóssil que nunca aceitou desaparecer. Ele vive longe da superfície, onde quase ninguém consegue observá-lo.",
        [
            ("Tubarão-cobra", "serpenteia diante da câmera", "água azul muito escura", "plano médio lateral"),
            ("Fileiras de dentes recurvados", "aparecem dentro da boca aberta", "abismo profundo", "macro frontal"),
            ("Tubarão-cobra em ataque", "lança o corpo para frente", "nuvem de sedimento", "plano dinâmico"),
            ("Brânquias ornamentadas", "ondulam no escuro", "luz de submersível", "close-up lateral"),
            ("Silhueta serpentina", "desaparece entre rochas negras", "fenda abissal", "plano geral"),
        ],
    ),
    (
        "Mais abaixo, o arquiteuthis, ou lula-gigante, transforma a falta de visão em mistério. Seus tentáculos podem alcançar comprimentos assustadores. Quase nunca é filmada viva, então cada encontro parece uma prova de que o oceano ainda esconde coisas grandes demais.",
        [
            ("Lula-gigante colossal", "surge entre nuvens de tinta", "abismo azul-preto", "plano geral monumental"),
            ("Olho enorme de lula-gigante", "reflete o farol de um submarino", "escuridão profunda", "macro extremo"),
            ("Tentáculos compridos", "cruzam diante da lente", "água escura com partículas", "plano subjetivo"),
            ("Submarino pequeno", "passa ao lado de uma sombra colossal", "fundo sem fim", "plano aberto lateral"),
            ("Lula-gigante em silhueta", "some no azul absoluto", "coluna d água profunda", "plano distante"),
        ],
    ),
    (
        "A escassez de comida cria soluções ainda mais estranhas. O peixe-engolidor-negro consegue engolir presas maiores do que ele próprio. Depois, sua barriga se estica como um balão escuro, carregando uma refeição enorme por um mundo onde a próxima pode nunca aparecer.",
        [
            ("Peixe-engolidor-negro", "flutua com barriga desproporcional", "abismo negro", "plano médio lateral"),
            ("Mandíbula expansível", "envolve uma presa maior", "água azul escura", "macro dramático"),
            ("Corpo translúcido escuro", "revela a forma da presa engolida", "luz fraca", "close-up científico"),
            ("Predador solitário", "desaparece carregando a refeição", "vazio submarino", "plano amplo"),
            ("Pequenos peixes ao longe", "evitam uma sombra imóvel", "profundidade escura", "plano geral"),
        ],
    ),
    (
        "Mesmo com robôs, câmeras e sonares, enxergamos apenas uma pequena parte desse reino. Cada mergulho revela uma criatura nova, uma adaptação impossível ou um comportamento que desafia o que imaginamos sobre a vida. O abismo continua guardando seus segredos.",
        [
            ("Veículo robótico de exploração", "desce por uma fenda estreita", "paredes rochosas gigantes", "plano aberto"),
            ("Braço mecânico", "ilumina uma criatura escondida", "fundo oceânico escuro", "plano subjetivo"),
            ("Mapa sonar luminoso", "revela vales profundos", "interface científica azul", "plano superior"),
            ("Sombra desconhecida", "cruza o limite do farol", "escuridão completa", "plano de suspense"),
            ("Submarino deixando o abismo", "parece pequeno diante do vazio", "coluna d água negra", "plano geral"),
        ],
    ),
    (
        "Esses animais não são monstros inventados. São sobreviventes de um ambiente que obriga cada espécie a ser extrema. Quanto mais descemos, mais a superfície parece distante. Se esse mergulho te deixou inquieto, siga o canal para encarar o próximo mistério.",
        [
            ("Montagem de predadores abissais", "surge em flashes na escuridão", "oceano preto e azul", "plano cinematográfico"),
            ("Olho bioluminescente", "apaga lentamente", "vazio total", "macro extremo"),
            ("Água-viva fantasmagórica", "sobe em direção a uma luz distante", "abismo sereno", "plano geral centralizado"),
            ("Último feixe de luz", "desaparece sobre o oceano profundo", "silhueta de submarino", "plano aberto"),
            ("Escuridão abissal absoluta", "engole a câmera", "partículas quase invisíveis", "plano final minimalista"),
        ],
    ),
]


SOUND_SCENES = {8: "whoosh_soft", 17: "whoosh_fast", 26: "whoosh_cinematic", 35: "whoosh_soft", 46: "whoosh_cinematic", 56: "whoosh_soft"}
ANNOTATIONS = {
    14: ["UMA LUZ...", "NUNCA É SÓ LUZ"],
    31: ["DENTES PARA O ESCURO"],
    49: ["O ABISMO AINDA ESCONDE", "COISAS GIGANTESCAS..."],
}


def transition(scene_number: int) -> dict[str, str]:
    choices = (("zoom_in", "to_right", "slow"), ("from_left", "to_left", "normal"), ("from_right", "to_right", "fast"), ("zoom_in", "to_left", "slow"), ("from_left", "to_right", "normal"))
    incoming, outgoing, speed = choices[(scene_number * 3 + scene_number // 4) % len(choices)]
    return {"in": incoming, "out": outgoing, "speed": speed}


def main() -> None:
    scenes = []
    scene_number = 1
    blocks = []
    for block_number, (text, beats) in enumerate(BLOCKS, start=1):
        block_scenes = []
        for subject, action, setting, framing in beats:
            scene = {
                "id": f"scene_{scene_number:02d}",
                "image": f"abissais_5min_{scene_number:02d}.jpg",
                "visual": {
                    "subject": subject,
                    "action": action,
                    "setting": setting,
                    "framing": framing,
                    "details": "documentário de terror abissal, escala exagerada e ameaçadora, anatomia biologicamente crível, contraste azul petróleo e preto, faróis duros, partículas densas, ultra detalhado, horizontal 16:9, sem texto, sem logotipos, sem marcas d água",
                },
                "transition": transition(scene_number),
                "sounds": {"transition": [SOUND_SCENES[scene_number]] if scene_number in SOUND_SCENES else [], "context": None},
            }
            if scene_number in ANNOTATIONS:
                scene["annotation"] = {"lines": ANNOTATIONS[scene_number], "at": "start"}
            block_scenes.append(scene)
            scenes.append(scene)
            scene_number += 1
        blocks.append({"id": f"block_{block_number:02d}", "text": text, "scenes": block_scenes})
    payload = {
        "title": "Os animais abissais mais assustadores do fundo do mar",
        "language": "pt-BR",
        "narrator_gender": "female",
        "background": "black",
        "background_animation": "movimento_sutil",
        "blocks": blocks,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Roteiro pronto: {OUTPUT}")


if __name__ == "__main__":
    main()
