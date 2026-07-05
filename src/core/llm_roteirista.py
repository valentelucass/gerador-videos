"""Ollama REST script generator for SynthReel."""

from __future__ import annotations

import json
import sys
from json import JSONDecodeError
from pathlib import Path
from typing import Any

import requests

try:
    from src.config.settings import settings
    from src.utils.text_helpers import (
        compactar_texto,
        contar_frases,
        contar_palavras_cenas,
        parece_ingles,
        sanitizar_busca_pexels,
        sanitizar_texto_tts,
    )
    from src.utils.logger import get_logger
except ModuleNotFoundError:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.append(str(PROJECT_ROOT))
    from src.config.settings import settings
    from src.utils.text_helpers import (
        compactar_texto,
        contar_frases,
        contar_palavras_cenas,
        parece_ingles,
        sanitizar_busca_pexels,
        sanitizar_texto_tts,
    )
    from src.utils.logger import get_logger


MIN_WORDS_LONGA = 230
MIN_WORDS_CURTA = 160


PROMPT_SISTEMA = """Você é um roteirista sênior de vídeos virais verticais (Dark/História/Ciência).
Sua tarefa é criar DUAS versões do roteiro.

REGRAS DE IDIOMA E SINTAXE (CRÍTICO PARA O TTS):
1. O "texto" DEVE estar em Português do Brasil (PT-BR) COM ACENTUAÇÃO CORRETA (á, é, í, ó, ú, ã, õ, ç). É obrigatório acentuar as palavras.
2. Escreva números por extenso (ex: "ano dois mil").
3. É PROIBIDO usar aspas, parênteses, asteriscos ou emojis. Use apenas letras, números por extenso e pontuação.

REGRAS DE RITMO E EDIÇÃO (CRÍTICO PARA RETENÇÃO):
- O motor Text-First depende da duração real da narração, não apenas do número de cenas.
- Cada "texto" deve ser um parágrafo narrativo extenso. É OBRIGATÓRIO que CADA "texto" tenha entre 3 a 5 frases longas e detalhadas.
- NÃO resuma demais. A IA deve priorizar contagem de palavras e retenção, mesmo que uma cena precise ficar mais longa.
- INÍCIO FRENÉTICO: As primeiras 3 cenas devem ter frases de alto impacto.
- DESENVOLVIMENTO: Ritmo ágil, mas com parágrafos completos e informativos.
- CONCLUSÃO: O ÚLTIMO objeto do array DEVE fazer o encerramento claro da história.

REGRAS DE BUSCA VISUAL (PEXELS):
- A chave "busca" DEVE ser em INGLÊS com 2 a 3 palavras LITERAIS (ex: "astronaut walking", "desert red planet").
- LITERAL significa algo que uma camera pode filmar: pessoa, objeto, local fisico, veiculo, construcao, paisagem ou acao fisica.
- PROIBIDO ESTRITAMENTE usar conceitos abstratos, emocao, atmosfera, genero narrativo ou clima psicologico como tag.
- Nunca use termos como "mystery", "cinematic mystery", "dark mood", "fear", "tension", "emotion", "secret" ou "destiny".
- Se a ideia for abstrata, traduza para imagem concreta (ex: em vez de "mystery", use "old locked door" ou "ancient stone map").

REGRAS DE TAMANHO E MONETIZAÇÃO (CRÍTICO):
- 'versao_longa': DEVE conter no mínimo 230 palavras no total, distribuídas em 8 a 10 objetos no array.
- 'versao_curta': DEVE conter no mínimo 160 palavras no total, distribuídas em 4 a 6 objetos no array.
- Se qualquer versão ficar abaixo dessa contagem mínima, o sistema falhará.

SAÍDA OBRIGATÓRIA (APENAS JSON, SEM COMENTÁRIOS):
O exemplo abaixo mostra apenas o formato. A resposta final deve preencher cenas suficientes para bater a contagem mínima.
{
  "versao_longa": [
    {"texto": "O ano é dois mil e cinquenta, e a humanidade finalmente encontrou a resposta para a maior pergunta de todas. Depois de décadas de exploração silenciosa, nossos sinais de rádio foram interceptados por algo além das estrelas. A mensagem não era um convite de paz, mas um aviso terrível para não olharmos para trás.", "busca": "satellite dish night"},
    {"texto": "No centro de controle da agência espacial, as telas começaram a piscar em vermelho enquanto um código desconhecido invadia os sistemas principais. Os cientistas correram para desconectar os servidores, mas já era tarde demais para impedir o vazamento dos dados. Aquele arquivo escondia a localização de uma arma capaz de alterar a órbita do planeta.", "busca": "control room screens"}
  ],
  "versao_curta": [
    {"texto": "Marte será o nosso próximo lar, mas a primeira cidade humana será construída sobre um cemitério escondido. O que os telescópios não mostraram foi a vasta rede de túneis que existe logo abaixo da superfície empoeirada.", "busca": "red planet surface"},
    {"texto": "Nenhum astronauta estava preparado para encontrar os restos petrificados de uma civilização que tentou escapar do mesmo destino que nos aguarda.", "busca": "astronaut walking desert"}
  ]
}
"""


class LLMRoteiristaError(RuntimeError):
    """Base error for clean Ollama failures."""


class LLMRoteiristaDuracaoError(LLMRoteiristaError):
    """Raised when the script is too short for the monetization contract."""


class LLMRoteirista:
    """Generates text-first scene scripts through local Ollama REST API."""

    OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
    DEFAULT_TIMEOUT = 120
    VERSAO_LONGA = "versao_longa"
    VERSAO_CURTA = "versao_curta"
    MIN_CENAS = {
        VERSAO_LONGA: 5,
        VERSAO_CURTA: 2,
    }
    MIN_PALAVRAS = {
        VERSAO_LONGA: MIN_WORDS_LONGA,
        VERSAO_CURTA: MIN_WORDS_CURTA,
    }

    def __init__(
        self,
        model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.model = model or settings.llm_model
        self.timeout = timeout
        self.session = session or requests.Session()
        self.logger = get_logger(__name__)

    def gerar_roteiro(self, tema: str) -> dict[str, list[dict[str, str]]]:
        """Generates both long and short scene scripts for the pipeline."""

        tema = _validar_tema(tema)
        prompt = self._montar_prompt(tema)
        self.logger.info("LLM: gerando roteiro tema='%s' modelo='%s'", tema, self.model)

        payload = self._chamar_ollama(prompt)
        try:
            roteiro = self._converter_payload_roteiro(payload)
        except LLMRoteiristaDuracaoError:
            raise
        except LLMRoteiristaError as exc:
            self.logger.warning("LLM: saida fora do contrato, tentando reparo JSON: %s", exc)
            try:
                payload_reparado = self._chamar_ollama(_montar_prompt_reparo(tema, payload))
                roteiro = self._converter_payload_roteiro(payload_reparado)
            except LLMRoteiristaDuracaoError:
                raise
            except LLMRoteiristaError as reparo_exc:
                raise LLMRoteiristaError(
                    "LLM: reparo JSON falhou; abortando sem fallback generico para "
                    "preservar o contrato de monetizacao."
                ) from reparo_exc

        self.logger.info(
            "LLM: roteiro gerado com longa=%s cenas/%s palavras e curta=%s cenas/%s palavras",
            len(roteiro[self.VERSAO_LONGA]),
            contar_palavras_cenas(roteiro[self.VERSAO_LONGA]),
            len(roteiro[self.VERSAO_CURTA]),
            contar_palavras_cenas(roteiro[self.VERSAO_CURTA]),
        )
        return roteiro

    def _chamar_ollama(self, prompt: str) -> str:
        try:
            response = self.session.post(
                self.OLLAMA_GENERATE_URL,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.Timeout as exc:
            raise LLMRoteiristaError(
                f"Ollama excedeu o timeout de {self.timeout}s para o modelo {self.model}."
            ) from exc
        except requests.ConnectionError as exc:
            raise LLMRoteiristaError(
                "Ollama nao esta respondendo em http://localhost:11434. "
                "Inicie o servidor com 'ollama serve'."
            ) from exc
        except requests.HTTPError as exc:
            detail = compactar_texto(response.text if "response" in locals() else "")
            raise LLMRoteiristaError(f"Ollama retornou HTTP {response.status_code}: {detail}") from exc
        except requests.RequestException as exc:
            raise LLMRoteiristaError(f"Falha na chamada ao Ollama: {exc}") from exc

        return _parse_http_response(response)

    def _converter_payload_roteiro(self, payload: str) -> dict[str, list[dict[str, str]]]:
        roteiro = _parse_roteiro(payload)
        _validar_versoes(roteiro)
        self.logger.info(
            "LLM: roteiro bruto validado com longa=%s cenas/%s palavras e curta=%s cenas/%s palavras",
            len(roteiro[self.VERSAO_LONGA]),
            contar_palavras_cenas(roteiro[self.VERSAO_LONGA]),
            len(roteiro[self.VERSAO_CURTA]),
            contar_palavras_cenas(roteiro[self.VERSAO_CURTA]),
        )
        return roteiro

    def _montar_prompt(self, tema: str) -> str:
        return f"""
{PROMPT_SISTEMA}

Tema do video:
{tema}

Repita mentalmente antes de responder:
- A raiz deve ser um objeto JSON com as chaves "versao_longa" e "versao_curta".
- "versao_longa" deve ter no mínimo duzentas e trinta palavras no total.
- "versao_curta" deve ter no mínimo cento e sessenta palavras no total.
- Os textos devem ser parágrafos narrativos extensos, não frases mínimas.
- As primeiras três cenas precisam ser rápidas e fortes.
- Todo "texto" deve estar em português brasileiro com acentuação correta.
- Toda "busca" deve estar em inglês e deve mostrar objetos, locais fisicos ou acoes fisicas filmaveis.
- Nenhuma "busca" pode conter conceitos abstratos como "mystery", "dark mood", "fear", "tension", "secret" ou "destiny".
- As duas versões precisam ter gancho, desenvolvimento e conclusão definitiva.
""".strip()


def _montar_prompt_reparo(tema: str, payload: str) -> str:
    return f"""
Converta a resposta abaixo para o contrato JSON do SynthReel.

Tema original: {tema}

Resposta quebrada:
{payload}

Regras obrigatorias:
- Retorne somente JSON valido.
- A raiz deve ser um objeto com as chaves "versao_longa" e "versao_curta".
- "versao_longa" deve conter de oito a dez objetos e somar no minimo duzentas e trinta palavras. Cada texto deve ter três a cinco frases longas.
- "versao_curta" deve conter de quatro a seis objetos e somar no minimo cento e sessenta palavras. Cada texto deve ter três a cinco frases longas.
- Cada texto deve ser um paragrafo narrativo extenso, com contexto suficiente para a voz rapida.
- Cada objeto deve ter exatamente as chaves "texto" e "busca".
- "texto" deve estar em portugues brasileiro com acentuacao correta, preservando acentos como á, é, í, ó, ú, ã, õ e ç.
- "busca" deve estar em ingles, com duas a tres palavras, usando apenas objetos, locais fisicos ou acoes fisicas filmaveis.
- "busca" nao pode conter conceitos abstratos como "mystery", "cinematic mystery", "dark mood", "fear", "tension", "emotion", "secret" ou "destiny".
- Escreva todos os numeros do texto por extenso.
- Dentro do valor de "texto" use apenas letras, espacos, virgulas e pontos finais.
- Remova aspas, parenteses, asteriscos, emojis, digitos e acronimos nao pronunciaveis.
- Nao use markdown, comentarios, numeracao ou explicacoes.
""".strip()

def _parse_http_response(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError as exc:
        raise LLMRoteiristaError("Ollama retornou JSON HTTP invalido.") from exc

    if not isinstance(payload, dict):
        raise LLMRoteiristaError("Ollama retornou payload HTTP inesperado.")

    raw_response = payload.get("response")
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise LLMRoteiristaError("Ollama nao retornou o campo 'response' com conteudo.")

    return raw_response.strip()


def _parse_roteiro(payload: str) -> dict[str, list[dict[str, Any]]]:
    try:
        roteiro = json.loads(payload)
    except JSONDecodeError as exc:
        raise LLMRoteiristaError(
            "Ollama retornou texto que nao e JSON valido no contrato de roteiro: "
            f"{compactar_texto(payload)}"
        ) from exc

    versoes = _extrair_versoes_roteiro(roteiro)
    if versoes is None:
        raise LLMRoteiristaError(
            "Ollama deve retornar um objeto JSON com as chaves 'versao_longa' e 'versao_curta'."
        )

    return versoes


def _extrair_versoes_roteiro(payload: Any) -> dict[str, list[dict[str, Any]]] | None:
    if isinstance(payload, dict):
        longa = payload.get("versao_longa")
        curta = payload.get("versao_curta")
        if longa is not None or curta is not None:
            if not isinstance(longa, list) or not isinstance(curta, list):
                raise LLMRoteiristaError(
                    "Ollama retornou versoes invalidas; 'versao_longa' e "
                    "'versao_curta' devem ser arrays."
                )
            return {"versao_longa": longa, "versao_curta": curta}

        for nested in payload.values():
            versoes = _extrair_versoes_roteiro(nested)
            if versoes is not None:
                return versoes

    if isinstance(payload, list):
        for item in payload:
            versoes = _extrair_versoes_roteiro(item)
            if versoes is not None:
                return versoes

    return None


def _validar_versoes(roteiro: dict[str, list[dict[str, Any]]]) -> None:
    for nome in (LLMRoteirista.VERSAO_LONGA, LLMRoteirista.VERSAO_CURTA):
        cenas = roteiro.get(nome)
        if not isinstance(cenas, list):
            raise LLMRoteiristaError(f"{nome} deve ser um array de cenas.")

        _validar_roteiro(
            cenas,
            nome=nome,
            min_cenas=LLMRoteirista.MIN_CENAS[nome],
        )
        _validar_minimo_palavras(
            cenas,
            nome=nome,
            min_palavras=LLMRoteirista.MIN_PALAVRAS[nome],
        )


def _validar_minimo_palavras(
    cenas: list[dict[str, Any]],
    *,
    nome: str,
    min_palavras: int,
) -> None:
    total_palavras = contar_palavras_cenas(cenas)
    if total_palavras < min_palavras:
        raise LLMRoteiristaDuracaoError(
            f"{nome} retornou {total_palavras} palavras; minimo exigido e "
            f"{min_palavras} para garantir a duracao minima com TTS rapido."
        )


def _validar_roteiro(
    roteiro: list[dict[str, Any]],
    *,
    nome: str = "roteiro",
    min_cenas: int = 2,
    max_cenas: int | None = None,
    min_frases_por_cena: int = 1,
) -> None:
    if not roteiro:
        raise LLMRoteiristaError(f"{nome} veio vazio.")

    if len(roteiro) < min_cenas:
        raise LLMRoteiristaError(
            f"{nome} retornou {len(roteiro)} cenas; minimo exigido e {min_cenas}."
        )

    if max_cenas is not None and len(roteiro) > max_cenas:
        raise LLMRoteiristaError(
            f"{nome} retornou {len(roteiro)} cenas; maximo permitido e {max_cenas}."
        )

    for index, cena in enumerate(roteiro, start=1):
        if not isinstance(cena, dict):
            raise LLMRoteiristaError(f"{nome} cena {index} nao e um objeto JSON.")

        texto = cena.get("texto")
        busca = cena.get("busca")
        if not isinstance(texto, str) or not texto.strip():
            raise LLMRoteiristaError(f"{nome} cena {index} nao possui 'texto' valido.")
        if not isinstance(busca, str) or not busca.strip():
            raise LLMRoteiristaError(f"{nome} cena {index} nao possui 'busca' valida.")

        texto = sanitizar_texto_tts(texto)
        if not texto:
            raise LLMRoteiristaError(
                f"{nome} cena {index} ficou sem texto narravel apos saneamento."
            )

        if parece_ingles(texto):
            raise LLMRoteiristaError(
                f"{nome} cena {index} parece estar em ingles; texto deve ser PT-BR."
            )

        total_frases = contar_frases(texto)
        if total_frases < min_frases_por_cena:
            raise LLMRoteiristaError(
                f"{nome} cena {index} retornou {total_frases} frase; "
                f"minimo exigido e {min_frases_por_cena}."
            )

        busca = sanitizar_busca_pexels(busca)
        if not busca:
            raise LLMRoteiristaError(
                f"{nome} cena {index} ficou sem busca valida apos saneamento."
            )

        cena["texto"] = texto
        cena["busca"] = busca


def _validar_tema(tema: str) -> str:
    tema = tema.strip()
    if not tema:
        raise ValueError("tema nao pode ser vazio.")
    return tema


def _teste_isolado() -> None:
    roteirista = LLMRoteirista()
    roteiro = roteirista.gerar_roteiro("O imperio de Roma")
    print(json.dumps(roteiro, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _teste_isolado()
