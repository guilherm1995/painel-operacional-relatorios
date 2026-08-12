"""Carrega os parametros de negocio (de/para da aba BASE do painel)."""

from __future__ import annotations

import json
import unicodedata
from functools import lru_cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_PARAMETROS = RAIZ / "config" / "parametros.json"


def normalizar(texto) -> str:
    """Maiusculas, sem acento e sem espacos duplicados - para casar chaves de/para.

    A extracao do campo vem com acentuacao inconsistente ("CONCLUIDO" x "CONCLUIDO"),
    entao toda comparacao de chave passa por aqui.
    """
    if texto is None:
        return ""
    txt = str(texto).strip().upper()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    return " ".join(txt.split())


class Parametros:
    def __init__(self, dados: dict):
        self._bruto = dados
        self.pontuacao = {normalizar(k): v for k, v in dados["pontuacao"].items()}
        self.status = {normalizar(k): v for k, v in dados["status"].items()}
        self.clusters = {normalizar(k): v for k, v in dados["clusters"].items()}
        self.motivos = {normalizar(k): v for k, v in dados["motivos"].items()}
        self.meta_carga = {normalizar(k): v for k, v in dados["meta_carga"].items()}
        self.meta_financeira = {normalizar(k): v for k, v in dados["meta_financeira"].items()}

    # -- de/para ---------------------------------------------------------
    def status_real(self, status_atividade) -> str:
        return (self.status.get(normalizar(status_atividade), {}) or {}).get("real") or ""

    def status_campo(self, status_atividade, campo: str):
        return (self.status.get(normalizar(status_atividade), {}) or {}).get(campo)

    def pontos(self, tipo_atividade) -> float:
        reg = self.pontuacao.get(normalizar(tipo_atividade))
        return float(reg["pts"]) if reg and reg.get("pts") is not None else 0.0

    def valor(self, tipo_atividade) -> float:
        reg = self.pontuacao.get(normalizar(tipo_atividade))
        return float(reg["valor"]) if reg and reg.get("valor") is not None else 0.0

    def cluster_da_cidade(self, cidade) -> str:
        return self.clusters.get(normalizar(cidade), "")

    def sub_motivo(self, motivo) -> str:
        return self.motivos.get(normalizar(motivo), "")

    def clusters_conhecidos(self) -> list[str]:
        """Clusters distintos da tabela CLUSTER (cidade -> area de trabalho)."""
        ordem = ["AT", "CGT", "VPA", "RIO"]
        achados = {v for v in self._bruto["clusters"].values()}
        presentes = [c for c in ordem if c in achados]
        extras = sorted(achados - set(ordem))
        return presentes + extras


@lru_cache(maxsize=1)
def carregar(caminho: str | None = None) -> Parametros:
    alvo = Path(caminho) if caminho else ARQUIVO_PARAMETROS
    if not alvo.exists():
        raise FileNotFoundError(
            f"{alvo} nao encontrado. Rode: python extrair_parametros.py \"caminho/do/PAINEL.xlsx\""
        )
    return Parametros(json.loads(alvo.read_text(encoding="utf-8")))
