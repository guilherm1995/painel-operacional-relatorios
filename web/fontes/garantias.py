"""Lista de garantias - mesma regra do app desktop (painel_desktop_operacao).

Garantia = reparo em aberto num contrato que teve servico concluido ha pouco:
ate 30 dias se o anterior foi reparo, ate 15 se foi ativacao ou mudanca.

Fonte: chamados_abertos_field_service.xlsx (chamados abertos) cruzado com
base OFS ok.xlsx (historico concluido), ambos na pasta do Operacional Database.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path

import pandas as pd

from ..config import CONFIG
from . import planilhas

LITORAL_SP = ["CGT", "BASE", "SST", "SSTBO", "IBL"]
RJ = ["RSD", "MPE", "VAS", "VRD", "PNDO", "VLC", "IZA", "TRS", "BMA",
      "PORE", "COLG", "BPI", "PFS", "PDS", "PNHE"]
TODAS_CIDADES = LITORAL_SP + RJ

CIDADES = {
    "CGT": "Caraguatatuba", "BASE": "Caraguatatuba 1", "SST": "São Sebastião",
    "SSTBO": "Boiçucanga", "IBL": "Ilhabela", "RSD": "Resende",
    "MPE": "Miguel Pereira", "VAS": "Vassouras", "VRD": "Volta Redonda",
    "PNDO": "Penedo", "VLC": "Valença", "IZA": "Itatiaia", "TRS": "Três Rios",
    "BMA": "Barra Mansa", "PORE": "Porto Real",
    "COLG": "Comendador Levy Gasparian", "BPI": "Barra do Piraí",
    "PFS": "Paty do Alferes", "PDS": "Paraíba do Sul", "PNHE": "Pinheiral",
}

DIAS_GARANTIA_REPARO = 30
DIAS_GARANTIA_ATIVACAO = 15

_cache_autenticador: dict = {"quando": 0.0, "status": {}, "erro": ""}
_trava_autenticador = threading.Lock()


# --------------------------------------------------------------------------
# leitura das planilhas
# --------------------------------------------------------------------------
def _coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    normal = {str(c).strip().lower(): c for c in df.columns}
    for nome in candidatos:
        if nome.strip().lower() in normal:
            return normal[nome.strip().lower()]
    return None


def _contrato(serie: pd.Series) -> pd.Series:
    """Contrato como texto limpo - vem ora como numero, ora com .0 no fim."""
    return (serie.astype(str).str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .str.replace(r"\s+", "", regex=True))


def _data(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, errors="coerce", dayfirst=True)


# O registro das planilhas, a leitura com cache e o recebimento de envios
# ficam em planilhas.py, compartilhados com a confirmacao de agenda.
ARQUIVOS = {n: i for n, i in planilhas.ARQUIVOS.items() if "garantias" in i["paginas"]}


def _carregar(nome: str) -> pd.DataFrame:
    return planilhas.carregar(nome)


def status_arquivos() -> list[dict]:
    """Planilhas que a lista de garantias consome."""
    return planilhas.status_arquivos("garantias")


def guardar_planilha(nome: str, conteudo: bytes) -> dict:
    return planilhas.guardar(nome, conteudo)



# --------------------------------------------------------------------------
# Autenticador (status online do contrato)
# --------------------------------------------------------------------------
def consultar_autenticador(contratos: list[str]) -> tuple[dict, str]:
    """Status ONLINE/OFFLINE por contrato. Reaproveita a consulta por alguns minutos.

    A consulta e lenta (~35s) e depende da VPN, entao varias pessoas abrindo a
    pagina ao mesmo tempo compartilham o mesmo resultado - MAS so quando a lista
    de contratos pedida e a mesma. A lista de garantias muda ao longo do dia
    (um contrato novo entra, outro sai), e sem essa checagem o cache devolvia a
    resposta antiga inteira: o contrato novo simplesmente nao vinha nela e virava
    "DESCONHECIDO" na tela, mesmo com a consulta tendo funcionado.
    """
    if not CONFIG.autenticador_ativo or not contratos:
        return {}, ""

    alvo = set(contratos)
    with _trava_autenticador:
        idade = time.time() - _cache_autenticador["quando"]
        cobre_pedido = alvo.issubset(_cache_autenticador.get("contratos") or set())
        if idade < CONFIG.autenticador_cache_segundos and _cache_autenticador["status"] and cobre_pedido:
            return _cache_autenticador["status"], _cache_autenticador["erro"]

        try:
            import io
            import requests
            import urllib3
            urllib3.disable_warnings()

            sessao = requests.Session()
            base = "https://provedor.example"
            sessao.post(f"{base}/status.php?action=save",
                        data={"contratos": "\n".join(contratos)},
                        verify=False, timeout=(5, 35))
            sessao.get(f"{base}/processa.php?bg=1", verify=False, timeout=(5, 35))
            resposta = sessao.get(f"{base}/ler_csv.php", verify=False, timeout=(5, 35))

            if "<table>" not in resposta.text:
                raise ValueError("resposta sem tabela")

            tabelas = pd.read_html(io.StringIO(resposta.text))
            if not tabelas:
                raise ValueError("nenhuma tabela na resposta")

            df = tabelas[0]
            df.columns = [str(c).lower().strip() for c in df.columns]
            col_contrato = next((c for c in df.columns if "contrato" in c), None)
            col_fim = next((c for c in df.columns if "stoptime" in c or c == "fim"), None)
            if not col_contrato:
                raise ValueError("coluna de contrato ausente")

            df[col_contrato] = _contrato(df[col_contrato])
            status = {}
            for contrato, grupo in df.groupby(col_contrato):
                online = False
                if col_fim is not None:
                    vazio = grupo[col_fim].isna() | (grupo[col_fim].astype(str).str.strip() == "")
                    online = bool(vazio.any())
                status[str(contrato)] = "ONLINE" if online else "OFFLINE"

            _cache_autenticador.update({"quando": time.time(), "status": status, "erro": "",
                                 "contratos": alvo})
            return status, ""

        except ModuleNotFoundError as erro:
            # nao e problema de rede: falta biblioteca. Dizer "verifique a VPN"
            # aqui manda o operador procurar no lugar errado.
            mensagem = (f"Falta a biblioteca '{erro.name}' nesta máquina, então a "
                        f"consulta ao Autenticador não roda. Rode o instalador de novo "
                        f"(python instalar.py) para instalá-la.")
            _cache_autenticador.update({"quando": time.time(), "status": {}, "erro": mensagem,
                                 "contratos": alvo})
            return {}, mensagem

        except Exception as erro:
            nome = type(erro).__name__
            if any(m in nome for m in ("Timeout", "ConnectionError", "SSLError", "ProxyError")):
                mensagem = (f"Não consegui falar com o Autenticador ({nome}). "
                            f"Confirme se a VPN está conectada.")
            else:
                mensagem = f"A consulta ao Autenticador falhou ({nome}): {str(erro)[:120]}"
            _cache_autenticador.update({"quando": time.time(), "status": {}, "erro": mensagem,
                                 "contratos": alvo})
            return {}, mensagem


# --------------------------------------------------------------------------
# calculo das garantias
# --------------------------------------------------------------------------
def _rotulo_servico(tipo: str) -> str:
    texto = str(tipo).lower()
    if "reparo" in texto:
        return "IRR"
    if "mudança" in texto or "mudanca" in texto or "mde" in texto:
        return "IFI de MDE"
    if "ativação" in texto or "ativacao" in texto:
        return "IFI"
    return str(tipo)[:16].upper()


def calcular(com_autenticador: bool = True) -> dict:
    chamados = _carregar("chamados_abertos_field_service.xlsx")
    historico = _carregar("base OFS ok.xlsx")

    onde = " · ".join(str(p) for p in CONFIG.pastas_de_dados())
    if chamados.empty:
        return {"erro": "Não encontrei (ou está vazio) o arquivo "
                        f"'chamados_abertos_field_service.xlsx'. Procurei em: {onde}",
                "regioes": [], "total": 0}
    if historico.empty:
        return {"erro": "Não encontrei (ou está vazio) o arquivo 'base OFS ok.xlsx'. "
                        f"Procurei em: {onde}", "regioes": [], "total": 0}

    col_contrato_h = _coluna(historico, ["Número do contrato", "Contrato"])
    col_data_h = _coluna(historico, ["Data"])
    col_status_h = _coluna(historico, ["Status da Atividade"])
    col_tipo_h = _coluna(historico, ["Tipo de Atividade.1", "Tipo de Atividade 2", "Tipo de Atividade"])
    col_tecnico_h = _coluna(historico, ["Recurso", "Técnico", "Nome do Técnico", "Nome", "Resource"])

    if not col_contrato_h or not col_tipo_h or not col_data_h:
        return {"erro": "A base OFS não tem as colunas de contrato, data ou tipo de atividade.",
                "regioes": [], "total": 0}

    historico = historico.copy()
    historico[col_contrato_h] = _contrato(historico[col_contrato_h])
    historico[col_data_h] = _data(historico[col_data_h])
    if col_status_h:
        historico = historico[historico[col_status_h].astype(str)
                              .str.contains("conclu", case=False, na=False)]
    if not col_tecnico_h:
        historico["__tecnico"] = "Não Identificado"
        col_tecnico_h = "__tecnico"

    if "CÓDIGO CONTRATO" not in chamados.columns or "FILA" not in chamados.columns:
        return {"erro": "O arquivo de chamados não tem as colunas 'CÓDIGO CONTRATO' e 'FILA'.",
                "regioes": [], "total": 0}

    chamados = chamados.copy()
    chamados["CÓDIGO CONTRATO"] = _contrato(chamados["CÓDIGO CONTRATO"])
    reparos = chamados[chamados["FILA"].astype(str).str.contains("REPARO", case=False, na=False)]
    reparos = reparos[~reparos["FILA"].astype(str)
                      .str.contains("PREVENTIVO", case=False, na=False)]
    if reparos.empty:
        return {"erro": "", "regioes": [], "total": 0, "invalidos": [],
                "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}

    df = reparos.merge(historico, left_on="CÓDIGO CONTRATO",
                       right_on=col_contrato_h, how="inner")
    if df.empty:
        return {"erro": "", "regioes": [], "total": 0, "invalidos": [],
                "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}

    col_ingresso = _coluna(df, ["DATA DE INGRESSO"])
    if not col_ingresso:
        return {"erro": "O arquivo de chamados não tem a coluna 'DATA DE INGRESSO'.",
                "regioes": [], "total": 0}

    df["AGING"] = (_data(df[col_ingresso]).dt.normalize()
                   - df[col_data_h].dt.normalize()).dt.days
    df = df[df["AGING"] >= 0]

    tipo = df[col_tipo_h].astype(str)
    dentro_reparo = tipo.str.contains("REPARO", case=False, na=False) & (df["AGING"] <= DIAS_GARANTIA_REPARO)
    dentro_alta = tipo.str.contains("ATIVAÇÃO|ATIVACAO|MUDANÇA|MUDANCA", case=False, na=False,
                                    regex=True) & (df["AGING"] <= DIAS_GARANTIA_ATIVACAO)
    df = df[dentro_reparo | dentro_alta]

    col_endereco = _coluna(df, ["Endereço", "Endereco", "Logradouro", "Rua", "ENDEREÇO"])

    # status online, se a consulta estiver ligada
    status_autenticador, erro_autenticador = ({}, "")
    if com_autenticador and not df.empty:
        status_autenticador, erro_autenticador = consultar_autenticador(
            sorted(df["CÓDIGO CONTRATO"].dropna().unique().tolist())
        )

    registros = []
    for _, linha in df.iterrows():
        contrato = str(linha["CÓDIGO CONTRATO"]).strip()
        registros.append({
            "contrato": contrato,
            "unidade": str(linha.get("UNIDADE", "")).strip().upper(),
            "aging": int(linha["AGING"]),
            "servico": _rotulo_servico(linha[col_tipo_h]),
            "tecnico": str(linha[col_tecnico_h]).upper()[:40],
            "endereco": (str(linha[col_endereco]).replace("\n", " ").upper()
                         if col_endereco else "ENDEREÇO NÃO ENCONTRADO"),
            "status": status_autenticador.get(contrato, "—" if not CONFIG.autenticador_ativo else "DESCONHECIDO"),
        })

    # tira duplicata: o mesmo reparo pode casar com varios servicos anteriores;
    # fica o mais recente (menor aging)
    registros.sort(key=lambda r: (r["contrato"], r["aging"]))
    unicos, vistos = [], set()
    for reg in registros:
        if reg["contrato"] in vistos:
            continue
        vistos.add(reg["contrato"])
        unicos.append(reg)

    regioes = []
    for nome, siglas in [("Litoral Norte SP", LITORAL_SP), ("Sul RJ", RJ)]:
        cidades = []
        for sigla in siglas:
            itens = [r for r in unicos if r["unidade"] == sigla]
            if itens:
                itens.sort(key=lambda r: r["aging"])
                cidades.append({"sigla": sigla,
                                "nome": CIDADES.get(sigla, sigla).upper(),
                                "itens": itens})
        regioes.append({"nome": nome, "cidades": cidades,
                        "total": sum(len(c["itens"]) for c in cidades)})

    invalidos = [r for r in unicos if r["unidade"] not in TODAS_CIDADES]

    return {
        "erro": "",
        "erro_autenticador": erro_autenticador,
        "regioes": regioes,
        "invalidos": invalidos,
        "total": len(unicos),
        "online": sum(1 for r in unicos if r["status"] == "ONLINE"),
        "offline": sum(1 for r in unicos if r["status"] == "OFFLINE"),
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
