"""Site do painel OPERACIONAL - roda no notebook e serve o painel, o backlog e as garantias."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from web.config import CONFIG          # noqa: E402
from web.fontes import (alertas, backlog, confirmacao, garantias,  # noqa: E402
                        painel, planilhas)

AQUI = Path(__file__).resolve().parent
TAMANHO_MAXIMO_UPLOAD = 60 * 1024 * 1024   # 60 MB
# muda a cada reinicio do site - forca o navegador a buscar de novo o css/js
# em vez de continuar usando uma copia antiga guardada em cache
VERSAO_ESTATICA = str(int(dt.datetime.now().timestamp()))

# "Desenvolvido por X 2026" -> nome (canto superior direito) + ano (canto
# inferior direito), separados a partir do mesmo texto configurado em
# painel.json para nao duplicar campo.
_creditos = re.match(r"^(.*?)\s+(\d{4})\s*$", CONFIG.assinatura)
CREDITO_NOME = _creditos.group(1) if _creditos else CONFIG.assinatura
CREDITO_ANO = _creditos.group(2) if _creditos else str(dt.datetime.now().year)

app = FastAPI(title=CONFIG.titulo, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=secrets.token_hex(32),
    max_age=CONFIG.sessao_horas * 3600,
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")

paginas = Jinja2Templates(directory=str(AQUI / "templates"))


def _render(request: Request, template: str, pagina: str, status_code: int = 200, **extra):
    """Renderiza um template com o contexto que todas as telas precisam."""
    contexto = {
        "pagina": pagina,
        "logado": bool(request.session.get("ok")),
        "titulo_site": CONFIG.titulo,
        "assinatura": CONFIG.assinatura,
        "credito_nome": CREDITO_NOME,
        "credito_ano": CREDITO_ANO,
        "autenticador_ativo": CONFIG.autenticador_ativo,
        "versao_estatica": VERSAO_ESTATICA,
        **extra,
    }
    return paginas.TemplateResponse(request, template, contexto, status_code=status_code)


def _autenticado(request: Request) -> bool:
    return bool(request.session.get("ok"))


def _para_login(request: Request) -> RedirectResponse:
    destino = request.url.path
    return RedirectResponse(f"/entrar?destino={destino}", status_code=303)


# --------------------------------------------------------------------------
# acesso
# --------------------------------------------------------------------------
@app.get("/entrar", response_class=HTMLResponse)
def tela_login(request: Request, destino: str = "/"):
    if _autenticado(request):
        return RedirectResponse(destino or "/", status_code=303)
    return _render(request, "login.html", "login", erro=None, destino=destino or "/")


@app.post("/entrar", response_class=HTMLResponse)
def fazer_login(request: Request, pin: str = Form(""), destino: str = Form("/")):
    if secrets.compare_digest(str(pin).strip(), str(CONFIG.pin)):
        request.session["ok"] = True
        return RedirectResponse(destino or "/", status_code=303)
    return _render(request, "login.html", "login", status_code=401,
                   erro="PIN incorreto.", destino=destino or "/")


@app.get("/sair")
def sair(request: Request):
    request.session.clear()
    return RedirectResponse("/entrar", status_code=303)


# --------------------------------------------------------------------------
# início
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    if not _autenticado(request):
        return _para_login(request)
    return _render(request, "inicio.html", "inicio",
        est=backlog.estatisticas(),
        entrantes=backlog.entrantes(),
        bot=backlog.bot_ativo(),
        telas=painel.listar(),
        problemas=CONFIG.problemas(),
        agora=dt.datetime.now().strftime("%d/%m/%Y %H:%M"))


# --------------------------------------------------------------------------
# painel
# --------------------------------------------------------------------------
@app.get("/painel", response_class=HTMLResponse)
def ver_painel(request: Request, config_salva: str = "", config_erro: str = ""):
    if not _autenticado(request):
        return _para_login(request)
    return _render(request, "painel.html", "painel",
                   telas=painel.listar(),
                   ultima=painel.ultima_extracao(),
                   config=painel.configuracao_atual(),
                   config_salva=config_salva, config_erro=config_erro,
                   resultado=None, erro=None)


@app.post("/painel/configuracao")
async def salvar_configuracao_painel(request: Request):
    if not _autenticado(request):
        return _para_login(request)

    form = await request.form()
    tipo_dia = str(form.get("tipo_dia") or "auto")
    clusters_ativos = form.getlist("cluster_ativo")
    carga_clusters = form.getlist("carga_cluster")
    mo_ativa = {
        chave[len("mo_"):]: valor
        for chave, valor in form.items()
        if chave.startswith("mo_") and isinstance(valor, str)
    }

    erro = ""
    try:
        await run_in_threadpool(painel.salvar_configuracao, tipo_dia, clusters_ativos,
                                carga_clusters, mo_ativa)
        mensagem = "Configuração salva."
    except Exception as falha:
        mensagem = ""
        erro = str(falha) or f"Falha ao salvar: {type(falha).__name__}"

    return RedirectResponse(
        f"/painel?config_salva={quote(mensagem)}&config_erro={quote(erro)}", status_code=303
    )


@app.post("/painel/gerar", response_class=HTMLResponse)
async def gerar_painel(request: Request, extracao: UploadFile | None = None):
    if not _autenticado(request):
        return _para_login(request)

    resultado, erro = None, None
    try:
        if extracao is None or not extracao.filename:
            raise ValueError("Nenhum arquivo foi enviado.")
        conteudo = await extracao.read()
        if len(conteudo) > TAMANHO_MAXIMO_UPLOAD:
            raise ValueError(
                f"Arquivo de {len(conteudo) / 1_048_576:.0f} MB — o limite é "
                f"{TAMANHO_MAXIMO_UPLOAD // 1_048_576} MB."
            )
        caminho = painel.guardar_upload(extracao.filename, conteudo)
        # gerar() renderiza as imagens com o Playwright sincrono, que nao roda
        # dentro do loop assincrono - por isso vai para uma thread separada.
        # Tem que ser uma thread NOVA a cada chamada (nao a threadpool
        # compartilhada do run_in_threadpool): o Playwright sincrono prende
        # seu dispatcher a thread que o iniciou, e reaproveitar uma thread de
        # pool entre chamadas diferentes quebra com
        # "'PlaywrightContextManager' object has no attribute '_playwright'".
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            resultado = await loop.run_in_executor(executor, painel.gerar, caminho)
    except Exception as falha:
        erro = str(falha) or f"Falha ao gerar: {type(falha).__name__}"

    return _render(request, "painel.html", "painel",
                   telas=painel.listar(),
                   ultima=painel.ultima_extracao(),
                   config=painel.configuracao_atual(),
                   config_salva="", config_erro="",
                   resultado=resultado, erro=erro)


@app.get("/painel/imagem/{nome}")
def imagem_painel(request: Request, nome: str, baixar: int = 0):
    if not _autenticado(request):
        return _para_login(request)
    caminho = painel.caminho(nome)
    if caminho is None:
        return HTMLResponse("Imagem não encontrada.", status_code=404)
    return FileResponse(
        caminho, media_type="image/png",
        filename=f"{nome}.png" if baixar else None,
    )


# --------------------------------------------------------------------------
# backlog
# --------------------------------------------------------------------------
@app.get("/backlog", response_class=HTMLResponse)
def ver_backlog(request: Request):
    if not _autenticado(request):
        return _para_login(request)

    imagens = backlog.imagens()
    por_regiao: dict[str, list] = {}
    for img in imagens:
        por_regiao.setdefault(img["regiao"], []).append(img)

    return _render(request, "backlog.html", "backlog",
        imagens=imagens,
        imagens_por_regiao=por_regiao,
        marca=backlog.marca_das_imagens(),
        minutos_entre_pedidos=backlog.MINUTOS_ENTRE_PEDIDOS,
        gerado_em=max((i["quando"] for i in imagens if i["quando"]), default=""),
        est=backlog.estatisticas(),
        entrantes=backlog.entrantes(),
        bot=backlog.bot_ativo(),
        log=backlog.log(),
        # O OFS GERAL alimenta o "Enviado D0" do backlog, então a idade dele
        # aparece aqui também — se estiver velho, a contagem sai menor que a real.
        arquivos=planilhas.status_arquivos("backlog"),
        ofs=backlog.status_ofs_do_backlog(),
        pasta_relatorios=str(CONFIG.relatorios_bot),
        caminho_log=str(CONFIG.log_bot))


@app.get("/backlog/imagem/{nome}")
def imagem_backlog(request: Request, nome: str, baixar: int = 0):
    if not _autenticado(request):
        return _para_login(request)
    caminho = backlog.caminho_imagem(nome)
    if caminho is None:
        return HTMLResponse("Imagem não encontrada.", status_code=404)
    return FileResponse(
        caminho, media_type="image/png",
        filename=nome if baixar else None,
    )


@app.post("/backlog/gerar")
async def gerar_backlog(request: Request):
    """Botão Atualizar: pede ao bot um backlog novo."""
    if not _autenticado(request):
        return JSONResponse({"ok": False, "erro": "Sessão expirada."}, status_code=401)
    resultado = await run_in_threadpool(backlog.pedir_backlog_novo)
    if resultado["ok"]:
        return JSONResponse(resultado, status_code=200)
    # 409: o pedido anterior ainda vale e o bot deve estar gerando — insistir
    # duplica o envio no grupo. 503 fica só para o bot realmente fora do ar,
    # que é o único caso em que tentar de novo resolve.
    ja_pedido = resultado.get("repetido") or resultado.get("demorou")
    return JSONResponse(resultado, status_code=409 if ja_pedido else 503)


@app.get("/backlog/estado")
def estado_backlog(request: Request):
    """Assinatura das imagens, para a tela avisar quando o bot gerar algo novo."""
    if not _autenticado(request):
        return JSONResponse({"marca": ""}, status_code=401)
    return {"marca": backlog.marca_das_imagens()}


@app.get("/backlog/log")
def log_backlog(request: Request):
    """Usado pelo auto-refresh do log na tela de backlog."""
    if not _autenticado(request):
        return JSONResponse({"linhas": []}, status_code=401)
    return {"linhas": backlog.log()}


# --------------------------------------------------------------------------
# garantias
# --------------------------------------------------------------------------
@app.get("/garantias", response_class=HTMLResponse)
def ver_garantias(request: Request, enviado: str = "", falhou: str = ""):
    if not _autenticado(request):
        return _para_login(request)
    return _render(request, "garantias.html", "garantias",
                   arquivos=garantias.status_arquivos(),
                   enviado=enviado, falhou=falhou)


@app.post("/garantias/enviar")
async def enviar_planilha(request: Request, alvo: str = Form(""),
                          volta: str = Form("/garantias"),
                          planilha: UploadFile | None = None):
    """Recebe uma das planilhas das garantias e substitui a que está em uso."""
    if not _autenticado(request):
        return _para_login(request)

    enviado, falhou = "", ""
    try:
        if planilha is None or not planilha.filename:
            raise ValueError("Nenhum arquivo foi enviado.")
        conteudo = await planilha.read()
        if len(conteudo) > TAMANHO_MAXIMO_UPLOAD:
            raise ValueError(
                f"Arquivo de {len(conteudo) / 1_048_576:.0f} MB — o limite é "
                f"{TAMANHO_MAXIMO_UPLOAD // 1_048_576} MB."
            )
        resultado = await run_in_threadpool(planilhas.guardar, alvo, conteudo)
        enviado = f"{resultado['rotulo']} atualizada: {resultado['linhas']} linhas."
        if resultado.get("bot_ok"):
            enviado += " O bot recebeu a base nova e vai reavaliar os reparos pendentes."
        elif resultado.get("bot_erro"):
            # A planilha do site foi gravada; só o repasse ao bot falhou. Fica
            # como aviso, não como erro, senão parece que o envio inteiro caiu.
            enviado += (" Atenção: não consegui repassar ao bot — "
                        f"{resultado['bot_erro']} Use o botão \"Enviar ao bot\".")
    except Exception as falha:
        falhou = str(falha) or f"Falha no envio: {type(falha).__name__}"

    destino = volta if volta in ("/garantias", "/confirmacao", "/backlog") else "/garantias"
    return RedirectResponse(
        f"{destino}?enviado={quote(enviado)}&falhou={quote(falhou)}", status_code=303
    )


@app.post("/garantias/enviar-ao-bot")
async def enviar_planilha_ao_bot(request: Request, alvo: str = Form(""),
                                 volta: str = Form("/garantias")):
    """Botão "Enviar ao bot": repassa a base em uso para a pasta que ele lê.

    O envio normal já faz isso sozinho. Este botão existe para os casos em que
    aquele repasse falhou (bot lendo o arquivo no exato instante) ou em que a
    planilha chegou ao site por fora, sem passar pelo formulário.
    """
    if not _autenticado(request):
        return _para_login(request)

    enviado, falhou = "", ""
    try:
        resultado = await run_in_threadpool(planilhas.enviar_ao_bot, alvo)
        if resultado["mesma_copia"]:
            enviado = ("O site já está lendo a própria cópia do bot — as duas "
                       "estão sempre iguais, não há o que sincronizar.")
        else:
            enviado = (f"Base enviada ao bot ({resultado['quando']}). "
                       "Ele recarrega na próxima varredura e reavalia os reparos pendentes.")
    except Exception as falha:
        falhou = str(falha) or f"Falha ao enviar ao bot: {type(falha).__name__}"

    destino = volta if volta in ("/garantias", "/confirmacao", "/backlog") else "/garantias"
    return RedirectResponse(
        f"{destino}?enviado={quote(enviado)}&falhou={quote(falhou)}", status_code=303
    )


@app.get("/garantias/dados", response_class=HTMLResponse)
def dados_garantias(request: Request):
    """A tabela em si - carregada por fetch para a pagina abrir na hora.

    A consulta ao Autenticador acontece aqui, a cada abertura, respeitando a janela
    de cache configurada em autenticador_cache_segundos.
    """
    if not _autenticado(request):
        return HTMLResponse('<div class="aviso erro">Sessão expirada. Recarregue a página.</div>',
                            status_code=401)
    try:
        dados = garantias.calcular(com_autenticador=CONFIG.autenticador_ativo)
    except Exception as falha:
        return HTMLResponse(
            f'<div class="aviso erro">Falha ao montar a lista: {type(falha).__name__} — {falha}</div>'
        )
    return _render(request, "garantias_dados.html", "garantias", dados=dados)


# --------------------------------------------------------------------------
# confirmação de agenda
# --------------------------------------------------------------------------
@app.get("/confirmacao", response_class=HTMLResponse)
def ver_confirmacao(request: Request, dia: str = "d0",
                    enviado: str = "", falhou: str = ""):
    if not _autenticado(request):
        return _para_login(request)
    return _render(request, "confirmacao.html", "confirmacao",
                   dia=dia if dia in ("d0", "d1") else "d0",
                   arquivos=planilhas.status_arquivos("confirmacao"),
                   enviado=enviado, falhou=falhou)


@app.get("/confirmacao/dados", response_class=HTMLResponse)
def dados_confirmacao(request: Request, dia: str = "d0"):
    """A tabela em si — a planilha do Google pode demorar alguns segundos."""
    if not _autenticado(request):
        return HTMLResponse('<div class="aviso erro">Sessão expirada. Recarregue a página.</div>',
                            status_code=401)
    try:
        dados = confirmacao.calcular(dia if dia in ("d0", "d1") else "d0")
    except Exception as falha:
        return HTMLResponse(
            f'<div class="aviso erro">Falha ao montar a confirmação: '
            f'{type(falha).__name__} — {falha}</div>'
        )
    return _render(request, "confirmacao_dados.html", "confirmacao", dados=dados)


@app.post("/confirmacao/enviar")
async def enviar_confirmacao_grupo(request: Request):
    """Botão 'Enviar não confirmados no grupo' - pede ao bot que mande a lista."""
    if not _autenticado(request):
        return JSONResponse({"ok": False, "erro": "Sessão expirada."}, status_code=401)
    form = await request.form()
    dia = str(form.get("dia") or "d0")
    resultado = await run_in_threadpool(
        confirmacao.enviar_nao_confirmados, dia if dia in ("d0", "d1") else "d0"
    )
    return JSONResponse(resultado, status_code=200 if resultado["ok"] else 400)


# --------------------------------------------------------------------------
# alerta de garantias
# --------------------------------------------------------------------------
@app.get("/alertas/garantias")
def alertas_garantias(request: Request):
    """Garantias que o bot notificou — o navegador compara e avisa o que é novo.

    Vale em qualquer página: quem estiver no Painel ou no Backlog também ouve.
    """
    if not _autenticado(request):
        return JSONResponse({"disponivel": False, "itens": []}, status_code=401)
    return alertas.garantias_notificadas()


@app.get("/saude")
def saude():
    return {"ok": True, "quando": dt.datetime.now().isoformat(timespec="seconds")}
