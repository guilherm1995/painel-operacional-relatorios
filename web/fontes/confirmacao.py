"""Confirmação de agenda - mesma regra do app desktop (painel_desktop_operacao).

Cruza a agenda do dia (OFS GERAL.csv) com as respostas do formulário de
confirmação (planilha do Google, ou um arquivo local) e responde três coisas:

  * CONFIRMADO   - quanto da agenda foi confirmado com o cliente, por região
  * EFETIVIDADE  - quanto da agenda foi produtivo, e quem são os ofensores
  * OPERADOR     - efetividade de cada operador, entre as O.S. que ele confirmou
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from ..config import CONFIG
from . import planilhas
from ..parametros_confirmacao import MAPEAMENTO_MOTIVOS, MOTIVO_PRODUTIVO

ARQUIVO_OFS = "OFS GERAL.csv"
# nomes aceitos para o forms quando ele vem de arquivo, e nao da planilha online
ARQUIVOS_FORMS = [
    "forms confirmação.xlsx",
    "forms confirmação.csv",
    "forms confirmação.xlsx - Respostas ao formulário 1.csv",
]

REGIOES = {
    "LITORAL NORTE SP": [
        "CARAGUATATUBA", "CARAGUATATUBA1", "SÃO SEBASTIÃO", "SAO SEBASTIAO",
        "BOIÇUCANGA", "ILHABELA", "CGT", "BASE", "SST", "SSTBO", "IBL",
    ],
    "SUL RJ": [
        "RESENDE", "MIGUEL PEREIRA", "VASSOURAS", "VOLTA REDONDA", "PENEDO",
        "VALENÇA", "VALENCA", "ITATIAIA", "TRÊS RIOS", "TRES RIOS",
        "BARRA MANSA", "PORTO REAL", "COMENDADOR LEVY GASPARIAN",
        "BARRA DO PIRAÍ", "BARRA DO PIRAI", "PATY DO ALFERES",
        "PARAÍBA DO SUL", "PARAIBA DO SUL", "PINHEIRAL",
        "RSD", "MPE", "VAS", "VRD", "PNDO", "VLC", "IZA", "TRS", "BMA",
        "PORE", "COLG", "BPI", "PFS", "PDS", "PNHE", "RIO", "RJ", "SUL RJ",
    ],
}


# --------------------------------------------------------------------------
# leitura
# --------------------------------------------------------------------------
def _coluna(df: pd.DataFrame, candidatos: list[str]) -> str | None:
    normal = {str(c).strip().lower(): c for c in df.columns}
    for nome in candidatos:
        if nome.strip().lower() in normal:
            return normal[nome.strip().lower()]
    return None


def _csv_robusto(caminho: Path) -> pd.DataFrame:
    for sep in (",", ";"):
        for enc in ("utf-8", "utf-8-sig", "latin1"):
            try:
                df = pd.read_csv(caminho, sep=sep, encoding=enc, on_bad_lines="skip")
                if len(df.columns) > 3:
                    df.columns = df.columns.str.strip()
                    return df
            except Exception:
                continue
    return pd.DataFrame()


def carregar_ofs() -> pd.DataFrame:
    return planilhas.carregar(ARQUIVO_OFS)


def carregar_forms() -> tuple[pd.DataFrame, str]:
    """Respostas do formulário. Planilha do Google primeiro; arquivo como reserva."""
    origem = ""
    sheet_id = CONFIG.forms_sheet_id
    credenciais = CONFIG.localizar_dado("google_credentials.json")

    if sheet_id and credenciais:
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials

            escopo = ["https://spreadsheets.google.com/feeds",
                      "https://www.googleapis.com/auth/drive"]
            creds = ServiceAccountCredentials.from_json_keyfile_name(str(credenciais), escopo)
            planilha = gspread.authorize(creds).open_by_key(sheet_id)
            aba = planilha.worksheet(CONFIG.forms_aba)
            df = pd.DataFrame(aba.get_all_records())
            if not df.empty:
                df.columns = df.columns.str.strip()
                return df, "planilha do Google (ao vivo)"
        except ModuleNotFoundError as erro:
            origem = (f"planilha do Google indisponível: falta a biblioteca "
                     f"'{erro.name}' (rode o instalador de novo)")
        except Exception as erro:
            origem = f"planilha do Google indisponível ({type(erro).__name__})"

    for nome in ARQUIVOS_FORMS:
        caminho = CONFIG.localizar_dado(nome)
        if caminho is None:
            continue
        df = (_csv_robusto(caminho) if caminho.suffix.lower() == ".csv"
              else pd.read_excel(caminho))
        if not df.empty:
            df.columns = df.columns.str.strip()
            quando = dt.datetime.fromtimestamp(caminho.stat().st_mtime)
            reserva = f"arquivo {caminho.name} de {quando:%d/%m %H:%M}"
            return df, (f"{origem}; usando {reserva}" if origem else reserva)

    return pd.DataFrame(), origem or "nenhuma fonte de forms encontrada"


# --------------------------------------------------------------------------
# apoio
# --------------------------------------------------------------------------
def _regiao(valor) -> str:
    texto = str(valor).upper().strip()
    for nome, termos in REGIOES.items():
        if any(t in texto for t in termos):
            return nome
    return "OUTROS"


def _categoria_motivo(motivo) -> str:
    if motivo is None or (isinstance(motivo, float) and pd.isna(motivo)):
        return "OUTROS"
    alvo = str(motivo).strip().upper()
    for chave, categoria in MAPEAMENTO_MOTIVOS.items():
        if chave.upper() == alvo:
            return categoria
    return "OUTROS"


def _os_limpa(serie: pd.Series) -> pd.Series:
    return serie.astype(str).str.split(".").str[0].str.strip()


def _tipo_servico(valor) -> str:
    """Ativação / Mudança de Endereço / Outros Serviços - mesmo criterio do app desktop.

    "ENDEREÇO" (nao so "MUDANÇA") porque Mudança de Cômodo tambem comeca com
    "Mudança" e cairia aqui por engano - ela pertence a "Outros Servicos".
    """
    texto = str(valor).upper()
    if "ATIVAÇÃO" in texto or "ATIVACAO" in texto:
        return "Ativação"
    if "ENDEREÇO" in texto or "ENDERECO" in texto or "MDE" in texto:
        return "Mudança de Endereço"
    return "Outros Serviços"


# Fora do total de confirmacao - decisao de negocio, nao entram na agenda,
# na contagem por regiao nem em Efetividade desta tela.
def _fora_da_confirmacao(valor) -> bool:
    texto = str(valor).upper()
    return ("UPGRADE" in texto or "DOWNGRADE" in texto
            or "CÔMODO" in texto or "COMODO" in texto)


def _pct(parte: int, total: int) -> float:
    return (parte / total) if total else 0.0


# --------------------------------------------------------------------------
# cálculo
# --------------------------------------------------------------------------
def calcular(dia: str = "d0") -> dict:
    delta = 1 if dia == "d1" else 0
    data_alvo = pd.Timestamp.now().normalize() + pd.Timedelta(days=delta)
    rotulo = "Amanhã (D+1)" if delta else "Hoje (D0)"
    vazio = {"dia": dia, "rotulo_dia": rotulo,
             "data_alvo": data_alvo.strftime("%d/%m/%Y"), "total": 0}

    ofs = carregar_ofs()
    if ofs.empty:
        onde = " · ".join(str(p) for p in CONFIG.pastas_de_dados())
        return {**vazio, "erro": f"Não encontrei o arquivo '{ARQUIVO_OFS}'. Procurei em: {onde}"}

    forms, origem_forms = carregar_forms()
    if forms.empty:
        return {**vazio, "erro": f"Sem respostas do formulário de confirmação ({origem_forms})."}

    # PREENCHIMENTO DE FORMS usa o historico completo do formulario (nao so o
    # dia alvo) - por isso a copia e feita antes do filtro de data mais abaixo.
    forms_historico = forms.copy()

    col_os = _coluna(ofs, ["Ordem de Serviço", "ID da Ordem de Serviço", "PROTOCOLO", "OS", "ID"])
    col_data = _coluna(ofs, ["Data", "Data Agendamento", "DATA DE AGENDAMENTO", "Data da Atividade"])
    col_area = _coluna(ofs, ["Cidade", "AREA", "UNIDADE", "Área de Trabalho", "Chave Workzone"])
    col_status = _coluna(ofs, ["Status da Atividade", "STATUS"])
    col_motivo = _coluna(ofs, ["Motivo de Encerramento das atividades", "Motivo"])
    col_tipo = _coluna(ofs, ["Tipo de Atividade.1", "Tipo de Atividade 2",
                             "Tipo de Atividade_2", "Tipo de Atividade", "Tipo", "TIPO"])
    col_contrato = _coluna(ofs, ["Número do contrato", "Numero do contrato", "CÓDIGO CONTRATO", "Contrato"])
    col_cliente = _coluna(ofs, ["Nome", "Cliente", "Nome do Cliente"])
    col_cidade = _coluna(ofs, ["Cidade"])

    if not col_os or not col_data:
        return {**vazio, "erro": "O OFS GERAL não tem as colunas de ordem de serviço e data."}

    ofs = ofs.copy()
    if col_status:
        ofs = ofs[~ofs[col_status].astype(str).str.contains("cancelad", case=False, na=False)]

    ofs[col_data] = pd.to_datetime(ofs[col_data], errors="coerce", dayfirst=True).dt.normalize()
    df = ofs[ofs[col_data] == data_alvo].copy()
    if df.empty:
        return {**vazio, "erro": "", "sem_agenda": True, "origem_forms": origem_forms}

    df["OS_CLEAN"] = _os_limpa(df[col_os])
    df["REGIAO"] = df[col_area].apply(_regiao) if col_area else "OUTROS"
    df["TIPO_SERVICO"] = df[col_tipo].apply(_tipo_servico) if col_tipo else "Outros Serviços"

    # Upgrade/Downgrade e Mudanca de Comodo nao entram no total de confirmacao
    if col_tipo:
        fora = df[col_tipo].apply(_fora_da_confirmacao)
        df = df[~fora]

    df = df[df["REGIAO"].isin(REGIOES)]
    if df.empty:
        return {**vazio, "erro": "", "sem_agenda": True, "origem_forms": origem_forms}

    # --- forms: status e operador por O.S. -----------------------------
    col_os_forms = _coluna(forms, ["ORDEM DE SERVIÇO", "OS", "Ordem de Serviço"])
    col_status_forms = _coluna(forms, ["STATUS", "Status"])
    col_operador = _coluna(forms, ["OPERADOR", "Nome do Operador", "Técnico", "Recurso"])
    col_data_forms = _coluna(forms, ["DATA DE AGENDAMENTO", "Data de Agendamento",
                                     "Data Agendamento", "Data do Agendamento"])
    if not col_os_forms:
        return {**vazio, "erro": "O formulário não tem a coluna de ordem de serviço."}

    forms = forms.copy()
    if col_data_forms:
        recorte = pd.to_datetime(forms[col_data_forms], errors="coerce", dayfirst=True).dt.normalize()
        forms = forms[recorte == data_alvo]
    forms["OS_CLEAN"] = _os_limpa(forms[col_os_forms])

    # CONFIRMADO ganha de qualquer outro status na mesma O.S.
    status_por_os: dict[str, str] = {}
    operador_por_os: dict[str, str] = {}
    for _, linha in forms.iterrows():
        chave = linha["OS_CLEAN"]
        texto = str(linha[col_status_forms]).upper().strip() if col_status_forms else ""
        if "CONFIRMADO" in texto or chave not in status_por_os:
            status_por_os[chave] = texto
        if col_operador and chave not in operador_por_os:
            operador_por_os[chave] = str(linha[col_operador]).upper().strip()

    df["CONFIRMADA"] = df["OS_CLEAN"].map(
        lambda o: "CONFIRMADO" in status_por_os.get(o, "")
    )
    df["OPERADOR"] = df["OS_CLEAN"].map(operador_por_os).fillna("NÃO TRATADO")

    # --- visão CONFIRMADO ------------------------------------------------
    # dentro de cada regiao, tambem quebra por tipo de servico (Ativacao,
    # Mudanca de Endereco, Outros) - mesma tela do app desktop.
    ORDEM_TIPOS = ["Ativação", "Mudança de Endereço", "Outros Serviços"]
    confirmado = []
    nao_confirmados_regiao = []
    for nome in REGIOES:
        recorte = df[df["REGIAO"] == nome]
        if recorte.empty:
            continue
        conf = int(recorte["CONFIRMADA"].sum())
        por_tipo = []
        for tipo in ORDEM_TIPOS:
            sub = recorte[recorte["TIPO_SERVICO"] == tipo]
            if sub.empty:
                continue
            conf_tipo = int(sub["CONFIRMADA"].sum())
            por_tipo.append({"nome": tipo, "total": len(sub), "confirmados": conf_tipo,
                             "pct": _pct(conf_tipo, len(sub))})
        confirmado.append({
            "nome": nome, "total": len(recorte), "confirmados": conf,
            "nao": len(recorte) - conf, "pct": _pct(conf, len(recorte)),
            "por_tipo": por_tipo,
        })

        pendentes = recorte[~recorte["CONFIRMADA"]]
        itens = []
        for _, linha in pendentes.iterrows():
            itens.append({
                "contrato": str(linha[col_contrato]).split(".")[0].strip() if col_contrato else linha["OS_CLEAN"],
                "cliente": str(linha[col_cliente]).strip() if col_cliente else "",
                "cidade": str(linha[col_cidade]).strip() if col_cidade else "",
                "tipo": linha["TIPO_SERVICO"],
            })
        nao_confirmados_regiao.append({"nome": nome, "itens": itens})

    conf_total = int(df["CONFIRMADA"].sum())

    # --- visão EFETIVIDADE: só O.S. com motivo mapeado ------------------
    efetividade, operadores = [], []
    avisos = []
    if col_motivo:
        conhecidos = {k.upper() for k in MOTIVO_PRODUTIVO}
        base = df[df[col_motivo].astype(str).str.strip().str.upper().isin(conhecidos)].copy()
        if base.empty:
            avisos.append("Nenhuma O.S. do dia tem motivo de encerramento mapeado, "
                          "então a efetividade ainda não pode ser calculada.")
        else:
            base["PRODUTIVO"] = base[col_motivo].astype(str).str.strip().str.upper().map(
                {k.upper(): v for k, v in MOTIVO_PRODUTIVO.items()}
            )
            base["MOTIVO_MACRO"] = base[col_motivo].apply(_categoria_motivo)

            for nome in REGIOES:
                recorte = base[base["REGIAO"] == nome]
                if recorte.empty:
                    continue
                produtivas = int(recorte["PRODUTIVO"].sum())
                nao = len(recorte) - produtivas
                improdutivas = recorte[~recorte["PRODUTIVO"]]
                efetividade.append({
                    "nome": nome, "total": len(recorte), "produtivas": produtivas,
                    "nao_produtivas": nao, "pct": _pct(produtivas, len(recorte)),
                    "ofensores": [
                        {"nome": categoria,
                         "quantidade": int((improdutivas["MOTIVO_MACRO"] == categoria).sum()),
                         "pct": _pct(int((improdutivas["MOTIVO_MACRO"] == categoria).sum()), nao)}
                        for categoria in ("CLIENTE", "TÉCNICA", "COMERCIAL", "OUTROS")
                    ],
                })

            # --- visão OPERADOR: entre as que ele confirmou -------------
            confirmadas = base[base["CONFIRMADA"]]
            for operador, grupo in confirmadas.groupby("OPERADOR"):
                tecnicos = int((grupo["MOTIVO_MACRO"] == "TÉCNICA").sum())
                denominador = len(grupo) - tecnicos
                produtivos = int(grupo["PRODUTIVO"].sum())
                operadores.append({
                    "nome": operador, "total": len(grupo), "produtivos": produtivos,
                    "tecnicos": tecnicos, "pct": _pct(produtivos, denominador),
                })
            operadores.sort(key=lambda o: -o["pct"])
    else:
        avisos.append("O OFS GERAL não tem a coluna de motivo de encerramento, "
                      "então efetividade e ranking de operadores ficam indisponíveis.")

    # --- PREENCHIMENTO DE FORMS: volume histórico por operador -----------
    # Diferente das secoes acima (que olham so a agenda do dia), aqui e o
    # total de formularios que cada operador ja preencheu, para os operadores
    # que aparecem no ranking de efetividade acima.
    preenchimento = []
    if operadores and col_status_forms:
        hist = forms_historico.copy()
        if col_operador:
            hist["OPERADOR"] = hist[col_operador].astype(str).str.upper().str.strip()
        else:
            hist["OS_CLEAN"] = _os_limpa(hist[col_os_forms])
            hist["OPERADOR"] = hist["OS_CLEAN"].map(operador_por_os)
        hist["STATUS_FORM"] = (hist[col_status_forms].astype(str).str.upper().str.strip()
                               .replace("CANCELAR", "CANCELADO"))

        interesse = ["CONFIRMADO", "CANCELADO", "REAGENDAMENTO"]
        hist = hist[hist["STATUS_FORM"].isin(interesse)]
        if not hist.empty:
            cruzado = hist.groupby(["OPERADOR", "STATUS_FORM"]).size().unstack(fill_value=0)
            totais = cruzado.sum()
            for op in operadores:
                nome_op = op["nome"]
                linha = cruzado.loc[nome_op] if nome_op in cruzado.index else pd.Series(0, index=interesse)
                total_op = int(linha.sum())
                if not total_op:
                    continue
                preenchimento.append({
                    "nome": nome_op,
                    "total": total_op,
                    "por_status": [
                        {"nome": st.capitalize(), "quantidade": int(linha.get(st, 0)),
                         "pct": _pct(int(linha.get(st, 0)), int(totais.get(st, 0)))}
                        for st in interesse
                    ],
                })

    return {
        "erro": "",
        "dia": dia,
        "rotulo_dia": rotulo,
        "data_alvo": data_alvo.strftime("%d/%m/%Y"),
        "origem_forms": origem_forms,
        "total": len(df),
        "confirmados": conf_total,
        "pct_confirmado": _pct(conf_total, len(df)),
        "confirmado": confirmado,
        "efetividade": efetividade,
        "operadores": operadores,
        "preenchimento": preenchimento,
        "nao_confirmados": nao_confirmados_regiao,
        "avisos": avisos,
        "gerado_em": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    }


# O Telegram recusa mensagem acima de 4096 caracteres; o bot ja trunca em
# 3800, mas cortando no meio da lista sem dizer o que ficou de fora. Para uma
# lista de contratos pendentes, perder o fim silenciosamente e pior do que
# mandar em varias mensagens - por isso a divisao acontece aqui, respeitando
# sempre o contrato inteiro (nunca corta uma linha ao meio).
LIMITE_CARACTERES_MENSAGEM = 3500


def _linha_item(item: dict) -> str:
    partes = [item["contrato"]]
    if item["cliente"]:
        partes.append(item["cliente"])
    if item["cidade"]:
        partes.append(item["cidade"])
    return "• " + " — ".join(partes)


def mensagem_nao_confirmados(dia: str = "d0") -> dict:
    """Monta o texto (em uma ou mais partes) com os contratos ainda nao confirmados.

    Usa o mesmo `calcular()` da tela - mesma base, mesma exclusao de
    Upgrade/Downgrade e Mudanca de Comodo, mesmo dia (D0/D1).
    """
    dados = calcular(dia)
    if dados.get("erro"):
        return {"ok": False, "erro": dados["erro"]}
    if dados.get("sem_agenda"):
        return {"ok": False, "erro": f"Sem agenda para {dados['rotulo_dia']} ({dados['data_alvo']})."}

    regioes = dados.get("nao_confirmados", [])
    total = sum(len(r["itens"]) for r in regioes)
    if total == 0:
        return {"ok": False, "erro": f"Nenhum pendente — {dados['rotulo_dia']} está 100% confirmado."}

    cabecalho = f"📋 *NÃO CONFIRMADOS — {dados['rotulo_dia'].upper()} ({dados['data_alvo']})*"
    subtitulo = f"_{total} contrato(s) sem confirmação, exceto Upgrade/Downgrade e Mudança de Cômodo_"

    # monta o corpo como uma lista plana de linhas (cabecalho de regiao + itens),
    # cada elemento e uma unidade que nunca vai ser partida ao meio
    unidades: list[str] = []
    for regiao in regioes:
        if not regiao["itens"]:
            continue
        unidades.append(f"*{regiao['nome']}* ({len(regiao['itens'])})")
        unidades.extend(_linha_item(item) for item in regiao["itens"])
        unidades.append("")

    blocos: list[str] = []
    atual: list[str] = []
    tamanho_atual = 0
    for unidade in unidades:
        acrescimo = len(unidade) + 1
        if atual and tamanho_atual + acrescimo > LIMITE_CARACTERES_MENSAGEM:
            blocos.append("\n".join(atual).strip())
            atual, tamanho_atual = [], 0
        atual.append(unidade)
        tamanho_atual += acrescimo
    if atual:
        blocos.append("\n".join(atual).strip())

    multiplas = len(blocos) > 1
    partes = []
    for i, corpo in enumerate(blocos, 1):
        topo = cabecalho + (f" _(parte {i}/{len(blocos)})_" if multiplas else "")
        partes.append(f"{topo}\n{subtitulo}\n\n{corpo}" if i == 1 else f"{topo}\n\n{corpo}")

    return {"ok": True, "partes": partes, "total": total}


def enviar_nao_confirmados(dia: str = "d0") -> dict:
    """Monta a lista e pede ao bot para mandar ao grupo, parte por parte."""
    from . import ponte_bot

    pronto = mensagem_nao_confirmados(dia)
    if not pronto["ok"]:
        return pronto

    for i, texto in enumerate(pronto["partes"], 1):
        resultado = ponte_bot.chamar("/enviar-mensagem", {"texto": texto})
        if not resultado["ok"]:
            resultado["erro"] = (
                f"Enviei {i - 1} de {len(pronto['partes'])} parte(s) e travei: {resultado['erro']}"
            )
            return resultado

    partes_info = f" em {len(pronto['partes'])} mensagens" if len(pronto["partes"]) > 1 else ""
    return {"ok": True, "total": pronto["total"],
            "mensagem": f"Lista enviada ao grupo{partes_info} — {pronto['total']} contrato(s)."}
