from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class Block(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    text: str = Field(min_length=3)
    scenes: list[Scene] = Field(min_length=1)


class Script(BaseModel):
    title: str = Field(min_length=3)
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
    narrator_gender: Literal["male", "female"]
    # Mantido para compatibilidade com roteiros já produzidos. O arquivo real
    # de fundo é escolhido no painel e salvo no manifesto do trabalho.
    background: str = "black"
    # O efeito é aplicado somente à imagem de fundo pelo renderizador. As cenas
    # continuam limpas para preservar a arte original.
    background_animation: Literal["none", "movimento_sutil", "movimento_lateral", "pulsacao"] = "movimento_sutil"
    blocks: list[Block] = Field(min_length=1)


class RenderRequest(BaseModel):
    script: Script
    background_image: str = Field(min_length=3)
    voice: str | None = None
