"""Cria e audita a base de assets persistentes da esteira horizontal."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
ASSETS_HORIZONTAL_DIR = ROOT_DIR / "workspace" / "assets" / "horizontal"
OVERLAYS_DIR = ASSETS_HORIZONTAL_DIR / "overlays"
TRANSITION_VIDEO_EXTENSIONS = {".avi", ".mkv", ".mov", ".mp4", ".webm"}
STATIC_BACKGROUND_EXTENSIONS = {".jpg", ".jpeg", ".png"}

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass(frozen=True)
class AssetObrigatorio:
    pasta: str
    nome: str
    descricao: str

    @property
    def path(self) -> Path:
        return ASSETS_HORIZONTAL_DIR / self.pasta / self.nome


PASTAS_OBRIGATORIAS = (
    "trilhas",
    "overlays",
    "fundos_estaticos",
)

ASSETS_OBRIGATORIOS = (
    AssetObrigatorio("trilhas", "fundo_documentario.mp3", "trilha documental base"),
    AssetObrigatorio("overlays", "seta_apontamento.png", "seta do template apontamento"),
)


def setup_assets_horizontal() -> bool:
    """Cria pastas e retorna True quando todos os assets obrigatorios existem."""

    print(f"{BOLD}Setup assets horizontais{RESET}")
    print(f"Base: {ASSETS_HORIZONTAL_DIR}\n")

    for pasta in PASTAS_OBRIGATORIAS:
        path = ASSETS_HORIZONTAL_DIR / pasta
        path.mkdir(parents=True, exist_ok=True)
        print(f"{GREEN}OK{RESET} pasta: {path}")

    print("")
    faltantes: list[AssetObrigatorio] = []
    for asset in ASSETS_OBRIGATORIOS:
        if asset.path.exists() and asset.path.stat().st_size > 0:
            print(f"{GREEN}OK{RESET} {asset.descricao}: {asset.path}")
            continue

        faltantes.append(asset)
        print(f"{RED}FALTA{RESET} {asset.descricao}: {asset.path}")

    fundos_estaticos = _fundos_estaticos_validos()
    if fundos_estaticos:
        print(
            f"{GREEN}OK{RESET} fundos estaticos: "
            f"{len(fundos_estaticos)} imagem(ns) valida(s)"
        )
    else:
        print(
            f"{RED}FALTA{RESET} fundos estaticos JPG/JPEG/PNG nao vazios em "
            f"{ASSETS_HORIZONTAL_DIR / 'fundos_estaticos'}"
        )

    colecoes = _colecoes_transicoes()
    total_transicoes = sum(quantidade for _, quantidade in colecoes)
    if colecoes:
        print(
            f"{GREEN}OK{RESET} colecoes de transicoes em overlays: "
            f"{len(colecoes)} pastas, {total_transicoes} clipes candidatos"
        )
        for nome, quantidade in colecoes:
            print(f"  - {nome}: {quantidade} clipes")
    else:
        print(
            f"{RED}FALTA{RESET} colecoes de transicoes audiovisuais em "
            f"subpastas de {OVERLAYS_DIR}"
        )

    if faltantes or not fundos_estaticos or not colecoes:
        print(f"\n{YELLOW}Antes de rodar renderizar_horizontal.py, jogue manualmente estes arquivos:{RESET}")
        for asset in faltantes:
            print(f"- {asset.nome} -> {asset.path.parent}")
        if not fundos_estaticos:
            print(
                "- ao menos um fundo estatico .jpg, .jpeg ou .png -> "
                f"{ASSETS_HORIZONTAL_DIR / 'fundos_estaticos'}"
            )
        if not colecoes:
            print(f"- pastas com clipes de transicao -> {OVERLAYS_DIR}")
        return False

    print(f"\n{GREEN}Tudo pronto para a esteira horizontal.{RESET}")
    return True


def _colecoes_transicoes() -> list[tuple[str, int]]:
    colecoes: list[tuple[str, int]] = []
    if not OVERLAYS_DIR.is_dir():
        return colecoes

    for pasta in sorted(
        (path for path in OVERLAYS_DIR.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    ):
        quantidade = sum(
            1
            for path in pasta.rglob("*")
            if path.is_file() and path.suffix.lower() in TRANSITION_VIDEO_EXTENSIONS
        )
        if quantidade:
            colecoes.append((pasta.name, quantidade))
    return colecoes


def _fundos_estaticos_validos() -> list[Path]:
    """Lista fundos de composicao aceitos pelo renderer horizontal."""

    diretorio = ASSETS_HORIZONTAL_DIR / "fundos_estaticos"
    if not diretorio.is_dir():
        return []

    fundos: list[Path] = []
    for path in sorted(diretorio.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.lower() not in STATIC_BACKGROUND_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > 0:
                fundos.append(path)
        except OSError:
            continue
    return fundos


def main() -> int:
    return 0 if setup_assets_horizontal() else 1


if __name__ == "__main__":
    raise SystemExit(main())
