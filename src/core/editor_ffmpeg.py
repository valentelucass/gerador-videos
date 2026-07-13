"""FFmpeg-only video treatment engine for SynthReel."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Iterable

try:
    from src.config.settings import OUTPUT_DIR, TEMP_DIR, settings
    from src.utils.logger import get_logger
except ModuleNotFoundError:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.append(str(ROOT_DIR))
    from src.config.settings import OUTPUT_DIR, TEMP_DIR, settings
    from src.utils.logger import get_logger


class FFmpegEngine:
    """Applies strict SynthReel visual treatments using native subprocess."""

    NARRATION_VOLUME = 1.18
    BACKGROUND_MUSIC_VOLUME = 0.18
    TRANSITION_VOLUME = 0.35

    def __init__(
        self,
        ffmpeg_bin: str | None = None,
        logger: logging.Logger | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin or settings.ffmpeg_bin
        self.ffprobe_bin = settings.ffprobe_bin
        self.logger = logger or get_logger(__name__)
        self.width = width or settings.default_width
        self.height = height or settings.default_height
        self.fps = fps or settings.default_fps

    def cortar_midia(
        self,
        input_path: str | Path,
        output_path: str | Path,
        start_time: float,
        duration: float,
    ) -> Path:
        """Cuts media with -ss and -t before -i for fast, sync-safe seeking."""

        input_file = self._require_input(input_path)
        output_file = self._prepare_output(output_path)
        self._require_non_negative(start_time, "start_time")
        self._require_positive(duration, "duration")

        args = [
            "-ss",
            self._fmt_time(start_time),
            "-t",
            self._fmt_time(duration),
            "-i",
            str(input_file),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(output_file),
        ]
        self._run_ffmpeg(args, "corte de midia")
        return output_file

    def aplicar_fullscreen_9x16(
        self,
        input_path: str | Path,
        output_path: str | Path,
    ) -> Path:
        """Converts vertical media to pure fullscreen 9:16."""

        input_file = self._require_input(input_path)
        output_file = self._prepare_output(output_path)
        filter_complex = (
            f"[0:v]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},setsar=1,format=yuv420p[v]"
        )

        args = [
            "-i",
            str(input_file),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_file),
        ]
        self._run_ffmpeg(args, "tratamento fullscreen 9:16")
        return output_file

    def aplicar_grid_1x3(
        self,
        input_path: str | Path,
        output_path: str | Path,
    ) -> Path:
        """Applies the 16:9 fallback grid by stacking three clean video copies."""

        input_file = self._require_input(input_path)
        output_file = self._prepare_output(output_path)
        row_height = self.height // 3

        filter_complex = (
            f"[0:v]scale={self.width}:{row_height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{row_height},setsar=1,split=3[v1][v2][v3];"
            "[v1][v2][v3]vstack=inputs=3[stacked];"
            f"[stacked]scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},setsar=1,format=yuv420p[v]"
        )

        args = [
            "-i",
            str(input_file),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output_file),
        ]
        self._run_ffmpeg(args, "tratamento grid 1x3")
        return output_file

    def aplicar_ken_burns(
        self,
        input_path: str | Path,
        output_path: str | Path,
        duration: float,
    ) -> Path:
        """Turns a photo into a 9:16 Ken Burns clip."""

        input_file = self._require_input(input_path)
        output_file = self._prepare_output(output_path)
        self._require_positive(duration, "duration")
        frames = max(1, int(round(self.fps * duration)))

        filter_complex = (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},"
            "zoompan="
            "z='min(zoom+0.0015,1.5)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={self.width}x{self.height}:fps={self.fps},"
            "setsar=1,format=yuv420p"
        )

        args = [
            "-loop",
            "1",
            "-i",
            str(input_file),
            "-filter_complex",
            filter_complex,
            "-t",
            self._fmt_time(duration),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
        self._run_ffmpeg(args, "tratamento ken burns")
        return output_file

    def criar_imagem_fallback(
        self,
        output_path: str | Path,
        color: str = "0x101018",
    ) -> Path:
        """Creates a local still image used when external media search fails."""

        output_file = self._prepare_output(output_path)
        args = [
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={self.width}x{self.height}:d=1",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(output_file),
        ]
        self._run_ffmpeg(args, "geracao de imagem fallback")
        return output_file

    def ajustar_duracao_video(
        self,
        input_path: str | Path,
        output_path: str | Path,
        duration: float,
    ) -> Path:
        """Forces a video-only clip to the exact target duration."""

        input_file = self._require_input(input_path)
        output_file = self._prepare_output(output_path)
        self._require_positive(duration, "duration")

        filter_chain = (
            f"tpad=stop_mode=clone:stop_duration={self._fmt_time(duration)},"
            f"trim=duration={self._fmt_time(duration)},"
            f"setpts=PTS-STARTPTS,fps={self.fps},format=yuv420p"
        )
        args = [
            "-i",
            str(input_file),
            "-vf",
            filter_chain,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
        self._run_ffmpeg(args, "normalizacao de duracao visual")
        return output_file

    def juntar_cenas(
        self,
        lista_videos_cortados: list[str | Path],
        output_final: str | Path,
    ) -> Path:
        """Joins rendered scene clips using FFmpeg's concat demuxer."""

        if not lista_videos_cortados:
            raise ValueError("lista_videos_cortados nao pode ser vazia.")

        output_file = self._prepare_output(output_final)
        concat_file = output_file.parent / f"{output_file.stem}_concat.txt"

        linhas = []
        for video_path in lista_videos_cortados:
            input_file = self._require_input(video_path)
            linhas.append(f"file '{self._escape_concat_path(input_file)}'\n")

        concat_file.write_text("".join(linhas), encoding="utf-8")

        args = [
            "-fflags",
            "+genpts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-bf",
            "0",
            "-r",
            str(self.fps),
            "-pix_fmt",
            "yuv420p",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
        self._run_ffmpeg(args, "concatenacao de cenas")
        return output_file

    def adicionar_audio(
        self,
        input_video: str | Path,
        input_audio: str | Path,
        output_path: str | Path,
    ) -> Path:
        """Muxes the final narration audio over the rendered video."""

        video_file = self._require_input(input_video)
        audio_file = self._require_input(input_audio)
        output_file = self._prepare_output(output_path)

        args = [
            "-i",
            str(video_file),
            "-i",
            str(audio_file),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
        self._run_ffmpeg(args, "mux de narracao final")
        return output_file

    def aplicar_transicoes_overlay(
        self,
        input_video: str | Path,
        transicoes: list[dict[str, object]],
        output_video: str | Path,
    ) -> Path:
        """Applies short transition videos as visual overlays over scene cuts."""

        video_file = self._require_input(input_video)
        output_file = self._prepare_output(output_video)
        transicoes_validas = [item for item in transicoes if item.get("path")]

        if not transicoes_validas:
            return self._copiar_video(video_file, output_file)

        args = ["-i", str(video_file)]
        filter_parts: list[str] = []
        current_video = "[0:v]"

        for index, item in enumerate(transicoes_validas, start=1):
            transition_file = self._require_input(Path(str(item["path"])))
            inicio = max(0.0, float(item["inicio"]))
            duracao = max(0.1, float(item["duracao"]))
            fim = inicio + duracao
            args.extend(["-i", str(transition_file)])
            filter_parts.append(
                f"[{index}:v]"
                f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                f"crop={self.width}:{self.height},fps={self.fps},trim=duration={self._fmt_time(duracao)},"
                f"setpts=PTS-STARTPTS+{self._fmt_time(inicio)}/TB,format=rgb24,"
                "colorkey=0x000000:0.20:0.12,colorchannelmixer=aa=0.50"
                f"[ov{index}]"
            )
            filter_parts.append(
                f"{current_video}[ov{index}]"
                f"overlay=0:0:eof_action=pass:shortest=0:"
                f"enable='between(t,{self._fmt_time(inicio)},{self._fmt_time(fim)})'"
                f"[v{index}]"
            )
            current_video = f"[v{index}]"

        args.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                current_video,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-bf",
                "0",
                "-r",
                str(self.fps),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_file),
            ]
        )
        self._run_ffmpeg(args, "overlays de transicao")
        return output_file

    def mixar_audio_final(
        self,
        input_video: str | Path,
        narracao: str | Path,
        output_path: str | Path,
        background_music: str | Path | None = None,
        transicoes: list[dict[str, object]] | None = None,
        duracao: float | None = None,
    ) -> Path:
        """Mixes narration, optional background music and transition audio."""

        video_file = self._require_input(input_video)
        narracao_file = self._require_input(narracao)
        output_file = self._prepare_output(output_path)
        duracao_final = duracao or self.obter_duracao(video_file)
        transicoes = transicoes or []

        args = ["-i", str(video_file), "-i", str(narracao_file)]
        filters = [
            "[1:a]aresample=48000,"
            "highpass=f=80,"
            "alimiter=limit=0.95,"
            f"volume={self.NARRATION_VOLUME:.2f}[narr]"
        ]
        audio_labels = ["[narr]"]
        input_index = 2

        if background_music is not None:
            music_file = self._require_input(background_music)
            if self._tem_audio(music_file):
                args.extend(["-stream_loop", "-1", "-i", str(music_file)])
                fade_out_start = max(0.0, duracao_final - 1.0)
                filters.append(
                    f"[{input_index}:a]aresample=48000,"
                    f"atrim=0:{self._fmt_time(duracao_final)},asetpts=PTS-STARTPTS,"
                    f"volume={self.BACKGROUND_MUSIC_VOLUME:.2f},"
                    "afade=t=in:st=0:d=0.70,"
                    f"afade=t=out:st={self._fmt_time(fade_out_start)}:d=0.80[music]"
                )
                audio_labels.append("[music]")
                input_index += 1

        for transition_index, item in enumerate(transicoes, start=1):
            transition_file = self._require_input(Path(str(item["path"])))
            if not self._tem_audio(transition_file):
                continue
            inicio = max(0.0, float(item["inicio"]))
            duracao = max(0.1, float(item["duracao"]))
            delay_ms = max(0, int(round(inicio * 1000)))
            args.extend(["-i", str(transition_file)])
            label = f"[transaudio{transition_index}]"
            filters.append(
                f"[{input_index}:a]aresample=48000,"
                f"atrim=0:{self._fmt_time(duracao)},asetpts=PTS-STARTPTS,"
                f"volume={self.TRANSITION_VOLUME:.2f},"
                f"adelay={delay_ms}|{delay_ms}{label}"
            )
            audio_labels.append(label)
            input_index += 1

        if len(audio_labels) == 1:
            filters.append("[narr]alimiter=limit=0.95[aout]")
        else:
            filters.append(
                f"{''.join(audio_labels)}"
                f"amix=inputs={len(audio_labels)}:duration=first:dropout_transition=0:normalize=0,"
                "alimiter=limit=0.95[aout]"
            )

        args.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_file),
            ]
        )
        self._run_ffmpeg(args, "mixagem de narracao, trilha e transicoes")
        return output_file

    def queimar_legendas(
        self,
        input_video: str | Path,
        ass_file: str | Path,
        output_video: str | Path,
    ) -> Path:
        """Burns ASS subtitles into the final video."""

        video_file = self._require_input(input_video)
        subtitles_file = self._require_input(ass_file)
        output_file = self._prepare_output(output_video)
        ass_filter_path = self._escape_filter_path(subtitles_file)

        args = [
            "-i",
            str(video_file),
            "-vf",
            f"ass='{ass_filter_path}'",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_file),
        ]
        self._run_ffmpeg(args, "queima de legendas ASS")
        return output_file

    def obter_duracao(self, input_path: str | Path) -> float:
        """Returns media duration in seconds using ffprobe."""

        input_file = self._require_input(input_path)
        command = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(input_file),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Falha ao obter duracao com ffprobe: {input_file}") from exc
        return float(result.stdout.strip())

    def obter_dimensoes(self, input_path: str | Path) -> tuple[int, int]:
        """Returns the first video stream width and height using ffprobe."""

        input_file = self._require_input(input_path)
        command = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(input_file),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Falha ao obter dimensoes com ffprobe: {input_file}") from exc

        output = result.stdout.strip()
        if "x" not in output:
            raise RuntimeError(f"ffprobe nao retornou dimensoes validas para: {input_file}")
        width_raw, height_raw = output.split("x", 1)
        return int(width_raw), int(height_raw)

    def _run_ffmpeg(self, args: Iterable[str], step_name: str) -> subprocess.CompletedProcess[str]:
        command = [self.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y", *args]
        self.logger.info("FFmpeg: %s", step_name)
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            message = f"FFmpeg nao encontrado: {self.ffmpeg_bin}"
            self.logger.error(message)
            raise RuntimeError(message) from exc
        except subprocess.CalledProcessError as exc:
            detail = self._compact_output(exc.stderr or exc.stdout)
            self.logger.error("Falha no FFmpeg (%s): %s", step_name, detail)
            raise RuntimeError(f"Falha no FFmpeg durante {step_name}: {detail}") from exc

    def _require_input(self, path: str | Path) -> Path:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Midia de entrada nao encontrada: {file_path}")
        return file_path

    @staticmethod
    def _prepare_output(path: str | Path) -> Path:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    @staticmethod
    def _require_positive(value: float, field_name: str) -> None:
        if value <= 0:
            raise ValueError(f"{field_name} deve ser maior que zero.")

    @staticmethod
    def _require_non_negative(value: float, field_name: str) -> None:
        if value < 0:
            raise ValueError(f"{field_name} nao pode ser negativo.")

    @staticmethod
    def _fmt_time(value: float) -> str:
        return f"{value:.3f}"

    @staticmethod
    def _escape_concat_path(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "'\\''")

    def _copiar_video(self, input_path: Path, output_path: Path) -> Path:
        args = ["-i", str(input_path), "-c", "copy", str(output_path)]
        self._run_ffmpeg(args, "copia de video")
        return output_path

    def _tem_audio(self, input_path: Path) -> bool:
        command = [
            self.ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(input_path),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        return bool(result.stdout.strip())

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")

    @staticmethod
    def _compact_output(output: str | None) -> str:
        if not output:
            return "sem detalhes no stderr/stdout"
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return " | ".join(lines[-6:]) if lines else "sem detalhes no stderr/stdout"


def _gerar_video_cor(engine: FFmpegEngine, output_path: Path, size: str, color: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:d=4:r={engine.fps}",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    engine._run_ffmpeg(args, f"geracao de video sintetico {size}")
    return output_path


def _gerar_foto_cor(engine: FFmpegEngine, output_path: Path, size: str, color: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:d=1",
        "-frames:v",
        "1",
        "-update",
        "1",
        str(output_path),
    ]
    engine._run_ffmpeg(args, "geracao de foto sintetica")
    return output_path


def _validar_saida(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Arquivo de teste invalido: {path}")


def _teste_isolado() -> None:
    """Creates synthetic inputs and validates all visual treatments locally."""

    logger = get_logger("ffmpeg_engine_test")
    engine = FFmpegEngine(logger=logger)
    temp_dir = TEMP_DIR / "ffmpeg_engine_test"
    output_dir = OUTPUT_DIR / "ffmpeg_engine_test"
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    vertical = _gerar_video_cor(engine, temp_dir / "entrada_vertical_9x16.mp4", "1080x1920", "red")
    horizontal = _gerar_video_cor(engine, temp_dir / "entrada_horizontal_16x9.mp4", "1920x1080", "blue")
    foto = _gerar_foto_cor(engine, temp_dir / "entrada_foto.png", "1080x1920", "green")

    saidas = [
        engine.cortar_midia(vertical, output_dir / "teste_corte.mp4", start_time=0.5, duration=2.0),
        engine.aplicar_fullscreen_9x16(vertical, output_dir / "teste_fullscreen_9x16.mp4"),
        engine.aplicar_grid_1x3(horizontal, output_dir / "teste_grid_1x3.mp4"),
        engine.aplicar_ken_burns(foto, output_dir / "teste_ken_burns.mp4", duration=3.0),
    ]

    for saida in saidas:
        _validar_saida(saida)
        logger.info("OK: %s (%s bytes)", saida, saida.stat().st_size)

    logger.info("Teste isolado concluido com sucesso.")


if __name__ == "__main__":
    _teste_isolado()
