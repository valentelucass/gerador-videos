"""Gera um MP4 de teste do roteiro de animais do fundo do mar.

Uso: python renderizar_teste_animais.py
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FFMPEG = Path.home() / "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.2-full_build/bin/ffmpeg.exe"
FFPROBE = FFMPEG.with_name("ffprobe.exe")
OUTPUT = ROOT / "workspace/lotes_horizontais/animais_fundo_do_mar"
IMAGES = ROOT / "assets/images"
MUSIC = ROOT / "music/fundo_documentario.mp3"
SCRIPT = ROOT / "roteiro_animais_fundo_do_mar_1min.json"

LEGACY_IMAGE_ORDER = [
    "Abyssal_animals_appear_at_depths_202607201836.jpeg",
    "Submarine_descends_towards_ocean…_2K_202607201837.jpeg",
    "Glowing_water_droplet_underwater…_2K_202607201837.jpeg",
    "Rocky_plain_seabed_emerges_darkness_202607201837.jpeg",
    "Diver_observes_ocean_depth_2K_202607201837.jpeg",
    "Anglerfish_swims_in_darkness_2K_202607201836.jpeg",
    "Anglerfish_bioluminescent_lure_g…_2K_202607201837.jpeg",
    "Fish_approaches_mysterious_light_2K_202607201837.jpeg",
    "Anglerfish_lunges_in_attack_2K_202607201837.jpeg",
    "Bioluminescent_creatures_in_dark…_2K_202607201837.jpeg",
    "Pink_dumbo_octopus_floats_open_202607201836.jpeg",
    "Dumbo_octopus_fins_beat_elegantly_202607201836.jpeg",
    "Dumbo_octopus_glides_over_seabed_202607201836.jpeg",
    "Viperfish_stares_at_camera_2K_202607201836.jpeg",
    "Viperfish_emits_blue_light_2K_202607201836.jpeg",
    "Abyssal_animals_appear_at_depths_202607201836.jpeg",
    "Fish_scales_and_eyes_reflect_202607201836.jpeg",
    "Robot_maps_seabed_underwater_2K_202607201836.jpeg",
    "Seafloor_map_reveals_mountains_v…_202607201836.jpeg",
    "Jellyfish_rises_towards_glow_2K_202607201836.jpeg",
]

# Assets disponíveis para as 20 cenas. A ordem segue a narrativa do roteiro.
IMAGE_ORDER = LEGACY_IMAGE_ORDER


def run(command: list[str]) -> None:
    print("Executando:", " ".join(f'"{part}"' if " " in part else part for part in command[:6]), "...")
    subprocess.run(command, check=True)


def duration(path: Path) -> float:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        text=True,
        capture_output=True,
    )
    return float(result.stdout.strip())


def main() -> None:
    if not FFMPEG.exists():
        raise FileNotFoundError(f"FFmpeg não encontrado: {FFMPEG}")
    if not MUSIC.exists():
        raise FileNotFoundError(f"Trilha ausente: {MUSIC}")

    missing = [name for name in IMAGE_ORDER if not (IMAGES / name).is_file()]
    if missing:
        raise FileNotFoundError("Imagens ausentes:\n" + "\n".join(missing))

    payload = json.loads(SCRIPT.read_text(encoding="utf-8"))
    text = " ".join(block["text"] for block in payload["blocks"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    voice = OUTPUT / "narracao.mp3"
    visual = OUTPUT / "video_sem_audio.mp4"
    final = OUTPUT / "animais_fundo_do_mar_final.mp4"

    if not voice.exists():
        run([
            "edge-tts", "--voice", "pt-BR-AntonioNeural", "--rate=-10%",
            "--text", text, "--write-media", str(voice),
        ])
    scene_duration = duration(voice) / len(IMAGE_ORDER) + 0.08
    playlist = OUTPUT / "imagens.ffconcat"
    lines = ["ffconcat version 1.0"]
    for name in IMAGE_ORDER:
        lines.extend([f"file '{(IMAGES / name).as_posix()}'", f"duration {scene_duration:.3f}"])
    # A repetição final faz o demuxer aplicar a duração também à última imagem.
    lines.append(f"file '{(IMAGES / IMAGE_ORDER[-1]).as_posix()}'")
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run([
        str(FFMPEG), "-y", "-safe", "0", "-f", "concat", "-i", str(playlist),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=24,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-movflags", "+faststart", str(visual),
    ])
    run([
        str(FFMPEG), "-y", "-safe", "0", "-f", "concat", "-i", str(playlist),
        "-i", str(voice), "-stream_loop", "-1", "-i", str(MUSIC),
        "-filter_complex",
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=24,format=yuv420p[video];"
        "[1:a]aresample=48000,asplit=2[voice_mix][voice_key];[2:a]aresample=48000,volume=0.22[music];"
        "[music][voice_key]sidechaincompress=threshold=0.035:ratio=8:attack=20:release=250[ducked];"
        "[voice_mix][ducked]amix=inputs=2:duration=first:normalize=0[a]",
        "-map", "[video]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", str(final),
    ])
    print(f"Vídeo pronto: {final}")


if __name__ == "__main__":
    main()
