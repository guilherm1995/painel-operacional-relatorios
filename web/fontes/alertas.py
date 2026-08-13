"""Garantias que o bot notificou, para o site avisar quem está com a tela aberta.

O bot já toca um som e manda a mensagem no grupo quando encontra uma garantia,
mas isso acontece na máquina dele -- quem está no site não fica sabendo. Aqui o
site lê o MESMO registro que ele grava (`dados/reparos_avaliados.json`) e
devolve as garantias já notificadas; o navegador compara com o que ele já viu e
avisa só o que é novo.

É o mesmo evento do bot, não uma segunda avaliação: nada aqui decide se um
reparo é garantia, só relata o que ele decidiu.
"""

from __future__ import annotations

import datetime as dt
import json

from ..config import CONFIG

ARQUIVO = "reparos_avaliados.json"

# Quantas garantias devolver. O bot guarda 45 dias de reparos avaliados e a
# tela não precisa de histórico -- ela só quer saber o que apareceu agora. O
# limite também segura o tamanho da lista que o navegador memoriza.
LIMITE = 40

# (mtime, tamanho) -> resposta pronta. O arquivo passa de 900 KB e a tela
# consulta a cada poucos segundos; sem isto seria reabrir e reanalisar tudo a
# cada volta, várias vezes por minuto.
_cache: tuple[tuple[float, int], dict] | None = None


def _texto(valor, padrao: str = "") -> str:
    if valor is None:
        return padrao
    texto = str(valor).strip()
    return texto or padrao


def _quando_abriu(info: dict) -> str:
    """data_abertura ISO -> dd/mm hh:mm, que é como o resto do site mostra."""
    bruto = _texto(info.get("data_abertura"))
    if not bruto:
        return ""
    try:
        return dt.datetime.fromisoformat(bruto).strftime("%d/%m %H:%M")
    except ValueError:
        return ""


def garantias_notificadas() -> dict:
    """As garantias que o bot já notificou, da mais recente para a mais antiga.

    `disponivel` separa "o bot não notificou nenhuma garantia" de "não consigo
    ler o arquivo do bot" -- sem isso a tela ficaria calada nos dois casos e
    ninguém perceberia que o alerta parou de funcionar.
    """
    global _cache

    caminho = CONFIG.dados_bot / ARQUIVO
    try:
        st = caminho.stat()
    except OSError:
        return {"disponivel": False, "quando": "", "itens": [],
                "onde": str(caminho)}

    assinatura = (st.st_mtime, st.st_size)
    if _cache is not None and _cache[0] == assinatura:
        return _cache[1]

    try:
        registros = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"disponivel": False, "quando": "", "itens": [],
                "onde": str(caminho)}

    if not isinstance(registros, dict):
        return {"disponivel": False, "quando": "", "itens": [],
                "onde": str(caminho)}

    itens = []
    for chave, info in registros.items():
        if not isinstance(info, dict) or not info.get("notificado"):
            continue
        itens.append({
            "id": _texto(info.get("os_id"), _texto(chave)),
            "contrato": _texto(info.get("codigo_contrato"), "—"),
            "unidade": _texto(info.get("unidade"), "—").upper(),
            "cliente": _texto(info.get("nome_cliente"), "—"),
            "bairro": _texto(info.get("bairro"), "—"),
            "telefones": _texto(info.get("telefones")),
            "tipo_anterior": _texto(info.get("tipo_anterior")),
            "dias_aging": info.get("dias_aging"),
            "abriu": _quando_abriu(info),
            "_ordem": _texto(info.get("data_abertura")),
        })

    # Sem carimbo de "notificada às", a abertura do chamado é o que melhor
    # aproxima a ordem de chegada -- o bot avalia o reparo logo que ele entra.
    itens.sort(key=lambda i: i["_ordem"], reverse=True)
    itens = [{c: v for c, v in item.items() if c != "_ordem"} for item in itens[:LIMITE]]

    resposta = {
        "disponivel": True,
        "quando": dt.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M"),
        "itens": itens,
        "onde": str(caminho),
    }
    _cache = (assinatura, resposta)
    return resposta
