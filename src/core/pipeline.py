"""SynthReel integration pipeline orchestrator."""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
import random
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

try:
    from src.config.settings import BACKGROUND_MUSIC_DIR, OUTPUT_DIR, TEMP_DIR, TRANSITIONS_DIR
    from src.core.editor_ffmpeg import FFmpegEngine
    from src.core.legendas_ass import GeradorLegendasASS
    from src.core.pexels_fetcher import PexelsFetcher, PexelsFetcherError
    from src.core.tts_clonador import TTSManager
    from src.core.whisper_sync import WhisperSync
    from src.utils.logger import get_logger
    from src.utils.run_reporter import RunReporter
    from src.utils.text_helpers import normalizar_ascii
    from src.utils.workspace_cleaner import WorkspaceCleaner
except ModuleNotFoundError:
    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.append(str(ROOT_DIR))
    from src.config.settings import BACKGROUND_MUSIC_DIR, OUTPUT_DIR, TEMP_DIR, TRANSITIONS_DIR
    from src.core.editor_ffmpeg import FFmpegEngine
    from src.core.legendas_ass import GeradorLegendasASS
    from src.core.pexels_fetcher import PexelsFetcher, PexelsFetcherError
    from src.core.tts_clonador import TTSManager
    from src.core.whisper_sync import WhisperSync
    from src.utils.logger import get_logger
    from src.utils.run_reporter import RunReporter
    from src.utils.text_helpers import normalizar_ascii
    from src.utils.workspace_cleaner import WorkspaceCleaner


class VideoPipeline:
    """Coordinates TTS, Whisper timing, Pexels media and FFmpeg rendering."""

    MAX_MEDIA_FALLBACK_PERCENT = 15.0

    CENAS_MOCK = [
        {"texto": "O universo e vasto e escuro.", "busca": "dark space stars"},
        {"texto": "Mas a luz viaja rapido.", "busca": "light speed laser"},
    ]

    def __init__(
        self,
        tts: TTSManager | None = None,
        whisper_sync: WhisperSync | None = None,
        pexels: PexelsFetcher | None = None,
        editor: FFmpegEngine | None = None,
        legendas: GeradorLegendasASS | None = None,
    ) -> None:
        self.logger = get_logger(__name__)
        self.tts = tts
        self.whisper_sync = whisper_sync
        self.pexels = pexels
        self.editor = editor
        self.legendas = legendas
        self.random = random.SystemRandom()
        self.temp_root = TEMP_DIR / "pipeline"
        self.output_root = OUTPUT_DIR
        self.temp_dir = self.temp_root / "last_run"
        self.output_dir = self.temp_dir / "render"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.last_result: dict[str, Any] | None = None

    def executar(
        self,
        metadata: dict[str, Any],
        voz: str = "lucas_clone",
        limpar_artefatos: bool = True,
        usar_musica: bool = True,
        usar_transicoes: bool = True,
        usar_legendas: bool = True,
    ) -> dict[str, Any]:
        """Runs one prepared metadata.json version without local LLM generation."""

        if not isinstance(metadata, dict):
            raise ValueError("Pipeline Text-First em lote recebe apenas um dicionario metadata.")

        tema = self._tema_metadata(metadata)
        nome_versao = self._normalizar_nome_versao(str(metadata.get("versao", "versao_longa")))
        cenas = self._cenas_metadata(metadata)
        midias_locais = self._normalizar_midias_locais(metadata.get("midias_locais"))
        output_filename = str(
            metadata.get("output_filename")
            or f"synthreel_{self._slug(tema)}_{nome_versao}.mp4"
        )
        job_name = self._job_name(f"{tema}_{nome_versao}")
        reporter = RunReporter(tema=tema, job_name=job_name)

        try:
            reporter.stage(
                "metadata",
                "Metadata externo recebido; LLM local ignorado.",
                versao=nome_versao,
                total_midias_locais=len(midias_locais["todos"]) if midias_locais else 0,
                output_filename=output_filename,
            )
            resultado = self._executar_cenas(
                cenas=cenas,
                job_name=job_name,
                nome_versao=nome_versao,
                audio_filename=self._audio_filename_for_voice(voz),
                video_sem_audio_filename="video_sem_audio.mp4",
                video_final_filename=output_filename,
                voz=voz,
                reporter=reporter,
                roteiro_source=str(metadata.get("source", "metadata.json")),
                limpar_artefatos=limpar_artefatos,
                usar_musica=usar_musica,
                usar_transicoes=usar_transicoes,
                usar_legendas=usar_legendas,
                midias_locais=midias_locais,
            )
            reporter.complete(resultado)
            self.last_result = resultado
            return resultado
        except Exception as exc:
            reporter.fail(exc)
            raise

    def executar_mock(
        self,
        limpar_artefatos: bool = True,
        usar_musica: bool = True,
        usar_transicoes: bool = True,
        usar_legendas: bool = True,
    ) -> dict[str, Any]:
        """Runs a physical end-to-end pipeline with two static scenes."""

        self.logger.info("Pipeline: inicio do mock integrado")
        job_name = self._job_name("pipeline mock")
        reporter = RunReporter(tema="mock integrado", job_name=job_name)
        cenas = [dict(cena) for cena in self.CENAS_MOCK]
        try:
            resultado = self._executar_cenas(
                cenas=cenas,
                job_name=job_name,
                nome_versao="mock",
                audio_filename="narracao_mock.mp3",
                video_sem_audio_filename="mock_video_sem_audio.mp4",
                video_final_filename="synthreel_mock_final.mp4",
                voz="mulher_01",
                reporter=reporter,
                roteiro_source="mock",
                limpar_artefatos=limpar_artefatos,
                usar_musica=usar_musica,
                usar_transicoes=usar_transicoes,
                usar_legendas=usar_legendas,
            )
            reporter.complete(resultado)
            self.last_result = resultado
            return resultado
        except Exception as exc:
            reporter.fail(exc)
            raise

    def _executar_cenas(
        self,
        cenas: list[dict[str, str]],
        job_name: str,
        nome_versao: str,
        audio_filename: str,
        video_sem_audio_filename: str,
        video_final_filename: str,
        voz: str,
        reporter: RunReporter,
        roteiro_source: str,
        limpar_artefatos: bool,
        usar_musica: bool,
        usar_transicoes: bool,
        usar_legendas: bool,
        midias_locais: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._preparar_workspace(job_name, nome_versao)
        cenas = self._validar_cenas(cenas)
        reporter.stage(
            "workspace",
            f"Workspace da {nome_versao} preparado.",
            versao=nome_versao,
            temp_dir=str(self.temp_dir),
            render_dir=str(self.output_dir),
            output_dir=str(self.output_root),
        )
        reporter.set_roteiro(cenas, source=roteiro_source, versao=nome_versao)
        texto_unico = " ".join(cena["texto"] for cena in cenas)

        audio_path = self.temp_dir / audio_filename
        self.logger.info("Pipeline: etapa TTS %s em %s cenas", nome_versao, len(cenas))
        audio_gerado = self._gerar_narracao_por_cenas(cenas, audio_path, voz=voz)
        reporter.set_tts(texto=texto_unico, voz=voz, output_path=audio_gerado, versao=nome_versao)

        self.logger.info("Pipeline: etapa Whisper %s", nome_versao)
        timestamps = self._whisper_sync().extrair_timestamps(audio_gerado)
        duracao_projetada = self._duracao_timestamps(timestamps)
        self.logger.info(
            "Whisper: {%s} - %s palavras extraidas. Duracao projetada: %.1f segundos.",
            nome_versao,
            len(timestamps),
            duracao_projetada,
        )
        cenas_temporizadas = self._calcular_tempos_cenas(cenas, timestamps)
        timestamps_legenda = self._gerar_timestamps_legenda(cenas_temporizadas, timestamps)
        reporter.set_whisper(
            timestamps=timestamps,
            cenas_temporizadas=cenas_temporizadas,
            versao=nome_versao,
            duracao=duracao_projetada,
        )

        midias_usadas: set[str] = set()
        clipes_visuais: list[Path] = []
        segmentos_visuais: list[dict[str, Any]] = []
        duracao_total_video = sum(float(cena["duracao"]) for cena in cenas_temporizadas)
        total_cenas = len(cenas_temporizadas)
        falhas_midia_local = 0

        for indice, cena in enumerate(cenas_temporizadas, start=1):
            self.logger.info(
                "Pipeline: cena %s busca='%s' duracao=%.3fs",
                indice,
                cena["busca"],
                cena["duracao"],
            )

            clipes_cena, segmentos_cena, cena_usou_fallback_local = self._renderizar_segmentos_cena(
                indice=indice,
                cena=cena,
                midias_usadas=midias_usadas,
                reporter=reporter,
                nome_versao=nome_versao,
                total_cenas=total_cenas,
                duracao_total_video=duracao_total_video,
                falhas_midia_local=falhas_midia_local,
                midias_locais=midias_locais,
            )
            if cena_usou_fallback_local:
                falhas_midia_local += 1
            clipes_visuais.extend(clipes_cena)
            segmentos_visuais.extend(segmentos_cena)

        if not clipes_visuais:
            raise RuntimeError("Nenhuma cena visual foi renderizada; concat abortado.")

        self.logger.info("Pipeline: concatenando cenas")
        video_sem_audio = self.output_dir / video_sem_audio_filename
        self._editor().juntar_cenas(clipes_visuais, video_sem_audio)
        reporter.set_concat(clipes=clipes_visuais, video_sem_audio=video_sem_audio, versao=nome_versao)

        duracao_total = sum(float(cena["duracao"]) for cena in cenas_temporizadas)
        transicoes = self._selecionar_transicoes(segmentos_visuais) if usar_transicoes else []
        musica_fundo = self._selecionar_musica_fundo() if usar_musica else None
        reporter.set_assets(background_music=musica_fundo, transicoes=transicoes, versao=nome_versao)

        self.logger.info("Pipeline: aplicando overlays de transicao")
        video_com_transicoes = self.output_dir / "video_com_transicoes.mp4"
        self._editor().aplicar_transicoes_overlay(video_sem_audio, transicoes, video_com_transicoes)

        self.logger.info("Pipeline: mixando narracao, musica e transicoes")
        video_mixado = self.output_dir / "video_mixado_sem_legendas.mp4"
        self._editor().mixar_audio_final(
            input_video=video_com_transicoes,
            narracao=audio_gerado,
            output_path=video_mixado,
            background_music=musica_fundo,
            transicoes=transicoes,
            duracao=duracao_total,
        )

        video_final = self.output_root / video_final_filename
        if usar_legendas:
            self.logger.info("Pipeline: gerando legendas ASS")
            ass_file = self.temp_dir / "legendas.ass"
            legenda_path = Path(
                self._legendas().gerar_arquivo(timestamps_legenda, ass_file, max_palavras_linha=2)
            )
            reporter.set_legendas(legenda_path, max_palavras_linha=2, versao=nome_versao)

            self.logger.info("Pipeline: queimando legendas no video final")
            self._editor().queimar_legendas(video_mixado, legenda_path, video_final)
        else:
            reporter.stage(
                "legendas",
                f"Legendas desativadas para {nome_versao}.",
                versao=nome_versao,
            )
            self._editor().aplicar_transicoes_overlay(video_mixado, [], video_final)

        cleanup_result: dict[str, Any] = {
            "mode": "disabled",
            "removed_files": 0,
            "removed_dirs": 0,
            "removed_bytes": 0,
            "removed_samples": [],
            "kept": [str(video_final.resolve())],
            "errors": [],
        }
        if limpar_artefatos:
            cleanup_result = WorkspaceCleaner().clean_run(
                temp_dir=self.temp_dir,
                output_dir=self.output_root / f"__sem_intermediarios_{job_name}_{nome_versao}",
                keep_paths=[video_final],
            )
            reporter.set_cleanup(cleanup_result, versao=nome_versao)
        else:
            reporter.stage(
                "cleanup",
                f"Limpeza de artefatos ignorada para debug em {nome_versao}.",
                versao=nome_versao,
            )

        resultado = {
            "versao": nome_versao,
            "video_final": str(video_final.resolve()),
            "log_dir": str(reporter.log_dir.resolve()),
            "summary_md": str(reporter.summary_md.resolve()),
            "summary_json": str(reporter.summary_json.resolve()),
            "execution_log": str(reporter.execution_log.resolve()),
            "cleanup": cleanup_result,
            "cenas": cenas_temporizadas,
            "segmentos_visuais": segmentos_visuais,
            "midias_usadas": sorted(midias_usadas),
            "falhas_midia_local": falhas_midia_local,
            "percentual_falhas_midia_local": round(
                self._calcular_percentual_falhas_midia(
                    falhas_midia_local=falhas_midia_local,
                    total_cenas=total_cenas,
                ),
                2,
            ),
            "limite_percentual_falhas_midia": self.MAX_MEDIA_FALLBACK_PERCENT,
            "background_music": str(musica_fundo.resolve()) if musica_fundo else None,
            "transicoes": [
                {
                    **item,
                    "path": str(Path(str(item["path"])).resolve()),
                }
                for item in transicoes
            ],
        }
        if not limpar_artefatos:
            resultado["audio"] = str(audio_gerado.resolve())
            resultado["video_sem_audio"] = str(video_sem_audio.resolve())

        reporter.set_outputs(resultado, versao=nome_versao)
        self.logger.info("Pipeline: render concluido em %s", video_final)
        self.last_result = resultado
        return resultado

    def _gerar_narracao_por_cenas(
        self,
        cenas: list[dict[str, str]],
        output_path: Path,
        voz: str,
    ) -> Path:
        segmentos_dir = output_path.parent / f"{output_path.stem}_segmentos_tts"
        segmentos_dir.mkdir(parents=True, exist_ok=True)
        suffix = output_path.suffix or ".mp3"
        audio_segmentos: list[Path] = []
        cleanup_paths: list[Path] = []

        try:
            for indice, cena in enumerate(cenas, start=1):
                texto_cena = str(cena["texto"]).strip()
                segmento_path = segmentos_dir / f"{output_path.stem}_cena_{indice:03d}{suffix}"
                cleanup_paths.append(segmento_path)

                self.logger.info(
                    "Pipeline: TTS cena %s/%s voz=%s caracteres=%s",
                    indice,
                    len(cenas),
                    voz,
                    len(texto_cena),
                )
                audio_gerado = Path(self._tts().narrar(texto_cena, segmento_path, voz=voz))
                cleanup_paths.append(audio_gerado)
                audio_segmentos.append(audio_gerado)

            self._concatenar_audios_soundfile(audio_segmentos, output_path)
            return output_path.resolve()
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        finally:
            self._limpar_segmentos_tts(cleanup_paths, segmentos_dir)

    def _concatenar_audios_soundfile(self, audio_paths: list[Path], output_path: Path) -> None:
        if not audio_paths:
            raise RuntimeError("Nenhum audio intermediario de TTS foi gerado.")

        chunks: list[np.ndarray] = []
        sample_rate: int | None = None
        channels: int | None = None

        for indice, audio_path in enumerate(audio_paths, start=1):
            if not audio_path.exists() or audio_path.stat().st_size == 0:
                raise RuntimeError(f"Audio intermediario de TTS invalido: {audio_path}")

            data, current_sample_rate = sf.read(str(audio_path), always_2d=True)
            if data.size == 0:
                raise RuntimeError(f"Audio intermediario de TTS vazio: {audio_path}")

            current_channels = int(data.shape[1])
            if sample_rate is None:
                sample_rate = int(current_sample_rate)
                channels = current_channels
            elif current_sample_rate != sample_rate:
                raise RuntimeError(
                    "Audios intermediarios de TTS possuem sample rates diferentes: "
                    f"esperado {sample_rate}, cena {indice} retornou {current_sample_rate}."
                )
            elif current_channels != channels:
                raise RuntimeError(
                    "Audios intermediarios de TTS possuem canais diferentes: "
                    f"esperado {channels}, cena {indice} retornou {current_channels}."
                )

            chunks.append(data)

        if sample_rate is None:
            raise RuntimeError("Nao foi possivel detectar o sample rate da narracao.")

        audio_final = np.concatenate(chunks, axis=0)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_kwargs: dict[str, str] = {}
        file_format = self._soundfile_format(output_path)
        subtype = self._soundfile_subtype(output_path)
        if file_format:
            write_kwargs["format"] = file_format
        if subtype:
            write_kwargs["subtype"] = subtype

        sf.write(str(output_path), audio_final, sample_rate, **write_kwargs)
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"Falha ao concatenar narracao final em: {output_path}")

        self.logger.info(
            "Pipeline: narracao final concatenada com %s segmentos em %s",
            len(audio_paths),
            output_path,
        )

    @staticmethod
    def _soundfile_format(output_path: Path) -> str | None:
        if output_path.suffix.lower() == ".mp3":
            return "MP3"
        return None

    @staticmethod
    def _soundfile_subtype(output_path: Path) -> str | None:
        if output_path.suffix.lower() == ".mp3":
            return "MPEG_LAYER_III"
        return None

    @staticmethod
    def _limpar_segmentos_tts(paths: list[Path], segmentos_dir: Path) -> None:
        vistos: set[str] = set()
        for path in paths:
            key = str(path.resolve())
            if key in vistos:
                continue
            vistos.add(key)
            path.unlink(missing_ok=True)

        try:
            segmentos_dir.rmdir()
        except OSError:
            pass

    def _renderizar_segmentos_cena(
        self,
        indice: int,
        cena: dict[str, Any],
        midias_usadas: set[str],
        reporter: RunReporter,
        nome_versao: str,
        total_cenas: int,
        duracao_total_video: float,
        falhas_midia_local: int,
        midias_locais: dict[str, Any] | None = None,
    ) -> tuple[list[Path], list[dict[str, Any]], bool]:
        duracao_total = float(cena["duracao"])
        midias_cena = self._midias_locais_para_cena(midias_locais, indice)
        if not midias_cena and midias_locais and indice == 1:
            midias_cena = list(midias_locais.get("todos") or [])
        if midias_cena:
            cena = {
                **cena,
                "_midias_locais_count": len(midias_cena),
            }
        duracoes = self._duracoes_segmentos_visuais(
            cena=cena,
            indice=indice,
            total_cenas=total_cenas,
            duracao_total_video=duracao_total_video,
        )
        if len(duracoes) > 1:
            self.logger.info(
                "Pipeline: B-Roll Break cena %s duracao=%.3fs em %s segmentos visuais.",
                indice,
                duracao_total,
                len(duracoes),
            )

        clipes: list[Path] = []
        segmentos: list[dict[str, Any]] = []
        inicio_visual = float(cena["inicio_audio"])
        cena_usou_fallback_local = False

        for segmento_indice, duracao_segmento in enumerate(duracoes, start=1):
            indice_visual = (indice * 10) + segmento_indice if len(duracoes) > 1 else indice
            cena_segmento = {
                **cena,
                "duracao": duracao_segmento,
                "segmento_visual": segmento_indice,
                "total_segmentos_visuais": len(duracoes),
                "inicio_visual_segmento": round(inicio_visual, 3),
                "fim_visual_segmento": round(inicio_visual + duracao_segmento, 3),
            }

            if midias_locais:
                midia = self._obter_midia_local_curada(
                    indice_cena=indice,
                    indice_visual=indice_visual,
                    indice_segmento=segmento_indice,
                    midias_locais=midias_locais,
                )
            else:
                midia = self._obter_midia_com_fallback(indice_visual, cena_segmento, midias_usadas)
            fallback_local = bool(midia.get("fallback_local"))
            cena_segmento["fallback_midia_local"] = fallback_local
            if fallback_local and not cena_usou_fallback_local:
                falhas_previstas = falhas_midia_local + 1
                percentual_previsto = self._calcular_percentual_falhas_midia(
                    falhas_midia_local=falhas_previstas,
                    total_cenas=total_cenas,
                )
                reporter.stage(
                    "midias",
                    (
                        f"Fallback local acionado na cena {indice} de {nome_versao}: "
                        f"{falhas_previstas}/{total_cenas} cenas "
                        f"({percentual_previsto:.2f}%)."
                    ),
                    versao=nome_versao,
                    indice=indice,
                    busca=cena.get("busca"),
                    falhas_midia_local=falhas_previstas,
                    total_cenas=total_cenas,
                    percentual_falhas=round(percentual_previsto, 2),
                    limite_percentual=self.MAX_MEDIA_FALLBACK_PERCENT,
                )
                self._validar_limite_falhas_midia(
                    falhas_midia_local=falhas_previstas,
                    total_cenas=total_cenas,
                )
                cena_usou_fallback_local = True
            midias_usadas.add(str(midia["id"]))

            clipe_visual = self._renderizar_cena(indice_visual, midia, cena_segmento)
            clipes.append(clipe_visual)
            segmentos.append(cena_segmento)
            reporter.add_scene(
                indice=indice,
                cena=cena_segmento,
                midia=midia,
                clipe_visual=clipe_visual,
                versao=nome_versao,
            )
            inicio_visual += duracao_segmento

        return clipes, segmentos, cena_usou_fallback_local

    @classmethod
    def _validar_limite_falhas_midia(
        cls,
        *,
        falhas_midia_local: int,
        total_cenas: int,
    ) -> float:
        percentual = cls._calcular_percentual_falhas_midia(
            falhas_midia_local=falhas_midia_local,
            total_cenas=total_cenas,
        )
        if percentual > cls.MAX_MEDIA_FALLBACK_PERCENT:
            raise RuntimeError(
                "Trava de qualidade visual acionada: fallback local em "
                f"{falhas_midia_local}/{total_cenas} cenas ({percentual:.2f}%), "
                f"acima do limite de {cls.MAX_MEDIA_FALLBACK_PERCENT:.2f}%. "
                "Abortando para evitar telas genericas em excesso."
            )
        return percentual

    @staticmethod
    def _calcular_percentual_falhas_midia(
        *,
        falhas_midia_local: int,
        total_cenas: int,
    ) -> float:
        if falhas_midia_local < 0:
            raise ValueError("falhas_midia_local nao pode ser negativo.")
        if total_cenas <= 0:
            raise ValueError("total_cenas deve ser maior que zero.")
        return (falhas_midia_local / total_cenas) * 100.0

    @staticmethod
    def _duracoes_segmentos_visuais(
        cena: dict[str, Any],
        indice: int,
        total_cenas: int,
        duracao_total_video: float,
    ) -> list[float]:
        duracao_total = round(float(cena["duracao"]), 3)
        if duracao_total <= 0:
            return [0.5]

        midias_locais_count = int(cena.get("_midias_locais_count") or 0)
        if indice == 1 and midias_locais_count > 1:
            segmentos = max(1, midias_locais_count)
            duracoes = [round(duracao_total / segmentos, 3) for _ in range(segmentos)]
            duracoes[-1] = round(duracao_total - sum(duracoes[:-1]), 3)
            return duracoes

        inicio_audio = float(cena.get("inicio_audio", 0.0))
        fim_audio = float(cena.get("fim_audio", inicio_audio + duracao_total))
        duracao_audio = round(max(0.0, fim_audio - inicio_audio), 3)
        if duracao_audio <= 3.0:
            return [duracao_total]

        primeira_metade = round(duracao_total / 2, 3)
        segunda_metade = round(duracao_total - primeira_metade, 3)
        return [primeira_metade, segunda_metade]

    def _obter_midia_local_curada(
        self,
        *,
        indice_cena: int,
        indice_visual: int,
        indice_segmento: int,
        midias_locais: dict[str, Any],
    ) -> dict[str, Any]:
        candidatos = self._midias_locais_para_cena(midias_locais, indice_cena)
        if not candidatos:
            candidatos = list(midias_locais.get("todos") or [])
        if not candidatos:
            raise RuntimeError("Nenhuma midia local curada disponivel para renderizacao.")

        posicao = (indice_segmento - 1) % len(candidatos)
        midia = dict(candidatos[posicao])
        midia["id"] = f"local_{indice_visual:02d}_{midia['id']}"
        self.logger.info(
            "Pipeline: usando midia local cena=%s segmento=%s arquivo=%s",
            indice_cena,
            indice_segmento,
            Path(str(midia["path_local"])).name,
        )
        return midia

    @staticmethod
    def _midias_locais_para_cena(
        midias_locais: dict[str, Any] | None,
        indice_cena: int,
    ) -> list[dict[str, Any]]:
        if not midias_locais:
            return []
        por_cena = midias_locais.get("por_cena") or {}
        return list(por_cena.get(indice_cena) or por_cena.get(str(indice_cena)) or [])

    def _obter_midia_com_fallback(
        self,
        indice: int,
        cena: dict[str, Any],
        midias_usadas: set[str],
    ) -> dict[str, Any]:
        try:
            return self._pexels().obter_midia_para_cena(
                query=cena["busca"],
                midias_usadas=midias_usadas,
                storage_path=self.temp_dir,
            )
        except (PexelsFetcherError, OSError, RuntimeError) as exc:
            self.logger.warning(
                "Pipeline: Pexels falhou na cena %s busca='%s'; usando fallback local: %s",
                indice,
                cena["busca"],
                exc,
            )
            return self._midia_fallback(indice)

    def _midia_fallback(self, indice: int) -> dict[str, Any]:
        fallback_path = self.temp_dir / "fallback_texture.png"
        if not fallback_path.exists():
            self._editor().criar_imagem_fallback(fallback_path)

        return {
            "id": f"fallback_{indice:02d}",
            "tipo": "foto",
            "path_local": str(fallback_path.resolve()),
            "precisa_de_grid": False,
            "is_photo": True,
            "orientacao": "portrait",
            "width": self._editor().width,
            "height": self._editor().height,
            "pexels_url": None,
            "autor": "SynthReel fallback",
            "download_url": None,
            "fallback_local": True,
        }

    def _renderizar_cena(self, indice: int, midia: dict[str, Any], cena: dict[str, Any]) -> Path:
        duracao = float(cena["duracao"])
        raw_path = Path(str(midia["path_local"]))
        stem = f"cena_{indice:02d}_{midia['id']}"

        if midia.get("is_photo"):
            visual_bruto = self.output_dir / f"{stem}_ken_burns_raw.mp4"
            visual_final = self.output_dir / f"{stem}_ken_burns.mp4"
            self._editor().aplicar_ken_burns(raw_path, visual_bruto, duration=duracao)
            return self._editor().ajustar_duracao_video(visual_bruto, visual_final, duration=duracao)

        cortado_path = self.temp_dir / f"{stem}_cortado.mp4"
        self._editor().cortar_midia(raw_path, cortado_path, start_time=0.0, duration=duracao)

        if midia.get("precisa_de_grid"):
            visual_bruto = self.output_dir / f"{stem}_grid_1x3_raw.mp4"
            visual_final = self.output_dir / f"{stem}_grid_1x3.mp4"
            self._editor().aplicar_grid_1x3(cortado_path, visual_bruto)
            return self._editor().ajustar_duracao_video(visual_bruto, visual_final, duration=duracao)

        visual_bruto = self.output_dir / f"{stem}_fullscreen_raw.mp4"
        visual_final = self.output_dir / f"{stem}_fullscreen.mp4"
        self._editor().aplicar_fullscreen_9x16(cortado_path, visual_bruto)
        return self._editor().ajustar_duracao_video(visual_bruto, visual_final, duration=duracao)

    def _selecionar_musica_fundo(self) -> Path | None:
        formatos = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
        musicas = self._listar_assets(BACKGROUND_MUSIC_DIR, formatos)
        if not musicas:
            self.logger.warning("Pipeline: nenhuma musica de fundo encontrada em %s", BACKGROUND_MUSIC_DIR)
            return None
        musica = self.random.choice(musicas)
        self.logger.info("Pipeline: musica de fundo selecionada: %s", musica.name)
        return musica

    def _selecionar_transicoes(self, cenas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        formatos = {".mp4", ".mov", ".webm"}
        assets = self._listar_assets(TRANSITIONS_DIR, formatos)
        if not assets or len(cenas) < 2:
            if not assets:
                self.logger.warning("Pipeline: nenhuma transicao encontrada em %s", TRANSITIONS_DIR)
            return []

        cortes: list[float] = []
        cursor = 0.0
        for cena in cenas[:-1]:
            cursor += float(cena["duracao"])
            cortes.append(cursor)

        escolhidos = (
            self.random.sample(assets, k=len(cortes))
            if len(assets) >= len(cortes)
            else [self.random.choice(assets) for _ in cortes]
        )

        transicoes: list[dict[str, Any]] = []
        for indice, (corte, asset) in enumerate(zip(cortes, escolhidos, strict=True), start=1):
            try:
                duracao_asset = self._editor().obter_duracao(asset)
            except RuntimeError:
                duracao_asset = 0.7
            duracao = round(min(max(duracao_asset, 0.25), 0.45), 3)
            inicio = round(max(0.0, corte - (duracao / 2)), 3)
            transicoes.append(
                {
                    "indice": indice,
                    "path": asset,
                    "inicio": inicio,
                    "duracao": duracao,
                    "corte": round(corte, 3),
                    "nome": asset.name,
                }
            )

        self.logger.info("Pipeline: %s transicoes selecionadas", len(transicoes))
        return transicoes

    @staticmethod
    def _listar_assets(root: Path, formatos: set[str]) -> list[Path]:
        if not root.exists():
            return []
        return sorted(
            [
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in formatos
            ],
            key=lambda item: item.name.lower(),
        )

    def _calcular_tempos_cenas(
        self,
        cenas: list[dict[str, str]],
        timestamps: list[dict[str, float | str]],
    ) -> list[dict[str, Any]]:
        if not timestamps:
            raise RuntimeError("Whisper nao retornou timestamps de palavras.")

        palavras_por_cena = [self._extrair_palavras(cena["texto"]) for cena in cenas]
        total_roteiro = sum(len(palavras) for palavras in palavras_por_cena)
        if total_roteiro <= 0:
            raise RuntimeError("Roteiro nao possui palavras validas para temporizacao.")

        limites_cenas = self._limites_cenas_proporcionais(cenas, timestamps, palavras_por_cena)
        palavras_alinhadas = self._alinhar_palavras_roteiro_com_whisper(
            cenas=cenas,
            timestamps_whisper=timestamps,
            limites_cenas=limites_cenas,
        )
        inicio_audio_total, fim_audio_total = self._intervalo_timestamps(timestamps)

        cenas_temporizadas: list[dict[str, Any]] = []
        cursor = 0
        for indice, cena in enumerate(cenas):
            quantidade_palavras = len(palavras_por_cena[indice])
            bloco = palavras_alinhadas[cursor : cursor + quantidade_palavras]
            cursor += quantidade_palavras

            if bloco:
                inicio = float(bloco[0]["inicio"])
                fim_fala = float(bloco[-1]["fim"])
            else:
                inicio, fim_fala = limites_cenas[indice]

            if indice == 0:
                inicio = min(inicio_audio_total, inicio)

            if cursor < len(palavras_alinhadas):
                fim_visual = float(palavras_alinhadas[cursor]["inicio"])
            else:
                fim_visual = max(fim_fala, fim_audio_total)
            fim_visual = max(fim_visual, fim_fala)

            cenas_temporizadas.append(self._montar_cena_temporizada(cena, inicio, fim_fala, fim_visual))

        self.logger.info(
            "Pipeline: cenas temporizadas com alinhamento difuso roteiro/Whisper em %s palavras oficiais.",
            total_roteiro,
        )
        return cenas_temporizadas

    def _gerar_timestamps_legenda(
        self,
        cenas_temporizadas: list[dict[str, Any]],
        timestamps_whisper: list[dict[str, float | str]],
    ) -> list[dict[str, float | str]]:
        """Uses Whisper timing but keeps the official roteiro text on screen."""

        palavras_por_cena = [self._extrair_palavras(cena["texto"]) for cena in cenas_temporizadas]
        total_roteiro = sum(len(palavras) for palavras in palavras_por_cena)
        if total_roteiro <= 0:
            raise RuntimeError("Roteiro nao possui palavras validas para legendas.")

        limites_cenas = [
            (float(cena["inicio_audio"]), float(cena["fim_audio"]))
            for cena in cenas_temporizadas
        ]
        palavras_alinhadas = self._alinhar_palavras_roteiro_com_whisper(
            cenas=cenas_temporizadas,
            timestamps_whisper=timestamps_whisper,
            limites_cenas=limites_cenas,
        )
        legendas = [
            {
                "palavra": str(item["palavra"]),
                "inicio": round(float(item["inicio"]), 3),
                "fim": round(float(item["fim"]), 3),
            }
            for item in palavras_alinhadas
        ]

        self.logger.info(
            "Pipeline: legendas alinhadas por similaridade usando %s palavras oficiais do roteiro.",
            len(legendas),
        )
        return legendas

    def _alinhar_palavras_roteiro_com_whisper(
        self,
        cenas: list[dict[str, Any]],
        timestamps_whisper: list[dict[str, float | str]],
        limites_cenas: list[tuple[float, float]],
    ) -> list[dict[str, Any]]:
        palavras_roteiro = self._palavras_roteiro_por_cena(cenas)
        if not palavras_roteiro:
            raise RuntimeError("Roteiro nao possui palavras validas para alinhamento.")

        palavras_whisper = self._palavras_whisper_normalizadas(timestamps_whisper)
        if palavras_whisper:
            roteiro_normalizado = [str(item["normalizado"]) for item in palavras_roteiro]
            whisper_normalizado = [str(item["normalizado"]) for item in palavras_whisper]
            matcher = SequenceMatcher(None, roteiro_normalizado, whisper_normalizado, autojunk=False)

            for tag, roteiro_inicio, roteiro_fim, whisper_inicio, whisper_fim in matcher.get_opcodes():
                if tag != "equal":
                    continue
                for roteiro_indice, whisper_indice in zip(
                    range(roteiro_inicio, roteiro_fim),
                    range(whisper_inicio, whisper_fim),
                    strict=True,
                ):
                    base = palavras_whisper[whisper_indice]
                    palavras_roteiro[roteiro_indice]["inicio"] = base["inicio"]
                    palavras_roteiro[roteiro_indice]["fim"] = base["fim"]
                    palavras_roteiro[roteiro_indice]["fonte_tempo"] = "whisper"

        self._interpolar_palavras_por_cena(palavras_roteiro, limites_cenas)
        return palavras_roteiro

    def _palavras_roteiro_por_cena(self, cenas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        palavras: list[dict[str, Any]] = []
        for cena_indice, cena in enumerate(cenas):
            for palavra in self._extrair_palavras(str(cena["texto"])):
                normalizado = self._normalizar_palavra_alinhamento(palavra)
                if not normalizado:
                    continue
                palavras.append(
                    {
                        "palavra": palavra,
                        "normalizado": normalizado,
                        "cena_indice": cena_indice,
                        "inicio": None,
                        "fim": None,
                        "fonte_tempo": "interpolado",
                    }
                )
        return palavras

    def _palavras_whisper_normalizadas(
        self,
        timestamps: list[dict[str, float | str]],
    ) -> list[dict[str, Any]]:
        palavras: list[dict[str, Any]] = []
        for item in timestamps:
            normalizado = self._normalizar_palavra_alinhamento(str(item.get("palavra", "")))
            if not normalizado:
                continue
            inicio = float(item["inicio"])
            fim = float(item["fim"])
            if fim <= inicio:
                fim = inicio + 0.18
            palavras.append(
                {
                    "normalizado": normalizado,
                    "inicio": round(inicio, 3),
                    "fim": round(fim, 3),
                }
            )
        return palavras

    @staticmethod
    def _interpolar_palavras_por_cena(
        palavras: list[dict[str, Any]],
        limites_cenas: list[tuple[float, float]],
    ) -> None:
        for cena_indice, (inicio_cena, fim_cena) in enumerate(limites_cenas):
            indices = [
                indice
                for indice, palavra in enumerate(palavras)
                if int(palavra["cena_indice"]) == cena_indice
            ]
            if not indices:
                continue

            fim_cena = max(fim_cena, inicio_cena + 0.18 * len(indices))
            cursor_local = 0
            while cursor_local < len(indices):
                indice_palavra = indices[cursor_local]
                if palavras[indice_palavra]["inicio"] is not None:
                    cursor_local += 1
                    continue

                inicio_run = cursor_local
                while (
                    cursor_local < len(indices)
                    and palavras[indices[cursor_local]]["inicio"] is None
                ):
                    cursor_local += 1
                fim_run = cursor_local
                run_indices = indices[inicio_run:fim_run]

                indice_anterior = indices[inicio_run - 1] if inicio_run > 0 else None
                indice_posterior = indices[fim_run] if fim_run < len(indices) else None
                limite_inicio = (
                    float(palavras[indice_anterior]["fim"])
                    if indice_anterior is not None
                    else inicio_cena
                )
                limite_fim = (
                    float(palavras[indice_posterior]["inicio"])
                    if indice_posterior is not None
                    else fim_cena
                )
                if limite_fim <= limite_inicio:
                    limite_fim = limite_inicio + (0.18 * len(run_indices))

                passo = (limite_fim - limite_inicio) / len(run_indices)
                for offset, indice_atual in enumerate(run_indices):
                    inicio = limite_inicio + (offset * passo)
                    fim = limite_inicio + ((offset + 1) * passo)
                    if fim <= inicio:
                        fim = inicio + 0.18
                    palavras[indice_atual]["inicio"] = round(inicio, 3)
                    palavras[indice_atual]["fim"] = round(fim, 3)

            for indice in indices:
                inicio = float(palavras[indice]["inicio"])
                fim = float(palavras[indice]["fim"])
                if fim <= inicio:
                    palavras[indice]["fim"] = round(inicio + 0.18, 3)

    def _limites_cenas_proporcionais(
        self,
        cenas: list[dict[str, Any]],
        timestamps: list[dict[str, float | str]],
        palavras_por_cena: list[list[str]] | None = None,
    ) -> list[tuple[float, float]]:
        if palavras_por_cena is None:
            palavras_por_cena = [self._extrair_palavras(str(cena["texto"])) for cena in cenas]

        total_palavras = max(1, sum(len(palavras) for palavras in palavras_por_cena))
        inicio_audio, fim_audio = self._intervalo_timestamps(timestamps)
        duracao_total = max(1.0, fim_audio - inicio_audio)
        cursor = inicio_audio
        limites: list[tuple[float, float]] = []

        for palavras in palavras_por_cena:
            duracao = duracao_total * (len(palavras) / total_palavras)
            fim = cursor + duracao
            limites.append((round(cursor, 3), round(fim, 3)))
            cursor = fim

        return limites

    @staticmethod
    def _intervalo_timestamps(timestamps: list[dict[str, float | str]]) -> tuple[float, float]:
        if not timestamps:
            return 0.0, 1.0
        inicio = min(float(item["inicio"]) for item in timestamps)
        fim = max(float(item["fim"]) for item in timestamps)
        if fim <= inicio:
            fim = inicio + 1.0
        return round(inicio, 3), round(fim, 3)

    @staticmethod
    def _normalizar_palavra_alinhamento(palavra: str) -> str:
        return normalizar_ascii("".join(re.findall(r"[A-Za-z0-9À-ÿ']+", palavra)))

    @staticmethod
    def _montar_cena_temporizada(
        cena: dict[str, str],
        inicio: float,
        fim_fala: float,
        fim_visual: float,
    ) -> dict[str, Any]:
        if fim_fala <= inicio:
            fim_fala = inicio + 1.0
        if fim_visual <= inicio:
            fim_visual = fim_fala

        return {
            **cena,
            "inicio_audio": round(inicio, 3),
            "fim_audio": round(fim_fala, 3),
            "fim_visual": round(fim_visual, 3),
            "duracao": round(max(0.5, fim_visual - inicio), 3),
        }

    @staticmethod
    def _extrair_palavras(texto: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9À-ÿ']+", texto)

    @staticmethod
    def _duracao_timestamps(timestamps: list[dict[str, float | str]]) -> float:
        if not timestamps:
            return 0.0
        return round(max(0.0, float(timestamps[-1]["fim"])), 3)

    def _preparar_workspace(self, job_name: str, nome_versao: str) -> None:
        versao_slug = self._slug(nome_versao)
        self.temp_dir = self.temp_root / job_name / versao_slug
        self.output_dir = self.temp_dir / "render"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _normalizar_midias_locais(self, midias: Any) -> dict[str, Any] | None:
        if not midias:
            return None
        if not isinstance(midias, list):
            raise ValueError("metadata.midias_locais deve ser uma lista.")

        todos: list[dict[str, Any]] = []
        por_cena: dict[int, list[dict[str, Any]]] = {}
        for indice, item in enumerate(midias, start=1):
            path, cena_indice = self._extrair_item_midia_local(item)
            if not path.exists() or path.stat().st_size == 0:
                raise FileNotFoundError(f"Midia local invalida: {path}")

            width, height = self._editor().obter_dimensoes(path)
            orientacao = self._orientacao_por_dimensoes(width, height)
            media = {
                "id": f"{indice:03d}_{self._slug(path.stem)}",
                "tipo": "video",
                "path_local": str(path.resolve()),
                "precisa_de_grid": orientacao == "landscape",
                "is_photo": False,
                "orientacao": orientacao,
                "width": width,
                "height": height,
                "pexels_url": None,
                "autor": "curadoria local",
                "download_url": None,
                "fallback_local": False,
                "source": "local_curado",
            }
            todos.append(media)
            cena_indice = cena_indice or self._indice_cena_por_nome(path)
            if cena_indice is not None:
                por_cena.setdefault(cena_indice, []).append(media)

        if not todos:
            raise ValueError("metadata.midias_locais nao possui videos validos.")
        return {"todos": todos, "por_cena": por_cena}

    @staticmethod
    def _extrair_item_midia_local(item: Any) -> tuple[Path, int | None]:
        if isinstance(item, (str, Path)):
            return Path(item), None
        if not isinstance(item, dict):
            raise ValueError("Cada item de midias_locais deve ser string ou objeto JSON.")

        raw_path = item.get("path") or item.get("path_local") or item.get("arquivo")
        if not raw_path:
            raise ValueError("Item de midias_locais sem path/path_local/arquivo.")

        raw_cena = item.get("cena") or item.get("indice_cena") or item.get("scene")
        cena_indice: int | None = None
        if raw_cena not in (None, ""):
            try:
                cena_indice = int(raw_cena)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Indice de cena invalido em midias_locais: {raw_cena}") from exc

        return Path(str(raw_path)), cena_indice

    @staticmethod
    def _indice_cena_por_nome(path: Path) -> int | None:
        match = re.match(r"cena_(\d+)(?:_|$)", path.stem, flags=re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1))

    @staticmethod
    def _orientacao_por_dimensoes(width: int, height: int) -> str:
        if height > width:
            return "portrait"
        if width > height:
            return "landscape"
        return "square"

    @staticmethod
    def _tema_metadata(metadata: dict[str, Any]) -> str:
        for key in ("tema", "nome_do_tema", "titulo", "title", "nome", "assunto"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "video"

    @staticmethod
    def _cenas_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
        cenas = metadata.get("cenas")
        if cenas is None:
            cenas = metadata.get("scenes")
        if not isinstance(cenas, list):
            raise ValueError("metadata deve conter uma lista em cenas.")
        return cenas

    @staticmethod
    def _normalizar_nome_versao(nome_versao: str) -> str:
        valor = nome_versao.strip().lower().replace("-", "_").replace(" ", "_")
        if valor in {"longa", "longo", "long", "tiktok", "kwai"}:
            return "versao_longa"
        if valor in {"curta", "curto", "short", "shorts", "reels"}:
            return "versao_curta"
        return valor or "versao_longa"

    def _tts(self) -> TTSManager:
        if self.tts is None:
            self.tts = TTSManager()
        return self.tts

    def _whisper_sync(self) -> WhisperSync:
        if self.whisper_sync is None:
            self.whisper_sync = WhisperSync()
        return self.whisper_sync

    def _pexels(self) -> PexelsFetcher:
        if self.pexels is None:
            self.pexels = PexelsFetcher()
        return self.pexels

    def _editor(self) -> FFmpegEngine:
        if self.editor is None:
            self.editor = FFmpegEngine()
        return self.editor

    def _legendas(self) -> GeradorLegendasASS:
        if self.legendas is None:
            self.legendas = GeradorLegendasASS(
                width=self._editor().width,
                height=self._editor().height,
            )
        return self.legendas

    @staticmethod
    def _validar_roteiros(roteiros: dict[str, list[dict[str, str]]]) -> dict[str, list[dict[str, str]]]:
        if not isinstance(roteiros, dict):
            raise ValueError("roteiro LLM deve ser um dicionario com versao_longa e versao_curta.")

        versoes: dict[str, list[dict[str, str]]] = {}
        for nome_versao in ("versao_longa", "versao_curta"):
            cenas = roteiros.get(nome_versao)
            if not isinstance(cenas, list):
                raise ValueError(f"roteiro LLM nao possui array valido para {nome_versao}.")
            versoes[nome_versao] = VideoPipeline._validar_cenas(cenas)

        return versoes

    @staticmethod
    def _validar_cenas(cenas: list[dict[str, str]]) -> list[dict[str, str]]:
        if not cenas:
            raise ValueError("roteiro nao possui cenas.")

        cenas_validas: list[dict[str, str]] = []
        for index, cena in enumerate(cenas, start=1):
            texto = str(cena.get("texto", "")).strip()
            busca = str(cena.get("busca", "")).strip()
            if not texto or not busca:
                raise ValueError(f"cena {index} precisa de texto e busca.")
            cenas_validas.append({"texto": texto, "busca": busca})
        return cenas_validas

    @staticmethod
    def _job_name(tema: str) -> str:
        slug = VideoPipeline._slug(tema)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{timestamp}_{slug}"

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9À-ÿ]+", "_", value.strip().lower())
        slug = slug.strip("_")[:48] or "video"
        return slug

    @staticmethod
    def _audio_filename_for_voice(voz: str) -> str:
        return "narracao.mp3"
