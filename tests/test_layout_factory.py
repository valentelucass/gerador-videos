"""Visual-contract tests for the eleven 1920x1080 horizontal templates."""

from __future__ import annotations

import unittest

from src.core.layout_factory import LayoutFactory


class LayoutFactoryReferenceTests(unittest.TestCase):
    def test_reference_geometry_for_all_templates(self) -> None:
        cases = {
            1: ({"principal": "video.mp4"}, (), ("scale=1920:1080",)),
            2: (
                {"principal": "video.mp4"},
                (),
                ("scale=1120:630", "overlay=x=400:y=225"),
            ),
            3: (
                {"esquerda": "a.jpg", "direita": "b.jpg", "seta": "arrow.png"},
                (),
                (
                    "scale=580:580",
                    "overlay=x=245:y=255",
                    "scale=415:415",
                    "overlay=x=1235:y=420",
                    "scale=300:300",
                ),
            ),
            4: (
                {},
                ("Descrição sem foto!", "Segunda linha"),
                ("color=c=white:s=1920x1080", "y=460", "y=540"),
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
                ("scale=690:690", "overlay=x=270:y=205", "scale=525:525"),
            ),
            8: (
                {"principal": "video.mp4"},
                ("Título", "Linha de corpo"),
                ("scale=440:780", "overlay=x=185:y=160", "x=840:y=335"),
            ),
            9: (
                {"esquerda": "a.jpg", "direita": "b.jpg"},
                ("Linha 1", "Linha 2"),
                ("scale=930:520", "overlay=x=228:y=190", "overlay=x=1212:y=190"),
            ),
            10: (
                {"esquerda": "a.jpg", "direita": "b.jpg"},
                (),
                ("overlay=x=188:y=295", "overlay=x=1172:y=295"),
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

        self.assertEqual(graph.count("drawtext="), 2)
        self.assertNotIn("Primeira linha\\nSegunda linha", graph)

    def test_static_image_gets_centered_continuous_ken_burns(self) -> None:
        graph = LayoutFactory.build_filter_complex(
            1,
            {"principal": "IMAGEM_IA.JPG"},
            total_frames=270,
        )

        self.assertEqual(graph.count("zoompan="), 1)
        self.assertIn("max(zoom,pzoom)", graph)
        self.assertIn("d=1", graph)
        self.assertIn("x='iw/2-(iw/zoom/2)'", graph)
        self.assertIn("y='ih/2-(ih/zoom/2)'", graph)
        self.assertIn("s=2208x1242:fps=30", graph)
        self.assertIn("+0.00055762", graph)
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
        self.assertIn("[arrow_cfr]scale=300:300", graph)

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


if __name__ == "__main__":
    unittest.main()
