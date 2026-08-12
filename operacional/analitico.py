"""Le a extracao bruta de campo e recria as colunas calculadas do painel.

A planilha original resolve isso com ~50 colunas de formula na aba ANALITICO
(XLOOKUP/SUMIFS/COUNTIFS). Aqui as mesmas regras viram codigo, para que tudo
seja calculado a partir de UMA fonte: a extracao do dia.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pandas as pd

from .parametros import Parametros, carregar, normalizar

# Colunas da extracao bruta que o painel consome (nomes conforme o export de campo)
# Cada campo aceita mais de um nome de coluna, na ordem de preferencia.
# O export vem com duas colunas "Tipo de Atividade": a generica (Normal) e a do
# servico (ATIVACAO, REPARO CORRETIVO...). Quem desempata a duplicata renomeia:
# o Excel escreve "_2", o pandas escreve ".1". As duas valem.
COLUNAS_ORIGEM = {
    "recurso": ["Recurso"],
    "data": ["Data"],
    "status_atividade": ["Status da Atividade"],
    "nome": ["Nome"],
    "cidade": ["Cidade"],
    "intervalo": ["Intervalo de Tempo"],
    "abertura": ["Data Abertura Chamado"],
    "inicio": ["Início"],
    "fim": ["Fim"],
    "duracao": ["Duração"],
    "tipo": ["Tipo de Atividade_2", "Tipo de Atividade.1",
             "Tipo de Atividade 2", "Tipo de Atividade2"],
    "motivo": ["Motivo de Encerramento das atividades"],
    "rota": ["Posição na Rota"],
    "contrato": ["Número do contrato", "Numero do contrato", "Contrato"],
}

SLA_REPARO_HORAS = 24.0      # reparo corretivo: 24h (coluna SLA / "24 HS")
PRAZO_ALTA_DIAS = 1.5        # coluna PRAZO ALTA

# AGING: prazo entre a abertura do chamado e o encerramento, por tipo de
# servico. Reparo Corretivo fica de fora - ele tem a metrica propria acima
# ("24 HS"), com prazo mais curto porque e uma falha ja em producao.
PRAZOS_AGING_HORAS = {
    "ATIVAÇÃO": 48.0,
    "MUDANÇA DE ENDEREÇO": 48.0,
    "UPGRADE/DOWNGRADE": 4 * 24.0,
    "MUDANÇA DE CÔMODO": 4 * 24.0,
}


def _acha_coluna(df: pd.DataFrame, desejadas: list[str]) -> str | None:
    """Primeira coluna que casa com algum dos nomes, ignorando acento/caixa/espacos."""
    disponiveis = {normalizar(col): col for col in df.columns}
    for desejada in desejadas:
        if achada := disponiveis.get(normalizar(desejada)):
            return achada
    return None


def _para_datetime(valor):
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return pd.NaT
    if isinstance(valor, dt.datetime):
        return valor
    if isinstance(valor, dt.date):
        return dt.datetime(valor.year, valor.month, valor.day)
    texto = str(valor).strip()
    # ISO (AAAA-MM-DD) vem de CSV; dd/mm/aaaa vem do export de campo.
    dia_primeiro = not re.match(r"^\d{4}-\d{2}-\d{2}", texto)
    return pd.to_datetime(texto, dayfirst=dia_primeiro, errors="coerce")


def _para_time(valor):
    """Normaliza a coluna de hora (vem como time, datetime, timedelta ou texto)."""
    if valor is None or valor == "" or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, dt.time):
        return valor
    if isinstance(valor, dt.datetime):
        return valor.time()
    if isinstance(valor, pd.Timedelta):
        total = int(valor.total_seconds())
        return dt.time((total // 3600) % 24, (total // 60) % 60, total % 60)
    texto = str(valor).strip()
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", texto)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if h < 24:
            return dt.time(h, mi, s)
    return None


def _combina(data, hora):
    """Junta a data da atividade com uma hora (equivale a Data + Fim no Excel)."""
    if pd.isna(data) or hora is None:
        return pd.NaT
    return dt.datetime.combine(pd.Timestamp(data).date(), hora)


def _abertura_para_datetime(texto, data_atividade):
    """'06/06 20:14' nao traz o ano - ele vem da data da atividade.

    Se a data montada cair depois da atividade, o chamado e do ano anterior
    (virada de ano: atividade em 02/01/2027 com abertura em 30/12).
    """
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return pd.NaT
    if isinstance(texto, dt.datetime):
        return texto

    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?[ T]+(\d{1,2}):(\d{2})", str(texto))
    if not m:
        return pd.to_datetime(texto, dayfirst=True, errors="coerce")

    dia, mes, ano, hora, minuto = m.groups()
    base = pd.Timestamp(data_atividade) if not pd.isna(data_atividade) else pd.Timestamp.now()
    if ano:
        ano = int(ano) + (2000 if len(ano) == 2 else 0)
    else:
        ano = base.year

    try:
        montada = dt.datetime(ano, int(mes), int(dia), int(hora), int(minuto))
    except ValueError:
        return pd.NaT
    if not ano and montada > base:
        montada = montada.replace(year=ano - 1)
    if montada > base + dt.timedelta(days=1):
        montada = montada.replace(year=montada.year - 1)
    return montada


def carregar_extracao(caminho: str | Path, aba: str | None = None) -> pd.DataFrame:
    """Le a extracao bruta (.xlsx ou .csv) e devolve o DataFrame cru."""
    caminho = Path(caminho)
    if caminho.suffix.lower() in {".csv", ".txt"}:
        for enc in ("utf-8-sig", "latin-1"):
            try:
                return pd.read_csv(caminho, sep=None, engine="python", encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Nao consegui ler {caminho} como CSV")

    xls = pd.ExcelFile(caminho)
    if aba is None:
        # prefere a aba ANALITICO; senao a primeira que tenha as colunas esperadas
        for candidata in xls.sheet_names:
            if normalizar(candidata) == "ANALITICO":
                aba = candidata
                break
        else:
            aba = xls.sheet_names[0]
    return pd.read_excel(xls, sheet_name=aba)


def preparar(df_bruto: pd.DataFrame, parametros: Parametros | None = None,
             agora: dt.datetime | None = None,
             somente_servicos: bool = True,
             clusters_excluidos: list[str] | None = None,
             servicos_excluidos: list[str] | None = None) -> pd.DataFrame:
    """Recria as colunas calculadas da aba ANALITICO.

    `agora` fixa o horario de referencia (usado por TEMPO VIDA e TEMPO INICIADA);
    default e o relogio do sistema.

    `somente_servicos` descarta o que a agenda traz mas nao e servico prestado -
    almoco, checklist, consulta medica, manutencao veicular. O criterio e a
    tabela PONTUACAO da aba BASE: tipo sem valor e sem baremo nao e servico.
    Sem isso eles entram na conta de produtividade e no Total Geral, que deixa
    de bater com a soma dos clusters.

    `clusters_excluidos` tira inteiramente do calculo um cluster que a operacao
    nao atende mais (ex.: "AT"), mesmo que uma atividade tenha sido lancada nele
    por engano. Diferente de so ocultar na tela: essas linhas nao contam nem no
    OPERACIONAL agregado.

    `servicos_excluidos` faz o mesmo para um tipo de atividade que deixou de ser
    prestado (ex.: "Clean Up - Casa Cliente"): a linha some de todas as 4 telas,
    nao so fica com zero.
    """
    par = parametros or carregar()
    agora = agora or dt.datetime.now()

    faltando = []
    dados = {}
    for chave, nomes in COLUNAS_ORIGEM.items():
        col = _acha_coluna(df_bruto, nomes)
        if col is None:
            faltando.append((chave, nomes))
            dados[chave] = pd.Series([None] * len(df_bruto))
        else:
            dados[chave] = df_bruto[col].reset_index(drop=True)

    obrigatorias = {"recurso", "data", "status_atividade", "cidade", "tipo"}
    criticas = [nomes for chave, nomes in faltando if chave in obrigatorias]
    if criticas:
        procuradas = "; ".join(" ou ".join(n) for n in criticas)
        raise ValueError(
            f"A extração não tem estas colunas obrigatórias: {procuradas}. "
            f"Colunas encontradas: {', '.join(str(c) for c in df_bruto.columns)}"
        )

    df = pd.DataFrame(dados)
    df = df[df["recurso"].notna() & (df["recurso"].astype(str).str.strip() != "")].reset_index(drop=True)
    if df.empty:
        raise ValueError("A extracao nao tem nenhuma linha com Recurso preenchido.")

    df["data"] = df["data"].map(_para_datetime)
    df["inicio"] = df["inicio"].map(_para_time)
    df["fim"] = df["fim"].map(_para_time)
    df["rota"] = pd.to_numeric(df["rota"], errors="coerce")

    df["status_norm"] = df["status_atividade"].map(normalizar)
    df["tipo_norm"] = df["tipo"].map(normalizar)

    descartados: dict[str, int] = {}
    if somente_servicos:
        eh_servico = df["tipo_norm"].isin(set(par.pontuacao))
        descartados = df.loc[~eh_servico, "tipo"].value_counts().to_dict()
        df = df[eh_servico].reset_index(drop=True)
        if df.empty:
            raise ValueError(
                "Nenhuma atividade da extração é um serviço conhecido. "
                "Tipos encontrados: " + ", ".join(str(t) for t in descartados)
            )

    df["concluido"] = df["status_norm"] == normalizar("CONCLUÍDO")

    # --- de/para da aba BASE -------------------------------------------
    df["status_real"] = df["status_atividade"].map(par.status_real)
    df["reparo_flag"] = df["status_atividade"].map(lambda v: par.status_campo(v, "reparo") or "")
    df["efetivo"] = df["status_atividade"].map(lambda v: par.status_campo(v, "efetivo") or "")
    df["info"] = df["status_atividade"].map(lambda v: par.status_campo(v, "info") or 0)
    df["pende"] = df["status_atividade"].map(lambda v: par.status_campo(v, "pende") or 0)
    df["cluster"] = df["cidade"].map(par.cluster_da_cidade)
    df["baremos"] = df["tipo"].map(par.pontos)
    df["valor_tabela"] = df["tipo"].map(par.valor)
    df["sub_motivo"] = df["motivo"].map(par.sub_motivo)

    # PONTOS / VALOR OK: so pontuam atividades concluidas
    df["pontos"] = df["baremos"].where(df["concluido"], 0.0)
    df["valor_ok"] = df["valor_tabela"].where(df["concluido"], 0.0)

    # --- clusters fora de operacao ---------------------------------------
    excluidos_norm: dict[str, int] = {}
    if clusters_excluidos:
        alvo = {normalizar(c) for c in clusters_excluidos if str(c).strip()}
        fora = df["cluster"].map(normalizar).isin(alvo)
        if fora.any():
            excluidos_norm = df.loc[fora, "cluster"].value_counts().to_dict()
            df = df[~fora].reset_index(drop=True)

    # --- servicos fora de operacao -----------------------------------------
    servicos_excluidos_norm: dict[str, int] = {}
    if servicos_excluidos:
        alvo_serv = {normalizar(s) for s in servicos_excluidos if str(s).strip()}
        fora_serv = df["tipo_norm"].isin(alvo_serv)
        if fora_serv.any():
            servicos_excluidos_norm = df.loc[fora_serv, "tipo"].value_counts().to_dict()
            df = df[~fora_serv].reset_index(drop=True)

    # --- mao de obra ----------------------------------------------------
    # VALIDA MO / MO CLUSTER: conta o tecnico uma unica vez; equipe "OPERACIONAL *"
    # (as bases moveis) nao entra na conta de mao de obra. Chamado ainda sem
    # tecnico roteado vem com o "Recurso" preenchido com a propria cidade/rota
    # (Posicao na Rota = 0) em vez do nome de uma pessoa - isso nao e mao de
    # obra e nao pode contar como tecnico.
    #
    # A deduplicacao roda SO entre as linhas elegiveis. Ate 07/08/2026 ela rodava
    # sobre o df inteiro, e aí uma linha que o filtro descarta ainda assim
    # gastava a vaga de "primeira aparicao" do tecnico: quem tivesse a PRIMEIRA
    # linha com rota 0 tinha todas as seguintes marcadas como repetidas e sumia
    # da contagem inteira, mesmo com o dia cheio de atividade roteirizada.
    # Depende da ORDEM das linhas na extracao, entao ia e voltava sozinho -- em
    # 11/06 nao aparecia; em 07/08 comeu 2 dos 23 tecnicos, e como o PROD REAL
    # divide por este numero, ele saía inflado (CGT 2,87 no lugar de 2,56).
    eh_operacional = df["recurso"].astype(str).str.upper().str.startswith("OPERACIONAL")
    sem_tecnico = df["rota"] == 0
    elegivel = ~eh_operacional & ~sem_tecnico
    df["valida_mo"] = 0
    df["mo_cluster"] = 0
    df.loc[elegivel, "valida_mo"] = (
        ~df.loc[elegivel].duplicated(subset=["recurso"])
    ).astype(int)
    df.loc[elegivel, "mo_cluster"] = (
        ~df.loc[elegivel].duplicated(subset=["recurso", "cluster"])
    ).astype(int)

    # --- abertura / SLA -------------------------------------------------
    df["abertura_dt"] = [
        _abertura_para_datetime(txt, data) for txt, data in zip(df["abertura"], df["data"])
    ]
    df["fim_dt"] = [_combina(d, h) for d, h in zip(df["data"], df["fim"])]
    df["inicio_dt"] = [_combina(d, h) for d, h in zip(df["data"], df["inicio"])]

    horas_ate_fim = (df["fim_dt"] - df["abertura_dt"]).dt.total_seconds() / 3600.0

    # SLA do reparo corretivo (coluna "24 HS"): fechou em ate 24h da abertura?
    eh_reparo = df["tipo_norm"] == normalizar("REPARO CORRETIVO")
    df["sla"] = ""
    alvo = eh_reparo & df["concluido"] & horas_ate_fim.notna()
    df.loc[alvo, "sla"] = horas_ate_fim[alvo].map(
        lambda h: "PRAZO" if h <= SLA_REPARO_HORAS else "FORA"
    )

    # AGING: fechou dentro do prazo do proprio tipo de servico (tabela acima)?
    prazos_aging_norm = {normalizar(k): v for k, v in PRAZOS_AGING_HORAS.items()}
    limite_aging = df["tipo_norm"].map(prazos_aging_norm)
    df["aging"] = ""
    alvo = limite_aging.notna() & df["concluido"] & horas_ate_fim.notna()
    df.loc[alvo, "aging"] = [
        "PADRAO" if h <= lim else "FORA"
        for h, lim in zip(horas_ate_fim[alvo], limite_aging[alvo])
    ]

    # PRAZO ALTA: encerramento em ate 1,5 dia da abertura
    df["horas_alta"] = horas_ate_fim
    df["prazo_alta"] = (horas_ate_fim / 24.0).map(
        lambda d: "" if pd.isna(d) else ("Vencido" if d > PRAZO_ALTA_DIAS else "Prazo")
    )

    # --- tempo de vida (reparo ainda em aberto) -------------------------
    # So conta para reparo corretivo cujo status pede verificacao
    # (PENDENTE / EM ROTA / INICIADO).
    em_verificacao = eh_reparo & (df["reparo_flag"] == "VERIFICA")
    df["tempo_vida"] = (pd.Timestamp(agora) - df["abertura_dt"]).where(em_verificacao)

    # VER PRAZO: "VENCIDO" acima de 24h de vida. A planilha tambem marca VENCIDO
    # quando nao ha tempo de vida (no Excel, celula vazia > 24:00 e verdadeiro),
    # ou seja, atividades ja encerradas entram como vencidas. Comportamento
    # mantido para o relatorio continuar batendo com o que o grupo recebe hoje;
    # use "prazo_somente_em_aberto" no painel.json para ocultar as encerradas.
    limite = pd.Timedelta(hours=SLA_REPARO_HORAS)
    df["ver_prazo"] = [
        "VENCIDO" if (pd.isna(tv) or tv >= limite) else "PRAZO"
        for tv in df["tempo_vida"]
    ]
    # regra de formatacao da planilha: TEMPO VIDA em vermelho acima de 1 dia
    df["tempo_vida_alerta"] = df["tempo_vida"] > pd.Timedelta(days=1)

    # --- tempo em execucao (atividade INICIADO) -------------------------
    iniciado = df["status_norm"] == normalizar("INICIADO")
    df["tempo_iniciada"] = (
        pd.Timestamp(agora).floor("min") - df["inicio_dt"]
    ).where(iniciado)

    df["conta"] = 1
    df.attrs["agora"] = agora
    df.attrs["data_referencia"] = df["data"].max()
    df.attrs["descartados"] = descartados
    df.attrs["clusters_excluidos"] = excluidos_norm
    df.attrs["servicos_excluidos"] = servicos_excluidos_norm
    return df


def momento_painel(df: pd.DataFrame) -> dt.datetime:
    """Data/hora do carimbo do cabecalho, pela mesma regra da planilha.

    A data e a da extracao; a hora e a do ultimo evento registrado
    (inicio das INICIADO, fim das CONCLUIDO e NAO CONCLUIDO) - nao o relogio.
    """
    data = df["data"].max()
    data = pd.Timestamp(data).date() if not pd.isna(data) else dt.date.today()

    iniciado = df["status_norm"] == normalizar("INICIADO")
    encerrado = df["status_norm"].isin(
        [normalizar("CONCLUÍDO"), normalizar("NÃO CONCLUÍDO")]
    )
    candidatos = [h for h in list(df.loc[iniciado, "inicio"]) + list(df.loc[encerrado, "fim"])
                  if isinstance(h, dt.time)]
    if not candidatos:
        return dt.datetime.combine(data, dt.datetime.now().time().replace(microsecond=0))
    return dt.datetime.combine(data, max(candidatos))


def diagnosticar(df: pd.DataFrame, parametros: Parametros | None = None) -> list[str]:
    """Avisos de dados que o de/para nao reconheceu.

    Cidade fora da tabela CLUSTER some das tabelas por cluster; tipo de atividade
    fora da PONTUACAO pontua zero. Nos dois casos o relatorio sai "certo" e
    silenciosamente errado - por isso o aviso.
    """
    par = parametros or carregar()
    avisos = []

    sem_cluster = sorted(df.loc[df["cluster"] == "", "cidade"].dropna().astype(str).unique())
    if sem_cluster:
        avisos.append(
            f"{len(df[df['cluster'] == ''])} atividade(s) com cidade fora da tabela CLUSTER "
            f"(nao entram nas tabelas por cluster): {', '.join(sem_cluster)}"
        )

    if excluidos := df.attrs.get("clusters_excluidos"):
        detalhe = ", ".join(f"{cluster} ({qtd})" for cluster, qtd in sorted(excluidos.items()))
        avisos.append(
            f"{sum(excluidos.values())} atividade(s) fora do painel por estarem em "
            f"cluster desativado: {detalhe}. Ajuste em Painel > Configurações se algum "
            f"desses clusters voltou a operar."
        )

    if servicos_fora := df.attrs.get("servicos_excluidos"):
        detalhe = ", ".join(f"{tipo} ({qtd})" for tipo, qtd in sorted(servicos_fora.items()))
        avisos.append(
            f"{sum(servicos_fora.values())} atividade(s) fora do painel por serem de um "
            f"serviço desativado: {detalhe}. Ajuste 'servicos_excluidos' em "
            f"config/painel.json se ele voltou a ser prestado."
        )

    if descartados := df.attrs.get("descartados"):
        detalhe = ", ".join(f"{tipo} ({qtd})" for tipo, qtd in sorted(descartados.items()))
        avisos.append(
            f"{sum(descartados.values())} atividade(s) fora do painel por não serem "
            f"serviço na tabela PONTUACAO: {detalhe}. Se alguma dessas for serviço "
            f"de verdade, acrescente na aba BASE."
        )

    conhecidos = set(par.pontuacao)
    sem_pontuacao = sorted({t for t in df["tipo_norm"] if t and t not in conhecidos})
    if sem_pontuacao:
        avisos.append(
            f"Tipo de atividade sem valor/pontos na tabela PONTUACAO "
            f"(pontuam zero): {', '.join(sem_pontuacao)}"
        )

    conhecidos = set(par.status)
    sem_status = sorted({s for s in df["status_norm"] if s and s not in conhecidos})
    if sem_status:
        avisos.append(
            f"Status fora da tabela STATUS (nao contam como INFOR/PEND/OK): "
            f"{', '.join(sem_status)}"
        )

    sem_abertura = int(df["abertura_dt"].isna().sum())
    if sem_abertura:
        avisos.append(f"{sem_abertura} atividade(s) sem data de abertura legivel (ficam fora do SLA).")

    return avisos


def clusters_presentes(df: pd.DataFrame) -> list[str]:
    """Clusters com atividade, na ordem em que aparecem no painel."""
    ordem = ["AT", "CGT", "VPA", "RIO"]
    presentes = [c for c in ordem if c in set(df["cluster"])]
    extras = sorted(set(df["cluster"]) - set(ordem) - {""})
    return presentes + extras
