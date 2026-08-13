"""Registro único das planilhas que o site consome.

Reúne, num lugar só: onde cada arquivo está, como lê-lo (com cache), e como
aceitar um envio pelo site. Assim garantias e confirmação de agenda usam a
mesma mecânica e a tela mostra a idade de cada fonte.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import os
import time
from pathlib import Path

import pandas as pd

from ..config import CONFIG

# nome do arquivo -> o que é, quem usa e como conferir se veio o certo.
# Em "colunas", um item que é lista significa "qualquer um destes serve".
ARQUIVOS: dict[str, dict] = {
    "chamados_abertos_field_service.xlsx": {
        "rotulo": "Chamados abertos",
        "descricao": "os reparos em aberto, exportados do Field Service",
        "paginas": ["garantias"],
        "colunas": ["CÓDIGO CONTRATO", "FILA", "DATA DE INGRESSO"],
    },
    "base OFS ok.xlsx": {
        "rotulo": "Base OFS",
        "descricao": "o histórico de serviços concluídos",
        "paginas": ["garantias"],
        "colunas": [["Número do contrato", "Contrato"], ["Data"],
                    ["Status da Atividade"],
                    ["Tipo de Atividade.1", "Tipo de Atividade 2", "Tipo de Atividade"]],
    },
    "base improdutivas 30 dias.xlsx": {
        "rotulo": "Improdutivas (30 dias)",
        # Arquivo SEPARADO da Base OFS de propósito: aquela é a exportação só
        # de atividades concluídas, e é dela que sai a garantia. Esta aqui é a
        # mesma exportação SEM o filtro de status -- é o único jeito de as
        # improdutivas aparecerem. Misturar as duas seria mexer numa base
        # crítica que já funciona para ganhar um arquivo a menos.
        "descricao": "os últimos 30 dias COM as improdutivas (exportar sem o filtro de status)",
        "paginas": ["garantias"],
        "colunas": [["Motivo de Encerramento das atividades"], ["Data"],
                    ["Número do contrato", "Contrato"], ["Nome"]],
    },
    "OFS GERAL.csv": {
        "rotulo": "OFS Geral",
        # Também alimenta o "Enviado D0" do backlog: o bot cruza o contrato do
        # chamado com os agendados para hoje aqui (ver backlog_ofs.py no bot).
        "descricao": "a agenda de hoje e amanhã, para a confirmação e para o Enviado D0 do backlog",
        "paginas": ["confirmacao", "backlog"],
        "colunas": [["Ordem de Serviço", "ID da Ordem de Serviço"], ["Data"],
                    ["Cidade", "Área de Trabalho", "Chave Workzone"]],
    },
}

# Planilhas que o BOT também lê, de um caminho fixo dentro da pasta dele
# (bot_campo_monitoramento.py: BASE_OFS_ARQUIVO = <pasta do bot>/dados/...).
#
# Por que espelhar: o bot compara o mtime desse arquivo a cada varredura e,
# quando muda, recarrega a base e REAVALIA os reparos pendentes -- é assim que
# um reparo que ainda não era garantia vira garantia e cai no grupo. O site,
# porém, grava os envios em <site>/dados. Sem copiar para lá, atualizar a base
# pelo site só arrumava a tela: o bot continuava avaliando pela base velha e
# nenhuma reavaliação acontecia.
ESPELHADOS_NO_BOT = {"base OFS ok.xlsx", "base improdutivas 30 dias.xlsx"}

# Quantas vezes insistir na troca do arquivo do bot. Ele abre a planilha a cada
# varredura e a leitura leva alguns segundos; cair bem nessa janela dá
# PermissionError, e esperar um pouco resolve.
TENTATIVAS_TROCA = 5
ESPERA_ENTRE_TENTATIVAS = 1.5

_cache: dict[str, tuple[tuple[str, float], pd.DataFrame]] = {}

# sha1 por caminho, para dizer se as duas cópias são a mesma sem reler 1,3 MB a
# cada abertura de tela. Uma entrada por arquivo: só interessa a versão atual.
_resumos: dict[str, tuple[tuple[float, int], str]] = {}


# --------------------------------------------------------------------------
# leitura
# --------------------------------------------------------------------------
def _ler_csv(caminho: Path) -> pd.DataFrame:
    for sep in (",", ";"):
        for enc in ("utf-8", "utf-8-sig", "latin1"):
            try:
                df = pd.read_csv(caminho, sep=sep, encoding=enc, on_bad_lines="skip")
                if len(df.columns) > 3:
                    return df
            except Exception:
                continue
    return pd.DataFrame()


def carregar(nome: str) -> pd.DataFrame:
    """Lê uma planilha, reaproveitando enquanto o arquivo não mudar.

    São arquivos de alguns MB e a leitura leva segundos. O cache é invalidado
    sozinho pela data de modificação, então atualizar o arquivo já reflete aqui.
    """
    caminho = CONFIG.localizar_dado(nome)
    if caminho is None:
        return pd.DataFrame()

    try:
        assinatura = (str(caminho), caminho.stat().st_mtime)
    except OSError:
        return pd.DataFrame()

    guardado = _cache.get(nome)
    if guardado and guardado[0] == assinatura:
        return guardado[1].copy()

    try:
        df = (_ler_csv(caminho) if caminho.suffix.lower() in (".csv", ".txt")
              else pd.read_excel(caminho))
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df

    df.columns = df.columns.str.strip()
    _cache[nome] = (assinatura, df)
    return df.copy()


# --------------------------------------------------------------------------
# a cópia que o bot lê
# --------------------------------------------------------------------------
def _resumo(caminho: Path) -> str:
    """sha1 do arquivo, reaproveitado enquanto ele não mudar."""
    try:
        st = caminho.stat()
    except OSError:
        return ""

    assinatura = (st.st_mtime, st.st_size)
    guardado = _resumos.get(str(caminho))
    if guardado and guardado[0] == assinatura:
        return guardado[1]

    digestor = hashlib.sha1()
    try:
        with caminho.open("rb") as arquivo:
            for bloco in iter(lambda: arquivo.read(1 << 20), b""):
                digestor.update(bloco)
    except OSError:
        return ""

    resumo = digestor.hexdigest()
    _resumos[str(caminho)] = (assinatura, resumo)
    return resumo


def caminho_no_bot(nome: str) -> Path | None:
    """Onde o bot procura essa planilha, ou None se ele não usa essa."""
    if nome not in ESPELHADOS_NO_BOT:
        return None
    return CONFIG.dados_bot / nome


def status_no_bot(nome: str) -> dict:
    """A cópia que o BOT lê confere com a que o site está usando?

    `igual` é o que importa na tela: enquanto for falso, a lista de garantias
    que o operador vê e a base que o bot avalia são duas coisas diferentes.
    """
    destino = caminho_no_bot(nome)
    if destino is None:
        return {"aplicavel": False}

    origem = CONFIG.localizar_dado(nome)
    registro = {
        "aplicavel": True,
        "pasta": str(destino.parent),
        "pasta_existe": destino.parent.is_dir(),
        "existe": destino.is_file(),
        "quando": "",
        "igual": False,
        # O site pode já estar lendo o próprio arquivo do bot (quando nada foi
        # enviado pela tela). Aí não há duas cópias e não há o que sincronizar.
        "mesma_copia": False,
    }

    if origem is None:
        return registro

    try:
        if origem.resolve() == destino.resolve():
            registro.update({"mesma_copia": True, "igual": True})
    except OSError:
        pass

    if registro["existe"]:
        try:
            ts = dt.datetime.fromtimestamp(destino.stat().st_mtime)
            registro["quando"] = ts.strftime("%d/%m/%Y %H:%M")
        except OSError:
            pass

    if not registro["mesma_copia"] and registro["existe"]:
        registro["igual"] = bool(_resumo(origem)) and _resumo(origem) == _resumo(destino)

    return registro


def enviar_ao_bot(nome: str) -> dict:
    """Leva a planilha em uso para a pasta onde o bot a procura.

    Grava num temporário e só então renomeia por cima: `os.replace` é atômico,
    então o bot nunca chega a ler um arquivo pela metade. O mtime resultante é
    o de agora, que é justamente o gatilho da reavaliação do lado dele.
    """
    destino = caminho_no_bot(nome)
    if destino is None:
        raise ValueError(f"'{nome}' não é uma planilha que o bot de monitoramento lê.")

    origem = CONFIG.localizar_dado(nome)
    if origem is None:
        raise ValueError(f"Não encontrei '{nome}' para enviar ao bot. Envie a planilha primeiro.")

    try:
        if origem.resolve() == destino.resolve():
            return {"mesma_copia": True, "destino": str(destino),
                    "quando": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
    except OSError:
        pass

    if not destino.parent.is_dir():
        raise ValueError(
            f"A pasta do bot não existe nesta máquina: {destino.parent}. "
            "Confira 'pasta_bot' em config/site.json."
        )

    provisorio = destino.parent / f".{nome}.novo"
    provisorio.write_bytes(origem.read_bytes())

    falha_final: Exception | None = None
    for tentativa in range(TENTATIVAS_TROCA):
        try:
            os.replace(provisorio, destino)
            break
        except PermissionError as falha:
            falha_final = falha
            if tentativa < TENTATIVAS_TROCA - 1:
                time.sleep(ESPERA_ENTRE_TENTATIVAS)
    else:
        provisorio.unlink(missing_ok=True)
        raise ValueError(
            "O bot está com a planilha aberta agora (ele relê a base a cada "
            "varredura) e não consegui substituí-la. Tente de novo em alguns "
            f"segundos. [{type(falha_final).__name__}]"
        )

    _resumos.pop(str(destino), None)
    return {"mesma_copia": False, "destino": str(destino),
            "quando": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}


# --------------------------------------------------------------------------
# situação e envio
# --------------------------------------------------------------------------
def status_arquivos(pagina: str | None = None) -> list[dict]:
    """Onde cada planilha está, de quando é e há quanto tempo — para a tela."""
    saida = []
    for nome, info in ARQUIVOS.items():
        if pagina and pagina not in info["paginas"]:
            continue
        caminho = CONFIG.localizar_dado(nome)
        registro = {
            "nome": nome, "rotulo": info["rotulo"], "descricao": info["descricao"],
            "existe": caminho is not None, "onde": "", "quando": "",
            "dias": None, "velho": False, "enviado_pelo_site": False,
            "bot": status_no_bot(nome),
        }
        if caminho is not None:
            ts = dt.datetime.fromtimestamp(caminho.stat().st_mtime)
            dias = (dt.datetime.now() - ts).total_seconds() / 86400
            registro.update({
                "onde": str(caminho.parent),
                "quando": ts.strftime("%d/%m/%Y %H:%M"),
                "dias": round(dias, 1),
                "velho": dias > 1.5,
                "enviado_pelo_site": caminho.parent == CONFIG.pasta_dados,
            })
        saida.append(registro)
    return saida


# --------------------------------------------------------------------------
# o que chegou pela tela
# --------------------------------------------------------------------------
# Motor de leitura por formato. Só o openpyxl vem instalado; os outros são
# pacotes à parte, e sem eles o pandas morre com uma mensagem que não ajuda
# ninguém ("Excel file format cannot be determined"). Por isso o formato é
# reconhecido aqui e a falta do motor vira recado em português.
_MOTORES = {
    "xlsx": ("openpyxl", "openpyxl"),
    "xls": ("xlrd", "xlrd"),
    "xlsb": ("pyxlsb", "pyxlsb"),
    "ods": ("odf", "odfpy"),
}


def _formato(conteudo: bytes) -> str:
    """Que arquivo é este, de verdade?

    Pela ASSINATURA e não pela extensão: quem exporta do OFS renomeia, e um
    .csv salvo como .xlsx (ou o contrário) é o engano mais comum da tela.
    """
    if conteudo[:4] == b"PK\x03\x04":
        # .xlsx, .xlsm e .ods são todos zip. O ODF se identifica no primeiro
        # item do pacote, que por norma é 'mimetype' e vem sem compressão.
        if b"opendocument" in conteudo[:200].lower():
            return "ods"
        return "xlsx"
    if conteudo[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        # OLE2: o Excel de antes de 2007 (.xls) e também o .xlsb
        return "xls"
    return "texto"


def _ler_enviado(conteudo: bytes, provisorio: Path) -> tuple[pd.DataFrame, str]:
    """Devolve (planilha, formato) do que foi enviado, ou erro explicado."""
    formato = _formato(conteudo)

    if formato == "texto":
        df = _ler_csv(provisorio)
        if df.empty:
            raise ValueError(
                "Não reconheci o arquivo: não é uma planilha do Excel e "
                "também não abriu como CSV. Reexporte do OFS em Excel (.xlsx) "
                "ou em CSV."
            )
        return df, formato

    modulo, pacote = _MOTORES[formato]
    try:
        importlib.import_module(modulo)
    except ImportError:
        raise ValueError(
            f"Este arquivo está no formato .{formato}, que precisa do pacote "
            f"'{pacote}' — e ele não está instalado no servidor. Reexporte "
            "como .xlsx (Excel moderno), que funciona sem instalar nada."
        ) from None

    try:
        return pd.read_excel(provisorio), formato
    except Exception as falha:
        raise ValueError(
            f"O arquivo parece .{formato}, mas não consegui abrir "
            f"({type(falha).__name__}). Pode estar corrompido ou protegido "
            "por senha."
        ) from falha


def _confere_colunas(df: pd.DataFrame, exigidas: list) -> list[str]:
    """Colunas exigidas que faltam. Item em lista = alternativas aceitas."""
    presentes = {str(c).strip().lower() for c in df.columns}
    faltando = []
    for exigida in exigidas:
        opcoes = exigida if isinstance(exigida, list) else [exigida]
        if not any(o.strip().lower() in presentes for o in opcoes):
            faltando.append(" ou ".join(opcoes))
    return faltando


def guardar(nome: str, conteudo: bytes) -> dict:
    """Valida e grava uma planilha enviada pelo site.

    Só substitui a atual depois de abrir sem erro e ter as colunas esperadas —
    assim um envio errado não derruba o relatório que depende dela.
    """
    if nome not in ARQUIVOS:
        raise ValueError(f"Arquivo '{nome}' não é uma das planilhas aceitas.")
    if not conteudo:
        raise ValueError("O arquivo chegou vazio.")

    info = ARQUIVOS[nome]
    CONFIG.pasta_dados.mkdir(parents=True, exist_ok=True)
    provisorio = CONFIG.pasta_dados / f".{nome}.novo"
    provisorio.write_bytes(conteudo)

    try:
        df, formato = _ler_enviado(conteudo, provisorio)

        if df.empty:
            raise ValueError("A planilha abriu, mas não tem nenhuma linha "
                             "(ou o separador do CSV não foi reconhecido).")
        df.columns = df.columns.str.strip()

        if faltando := _confere_colunas(df, info["colunas"]):
            raise ValueError(
                "A planilha não parece ser a certa: faltam as colunas "
                + ", ".join(f"'{f}'" for f in faltando)
                + f". Esperado {info['descricao']}."
            )

        destino = CONFIG.pasta_dados / nome
        esperado = "texto" if destino.suffix.lower() in (".csv", ".txt") else "xlsx"

        if formato == esperado:
            # Mesmo formato do nome oficial: grava o arquivo original, byte a
            # byte. Reescrever à toa só arriscaria mudar valor.
            destino.write_bytes(provisorio.read_bytes())
            convertido = ""
        else:
            # Formato diferente do que o resto do sistema espera (um .xls
            # antigo, um CSV). Converte AQUI, uma vez, em vez de ensinar o
            # bot e cada tela a lidar com cada formato -- eles continuam
            # abrindo um arquivo só, do jeito de sempre.
            if esperado == "xlsx":
                df.to_excel(destino, index=False)
            else:
                df.to_csv(destino, index=False, encoding="utf-8-sig")
            convertido = formato

        _cache.pop(nome, None)
        _resumos.pop(str(destino), None)

        resultado = {"nome": nome, "rotulo": info["rotulo"], "linhas": len(df),
                     "quando": dt.datetime.now().strftime("%d/%m/%Y %H:%M")}
        if convertido:
            resultado["convertido_de"] = convertido

        # Levar a mesma planilha até o bot faz parte do envio: sem isso a tela
        # passaria a mostrar a base nova enquanto ele continua avaliando
        # garantias pela antiga. Uma falha aqui não invalida o envio -- o
        # arquivo do site já está gravado e o botão "Enviar ao bot" refaz só
        # esta parte.
        if nome in ESPELHADOS_NO_BOT:
            try:
                enviar_ao_bot(nome)
                resultado["bot_ok"] = True
            except Exception as falha:
                resultado["bot_erro"] = str(falha) or type(falha).__name__

        return resultado
    finally:
        provisorio.unlink(missing_ok=True)
