"""Identidade visual do painel - cores e CSS extraidos do proprio arquivo Excel."""

from __future__ import annotations

# Cores lidas do tema e das celulas do NOVO PAINEL.xlsx
AZUL_ESCURO = "#074F6A"   # cabecalho de tabela, linha OPERACIONAL / Total Geral
AZUL_MEDIO = "#729FB3"    # linha de agrupamento (cluster)
AZUL_GRUPO = "#156082"    # faixa de cluster e rotulos na CARGA DE SERVICOS
AZUL_BORDA = "#9BB9C6"    # bordas finas
AMARELO = "#FFFF00"       # seletor de tipo de dia
CINZA_CLARO = "#F2F2F2"   # coluna CARGA
CINZA_MEDIO = "#D8D8D8"   # coluna $ Alta
TEXTO = "#000000"
VERMELHO = "#FF0000"      # regra "TEMPO VIDA > 1 dia"
ESCALA_FRIA = "#FCFCFF"   # inicio da escala de cor do TEMPO
ESCALA_QUENTE = "#F8696B"  # fim da escala de cor do TEMPO
BRANCO = "#FFFFFF"

# Aptos Narrow e a fonte do arquivo; Calibri e o fallback fiel quando nao instalada.
FONTE = '"Aptos Narrow", "Aptos", Calibri, "Segoe UI", Arial, sans-serif'


def largura_px(largura_excel: float) -> int:
    """Converte largura de coluna do Excel (em caracteres) para pixels."""
    return round(largura_excel * 7 + 5)


CSS = f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: {FONTE};
  font-size: 14.7px;              /* 11pt */
  color: {TEXTO};
  background: {BRANCO};
  -webkit-font-smoothing: antialiased;
}}

.painel {{
  display: inline-block;
  background: {BRANCO};
  padding: 10px 12px 14px 12px;
}}

/* ---------- faixa de titulo ---------- */
.cabecalho {{
  display: flex;
  align-items: center;
  border: 2px solid {TEXTO};
  height: 52px;
  background: {BRANCO};
  margin-bottom: 14px;
}}
.cabecalho .logo {{
  flex: 0 0 auto;
  padding: 0 14px;
  display: flex;
  align-items: center;
}}
.cabecalho .logo img {{ height: 40px; display: block; }}
.cabecalho .titulo {{
  flex: 1 1 auto;
  text-align: center;
  font-weight: bold;
  font-size: 18.7px;              /* 14pt */
  letter-spacing: .2px;
}}
.cabecalho .quando {{
  flex: 0 0 auto;
  padding: 0 14px;
  text-align: center;
  line-height: 1.35;
  font-size: 14.7px;
  min-width: 108px;
}}

/* ---------- tabelas ---------- */
table {{
  border-collapse: collapse;
  background: {BRANCO};
}}
th, td {{
  border: 1px solid {AZUL_BORDA};
  height: 24px;
  padding: 0 6px;
  text-align: center;
  white-space: nowrap;
  vertical-align: middle;
}}
thead th {{
  background: {AZUL_ESCURO};
  color: {BRANCO};
  font-weight: bold;
  border-color: {AZUL_ESCURO};
}}
tr.cluster > td {{
  background: {AZUL_MEDIO};
  color: {BRANCO};
  font-weight: bold;
  border-color: {AZUL_MEDIO};
}}
tr.total > td {{
  background: {AZUL_ESCURO};
  color: {BRANCO};
  font-weight: bold;
  border-color: {AZUL_ESCURO};
}}
tr.subtotal > td {{ font-weight: bold; }}
td.vermelho {{ color: {VERMELHO}; }}
td.esquerda {{ text-align: left; }}

/* ---------- bloco superior da capa ---------- */
.topo {{
  display: flex;
  align-items: flex-start;
  gap: 60px;
  margin-bottom: 16px;
}}

/* ---------- grafico REALIZADO ---------- */
.grafico {{ padding-top: 2px; }}
.grafico .rotulo {{
  text-align: center;
  font-weight: bold;
  font-size: 14.7px;
  margin-bottom: 6px;
}}
.grafico .barras {{
  display: flex;
  align-items: flex-end;
  gap: 34px;
  height: 76px;
  padding: 0 18px;
}}
.grafico .coluna {{
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: center;
  height: 100%;
  width: 62px;
}}
.grafico .valor {{ font-size: 12px; margin-bottom: 3px; white-space: nowrap; }}
.grafico .barra {{ width: 100%; background: {AZUL_ESCURO}; }}
.grafico .eixo {{
  border-top: 1px solid {AZUL_BORDA};
  display: flex;
  gap: 34px;
  padding: 3px 18px 0 18px;
}}
.grafico .eixo span {{ width: 62px; text-align: center; font-size: 12px; }}

.rodape {{
  margin-top: 8px;
  font-size: 12px;
  color: #5A6B76;
}}

/* ---------- carga de servicos ---------- */
.seletor-dia {{
  display: inline-block;
  background: {AMARELO};
  border: 1px solid {TEXTO};
  font-weight: bold;
  text-decoration: underline;
  padding: 2px 26px 2px 10px;
  margin-bottom: 6px;
  position: relative;
}}
.seletor-dia::after {{
  content: "";
  position: absolute;
  right: 8px; top: 50%;
  margin-top: -2px;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid {TEXTO};
}}

table.carga th.grupo {{
  background: {AZUL_GRUPO};
  border-color: {AZUL_GRUPO};
  font-size: 14.7px;
}}
table.carga th.sub, table.carga td.rotulo-total {{
  background: {AZUL_MEDIO};
  color: {BRANCO};
  border-color: {AZUL_MEDIO};
  font-weight: bold;
}}
table.carga td.rotulo {{
  background: {AZUL_GRUPO};
  color: {BRANCO};
  font-weight: bold;
  border-color: {AZUL_GRUPO};
  text-align: left;
  padding-left: 8px;
}}
table.carga td.carga {{ background: {CINZA_CLARO}; }}
table.carga td.alta {{ background: {CINZA_MEDIO}; }}
table.carga tr.linha-total > td {{
  background: {AZUL_MEDIO};
  color: {BRANCO};
  font-weight: bold;
  border-color: {AZUL_MEDIO};
}}
table.carga td.grupo-inicio {{ border-left: 2px solid {AZUL_GRUPO}; }}
table.carga td.grupo-fim {{ border-right: 2px solid {AZUL_GRUPO}; }}
table.carga th.grupo-inicio {{ border-left: 2px solid {AZUL_GRUPO}; }}
table.carga th.grupo-fim {{ border-right: 2px solid {AZUL_GRUPO}; }}
table.carga td.indicador {{ background: {CINZA_CLARO}; }}
"""
