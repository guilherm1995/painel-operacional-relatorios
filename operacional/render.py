"""Monta o HTML dos relatorios e grava as imagens PNG.

A renderizacao usa o Chromium do Playwright: o mesmo HTML/CSS vira uma imagem
identica em qualquer maquina, sem depender do Excel instalado.
"""

from __future__ import annotations

import base64
import datetime as dt
import time
from html import escape
from pathlib import Path

from .estilo import (AZUL_ESCURO, CSS, ESCALA_FRIA, ESCALA_QUENTE, largura_px)
from .relatorios import inteiro, num, pct

# --------------------------------------------------------------------------
# 4. CARGA DE SERVICOS
# --------------------------------------------------------------------------
COLUNAS_CARGA = ["AGENDA", "CARGA", "GAP/OVER", "$ Alta"]
LARGURAS_CARGA = [7.8, 9.5, 12.0, 8.3]

RAIZ = Path(__file__).resolve().parent.parent
LOGO = RAIZ / "assets" / "logo_operacional.png"


def _logo_data_uri() -> str:
    if not LOGO.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")


def _mistura(cor_a: str, cor_b: str, t: float) -> str:
    """Interpola duas cores hex - reproduz a escala de cor do Excel."""
    a = [int(cor_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(cor_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _cabecalho(titulo: str, momento: dt.datetime) -> str:
    logo = _logo_data_uri()
    img = f'<img src="{logo}" alt="OPERACIONAL TELECOM">' if logo else "OPERACIONAL"
    return f"""
    <div class="cabecalho">
      <div class="logo">{img}</div>
      <div class="titulo">{escape(titulo)}</div>
      <div class="quando">{momento.strftime('%d/%m/%Y')}<br>{momento.strftime('%H:%M:%S')}</div>
    </div>"""


def _pagina(conteudo: str, largura: int | None = None) -> str:
    estilo_largura = f" style=\"width:{largura}px\"" if largura else ""
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="painel" id="painel"{estilo_largura}>{conteudo}</div></body></html>"""


def _colgroup(larguras_excel: list[float]) -> str:
    cols = "".join(f'<col style="width:{largura_px(w)}px">' for w in larguras_excel)
    return f"<colgroup>{cols}</colgroup>"


# --------------------------------------------------------------------------
# 1. PAINEL DE ACOMPANHAMENTO
# --------------------------------------------------------------------------
def html_capa(dados: dict, momento: dt.datetime) -> str:
    larguras_prod = [24.7, 15.7, 17.7, 16.5]
    linhas = []
    for l in dados["produtividade"]:
        classe = "total" if l.get("total") else "cluster"
        linhas.append(
            f'<tr class="{classe}"><td>{escape(l["cluster"])}</td>'
            f'<td>{inteiro(l["mo_ativa"])}</td>'
            f'<td>{num(l["meta_prod"])}</td>'
            f'<td>{num(l["prod_real"])}</td></tr>'
        )
    tabela_prod = f"""
    <table>{_colgroup(larguras_prod)}
      <thead><tr><th>CLUSTER</th><th>M.O ATIVA</th><th>META PROD $</th><th>PROD REAL</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody>
    </table>"""

    # grafico REALIZADO
    valores = dados["grafico"]
    maximo = max([abs(v["valor"]) for v in valores] + [0.01])
    colunas, eixo = [], []
    for v in valores:
        altura = max(round(abs(v["valor"]) / maximo * 52), 0)
        colunas.append(
            f'<div class="coluna"><div class="valor">{pct(v["valor"])}</div>'
            f'<div class="barra" style="height:{altura}px"></div></div>'
        )
        eixo.append(f'<span>{escape(v["cluster"])}</span>')
    grafico = f"""
    <div class="grafico">
      <div class="rotulo">REALIZADO</div>
      <div class="barras">{''.join(colunas)}</div>
      <div class="eixo">{''.join(eixo)}</div>
    </div>"""

    # tabela de status
    larguras_status = [24.7, 15.7, 17.7, 16.5, 22.8, 12.7, 16.3]
    linhas = []
    for l in dados["status"]:
        classe = {"cluster": "cluster", "total": "subtotal"}.get(l["nivel"], "")
        casas = 0 if l["nivel"] == "cluster" else 2
        linhas.append(
            f'<tr class="{classe}"><td>{escape(str(l["rotulo"]))}</td>'
            f'<td>{inteiro(l["infor"])}</td>'
            f'<td>{inteiro(l["pend"])}</td>'
            f'<td>{inteiro(l["ok"])}</td>'
            f'<td>{pct(l["efetividade"], casas)}</td>'
            f'<td>{pct(l["sla24"]) if l["sla24"] is not None else ""}</td>'
            f'<td>{pct(l["aging"]) if l["aging"] is not None else ""}</td></tr>'
        )
    tabela_status = f"""
    <table>{_colgroup(larguras_status)}
      <thead><tr><th></th><th>INFOR</th><th>PEND</th><th>OK</th>
        <th>EFETIVIDADE</th><th>24 HS</th><th>AGING</th></tr></thead>
      <tbody>{''.join(linhas)}</tbody>
    </table>"""

    conteudo = (_cabecalho(dados["titulo"], momento)
                + f'<div class="topo">{tabela_prod}{grafico}</div>'
                + tabela_status)
    return _pagina(conteudo)


# --------------------------------------------------------------------------
# 2. GESTAO DE PRAZOS
# --------------------------------------------------------------------------
def html_prazos(dados: dict, momento: dt.datetime) -> str:
    larguras = [11.3, 32.3, 7.5, 14.7, 12.5, 9.7, 38.3, 11.7, 46.7, 24.5, 3.7]
    cabecalhos = ["HORA", "TEMPO VIDA", "ROTA", "ABERTURA", "STATUS", "CLUSTER",
                  "Recurso", "contrato", "CLEINTE", "Cidade", "UN"]

    linhas = []
    for l in dados["linhas"]:
        alerta = ' class="vermelho"' if l["tempo_vida_alerta"] else ""
        linhas.append(
            f'<tr><td>{escape(l["hora"])}</td>'
            f'<td{alerta}>{escape(l["tempo_vida"])}</td>'
            f'<td>{escape(l["rota"])}</td>'
            f'<td>{escape(l["abertura"])}</td>'
            f'<td>{escape(str(l["status"]))}</td>'
            f'<td>{escape(str(l["cluster"]))}</td>'
            f'<td>{escape(str(l["recurso"]))}</td>'
            f'<td>{escape(l["contrato"])}</td>'
            f'<td>{escape(str(l["cliente"]))}</td>'
            f'<td>{escape(str(l["cidade"]))}</td>'
            f'<td>{l["un"]}</td></tr>'
        )
    if not linhas:
        linhas.append(f'<tr><td colspan="{len(cabecalhos)}">Nenhum reparo corretivo em aberto.</td></tr>')

    rodape = ""
    if dados["exibidas"] < dados["total"]:
        rodape = (f'<div class="rodape">Exibindo {dados["exibidas"]} de {dados["total"]} '
                  f'reparos corretivos, do mais antigo para o mais recente.</div>')

    ths = "".join(f"<th>{escape(h)}</th>" for h in cabecalhos)
    conteudo = (_cabecalho(dados["titulo"], momento)
                + f'<table>{_colgroup(larguras)}<thead><tr>{ths}</tr></thead>'
                  f'<tbody>{"".join(linhas)}</tbody></table>' + rodape)
    return _pagina(conteudo)


# --------------------------------------------------------------------------
# 3. TEMPO DE EXECUCAO
# --------------------------------------------------------------------------
def html_tempo(dados: dict, momento: dt.datetime) -> str:
    larguras = [18.5, 9.7, 12.7, 35.0, 21.7, 22.2, 28.2, 20.5]
    cabecalhos = ["TEMPO", "HR INÍCIO", "CLUSTER", "TÉCNICO", "CONTRATO",
                  "TIPO", "Cidade", "STATUS"]

    linhas = []
    for l in dados["linhas"]:
        fundo = ""
        if l["intensidade"] > 0:
            fundo = f' style="background:{_mistura(ESCALA_FRIA, ESCALA_QUENTE, l["intensidade"])}"'
        linhas.append(
            f'<tr><td{fundo}>{escape(l["tempo"])}</td>'
            f'<td>{escape(l["hr_inicio"])}</td>'
            f'<td>{escape(str(l["cluster"]))}</td>'
            f'<td>{escape(str(l["tecnico"]))}</td>'
            f'<td>{escape(l["contrato"])}</td>'
            f'<td>{escape(l["tipo"])}</td>'
            f'<td>{escape(str(l["cidade"]))}</td>'
            f'<td>{escape(str(l["status"]))}</td></tr>'
        )
    if not linhas:
        linhas.append(f'<tr><td colspan="{len(cabecalhos)}">Nenhuma atividade iniciada no momento.</td></tr>')

    rodape = ""
    if dados["exibidas"] < dados["total"]:
        rodape = (f'<div class="rodape">Exibindo {dados["exibidas"]} de {dados["total"]} '
                  f'atividades iniciadas, da mais demorada para a mais recente.</div>')

    ths = "".join(f"<th>{escape(h)}</th>" for h in cabecalhos)
    conteudo = (_cabecalho(dados["titulo"], momento)
                + f'<table>{_colgroup(larguras)}<thead><tr>{ths}</tr></thead>'
                  f'<tbody>{"".join(linhas)}</tbody></table>' + rodape)
    return _pagina(conteudo)


def html_carga(dados: dict, momento: dt.datetime) -> str:
    colunas = dados["colunas"]

    def celula(valor, classe: str, casas: int = 0) -> str:
        return f'<td class="{classe}">{num(valor, casas)}</td>'

    # colgroup: rotulo + 4 colunas por cluster
    cols = [f'<col style="width:{largura_px(22.7)}px">']
    for _ in colunas:
        cols += [f'<col style="width:{largura_px(w)}px">' for w in LARGURAS_CARGA]
    colgroup = f'<colgroup>{"".join(cols)}</colgroup>'

    # duas faixas de cabecalho: nome do cluster e as 4 metricas
    grupos = '<th class="sub"></th>'
    sub = '<th class="sub">TIPO</th>'
    for c in colunas:
        grupos += f'<th class="grupo grupo-inicio grupo-fim" colspan="4">{escape(c["cluster"])}</th>'
        for i, titulo in enumerate(COLUNAS_CARGA):
            borda = " grupo-inicio" if i == 0 else (" grupo-fim" if i == 3 else "")
            sub += f'<th class="sub{borda}">{escape(titulo)}</th>'

    # uma linha por tipo de servico
    corpo = []
    for i, tipo in enumerate(dados["tipos"]):
        linha = f'<td class="rotulo">{escape(tipo)}</td>'
        for c in colunas:
            l = c["linhas"][i]
            linha += celula(l["agenda"], "grupo-inicio")
            linha += celula(l["carga"], "carga")
            linha += celula(l["gap"], "")
            linha += celula(l["alta"], "alta grupo-fim")
        corpo.append(f"<tr>{linha}</tr>")

    # TOTAL
    linha = '<td class="rotulo-total">TOTAL</td>'
    for c in colunas:
        t = c["totais"]
        linha += (f'<td class="grupo-inicio">{num(t["agenda"], 0)}</td>'
                  f'<td>{num(t["carga"], 0)}</td>'
                  f'<td>{num(t["gap"], 0)}</td>'
                  f'<td class="grupo-fim">{num(t["alta"], 0)}</td>')
    corpo.append(f'<tr class="linha-total">{linha}</tr>')

    # TECNICOS ATIVOS e MEDIA POR TECNICO ocupam as 4 colunas de cada cluster
    linha = '<td class="rotulo">TÉCNICOS ATIVOS</td>'
    for c in colunas:
        linha += f'<td class="indicador grupo-inicio grupo-fim" colspan="4">{inteiro(c["tecnicos"])}</td>'
    corpo.append(f"<tr>{linha}</tr>")

    linha = '<td class="rotulo">MÉDIA POR TÉCNICO</td>'
    for c in colunas:
        media = "" if c["media_tecnico"] is None else num(c["media_tecnico"], 2)
        linha += f'<td class="indicador grupo-inicio grupo-fim" colspan="4">{media}</td>'
    corpo.append(f"<tr>{linha}</tr>")

    rodape = ""
    if dados["ocultos"]:
        rodape = (f'<div class="rodape">Sem atividade hoje, somado apenas no OPERACIONAL: '
                  f'{", ".join(dados["ocultos"])}.</div>')

    conteudo = (_cabecalho(dados["titulo"], momento)
                + f'<div class="seletor-dia">{escape(dados["rotulo_dia"])}</div>'
                + f'<table class="carga">{colgroup}'
                  f'<thead><tr>{grupos}</tr><tr>{sub}</tr></thead>'
                  f'<tbody>{"".join(corpo)}</tbody></table>' + rodape)
    return _pagina(conteudo)


# --------------------------------------------------------------------------
# renderizacao
# --------------------------------------------------------------------------
def gravar_imagens(paginas: dict[str, str], pasta: str | Path, escala: int = 2) -> list[Path]:
    """Renderiza {nome: html} em PNG. Devolve os caminhos gravados.

    O lancamento do Chromium tenta de novo uma vez se a maquina estiver sob
    pressao momentanea de RAM/CPU (ex: bot e site abrindo Chromium ao mesmo
    tempo) - nesse caso o driver falha ao abrir, mas geralmente funciona
    alguns segundos depois, quando o pico passa.
    """
    from playwright.sync_api import sync_playwright

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    gerados: list[Path] = []

    with sync_playwright() as p:
        navegador = None
        ultimo_erro = None
        for tentativa in range(1, 3):
            try:
                navegador = p.chromium.launch()
                break
            except Exception as erro:
                ultimo_erro = erro
                if tentativa < 2:
                    time.sleep(5)
        if navegador is None:
            raise ultimo_erro
        pagina = navegador.new_page(device_scale_factor=escala)
        for nome, html in paginas.items():
            destino = pasta / f"{nome}.png"
            pagina.set_content(html, wait_until="load")
            pagina.locator("#painel").screenshot(path=str(destino))
            gerados.append(destino)
        navegador.close()
    return gerados
