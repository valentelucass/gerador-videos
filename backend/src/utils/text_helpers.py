"""Shared text helpers for SynthReel."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def compactar_texto(texto: str | None) -> str:
    if not texto:
        return "sem detalhes"
    return " ".join(texto.split())[:500]


def texto_tts_seguro(texto: str) -> bool:
    if re.search(r"\d", texto):
        return False
    return re.fullmatch(r"[A-Za-zÀ-ÿ .,]+", texto) is not None


def parece_ingles(texto: str) -> bool:
    palavras = [normalizar_ascii(palavra) for palavra in re.findall(r"[A-Za-zÀ-ÿ]+", texto)]
    if not palavras:
        return False

    marcadores_ingles = {
        "about",
        "after",
        "and",
        "because",
        "before",
        "city",
        "dark",
        "during",
        "earth",
        "first",
        "from",
        "human",
        "into",
        "landed",
        "mars",
        "moon",
        "that",
        "the",
        "this",
        "through",
        "was",
        "were",
        "when",
        "while",
        "with",
        "world",
    }
    marcadores_portugues = {
        "ainda",
        "ano",
        "cidade",
        "como",
        "com",
        "da",
        "de",
        "do",
        "dos",
        "era",
        "essa",
        "esse",
        "foi",
        "historia",
        "homem",
        "lua",
        "mais",
        "marte",
        "na",
        "no",
        "para",
        "por",
        "quando",
        "que",
        "sem",
        "sobre",
        "terra",
        "uma",
    }
    ingles = sum(1 for palavra in palavras if palavra in marcadores_ingles)
    portugues = sum(1 for palavra in palavras if palavra in marcadores_portugues)
    return ingles >= 3 and ingles > portugues


def normalizar_ascii(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto)
    return "".join(char for char in normalizado if not unicodedata.combining(char)).lower()


def contar_frases(texto: str) -> int:
    return len([parte for parte in re.split(r"[.]+", texto) if parte.strip()])


def contar_palavras_cenas(cenas: list[dict[str, Any]]) -> int:
    total = 0
    for cena in cenas:
        texto = str(cena.get("texto", ""))
        total += len(re.findall(r"[A-Za-zÀ-ÿ]+", texto))
    return total


def sanitizar_busca_pexels(busca: str) -> str:
    palavras = re.findall(r"[A-Za-z]+", busca.lower())
    termos_abstratos = {
        "cinematic",
        "concept",
        "darkness",
        "dramatic",
        "emotion",
        "epic",
        "fear",
        "feeling",
        "idea",
        "mood",
        "mystery",
        "symbol",
        "symbolic",
        "tension",
    }
    palavras_limpas = [palavra for palavra in palavras if palavra not in termos_abstratos]
    palavras_limpas = palavras_limpas[:3]
    if not palavras_limpas:
        return ""
    return " ".join(palavras_limpas)


def sanitizar_texto_tts(texto: str) -> str:
    texto = re.sub(r"\d+", lambda match: numero_por_extenso(int(match.group(0))), texto)
    texto = re.sub(r"\b[A-Za-zÀ-ÿ]\s*\.\s*(?:[A-Za-zÀ-ÿ]\s*\.)+", " ", texto)
    texto = re.sub(r"[^A-Za-zÀ-ÿ .,]", " ", texto)
    texto = " ".join(texto.split())
    texto = texto.replace(" ,", ",").replace(" .", ".")
    texto = texto.strip(" .,")
    if texto and texto[-1] not in ".,":
        texto += "."
    return texto


def numero_por_extenso(numero: int) -> str:
    unidades = {
        0: "zero",
        1: "um",
        2: "dois",
        3: "três",
        4: "quatro",
        5: "cinco",
        6: "seis",
        7: "sete",
        8: "oito",
        9: "nove",
        10: "dez",
        11: "onze",
        12: "doze",
        13: "treze",
        14: "quatorze",
        15: "quinze",
        16: "dezesseis",
        17: "dezessete",
        18: "dezoito",
        19: "dezenove",
    }
    dezenas = {
        20: "vinte",
        30: "trinta",
        40: "quarenta",
        50: "cinquenta",
        60: "sessenta",
        70: "setenta",
        80: "oitenta",
        90: "noventa",
    }
    centenas = {
        100: "cem",
        200: "duzentos",
        300: "trezentos",
        400: "quatrocentos",
        500: "quinhentos",
        600: "seiscentos",
        700: "setecentos",
        800: "oitocentos",
        900: "novecentos",
    }

    if numero < 20:
        return unidades[numero]
    if numero < 100:
        dezena = (numero // 10) * 10
        resto = numero % 10
        return dezenas[dezena] if resto == 0 else f"{dezenas[dezena]} e {unidades[resto]}"
    if numero < 1000:
        centena = (numero // 100) * 100
        resto = numero % 100
        prefixo = "cento" if centena == 100 and resto else centenas[centena]
        return prefixo if resto == 0 else f"{prefixo} e {numero_por_extenso(resto)}"
    if numero < 10000:
        milhar = numero // 1000
        resto = numero % 1000
        prefixo = "mil" if milhar == 1 else f"{numero_por_extenso(milhar)} mil"
        return prefixo if resto == 0 else f"{prefixo} e {numero_por_extenso(resto)}"

    return "muitos"
