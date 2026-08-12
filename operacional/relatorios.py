"""Monta os tres relatorios que vao para o grupo.

1. capa_envio_grupo  -> PAINEL DE ACOMPANHAMENTO
2. gestao_prazos     -> GESTAO DE PRAZOS
3. tempo_execucao    -> TEMPO DE EXECUCAO

Cada funcao devolve um dicionario pronto para o template - sem HTML aqui.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd

from .analitico import clusters_presentes
from .parametros import normalizar

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_PAINEL = RAIZ / "config" / "painel.json"

REPARO_CORRETIVO = normalizar("REPARO CORRETIVO")
ATIVACAO = normalizar("ATIVAÇÃO")
CANCELADO = normalizar("CANCELADO")


def carregar_config(caminho: str | Path | None = None) -> dict:
    alvo = Path(caminho) if caminho else ARQUIVO_PAINEL
    if not alvo.exists():
        return {}
    return json.loads(alvo.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# formatacao pt-BR
# --------------------------------------------------------------------------
def num(valor, casas: int = 2) -> str:
    if valor is None or pd.isna(valor):
        return ""
    texto = f"{float(valor):,.{casas}f}"
    return texto.replace(",", "º").replace(".", ",").replace("º", ".")


def pct(valor, casas: int = 2) -> str:
    if valor is None or pd.isna(valor):
        return ""
    return num(float(valor) * 100, casas) + "%"


def reais(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    return "R$ " + num(valor, 2)


def inteiro(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    return f"{int(round(float(valor))):,}".replace(",", ".")


def duracao_hms(td) -> str:
    """Timedelta -> [h]:mm:ss, com as horas acumuladas (nao reinicia em 24h)."""
    if td is None or pd.isna(td):
        return ""
    total = int(pd.Timedelta(td).total_seconds())
    sinal = "-" if total < 0 else ""
    total = abs(total)
    return f"{sinal}{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def hora_hms(valor) -> str:
    if valor is None or pd.isna(valor):
        return ""
    if isinstance(valor, dt.time):
        return valor.strftime("%H:%M:%S")
    if isinstance(valor, (dt.datetime, pd.Timestamp)):
        return valor.strftime("%H:%M:%S")
    return str(valor)


def data_hora_curta(valor) -> str:
    """Formato usado na coluna ABERTURA: dd/mm HH:MM."""
    if valor is None or pd.isna(valor):
        return ""
    return pd.Timestamp(valor).strftime("%d/%m %H:%M")


# --------------------------------------------------------------------------
# calculos compartilhados
# --------------------------------------------------------------------------
def efetivo_configurado(cfg: dict, cluster: str, calculado: int) -> int:
    """Efetivo de um cluster: o valor de `mo_ativa_manual`, ou o calculado.

    O mesmo numero vale para a M.O ATIVA da capa e para os TECNICOS ATIVOS da
    carga - preencher em um lugar so ja atende as duas telas.
    """
    tabela = {normalizar(k): v for k, v in (cfg.get("mo_ativa_manual") or {}).items()}
    valor = tabela.get(normalizar(cluster))
    return int(valor) if valor is not None else int(calculado)


def _taxa(serie: pd.Series, bons: str, ruins: str):
    """Percentual de `bons` sobre bons+ruins; None quando nao ha base."""
    total = (serie == bons).sum() + (serie == ruins).sum()
    if total == 0:
        return None
    return (serie == bons).sum() / total


def _efetividade_por_linha(df: pd.DataFrame) -> pd.Series:
    """Recria a coluna EFETIVI: OK / (OK + NAO CONCLUIDO) dentro de cluster+tipo.

    O painel exibe a MEDIA dessa coluna, entao a efetividade de um cluster fica
    ponderada pelo numero de atividades de cada tipo - e isso e reproduzido aqui
    para o numero bater com o que o grupo ja conhece.
    """
    nao_concluido = normalizar("NÃO CONCLUÍDO")
    ok = df.groupby(["cluster", "tipo_norm"])["efetivo"].transform(lambda s: (s == "SIM").sum())
    nok = df.groupby(["cluster", "tipo_norm"])["status_norm"].transform(
        lambda s: (s == nao_concluido).sum()
    )
    base = ok + nok
    return (ok / base.where(base > 0)).fillna(0.0)


def _bloco_contagem(df: pd.DataFrame, efetivi: pd.Series) -> dict:
    """INFOR / PEND / OK / EFETIVIDADE / 24 HS / AGING / VALOR R$ de um recorte."""
    if df.empty:
        return {"infor": 0, "pend": 0, "ok": 0, "efetividade": 0.0,
                "sla24": None, "aging": None, "valor": 0.0}
    return {
        "infor": int(df["info"].sum()),
        "pend": int(df["pende"].sum()),
        "ok": int(df["concluido"].sum()),
        "efetividade": float(efetivi.loc[df.index].mean()),
        "sla24": _taxa(df["sla"], "PRAZO", "FORA"),
        "aging": _taxa(df["aging"], "PADRAO", "FORA"),
        "valor": float(df["valor_ok"].sum()),
    }


# --------------------------------------------------------------------------
# 1. CAPA ENVIO GRUPO
# --------------------------------------------------------------------------
def capa_envio_grupo(df: pd.DataFrame, config: dict | None = None) -> dict:
    cfg = config or carregar_config()
    meta_prod = float(cfg.get("meta_produtividade", 3.7))
    metas_rs = {normalizar(k): v for k, v in (cfg.get("meta_financeira_dia") or {}).items()}

    clusters = clusters_presentes(df)
    efetivi = _efetividade_por_linha(df)

    # --- tabela de produtividade ---
    produtividade = []
    media_por_linha = pd.Series(0.0, index=df.index)
    for cluster in clusters:
        recorte = df[df["cluster"] == cluster]
        mo_cluster = int(recorte["mo_cluster"].sum())
        pontos = float(recorte["pontos"].sum())
        prod_real = pontos / mo_cluster if mo_cluster else 0.0
        media_por_linha.loc[recorte.index] = prod_real

        mo_ativa = efetivo_configurado(cfg, cluster, int(recorte["valida_mo"].sum()))

        realizado = float(recorte["valor_ok"].sum())
        meta_rs = float(metas_rs.get(normalizar(cluster), 0) or 0)
        produtividade.append({
            "cluster": cluster,
            "mo_ativa": int(mo_ativa),
            "meta_prod": meta_prod,
            "prod_real": prod_real,
            "meta_rs": meta_rs,
            "realizado_rs": realizado,
            "ating_rs": (realizado / meta_rs) if meta_rs else 0.0,
        })

    # OPERACIONAL: a planilha usa AVERAGE da coluna MEDIA PRODUCAO, ou seja, a media
    # dos clusters ponderada pelo numero de atividades de cada um.
    total_mo = efetivo_configurado(cfg, "OPERACIONAL",
                                   sum(l["mo_ativa"] for l in produtividade))
    total_meta_rs = sum(l["meta_rs"] for l in produtividade)
    total_realizado = sum(l["realizado_rs"] for l in produtividade)
    produtividade.append({
        "cluster": "OPERACIONAL",
        "mo_ativa": total_mo,
        "meta_prod": meta_prod,
        "prod_real": float(media_por_linha.mean()) if len(df) else 0.0,
        "meta_rs": total_meta_rs,
        "realizado_rs": total_realizado,
        "ating_rs": (total_realizado / total_meta_rs) if total_meta_rs else 0.0,
        "total": True,
    })

    # --- tabela de status por cluster e tipo ---
    status_linhas = []
    for cluster in clusters:
        recorte = df[df["cluster"] == cluster]
        status_linhas.append({"rotulo": cluster, "nivel": "cluster", **_bloco_contagem(recorte, efetivi)})
        tipos = sorted(recorte["tipo"].dropna().unique(), key=lambda t: normalizar(t))
        for tipo in tipos:
            sub = recorte[recorte["tipo"] == tipo]
            status_linhas.append({"rotulo": str(tipo).upper(), "nivel": "tipo",
                                  **_bloco_contagem(sub, efetivi)})
    status_linhas.append({"rotulo": "Total Geral", "nivel": "total",
                          **_bloco_contagem(df, efetivi)})

    return {
        "titulo": "PAINEL DE ACOMPANHAMENTO",
        "produtividade": produtividade,
        "status": status_linhas,
        "grafico": [
            {"cluster": l["cluster"], "valor": l["ating_rs"]}
            for l in produtividade if not l.get("total")
        ],
    }


# --------------------------------------------------------------------------
# 2. GESTAO DE PRAZOS
# --------------------------------------------------------------------------
def gestao_prazos(df: pd.DataFrame, config: dict | None = None) -> dict:
    cfg = config or carregar_config()
    limite = int(cfg.get("limite_linhas_prazo", 0) or 0)

    recorte = df[df["tipo_norm"] == REPARO_CORRETIVO].copy()
    if cfg.get("prazo_somente_em_aberto"):
        # so reparos ainda em aberto (com tempo de vida correndo)
        recorte = recorte[recorte["tempo_vida"].notna()]
    # vencido (>=24h de vida) nao entra na tela - so o que ainda esta no prazo
    recorte = recorte[recorte["ver_prazo"] != "VENCIDO"]
    # ordena pelo reparo mais antigo em aberto; os sem tempo de vida vao ao fim
    recorte["_ordem"] = recorte["tempo_vida"].fillna(pd.Timedelta(0))
    recorte = recorte.sort_values("_ordem", ascending=False)

    total = len(recorte)
    if limite:
        recorte = recorte.head(limite)

    linhas = [{
        "hora": hora_hms(r["abertura_dt"]),
        "tempo_vida": duracao_hms(r["tempo_vida"]),
        "tempo_vida_alerta": bool(r["tempo_vida_alerta"]),
        "rota": "" if pd.isna(r["rota"]) else inteiro(r["rota"]),
        "abertura": data_hora_curta(r["abertura_dt"]),
        "status": r["status_atividade"],
        "cluster": r["cluster"],
        "recurso": r["recurso"],
        "contrato": "" if pd.isna(r["contrato"]) else str(r["contrato"]).split(".")[0],
        "cliente": r["nome"],
        "cidade": r["cidade"],
        "un": 1,
        "vencido": r["ver_prazo"] == "VENCIDO",
    } for _, r in recorte.iterrows()]

    return {
        "titulo": "GESTÃO DE PRAZOS",
        "linhas": linhas,
        "total": total,
        "exibidas": len(linhas),
        "vencidos": int((recorte["ver_prazo"] == "VENCIDO").sum()),
    }


# --------------------------------------------------------------------------
# 3. TEMPO DE EXECUCAO
# --------------------------------------------------------------------------
def tempo_execucao(df: pd.DataFrame, config: dict | None = None) -> dict:
    cfg = config or carregar_config()
    limite = int(cfg.get("limite_linhas_tempo", 0) or 0)
    alerta_min = float(cfg.get("alerta_tempo_execucao_min", 20))

    recorte = df[df["status_norm"] == normalizar("INICIADO")].copy()
    recorte = recorte.sort_values("tempo_iniciada", ascending=False)

    total = len(recorte)
    if limite:
        recorte = recorte.head(limite)

    maximo = recorte["tempo_iniciada"].max() if not recorte.empty else pd.Timedelta(0)
    maximo_min = max(pd.Timedelta(maximo).total_seconds() / 60, 1) if not pd.isna(maximo) else 1

    linhas = []
    for _, r in recorte.iterrows():
        minutos = 0.0
        if not pd.isna(r["tempo_iniciada"]):
            minutos = pd.Timedelta(r["tempo_iniciada"]).total_seconds() / 60
        # intensidade 0..1 do destaque vermelho, so acima do limite de alerta
        intensidade = 0.0
        if minutos >= alerta_min:
            intensidade = min(1.0, (minutos - alerta_min) / max(maximo_min - alerta_min, 1) * 0.75 + 0.25)
        linhas.append({
            "tempo": duracao_hms(r["tempo_iniciada"]),
            "hr_inicio": hora_hms(r["inicio"]),
            "cluster": r["cluster"],
            "tecnico": r["recurso"],
            "contrato": "" if pd.isna(r["contrato"]) else str(r["contrato"]).split(".")[0],
            "tipo": str(r["tipo"]).upper() if r["tipo"] else "",
            "cidade": r["cidade"],
            "status": r["status_atividade"],
            "intensidade": round(intensidade, 3),
        })

    return {
        "titulo": "TEMPO DE EXECUÇÃO",
        "linhas": linhas,
        "total": total,
        "exibidas": len(linhas),
    }


# --------------------------------------------------------------------------
# 4. CARGA DE SERVICOS
# --------------------------------------------------------------------------
# Ordem das linhas, igual a da planilha
TIPOS_CARGA = [
    "ATIVAÇÃO",
    "MUDANÇA DE ENDEREÇO",
    "MUDANÇA DE CÔMODO",
    "REPARO CORRETIVO",
    "REPARO PREVENTIVO",
    "UPGRADE/DOWNGRADE",
    "CLEAN UP - CASA CLIENTE",
]
# Servicos que ja sao "alta": o gap vale 1 para 1, sem conversao
TIPOS_ALTA = {normalizar("ATIVAÇÃO"), normalizar("MUDANÇA DE ENDEREÇO")}

DIAS = {"util": "semana", "sabado": "sabado", "domingo": "domingo"}
ROTULO_DIA = {"util": "DIA UTIL", "sabado": "SABADO", "domingo": "DOMINGO"}


def tipo_de_dia(data) -> str:
    """Segunda a sexta = util; sabado e domingo pelo proprio dia."""
    if data is None or pd.isna(data):
        return "util"
    return {5: "sabado", 6: "domingo"}.get(pd.Timestamp(data).weekday(), "util")


def carga_servicos(df: pd.DataFrame, config: dict | None = None,
                   dia: str | None = None) -> dict:
    from .parametros import carregar as carregar_parametros

    cfg = config or carregar_config()
    par = carregar_parametros()
    fator = float(cfg.get("fator_alta", 0.4))

    # cancelado e apenas o registro de que um servico deixou de ser prestado -
    # nao e carga de trabalho do dia, entao nao entra na contagem (nem no
    # gap/over nem no $ alta que derivam dela).
    df = df[df["status_norm"] != CANCELADO]

    # tipo de dia: o que veio na linha de comando manda; depois o painel.json;
    # "auto" (ou vazio) cai no dia da semana da propria extracao
    if not dia:
        dia = str(cfg.get("tipo_dia") or "auto").strip().lower()
    if dia not in DIAS:
        dia = tipo_de_dia(df.attrs.get("data_referencia"))
    coluna_meta = DIAS[dia]

    participantes = [normalizar(c) for c in
                     (cfg.get("carga_clusters") or ["CGT", "VPA", "RIO"])]
    zeradas = {normalizar(k) for k in (cfg.get("carga_agenda_zerada") or [])}

    # servico desativado nao vira linha aqui - preparar() ja tirou essas
    # atividades do df, mas a lista de tipos e fixa e precisa do mesmo filtro,
    # senao a linha aparece com AGENDA da BASE e CARGA sempre zero.
    servicos_fora = {normalizar(s) for s in (cfg.get("servicos_excluidos") or [])}
    tipos_carga = [t for t in TIPOS_CARGA if normalizar(t) not in servicos_fora]

    def agenda(cluster: str, tipo: str) -> float:
        chave = normalizar(f"{cluster}_{tipo}")
        if chave in zeradas:
            return 0.0
        reg = par.meta_carga.get(chave)
        if not reg:
            return 0.0
        return float(reg.get(coluna_meta) or 0)

    def alta(gap: float, tipo: str) -> float:
        if normalizar(tipo) in TIPOS_ALTA:
            return gap
        return gap / fator if gap < 0 else gap * fator

    # --- uma coluna por cluster participante ---
    colunas = []
    for cluster in participantes:
        recorte = df[df["cluster"] == cluster]
        linhas = []
        for tipo in tipos_carga:
            meta = agenda(cluster, tipo)
            carga = int((recorte["tipo_norm"] == normalizar(tipo)).sum())
            gap = carga - meta
            linhas.append({"agenda": meta, "carga": carga, "gap": gap,
                           "alta": alta(gap, tipo)})
        mo = efetivo_configurado(cfg, cluster, int(recorte["valida_mo"].sum()))
        colunas.append({"cluster": cluster, "linhas": linhas, "tecnicos": mo})

    # --- OPERACIONAL: soma de todos os participantes, mesmo os que nao aparecem ---
    linhas_operacional = []
    for i, tipo in enumerate(tipos_carga):
        meta = sum(c["linhas"][i]["agenda"] for c in colunas)
        carga = sum(c["linhas"][i]["carga"] for c in colunas)
        gap = carga - meta
        linhas_operacional.append({"agenda": meta, "carga": carga, "gap": gap,
                              "alta": alta(gap, tipo)})
    operacional = {"cluster": "OPERACIONAL", "linhas": linhas_operacional, "total": True,
              "tecnicos": efetivo_configurado(cfg, "OPERACIONAL",
                                              sum(c["tecnicos"] for c in colunas))}

    # totais e media por tecnico
    for coluna in colunas + [operacional]:
        coluna["totais"] = {
            chave: sum(l[chave] for l in coluna["linhas"])
            for chave in ("agenda", "carga", "gap", "alta")
        }
        tec = coluna["tecnicos"]
        coluna["media_tecnico"] = (coluna["totais"]["carga"] / tec) if tec else None

    # cluster sem nenhuma atividade nao vira coluna, mas continua somando no OPERACIONAL
    if cfg.get("carga_ocultar_sem_atividade", True):
        visiveis = [c for c in colunas if c["totais"]["carga"] > 0]
    else:
        visiveis = colunas

    return {
        "titulo": "CARGA DE SERVIÇOS",
        "dia": dia,
        "rotulo_dia": ROTULO_DIA.get(dia, "DIA UTIL"),
        "tipos": tipos_carga,
        "colunas": visiveis + [operacional],
        "ocultos": [c["cluster"] for c in colunas if c not in visiveis],
    }
