"""Extrai as tabelas de parametros da aba BASE do painel Excel para config/parametros.json.

Rode uma vez (ou sempre que a aba BASE mudar):

    python extrair_parametros.py "C:/caminho/para/Desktop/NOVO PAINEL (1).xlsx"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl

RAIZ = Path(__file__).parent
DESTINO = RAIZ / "config" / "parametros.json"


def _texto(v):
    if v is None:
        return None
    return str(v).strip()


def _linhas(ws, primeira, ultima, colunas):
    """Le um bloco da planilha como lista de dicts, ignorando linhas vazias."""
    saida = []
    for r in range(primeira, ultima + 1):
        registro = {}
        for nome, col in colunas.items():
            registro[nome] = ws[f"{col}{r}"].value
        if all(v is None for v in registro.values()):
            continue
        saida.append(registro)
    return saida


def extrair(caminho_xlsx: Path) -> dict:
    wb = openpyxl.load_workbook(caminho_xlsx, data_only=True)
    base = wb["BASE"]

    # PONTUACAO (A1:E10) - valor e pontos por tipo de atividade
    pontuacao = {}
    for reg in _linhas(base, 2, 10, {"tipo": "A", "valor": "B", "pts": "C", "moeda": "D", "oper": "E"}):
        tipo = _texto(reg["tipo"])
        if not tipo:
            continue
        pontuacao[tipo.upper()] = {
            "valor": reg["valor"],
            "pts": reg["pts"],
            "oper": _texto(reg["oper"]),
        }

    # STATUS (H1:M8) - de/para do status da atividade
    status = {}
    for reg in _linhas(base, 2, 8, {"status": "H", "real": "I", "reparo": "J",
                                    "efetivo": "K", "info": "L", "pende": "M"}):
        chave = _texto(reg["status"])
        if not chave:
            continue
        status[chave.upper()] = {
            "real": _texto(reg["real"]),
            "reparo": _texto(reg["reparo"]),
            "efetivo": _texto(reg["efetivo"]),
            "info": int(reg["info"] or 0),
            "pende": int(reg["pende"] or 0),
        }

    # CLUSTER (O1:P34) - cidade -> area de trabalho
    clusters = {}
    for reg in _linhas(base, 2, 34, {"cidade": "O", "area": "P"}):
        cidade = _texto(reg["cidade"])
        area = _texto(reg["area"])
        if cidade and area:
            clusters[cidade.upper()] = area.upper()

    # MOTIVOS (S1:T23) - motivo de encerramento -> sub motivo
    motivos = {}
    for reg in _linhas(base, 2, 23, {"motivo": "S", "sub": "T"}):
        motivo = _texto(reg["motivo"])
        sub = _texto(reg["sub"])
        if motivo and sub:
            motivos[motivo.upper()] = sub

    # Tabela30 (AE4:AH32) - meta de carga por cluster_tipo
    meta_carga = {}
    for reg in _linhas(base, 5, 32, {"chave": "AE", "semana": "AF", "sabado": "AG", "domingo": "AH"}):
        chave = _texto(reg["chave"])
        if not chave:
            continue
        meta_carga[chave.upper()] = {
            "semana": reg["semana"],
            "sabado": reg["sabado"],
            "domingo": reg["domingo"],
        }

    # Tabela31 (AE35:AH40) - meta financeira por cluster
    meta_financeira = {}
    for reg in _linhas(base, 36, 40, {"cluster": "AE", "semana": "AF", "sabado": "AG", "domingo": "AH"}):
        cluster = _texto(reg["cluster"])
        if not cluster:
            continue
        meta_financeira[cluster.upper()] = {
            "semana": reg["semana"],
            "sabado": reg["sabado"],
            "domingo": reg["domingo"],
        }

    return {
        "_origem": caminho_xlsx.name,
        "pontuacao": pontuacao,
        "status": status,
        "clusters": clusters,
        "motivos": motivos,
        "meta_carga": meta_carga,
        "meta_financeira": meta_financeira,
    }


def main() -> None:
    if len(sys.argv) > 1:
        caminho = Path(sys.argv[1])
    else:
        caminho = Path(r"C:\caminho\para\Desktop\NOVO PAINEL (1).xlsx")
    if not caminho.exists():
        raise SystemExit(f"Arquivo nao encontrado: {caminho}")

    dados = extrair(caminho)
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Gravado: {DESTINO}")
    for chave in ("pontuacao", "status", "clusters", "motivos", "meta_carga", "meta_financeira"):
        print(f"  {chave}: {len(dados[chave])} registros")


if __name__ == "__main__":
    main()
