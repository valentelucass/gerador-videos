"""Visual-contract tests for the eleven 1920x1080 horizontal templates."""

from __future__ import annotations

import unittest

from backend.src.core.layout_factory import LayoutFactory


class LayoutFactoryReferenceTests(unittest.TestCase):
    def test_reference_geometry_for_all_templates(self) -> None:
        cases = {
            1: ({"principal": "video.mp4"}, (), ("scale=1920:1080",)),
            2: (
                {"principal": "video.mp4"},
                (),
                ("scale=1320:742", "1080-911*(t/0.55),169)", "[fg_sized]null[fg]"),
            ),
            3: (
                {"esquerda": "a.jpg", "direita": "b.jpg", "seta": "arrow.png"},
                (),
                (
                    "scale=580:580",
                    "-580+825*(t/0.45),245)",
                    "scale=415:415",
                    "1920-685*(t/0.55),1235)",
                    "scale=250:250",
                    "overlay=x=875:y=355",
                ),
            ),
            4: (
                {},
                ("Descrição sem foto!", "Segunda linha"),
                ("color=c=white:s=1920x1080", "fontsize=138", "y=387", "y=555", "__SYNTHREEL_DISPLAY_FONT__", "borderw=8"),
            ),
            5: (
                {"celular_1": "a.jpg", "celular_2": "b.jpg", "celular_3": "c.jpg"},
                (),
                ("scale=640:1080", "overlay=x=1280:y=0"),
            ),
            6: (
                {"principal": "video.mp4"},
                ("Descrição sem foto!", "Segunda linha"),
                ("x=150:y=490", "x=150:y=570", "boxcolor=black@0.62"),
            ),
            7: (
                {"esquerda": "a.jpg", "direita": "b.jpg"},
                ("Informação",),
                ("scale=690:690", "1080-875*(t/0.50),205)", "scale=525:525"),
            ),
            8: (
                {"principal": "video.mp4"},
                ("Título", "Linha de corpo"),
                ("scale=560:820", "-560+810*(t/0.50),250)", "x=1330-(text_w/2):y=335"),
            ),
            9: (
                {"esquerda": "a.jpg", "direita": "b.jpg"},
                ("Linha 1", "Linha 2"),
                ("scale=930:520", "1.18-0.18*t/0.55", "411-183*(t/0.55),228)", "1920-708*((t-0.55)/0.55),1212)", "between(t,1.120"),
            ),
            10: (
                {"esquerda": "a.jpg", "direita": "b.jpg"},
                (),
                ("scale=1920:1080", "1920-990*(t/0.50)", "188*(t/0.50)", "1920-748*((t-0.50)/0.55),1172)"),
            ),
            11: (
                {"esquerda": "a.jpg"},
                ("Tópico 1", "Tópico 2"),
                ("scale=730:730", "overlay=x=270:y=195", "•  Tópico 1"),
            ),
            12: (
                {"esquerda": "a.jpg"},
                ("Tópico 1", "Tópico 2", "Tópico 3", "Tópico 4"),
                ("scale=730:730", "overlay=x=270:y=195", "•  Tópico 1"),
            ),
        }

        for template_id, (media, texts, expected) in cases.items():
            with self.subTest(template_id=template_id):
                graph = LayoutFactory.build_filter_complex(template_id, media, texts)
                self.assertTrue(graph.endswith("[vout]"))
                for fragment in expected:
                    self.assertIn(fragment, graph)

    def test_multiline_items_are_split_into_independent_drawtext_filters(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            4,
            {},
            ("Primeira linha\nSegunda linha",),
        )

        self.assertGreater(graph.count("drawtext="), 2)
        self.assertIn("enable='between(t,0.100,", graph)
        self.assertNotIn("Primeira linha\\nSegunda linha", graph)

    def test_text_color_is_applied_to_every_screen_text_template(self) -> None:
        graph = LayoutFactory.build_filter_complex(4, {}, ("Título",), cor_texto="#ffcc00")

        self.assertIn("fontcolor=0xffcc00", graph)
        self.assertNotIn("fontcolor=white", graph)

        dark_graph = LayoutFactory.build_filter_complex(4, {}, ("Título",), cor_texto="black")
        self.assertIn("bordercolor=white@0.88", dark_graph)

    def test_text_outline_can_be_enabled_with_color_or_removed(self) -> None:
        outlined = LayoutFactory.build_filter_complex(
            8,
            {"principal": "imagem.jpg"},
            ("Título",),
            cor_texto="white",
            borda_texto=True,
            cor_borda_texto="#ffcc00",
        )
        without_outline = LayoutFactory.build_filter_complex(
            4, {}, ("Título",), borda_texto=False
        )

        self.assertIn("borderw=4:bordercolor=0xffcc00", outlined)
        self.assertNotIn("borderw=", without_outline)

    def test_template_12_skips_empty_topics_and_closes_each_filter_chain(self) -> None:
        casos = (
            (("Topico Um", "", "", ""), 1, "topic0", ("y=260",)),
            (
                ("Topico Um", "Topico Dois", "", ""),
                2,
                "topic1",
                ("y=260", "y=415"),
            ),
            (
                ("Topico Um", "Topico Dois", "Topico Tres", ""),
                3,
                "topic2",
                ("y=260", "y=415", "y=570"),
            ),
        )

        for textos, quantidade_drawtext, ultimo_label, posicoes_y in casos:
            with self.subTest(textos=textos):
                graph = LayoutFactory.build_filter_complex(
                    12,
                    {"esquerda": "a.jpg"},
                    textos,
                )

                self.assertGreaterEqual(graph.count("drawtext="), quantidade_drawtext)
                self.assertTrue(graph.endswith("[vout]"))
                self.assertIn(f"[{ultimo_label}]format=yuv420p[vout]", graph)
                for posicao_y in posicoes_y:
                    self.assertIn(posicao_y, graph)
                self.assertNotIn("drawtext=text='•  '", graph)
                self.assertIn("enable='gte(t,", graph)

    def test_template_six_breaks_caption_in_two_lines_and_types_sequentially(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            6,
            {"principal": "imagem.jpg"},
            ("El Costo Humano de una Guerra Larga",),
        )

        self.assertIn("text='El Costo Humano de'", graph)
        self.assertIn("text='una Guerra Larga'", graph)
        self.assertIn("y=490", graph)
        self.assertIn("y=570", graph)
        primeira_conclusao = graph.index("enable='gte(t,")
        segunda_linha = graph.index("y=570")
        self.assertLess(primeira_conclusao, segunda_linha)

    def test_static_image_gets_centered_continuous_ken_burns(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            1,
            {"principal": "IMAGEM_IA.JPG"},
            total_frames=270,
        )

        self.assertEqual(graph.count("zoompan="), 1)
        self.assertIn("z='1+(0.060000*on/269)'", graph)
        self.assertIn("d=1", graph)
        self.assertIn("x='floor((iw-iw/zoom)/2)'", graph)
        self.assertIn("y='floor((ih-ih/zoom)/2)'", graph)
        self.assertIn("s=6106x3436:fps=30", graph)
        self.assertNotIn("pzoom", graph)
        self.assertLess(graph.index("zoompan="), graph.rindex("scale=1920:1080"))

    def test_video_never_gets_ken_burns(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            1,
            {"principal": "video_ia.mp4"},
            indices_imagens=frozenset(),
            total_frames=150,
        )

        self.assertNotIn("zoompan=", graph)

    def test_template_three_animates_scene_images_but_not_arrow_png(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            3,
            {
                "esquerda": "foto_a.jpeg",
                "direita": "foto_b.PNG",
                "seta": "seta_apontamento.png",
            },
            total_frames=150,
        )

        self.assertEqual(graph.count("zoompan="), 2)
        self.assertNotIn("[2:v]scale=346:346", graph)
        self.assertIn("[2:v]fps=30[arrow_cfr]", graph)
        self.assertIn("[arrow_cfr]scale=250:250", graph)

    def test_static_background_templates_use_unanimated_persistent_base(self) -> None:
        cases = {
            3: (
                {
                    "esquerda": "left.mp4",
                    "direita": "right.mp4",
                    "seta": "seta_apontamento.png",
                    "fundo_estatico": "background.jpeg",
                },
                (),
            ),
            4: ({"fundo_estatico": "background.jpeg"}, ("Texto",)),
            7: (
                {
                    "esquerda": "left.mp4",
                    "direita": "right.mp4",
                    "fundo_estatico": "background.jpeg",
                },
                ("Texto",),
            ),
            8: (
                {"principal": "phone.mp4", "fundo_estatico": "background.jpeg"},
                ("Titulo",),
            ),
            9: (
                {
                    "esquerda": "left.mp4",
                    "direita": "right.mp4",
                    "fundo_estatico": "background.jpeg",
                },
                ("Texto",),
            ),
            10: (
                {
                    "esquerda": "left.mp4",
                    "direita": "right.mp4",
                    "fundo_estatico": "background.jpeg",
                },
                (),
            ),
            11: (
                {"esquerda": "left.mp4", "fundo_estatico": "background.jpeg"},
                ("Um", "Dois", "Tres", "Quatro"),
            ),
        }

        for template_id, (media, texts) in cases.items():
            with self.subTest(template_id=template_id):
                graph = LayoutFactory.build_filter_complex(template_id, media, texts)
                background_index = len(media) - 1
                self.assertIn(f"[{background_index}:v]fps=30[base_cfr]", graph)
                self.assertIn(
                    "[base_cfr]scale=1920:1080:force_original_aspect_ratio="
                    "increase,crop=1920:1080[base]",
                    graph,
                )
                self.assertNotIn("color=c=white", graph)
                self.assertNotIn("zoompan=", graph)

    def test_mixed_template_animates_only_the_static_slot(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            10,
            {"esquerda": "foto.jpg", "direita": "video.mp4"},
            total_frames=150,
        )

        self.assertEqual(graph.count("zoompan="), 1)
        self.assertIn("[1:v]fps=30[right_cfr]", graph)
        self.assertIn("[right_cfr]scale=520:520", graph)

    def test_every_physical_media_input_starts_with_cfr_normalization(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            3,
            {
                "esquerda": "left.mp4",
                "direita": "right.jpg",
                "seta": "seta_apontamento.png",
                "fundo_estatico": "background.jpeg",
            },
            total_frames=150,
        )

        for index in range(4):
            self.assertIn(f"[{index}:v]fps=30", graph)

    def test_template_two_animates_once_before_shared_split(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            2,
            {"principal": "foto.png"},
            total_frames=150,
        )

        self.assertEqual(graph.count("zoompan="), 1)
        self.assertLess(graph.index("zoompan="), graph.index("split=2"))
        self.assertIn("y='if(lt(t,0.55),1080-911*(t/0.55),169)'", graph)

    def test_template_two_keeps_foreground_card_geometry_stable(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            2,
            {"principal": "video.mp4"},
            total_frames=120,
        )

        self.assertIn("[fg_sized]null[fg]", graph)
        self.assertIn("overlay=x='300'", graph)
        self.assertNotIn("eval=frame[fg]", graph)


if __name__ == "__main__":
    unittest.main()
