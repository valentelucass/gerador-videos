from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .core.tts_neural import TTSNeuralEngine


class VisualBrief(BaseModel):
    subject: str = Field(min_length=3)
    action: str = Field(min_length=3)
    setting: str = Field(min_length=3)
    framing: str = Field(min_length=3)
    details: str = Field(min_length=3)


class Transition(BaseModel):
    in_: Literal["zoom_in", "from_left", "from_right", "none"] = Field(alias="in")
    out: Literal["to_left", "to_right", "none"]
    speed: Literal["fast", "normal", "slow"] = "normal"

    model_config = {"populate_by_name": True}


SoundEffect = Literal[
    "whoosh_fast", "whoosh_cinematic", "whoosh_soft", "click", "wrong_answer",
    "camera_shutter", "cash_register", "crumpled_paper", "new_idea", "boxing_bell",
    "paper_flip", "shutter_click", "bottle_cork", "celebration", "writing",
]


class ContextSound(BaseModel):
    type: SoundEffect
    at: Literal["start", "middle", "end"] = "middle"


class Annotation(BaseModel):
    """A short on-screen note, synchronized to the corresponding scene."""

    lines: list[str] = Field(min_length=1, max_length=2)
    at: Literal["start", "middle", "end"] = "start"
    emoji: str | None = Field(default=None, max_length=8)

    @field_validator("lines")
    @classmethod
    def readable_lines(cls, value: list[str]) -> list[str]:
        cleaned = [line.strip() for line in value]
        if any(not line for line in cleaned):
            raise ValueError("annotation.lines não pode conter linhas vazias.")
        if any(len(line) > 32 for line in cleaned):
            raise ValueError("Cada linha de annotation deve ter no máximo 32 caracteres.")
        return cleaned


class SceneSounds(BaseModel):
    # Sempre explícito: o renderizador nunca cria uma cadência automática.
    transition: list[SoundEffect] = Field(default_factory=list)
    context: ContextSound | None = None


class Scene(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    # É a referência editorial obrigatória da cena. O arquivo físico pode
    # preservar o nome autodescritivo que o Google Flow gerar.
    image_id: int = Field(ge=1)
    tipo_midia: Literal["imagem", "video_generico"]
    # Chave curta em inglês, criada junto com o roteiro. Ela aproxima o JSON
    # do nome descritivo que o Google Flow escolhe ao baixar a imagem.
    asset_key: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+){1,7}$")
    image: str = Field(min_length=3)
    visual: VisualBrief
    transition: Transition
    sounds: SceneSounds = Field(default_factory=SceneSounds)
    annotation: Annotation | None = None

    @field_validator("image")
    @classmethod
    def only_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("image deve ser somente o nome do arquivo.")
        return value

    @model_validator(mode="after")
    def validate_media_extension(self) -> "Scene":
        suffix = Path(self.image).suffix.lower()
        expected = ".png" if self.tipo_midia == "imagem" else ".mp4"
        if suffix != expected:
            raise ValueError(
                f"A cena {self.id} usa tipo_midia='{self.tipo_midia}' e precisa de arquivo {expected}."
            )
        if self.tipo_midia == "video_generico" and self.transition.in_ != "zoom_in":
            raise ValueError(
                f"A cena {self.id} usa B-roll e precisa de transition.in='zoom_in' (fullscreen)."
            )
        return self


class Block(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    text: str = Field(min_length=3)
    scenes: list[Scene] = Field(min_length=1)


class Script(BaseModel):
    title: str = Field(min_length=3)
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    narrator_gender: Literal["male", "female"]
    # A voz é parte do roteiro, nunca uma escolha do envelope de renderização.
    # O validador abaixo ainda completa roteiros legados que não tinham o campo.
    voice: str = Field(min_length=3)
    # Mantido para compatibilidade com roteiros já produzidos. O arquivo real
    # de fundo é escolhido no painel e salvo no manifesto do trabalho.
    background: str = "black"
    # O efeito é aplicado somente à imagem de fundo pelo renderizador. As cenas
    # continuam limpas para preservar a arte original.
    background_animation: Literal["none", "movimento_sutil", "movimento_lateral", "pulsacao"] = "movimento_sutil"
    blocks: list[Block] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def add_voice_to_legacy_scripts(cls, value: object) -> object:
        """Completa somente roteiros antigos, sem aceitar override externo."""
        if not isinstance(value, dict) or value.get("voice") is not None:
            return value

        language = value.get("language")
        if not isinstance(language, str):
            return value
        narrator_gender = value.get("narrator_gender")
        try:
            voice = TTSNeuralEngine.default_voice_for(
                language, narrator_gender if isinstance(narrator_gender, str) else "male"
            )
        except ValueError:
            # Deixa o Pydantic reportar o erro original de idioma/campos.
            return value
        return {**value, "voice": voice}

    @model_validator(mode="after")
    def validate_neural_voice(self) -> "Script":
        try:
            self.voice = TTSNeuralEngine.validate_voice(self.language, self.voice)
            voice_gender = TTSNeuralEngine.gender_for_voice(self.language, self.voice)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if voice_gender != self.narrator_gender:
            raise ValueError(
                f"A voz {self.voice} exige narrator_gender='{voice_gender}'."
            )

        # ``image`` é também a chave física do asset no workspace. Se duas
        # cenas a reutilizarem, uma escolha feita na curadoria do Pexels
        # sobrescreve silenciosamente a outra no mesmo arquivo. A duplicação
        # ainda faz a revisão e o render parecerem usar a mídia errada.
        scenes = [scene for block in self.blocks for scene in block.scenes]
        image_names = [scene.image for scene in scenes]
        repeated_images = sorted({name for name in image_names if image_names.count(name) > 1})
        if repeated_images:
            raise ValueError(
                "Cada cena deve usar um nome de arquivo 'image' exclusivo; "
                "nomes repetidos sobrescrevem a mídia escolhida na curadoria: "
                + ", ".join(repeated_images)
            )

        return self


class RenderRequest(BaseModel):
    script: Script
    # O nome do JSON é a referência editorial da cena. Quando o operador
    # envia um arquivo com outro nome, este mapa resolve somente a fonte física
    # usada na renderização, sem reescrever o roteiro.
    image_bindings: dict[str, str] = Field(default_factory=dict)
    # Vínculos escolhidos conscientemente no painel atual. O campo legado
    # ``image_bindings`` é aceito para não quebrar clientes antigos, mas não
    # pode mais carregar o antigo pareamento automático por ordem de upload.
    manual_image_bindings: dict[str, str] = Field(default_factory=dict)
    # Ordem dos arquivos enviados nesta tela. É uma salvaguarda para que o
    # servidor consiga concluir os vínculos automáticos mesmo se a página
    # estiver com um estado React desatualizado.
    uploaded_images: list[str] = Field(default_factory=list)
    # O painel pode omitir o fundo. Nesse caso a API usa o fundo padrão
    # aprovado, em vez de impedir a renderização.
    background_image: str | None = None
    # Nome de arquivo escolhido no catálogo do painel. A API confere se ele
    # pertence à pasta de trilhas antes de iniciar o trabalho.
    music_name: str | None = None
    # Escolha visual do projeto, separada do JSON editorial. Ela define a
    # família usada nas annotations/CTAs sem alterar nenhum campo do roteiro.
    text_style: Literal[
        "impact", "serif_vintage", "minimalista", "constelacao_dourada",
        "impact_sem_borda", "branco_limpo", "neon_violeta", "coral_contorno",
        "ouro_sem_contorno", "prata_azul", "verde_lima", "azul_eletrico",
        "vermelho_alerta", "rosa_chiclete", "laranja_energia", "cinza_aco",
        "azul_marinho", "roxo_real", "verde_menta", "amarelo_retro",
    ] = "impact"

    @field_validator("image_bindings")
    @classmethod
    def only_filename_image_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        for expected_name, uploaded_name in value.items():
            for label, name in (("nome do JSON", expected_name), ("arquivo enviado", uploaded_name)):
                if (
                    not name
                    or Path(name).name != name
                    or "/" in name
                    or "\\" in name
                    or name in {".", ".."}
                ):
                    raise ValueError(f"image_bindings: {label} deve ser somente o nome do arquivo.")
        return value

    @field_validator("manual_image_bindings")
    @classmethod
    def only_filename_manual_image_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        return cls.only_filename_image_bindings(value)

    @field_validator("uploaded_images")
    @classmethod
    def only_filename_uploaded_images(cls, value: list[str]) -> list[str]:
        for name in value:
            if not name or Path(name).name != name or "/" in name or "\\" in name or name in {".", ".."}:
                raise ValueError("uploaded_images deve conter somente nomes de arquivos.")
        return value

    # Clientes antigos podem ainda enviar ``voice`` neste envelope. Campos
    # extras são ignorados deliberadamente para que nada fora do JSON altere a
    # voz já validada em ``script.voice``.
    model_config = {"extra": "ignore"}


class ValidationRequest(BaseModel):
    """Envelope de validação com compatibilidade para o corpo legado.

    O primeiro painel enviava o ``Script`` diretamente para ``/api/validate``.
    O painel novo pode acrescentar ``image_bindings`` sem obrigar clientes
    antigos a mudar de uma vez.
    """

    script: Script
    image_bindings: dict[str, str] = Field(default_factory=dict)
    manual_image_bindings: dict[str, str] = Field(default_factory=dict)
    uploaded_images: list[str] = Field(default_factory=list)
    # A prévia usa a voz real e pode ser solicitada pelo painel antes de criar
    # um trabalho de renderização. Mantemos desligada para clientes legados.
    measure_timing: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_script_body(cls, value: object) -> object:
        if isinstance(value, dict) and "script" not in value:
            return {"script": value}
        return value

    @field_validator("image_bindings")
    @classmethod
    def only_filename_image_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        # Mantém o mesmo contrato do envelope de renderização, sem introduzir
        # caminhos arbitrários durante uma simples validação.
        return RenderRequest.only_filename_image_bindings(value)

    @field_validator("manual_image_bindings")
    @classmethod
    def only_filename_manual_image_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        return RenderRequest.only_filename_image_bindings(value)

    @field_validator("uploaded_images")
    @classmethod
    def only_filename_uploaded_images(cls, value: list[str]) -> list[str]:
        return RenderRequest.only_filename_uploaded_images(value)


class PexelsCandidatesRequest(BaseModel):
    script: Script
    queries: dict[str, str] = Field(default_factory=dict)
    # Uma edição de descrição no painel não deve refazer a busca de todas as
    # cenas de B-roll do roteiro.
    scene_id: str | None = None

    @field_validator("queries")
    @classmethod
    def safe_queries(cls, value: dict[str, str]) -> dict[str, str]:
        for scene_id, query in value.items():
            if not scene_id or len(query.strip()) < 3 or len(query) > 120:
                raise ValueError("Cada busca do Pexels deve ter entre 3 e 120 caracteres.")
        return {scene_id: query.strip() for scene_id, query in value.items()}


class PexelsDownloadRequest(PexelsCandidatesRequest):
    scene_id: str
    video_id: int = Field(gt=0)


class TranslationRequest(BaseModel):
    # Blocos narrativos podem ser maiores que uma única requisição ao serviço
    # de tradução. O adaptador os divide internamente, sem descartar texto.
    text: str = Field(min_length=1, max_length=12_000)
    source_language: str = Field(min_length=2, max_length=10)


class AnimationAutomationRequest(BaseModel):
    """Nomes já presentes na biblioteca de imagens do painel atual."""

    filenames: list[str] = Field(min_length=1)
    # O checkpoint da automação é isolado por projeto do painel. Sem esse
    # escopo, um RESUME_URL ou uma imagem com o mesmo nome poderia fazer um
    # projeto novo continuar a animação de outro.
    project_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    # Retomada de um projeto Vibes externo nunca é implícita: o botão normal
    # sempre cria/usa o fluxo limpo deste projeto local.
    resume_existing: bool = False

    @field_validator("filenames")
    @classmethod
    def only_image_filenames(cls, value: list[str]) -> list[str]:
        names = list(dict.fromkeys(value))
        for name in names:
            if not name or Path(name).name != name or Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise ValueError("A automação aceita somente nomes de imagens já enviados ao painel.")
        return names
