"""Automacao dos relatorios do painel OPERACIONAL."""

from .analitico import carregar_extracao, preparar
from .relatorios import (capa_envio_grupo, carga_servicos, carregar_config,
                         gestao_prazos, tempo_execucao)

__all__ = [
    "carregar_extracao",
    "preparar",
    "capa_envio_grupo",
    "gestao_prazos",
    "tempo_execucao",
    "carga_servicos",
    "carregar_config",
]
