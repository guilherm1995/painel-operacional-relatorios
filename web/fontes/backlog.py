"""Le o que o bot de monitoramento produz: backlog, estatisticas e log.

O bot roda na mesma maquina e grava em disco. Aqui so lemos - nada e alterado
na pasta dele.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from ..config import CONFIG

# Nomes de arquivo do bot: backlog_<categoria>_<subtipo>_<regiao>.png
REGIOES = {
    "litoral_norte": "Litoral Norte SP",
    "sul_rj": "Sul RJ",
}

UNIDADES = {
    "CGT": "Caraguatatuba", "BASE": "Caraguatatuba 1", "SST": "São Sebastião",
    "SSTBO": "Boiçucanga", "IBL": "Ilhabela", "BERT": "Bertioga",
    "RSD": "Resende", "MPE": "Miguel Pereira", "VAS": "Vassouras",
    "VRD": "Volta Redonda", "PNDO": "Penedo", "VLC": "Valença",
    "IZA": "Itatiaia", "TRS": "Três Rios", "BMA": "Barra Mansa",
    "PORE": "Porto Real", "COLG": "Comendador Levy Gasparian",
    "BPI": "Barra do Piraí", "PFS": "Paty do Alferes",
    "PDS": "Paraíba do Sul", "PNHE": "Pinheiral",
}

LITORAL = {"CGT", "BASE", "SST", "SSTBO", "IBL", "BERT"}

# Minutos de tolerancia para o CAPEX pendente ainda ser considerado ao vivo.
# O bot varre a cada ~25s, entao qualquer coisa acima de alguns minutos ja
# significa que ele nao esta escrevendo - nao que o CAPEX caiu para zero.
MINUTOS_CAPEX_CONFIAVEL = 10

# Idade maxima do OFS GERAL para o cruzamento do "Enviado D0" valer. Tem que
# ser o MESMO numero de MAX_IDADE_OFS_HORAS em backlog_ofs.py, no bot: se os
# dois discordarem, a tela diz que esta tudo bem enquanto o bot ignora o
# arquivo (ou o contrario), e ninguem entende por que o D0 nao mudou.
HORAS_OFS_CONFIAVEL = 24

# Janela em que um pedido de backlog barra os seguintes. Uma rodada completa
# leva ~80s (4 tipos x 2 regioes) e a ponte do bot NAO recusa pedido repetido:
# em 05/08/2026 tres cliques viraram tres geracoes simultaneas e o grupo recebeu
# as mesmas imagens em triplicata (22 imagens em 3min30). Enquanto a trava nao
# existir do lado do bot, e aqui que o clique repetido para.
MINUTOS_ENTRE_PEDIDOS = 5

# Quando o ultimo pedido saiu daqui. Vale so para este processo do site, que e
# unico (uvicorn sem workers extras, ver iniciar_site.py).
_ultimo_pedido: dt.datetime | None = None

# O bot nomeia o arquivo como <grupo do comando>_<categoria dentro do grupo>,
# mas a convenção já mudou ao longo do tempo: hoje CAPEX grava "capex_ativacao",
# arquivos antigos gravavam só "ativacao" (sem grupo). O card mostra sempre só o
# tipo de serviço, nunca o grupo/comando - a região já aparece no título da
# seção acima, e repetir "CAPEX" ali só criava um card diferente para o mesmo
# assunto conforme a versão do bot que gerou o arquivo.
GRUPOS_BACKLOG = ("capex", "reparo", "upgrade", "mudanca_comodo")


def _assunto_legivel(bruto: str) -> str:
    """<grupo>_<categoria> (ou só <categoria>, em nomes antigos) -> tipo de serviço."""
    categoria = bruto
    for grupo in GRUPOS_BACKLOG:
        if bruto == grupo:
            categoria = grupo
            break
        if bruto.startswith(grupo + "_"):
            categoria = bruto[len(grupo) + 1:]
            break
    return categoria.replace("_", " ").upper() or "GERAL"


def _ler_json(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return padrao


def _idade(caminho: Path) -> dict:
    """Data de modificacao do arquivo, ja formatada e com o quanto envelheceu."""
    try:
        ts = dt.datetime.fromtimestamp(caminho.stat().st_mtime)
    except OSError:
        return {"quando": "", "minutos": None, "velho": True}
    minutos = (dt.datetime.now() - ts).total_seconds() / 60
    return {
        "quando": ts.strftime("%d/%m/%Y %H:%M"),
        "minutos": round(minutos),
        "velho": minutos > 180,
    }


def imagens() -> list[dict]:
    """Imagens de backlog geradas pelo bot, agrupadas por assunto.

    O bot já teve um bug (corrigido no código-fonte dele) que gerava nomes
    de arquivo extras para reparo/upgrade/mudança de cômodo. Os arquivos
    antigos continuam no disco e, sem tratamento, viravam cards duplicados
    ou com título estranho ("REPARO UPGRADE"). Por assunto+região, fica só
    o arquivo mais recente.
    """
    pasta = CONFIG.relatorios_bot
    if not pasta.exists():
        return []

    candidatos: dict[tuple[str, str], dict] = {}
    for arquivo in sorted(pasta.glob("backlog_*.png")):
        nome = arquivo.stem
        regiao_chave = next((k for k in REGIOES if nome.endswith(k)), None)
        bruto = nome[len("backlog_"):]
        if regiao_chave:
            bruto = bruto[: -len(regiao_chave) - 1]
        regiao = REGIOES.get(regiao_chave, "Geral")
        assunto = _assunto_legivel(bruto)
        mtime = arquivo.stat().st_mtime

        chave = (assunto, regiao)
        atual = candidatos.get(chave)
        if atual is not None and atual["_mtime"] >= mtime:
            continue
        candidatos[chave] = {
            "arquivo": arquivo.name,
            "assunto": assunto,
            "regiao": regiao,
            "_mtime": mtime,
            **_idade(arquivo),
        }

    # "Geral" (arquivo sem região no nome) é resíduo de antes do bot separar
    # por região - some quando já existe a versão regional do mesmo assunto.
    assuntos_com_regiao = {a for a, r in candidatos if r != "Geral"}
    candidatos = {
        (a, r): item for (a, r), item in candidatos.items()
        if r != "Geral" or a not in assuntos_com_regiao
    }

    saida = [{k: v for k, v in item.items() if k != "_mtime"} for item in candidatos.values()]
    saida.sort(key=lambda i: (i["assunto"], i["regiao"]))
    return saida


def caminho_imagem(nome: str) -> Path | None:
    """Resolve o nome de uma imagem dentro da pasta do bot, sem deixar escapar dela."""
    if not re.fullmatch(r"[\w.\-]+\.png", nome or ""):
        return None
    alvo = (CONFIG.relatorios_bot / nome).resolve()
    try:
        alvo.relative_to(CONFIG.relatorios_bot.resolve())
    except ValueError:
        return None
    return alvo if alvo.is_file() else None


def estatisticas() -> dict:
    """Contadores do dia que o bot mantem em dados/estatisticas_status.json."""
    arquivo = CONFIG.dados_bot / "estatisticas_status.json"
    dados = _ler_json(arquivo, {})
    if not dados:
        return {"disponivel": False}

    capex_rj = int(dados.get("capex_pendente_sul_rj") or 0)
    capex_sp = int(dados.get("capex_pendente_litoral_sp") or 0)
    idade = _idade(arquivo)

    # O bot regrava este arquivo a cada varredura (~25s). Se ele parou de mexer
    # ha varios minutos, o numero de CAPEX na tela nao vale mais: ou o bot caiu,
    # ou o laco de monitoramento travou. Nesses casos a tela passa a dizer que
    # nao sabemos, em vez de exibir um numero velho com cara de numero novo.
    minutos = idade.get("minutos")
    capex_fresco = minutos is not None and minutos <= MINUTOS_CAPEX_CONFIAVEL

    return {
        "disponivel": True,
        "data_referencia": dados.get("data_referencia", ""),
        "capex_pendente_sul_rj": capex_rj,
        "capex_pendente_litoral_sp": capex_sp,
        "capex_pendente_total": capex_rj + capex_sp,
        "capex_fresco": capex_fresco,
        "capex_notificadas_hoje": int(dados.get("capex_notificadas_hoje") or 0),
        "garantias_notificadas_hoje": int(dados.get("garantias_notificadas_hoje") or 0),
        "erros_log_hoje": int(dados.get("erros_log_hoje") or 0),
        "os_analisadas_hoje": int(dados.get("os_analisadas_hoje") or 0),
        **idade,
    }


def status_ofs_do_backlog() -> dict:
    """Situacao do OFS GERAL do ponto de vista do "Enviado D0" do backlog.

    O bot so cruza o backlog com o OFS quando ele tem menos de
    HORAS_OFS_CONFIAVEL horas; acima disso cai na regra antiga (so o
    agendamento do CAMPO). Esta funcao devolve exatamente esse veredito para a
    tela dizer a mesma coisa que o bot esta fazendo.
    """
    caminho = CONFIG.localizar_dado("OFS GERAL.csv")
    if caminho is None:
        return {"existe": False, "vale": False, "horas": None, "quando": ""}

    try:
        ts = dt.datetime.fromtimestamp(caminho.stat().st_mtime)
    except OSError:
        return {"existe": False, "vale": False, "horas": None, "quando": ""}

    horas = (dt.datetime.now() - ts).total_seconds() / 3600
    return {
        "existe": True,
        "vale": horas <= HORAS_OFS_CONFIAVEL,
        "horas": round(horas, 1),
        "quando": ts.strftime("%d/%m/%Y %H:%M"),
        "limite_horas": HORAS_OFS_CONFIAVEL,
    }


def entrantes() -> dict:
    """Entrantes CAPEX de hoje por unidade, mais a serie dos ultimos dias."""
    hoje = _ler_json(CONFIG.dados_bot / "entrantes_capex_hoje.json", {})
    historico = _ler_json(CONFIG.dados_bot / "historico_entrantes_capex.json", {})

    por_unidade = hoje.get("por_unidade") or {}
    linhas = [{
        "sigla": sigla,
        "nome": UNIDADES.get(sigla, sigla),
        "regiao": "Litoral Norte SP" if sigla in LITORAL else "Sul RJ",
        "quantidade": int(qtd or 0),
    } for sigla, qtd in por_unidade.items()]
    linhas.sort(key=lambda l: -l["quantidade"])

    serie = [{"data": data, "total": sum(int(v or 0) for v in unidades.values())}
             for data, unidades in sorted(historico.items())][-14:]

    return {
        "data_referencia": hoje.get("data_referencia", ""),
        "linhas": linhas,
        "total": sum(l["quantidade"] for l in linhas),
        "serie": serie,
        "maximo_serie": max([s["total"] for s in serie], default=0),
    }


def log(linhas: int = 120) -> list[dict]:
    """Ultimas linhas do log do bot, com o nivel destacado."""
    caminho = CONFIG.log_bot
    if not caminho.exists():
        return []
    try:
        with caminho.open("r", encoding="utf-8", errors="replace") as fh:
            ultimas = fh.readlines()[-linhas:]
    except OSError:
        return []

    saida = []
    for linha in ultimas:
        texto = linha.rstrip()
        if not texto:
            continue
        nivel = "info"
        if re.search(r"\b(ERROR|CRITICAL|ERRO)\b", texto):
            nivel = "erro"
        elif re.search(r"\bWARNING\b|AVISO", texto):
            nivel = "aviso"
        elif re.search(r"\bDEBUG\b", texto):
            nivel = "debug"
        saida.append({"texto": texto, "nivel": nivel})
    return saida


def bot_ativo() -> dict:
    """O bot esta rodando agora? Usa o lock e a idade do log como indicio."""
    lock = CONFIG.pasta_bot / "logs" / "monitor_campo.lock"
    info = _idade(CONFIG.log_bot)
    minutos = info.get("minutos")
    rodando = lock.exists() and minutos is not None and minutos < 15
    return {
        "rodando": rodando,
        "lock": lock.exists(),
        "log_em": info.get("quando", ""),
        "log_minutos": minutos,
    }


def pedir_backlog_novo() -> dict:
    """Pede ao bot que gere um backlog agora.

    Só o processo do bot consegue: a lista de chamados fica na memória dele.
    O site apenas dispara o pedido; as imagens aparecem quando ficarem prontas.

    Pedido repetido dentro de MINUTOS_ENTRE_PEDIDOS volta com `repetido`, sem
    chegar à ponte.
    """
    from . import ponte_bot

    global _ultimo_pedido

    agora = dt.datetime.now()
    if _ultimo_pedido is not None:
        faltam = MINUTOS_ENTRE_PEDIDOS - (agora - _ultimo_pedido).total_seconds() / 60
        if faltam > 0:
            return {"ok": False, "repetido": True, "erro": (
                f"Já pedi um backlog às {_ultimo_pedido.strftime('%H:%M')} e o bot ainda "
                f"pode estar gerando. Espere {max(1, round(faltam))} min — pedir de novo "
                "faz o grupo receber as mesmas imagens duplicadas."
            )}

    # Marca ANTES de chamar. A ponte leva alguns segundos para responder e é
    # exatamente nessa janela que o segundo clique passava: em 05/08 os dois
    # primeiros pedidos saíram com 2,7s de diferença.
    _ultimo_pedido = agora
    resultado = ponte_bot.chamar("/gerar-backlog")

    if resultado["ok"]:
        resultado.setdefault("mensagem", "Backlog solicitado. As imagens aparecem em alguns minutos.")
    elif not resultado.get("demorou"):
        # Bot fora do ar não consumiu pedido nenhum: libera para tentar de novo
        # assim que ele voltar. Timeout de leitura, ao contrário, mantém a trava
        # — lá o pedido chegou e uma rodada provavelmente já está correndo.
        _ultimo_pedido = None
    return resultado


def marca_das_imagens() -> str:
    """Assinatura do conjunto de imagens - muda quando o bot gera algo novo.

    Usada pela tela de backlog para perceber que ha material novo sem precisar
    recarregar a pagina inteira o tempo todo.
    """
    pasta = CONFIG.relatorios_bot
    if not pasta.exists():
        return ""
    partes = []
    for arquivo in sorted(pasta.glob("backlog_*.png")):
        try:
            partes.append(f"{arquivo.name}:{int(arquivo.stat().st_mtime)}")
        except OSError:
            continue
    return "|".join(partes)


def resumo() -> dict:
    return {
        "estatisticas": estatisticas(),
        "entrantes": entrantes(),
        "imagens": imagens(),
        "bot": bot_ativo(),
    }
