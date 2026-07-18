"""Per-run reporting for SynthReel executions."""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from backend.src.config.settings import LOGS_DIR
    from backend.src.utils.logger import configure_file_logging, get_logger
except ModuleNotFoundError:
    import sys

    ROOT_DIR = Path(__file__).resolve().parents[2]
    if str(ROOT_DIR) not in sys.path:
        sys.path.append(str(ROOT_DIR))
    from backend.src.config.settings import LOGS_DIR
    from backend.src.utils.logger import configure_file_logging, get_logger


class RunReporter:
    """Collects a readable execution report and a technical log file."""

    def __init__(self, tema: str, job_name: str) -> None:
        self.tema = tema
        self.job_name = job_name
        self.started_at = datetime.now()
        self.started_perf = time.time()
        self.log_dir = LOGS_DIR / self.started_at.strftime("%Y-%m-%d") / job_name
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.execution_log = configure_file_logging(self.log_dir / "execution.log")
        self.summary_json = self.log_dir / "summary.json"
        self.summary_md = self.log_dir / "summary.md"
        self.logger = get_logger(__name__)
        self.data: dict[str, Any] = {
            "tema": tema,
            "job_name": job_name,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "status": "running",
            "stages": [],
            "roteiro": [],
            "tts": {},
            "whisper": {},
            "cenas": [],
            "concat": {},
            "assets": {},
            "legendas": {},
            "cleanup": {},
            "outputs": {},
            "versoes": {},
            "errors": [],
        }
        self.stage("inicio", "Execucao iniciada.", tema=tema, job_name=job_name)

    def stage(self, name: str, summary: str, **details: Any) -> None:
        self.data["stages"].append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "name": name,
                "summary": summary,
                "details": self._json_safe(details),
            }
        )
        self.logger.info("RUN %s: %s", name, summary)
        self.write()

    def set_roteiro(self, cenas: list[dict[str, Any]], source: str, versao: str | None = None) -> None:
        target = self._target(versao)
        target["roteiro"] = {
            "source": source,
            "total_cenas": len(cenas),
            "cenas": self._json_safe(cenas),
        }
        self.stage(
            "roteiro",
            f"Roteiro {self._label(versao)} recebido com {len(cenas)} cenas.",
            source=source,
            versao=versao,
        )

    def set_tts(self, texto: str, voz: str, output_path: str | Path, versao: str | None = None) -> None:
        target = self._target(versao)
        target["tts"] = {
            "voz": voz,
            "output_path": str(Path(output_path).resolve()),
            "total_caracteres": len(texto),
            "texto_unico": texto,
        }
        self.stage(
            "tts",
            f"Narracao gerada para {self._label(versao)}.",
            voz=voz,
            output_path=str(output_path),
            versao=versao,
        )

    def set_whisper(
        self,
        timestamps: list[dict[str, Any]],
        cenas_temporizadas: list[dict[str, Any]],
        versao: str | None = None,
        duracao: float | None = None,
    ) -> None:
        target = self._target(versao)
        duracao_total = duracao if duracao is not None else (
            round(float(timestamps[-1]["fim"]), 3) if timestamps else 0.0
        )
        target["whisper"] = {
            "total_palavras": len(timestamps),
            "duracao_projetada": duracao_total,
            "primeira_palavra": timestamps[0] if timestamps else None,
            "ultima_palavra": timestamps[-1] if timestamps else None,
        }
        self.stage(
            "whisper",
            f"Whisper {self._label(versao)} extraiu {len(timestamps)} palavras em {duracao_total}s e sincronizou {len(cenas_temporizadas)} cenas.",
            versao=versao,
        )

    def add_scene(
        self,
        indice: int,
        cena: dict[str, Any],
        midia: dict[str, Any],
        clipe_visual: str | Path,
        versao: str | None = None,
    ) -> None:
        target = self._target(versao)
        target["cenas"].append(
            {
                "indice": indice,
                "texto": cena.get("texto"),
                "busca": cena.get("busca"),
                "inicio_audio": cena.get("inicio_audio"),
                "fim_audio": cena.get("fim_audio"),
                "fim_visual": cena.get("fim_visual"),
                "duracao": cena.get("duracao"),
                "segmento_visual": cena.get("segmento_visual"),
                "total_segmentos_visuais": cena.get("total_segmentos_visuais"),
                "inicio_visual_segmento": cena.get("inicio_visual_segmento"),
                "fim_visual_segmento": cena.get("fim_visual_segmento"),
                "midia": self._compact_media(midia),
                "clipe_visual": str(Path(clipe_visual).resolve()),
            }
        )
        segmento = cena.get("segmento_visual") or 1
        total_segmentos = cena.get("total_segmentos_visuais") or 1
        segmento_label = (
            f" segmento {segmento}/{total_segmentos}" if int(total_segmentos) > 1 else ""
        )
        tipo = "fallback local" if str(midia.get("id", "")).startswith("fallback") else "Pexels"
        self.stage(
            "cena",
            f"Cena {indice}{segmento_label} de {self._label(versao)} renderizada com midia {tipo}.",
            indice=indice,
            versao=versao,
            segmento_visual=segmento,
            total_segmentos_visuais=total_segmentos,
            inicio_visual_segmento=cena.get("inicio_visual_segmento"),
            fim_visual_segmento=cena.get("fim_visual_segmento"),
            busca=cena.get("busca"),
            midia_id=midia.get("id"),
            duracao=cena.get("duracao"),
        )

    def set_concat(self, clipes: list[Path], video_sem_audio: str | Path, versao: str | None = None) -> None:
        target = self._target(versao)
        target["concat"] = {
            "total_clipes": len(clipes),
            "clipes": [str(Path(path).resolve()) for path in clipes],
            "video_sem_audio": str(Path(video_sem_audio).resolve()),
        }
        self.stage(
            "concat",
            f"{len(clipes)} clipes visuais unidos no video sem audio de {self._label(versao)}.",
            output=str(video_sem_audio),
            versao=versao,
        )

    def set_assets(
        self,
        background_music: str | Path | None,
        transicoes: list[dict[str, Any]],
        versao: str | None = None,
    ) -> None:
        target = self._target(versao)
        target["assets"] = {
            "background_music": str(Path(background_music).resolve()) if background_music else None,
            "transicoes": self._json_safe(transicoes),
        }
        musica_nome = Path(background_music).name if background_music else "nenhuma"
        self.stage(
            "assets",
            f"Assets selecionados para {self._label(versao)}: musica={musica_nome}, transicoes={len(transicoes)}.",
            background_music=musica_nome,
            total_transicoes=len(transicoes),
            versao=versao,
        )

    def set_legendas(self, ass_file: str | Path, max_palavras_linha: int, versao: str | None = None) -> None:
        target = self._target(versao)
        target["legendas"] = {
            "ass_file": str(Path(ass_file).resolve()),
            "max_palavras_linha": max_palavras_linha,
            "estilo": "central, amarelo, grande, borda preta",
        }
        self.stage(
            "legendas",
            f"Legendas ASS geradas para {self._label(versao)} com ate {max_palavras_linha} palavras por bloco.",
            ass_file=str(ass_file),
            versao=versao,
        )

    def set_cleanup(self, cleanup: dict[str, Any], versao: str | None = None) -> None:
        target = self._target(versao)
        target["cleanup"] = self._json_safe(cleanup)
        removed_files = cleanup.get("removed_files", 0)
        removed_dirs = cleanup.get("removed_dirs", 0)
        removed_mb = round(float(cleanup.get("removed_bytes", 0)) / 1024 / 1024, 2)
        self.stage(
            "cleanup",
            f"Limpeza de {self._label(versao)} removeu {removed_files} arquivos e {removed_dirs} diretorios temporarios.",
            removed_files=removed_files,
            removed_dirs=removed_dirs,
            removed_mb=removed_mb,
            versao=versao,
        )

    def set_outputs(self, outputs: dict[str, Any], versao: str | None = None) -> None:
        target = self._target(versao)
        target["outputs"] = self._json_safe(outputs)
        self.stage(
            "saida",
            f"Saidas registradas para {self._label(versao)}.",
            versao=versao,
            video_final=outputs.get("video_final"),
        )

    def _target(self, versao: str | None) -> dict[str, Any]:
        if not versao:
            return self.data

        versoes = self.data.setdefault("versoes", {})
        if versao not in versoes:
            versoes[versao] = {
                "roteiro": {},
                "tts": {},
                "whisper": {},
                "cenas": [],
                "concat": {},
                "assets": {},
                "legendas": {},
                "cleanup": {},
                "outputs": {},
            }
        return versoes[versao]

    @staticmethod
    def _label(versao: str | None) -> str:
        return versao or "execucao"

    def complete(self, outputs: dict[str, Any]) -> None:
        self.data["status"] = "success"
        self.data["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.data["elapsed_seconds"] = round(time.time() - self.started_perf, 3)
        self.data["outputs"] = self._json_safe(outputs)
        self.stage("final", "Execucao concluida com sucesso.", **outputs)
        self.write()

    def fail(self, exc: BaseException) -> None:
        self.data["status"] = "failed"
        self.data["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.data["elapsed_seconds"] = round(time.time() - self.started_perf, 3)
        self.data["errors"].append(
            {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        self.stage("erro", f"Execucao falhou: {exc}")
        self.write()

    def write(self) -> None:
        self.summary_json.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self.summary_md.write_text(self._to_markdown(), encoding="utf-8")

    def _to_markdown(self) -> str:
        lines = [
            f"# SynthReel Run - {self.job_name}",
            "",
            f"- Tema: {self.tema}",
            f"- Status: {self.data.get('status')}",
            f"- Inicio: {self.data.get('started_at')}",
            f"- Fim: {self.data.get('finished_at', 'em andamento')}",
            f"- Duracao: {self.data.get('elapsed_seconds', 'em andamento')}s",
            f"- Log tecnico: {self.execution_log}",
            "",
            "## Etapas",
        ]

        for stage in self.data["stages"]:
            lines.append(f"- {stage['time']} | {stage['name']}: {stage['summary']}")

        versoes = self.data.get("versoes") or {}
        if versoes:
            nomes_ordenados = [
                nome for nome in ("versao_longa", "versao_curta") if nome in versoes
            ]
            nomes_ordenados.extend(nome for nome in versoes if nome not in nomes_ordenados)
            for nome in nomes_ordenados:
                self._append_versao_markdown(lines, nome, versoes[nome])

        roteiro = self.data.get("roteiro") or {}
        if roteiro:
            lines.extend(["", "## Roteiro"])
            for idx, cena in enumerate(roteiro.get("cenas", []), start=1):
                lines.append(f"{idx}. Texto: {cena.get('texto')}")
                lines.append(f"   Busca: {cena.get('busca')}")

        tts = self.data.get("tts") or {}
        if tts:
            lines.extend(
                [
                    "",
                    "## TTS",
                    f"- Voz: {tts.get('voz')}",
                    f"- Audio: {tts.get('output_path')}",
                    f"- Caracteres: {tts.get('total_caracteres')}",
                    f"- Texto unido: {tts.get('texto_unico')}",
                ]
            )

        whisper = self.data.get("whisper") or {}
        if whisper:
            lines.extend(
                [
                    "",
                    "## Whisper",
                    f"- Palavras detectadas: {whisper.get('total_palavras')}",
                    f"- Primeira palavra: {whisper.get('primeira_palavra')}",
                    f"- Ultima palavra: {whisper.get('ultima_palavra')}",
                ]
            )

        if self.data.get("cenas"):
            lines.extend(["", "## Cenas Renderizadas"])
            for cena in self.data["cenas"]:
                midia = cena.get("midia") or {}
                lines.append(f"### Cena {cena.get('indice')}")
                lines.append(f"- Texto: {cena.get('texto')}")
                lines.append(f"- Busca Pexels: {cena.get('busca')}")
                lines.append(
                    "- Tempo: "
                    f"{cena.get('inicio_audio')}s -> {cena.get('fim_audio')}s "
                    f"(visual ate {cena.get('fim_visual')}s, duracao {cena.get('duracao')}s)"
                )
                total_segmentos = cena.get("total_segmentos_visuais") or 1
                if int(total_segmentos) > 1:
                    lines.append(
                        "- Segmento visual: "
                        f"{cena.get('segmento_visual')}/{total_segmentos} "
                        f"({cena.get('inicio_visual_segmento')}s -> "
                        f"{cena.get('fim_visual_segmento')}s)"
                    )
                lines.append(
                    "- Midia: "
                    f"{midia.get('tipo')} id={midia.get('id')} orientacao={midia.get('orientacao')} "
                    f"grid={midia.get('precisa_de_grid')} foto={midia.get('is_photo')}"
                )
                tratamento = "ken_burns" if midia.get("is_photo") else (
                    "grid_1x3" if midia.get("precisa_de_grid") else "fullscreen_9x16"
                )
                lines.append(f"- Tratamento visual: {tratamento}")
                lines.append(f"- Recorte bruto: start=0.0s, duration={cena.get('duracao')}s")
                lines.append(f"- Autor: {midia.get('autor')}")
                lines.append(f"- Origem: {midia.get('pexels_url')}")
                lines.append(f"- Arquivo bruto: {midia.get('path_local')}")
                lines.append(f"- Clipe final da cena: {cena.get('clipe_visual')}")

        concat = self.data.get("concat") or {}
        if concat:
            lines.extend(
                [
                    "",
                    "## Concat",
                    f"- Total de clipes: {concat.get('total_clipes')}",
                    f"- Video sem audio: {concat.get('video_sem_audio')}",
                ]
            )

        assets = self.data.get("assets") or {}
        if assets:
            lines.extend(["", "## Assets"])
            lines.append(f"- Musica de fundo: {assets.get('background_music')}")
            transicoes = assets.get("transicoes") or []
            lines.append(f"- Transicoes: {len(transicoes)}")
            for item in transicoes:
                lines.append(
                    f"  - Corte {item.get('indice')}: {item.get('nome')} "
                    f"em {item.get('inicio')}s por {item.get('duracao')}s"
                )

        legendas = self.data.get("legendas") or {}
        if legendas:
            lines.extend(
                [
                    "",
                    "## Legendas",
                    f"- Arquivo ASS: {legendas.get('ass_file')}",
                    f"- Maximo de palavras: {legendas.get('max_palavras_linha')}",
                    f"- Estilo: {legendas.get('estilo')}",
                ]
            )

        cleanup = self.data.get("cleanup") or {}
        if cleanup:
            removed_mb = round(float(cleanup.get("removed_bytes", 0)) / 1024 / 1024, 2)
            lines.extend(
                [
                    "",
                    "## Limpeza",
                    f"- Arquivos removidos: {cleanup.get('removed_files')}",
                    f"- Diretorios removidos: {cleanup.get('removed_dirs')}",
                    f"- Espaco liberado: {removed_mb} MB",
                ]
            )
            kept = cleanup.get("kept") or []
            if kept:
                lines.append("- Mantidos:")
                for path in kept:
                    lines.append(f"  - {path}")
            if cleanup.get("errors"):
                lines.append(f"- Avisos: {len(cleanup.get('errors'))} itens nao puderam ser removidos.")

        outputs = self.data.get("outputs") or {}
        if outputs:
            lines.extend(["", "## Saidas"])
            for key, value in outputs.items():
                if key in {"cleanup", "cenas", "segmentos_visuais", "midias_usadas", "versoes"}:
                    continue
                lines.append(f"- {key}: {value}")

        if self.data.get("errors"):
            lines.extend(["", "## Erros"])
            for error in self.data["errors"]:
                lines.append(f"- {error.get('type')}: {error.get('message')}")

        lines.append("")
        return "\n".join(lines)

    def _append_versao_markdown(self, lines: list[str], nome: str, data: dict[str, Any]) -> None:
        titulo = nome.replace("_", " ").title()
        lines.extend(["", f"## {titulo}"])

        roteiro = data.get("roteiro") or {}
        if roteiro:
            lines.append(f"- Total de cenas: {roteiro.get('total_cenas')}")
            lines.append(f"- Fonte: {roteiro.get('source')}")
            lines.append("")
            lines.append("### Roteiro")
            for idx, cena in enumerate(roteiro.get("cenas", []), start=1):
                lines.append(f"{idx}. Texto: {cena.get('texto')}")
                lines.append(f"   Busca: {cena.get('busca')}")

        tts = data.get("tts") or {}
        if tts:
            lines.extend(
                [
                    "",
                    "### TTS",
                    f"- Voz: {tts.get('voz')}",
                    f"- Audio: {tts.get('output_path')}",
                    f"- Caracteres: {tts.get('total_caracteres')}",
                    f"- Texto unido: {tts.get('texto_unico')}",
                ]
            )

        whisper = data.get("whisper") or {}
        if whisper:
            lines.extend(
                [
                    "",
                    "### Whisper",
                    f"- Palavras detectadas: {whisper.get('total_palavras')}",
                    f"- Duracao projetada: {whisper.get('duracao_projetada')}s",
                    f"- Primeira palavra: {whisper.get('primeira_palavra')}",
                    f"- Ultima palavra: {whisper.get('ultima_palavra')}",
                ]
            )

        cenas = data.get("cenas") or []
        if cenas:
            lines.extend(["", "### Cenas Renderizadas"])
            for cena in cenas:
                midia = cena.get("midia") or {}
                lines.append(f"#### Cena {cena.get('indice')}")
                lines.append(f"- Texto: {cena.get('texto')}")
                lines.append(f"- Busca Pexels: {cena.get('busca')}")
                lines.append(
                    "- Tempo: "
                    f"{cena.get('inicio_audio')}s -> {cena.get('fim_audio')}s "
                    f"(visual ate {cena.get('fim_visual')}s, duracao {cena.get('duracao')}s)"
                )
                total_segmentos = cena.get("total_segmentos_visuais") or 1
                if int(total_segmentos) > 1:
                    lines.append(
                        "- Segmento visual: "
                        f"{cena.get('segmento_visual')}/{total_segmentos} "
                        f"({cena.get('inicio_visual_segmento')}s -> "
                        f"{cena.get('fim_visual_segmento')}s)"
                    )
                lines.append(
                    "- Midia: "
                    f"{midia.get('tipo')} id={midia.get('id')} orientacao={midia.get('orientacao')} "
                    f"grid={midia.get('precisa_de_grid')} foto={midia.get('is_photo')}"
                )
                tratamento = "ken_burns" if midia.get("is_photo") else (
                    "grid_1x3" if midia.get("precisa_de_grid") else "fullscreen_9x16"
                )
                lines.append(f"- Tratamento visual: {tratamento}")
                lines.append(f"- Clipe final da cena: {cena.get('clipe_visual')}")

        concat = data.get("concat") or {}
        if concat:
            lines.extend(
                [
                    "",
                    "### Concat",
                    f"- Total de clipes: {concat.get('total_clips', concat.get('total_clipes'))}",
                    f"- Video sem audio: {concat.get('video_sem_audio')}",
                ]
            )

        assets = data.get("assets") or {}
        if assets:
            transicoes = assets.get("transicoes") or []
            lines.extend(
                [
                    "",
                    "### Assets",
                    f"- Musica de fundo: {assets.get('background_music')}",
                    f"- Transicoes: {len(transicoes)}",
                ]
            )
            for item in transicoes:
                lines.append(
                    f"  - Corte {item.get('indice')}: {item.get('nome')} "
                    f"em {item.get('inicio')}s por {item.get('duracao')}s"
                )

        legendas = data.get("legendas") or {}
        if legendas:
            lines.extend(
                [
                    "",
                    "### Legendas",
                    f"- Arquivo ASS: {legendas.get('ass_file')}",
                    f"- Maximo de palavras: {legendas.get('max_palavras_linha')}",
                    f"- Estilo: {legendas.get('estilo')}",
                ]
            )

        cleanup = data.get("cleanup") or {}
        if cleanup:
            removed_mb = round(float(cleanup.get("removed_bytes", 0)) / 1024 / 1024, 2)
            lines.extend(
                [
                    "",
                    "### Limpeza",
                    f"- Arquivos removidos: {cleanup.get('removed_files')}",
                    f"- Diretorios removidos: {cleanup.get('removed_dirs')}",
                    f"- Espaco liberado: {removed_mb} MB",
                ]
            )

        outputs = data.get("outputs") or {}
        if outputs:
            lines.extend(["", "### Saidas"])
            for key, value in outputs.items():
                if key in {"cleanup", "cenas", "segmentos_visuais", "midias_usadas"}:
                    continue
                lines.append(f"- {key}: {value}")

    @staticmethod
    def _compact_media(midia: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "id",
            "tipo",
            "path_local",
            "precisa_de_grid",
            "is_photo",
            "orientacao",
            "width",
            "height",
            "pexels_url",
            "autor",
            "download_url",
            "fallback_local",
        ]
        return {key: midia.get(key) for key in keys}

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value.resolve())
        if isinstance(value, dict):
            return {str(key): RunReporter._json_safe(item) for key, item in value.items()}
        if isinstance(value, list):
            return [RunReporter._json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [RunReporter._json_safe(item) for item in value]
        return value
