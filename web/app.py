"""Site do painel OPERACIONAL - roda no notebook e serve o painel, o backlog e as garantias."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import secrets
import sys
import time
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

from web import acesso, correio        # noqa: E402
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
    secret_key=acesso.chave_de_sessao(),
    max_age=CONFIG.sessao_horas * 3600,
    # "lax" e o que faz a volta do Google funcionar: o retorno e uma navegacao
    # de topo vinda de outro site, e com "strict" o cookie nao viajaria junto.
    same_site="lax",
    https_only=CONFIG.cookie_seguro,
)
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")

paginas = Jinja2Templates(directory=str(AQUI / "templates"))


def _render(request: Request, template: str, pagina: str, status_code: int = 200, **extra):
    """Renderiza um template com o contexto que todas as telas precisam."""
    contexto = {
        "pagina": pagina,
        # a mesma pergunta que as rotas fazem, e nao so a marca da sessao:
        # senao quem foi revogado continuaria vendo o menu completo
        "logado": _autenticado(request),
        "titulo_site": CONFIG.titulo,
        "assinatura": CONFIG.assinatura,
        "credito_nome": CREDITO_NOME,
        "credito_ano": CREDITO_ANO,
        "autenticador_ativo": CONFIG.autenticador_ativo,
        "versao_estatica": VERSAO_ESTATICA,
        "usuario": request.session.get("nome") or request.session.get("email") or "",
        "sou_admin": acesso.e_admin(request.session.get("email", "")),
        **extra,
    }
    return paginas.TemplateResponse(request, template, contexto, status_code=status_code)


def _autenticado(request: Request) -> bool:
    """Sessao aberta E e-mail ainda na lista de acesso.

    Conferir a lista a cada pedido, e nao so no login, e o que faz tirar
    alguem de 'emails_autorizados' valer na hora -- sem isso a pessoa
    continuaria dentro ate a sessao vencer, o que pode ser meio dia.
    """
    if not request.session.get("ok"):
        return False
    if not acesso.autorizado(request.session.get("email", "")):
        request.session.clear()
        return False
    return True


def _para_login(request: Request) -> RedirectResponse:
    destino = quote(request.url.path, safe="/")
    return RedirectResponse(f"/entrar?destino={destino}", status_code=303)


# --------------------------------------------------------------------------
# acesso
# --------------------------------------------------------------------------
porteiro = logging.getLogger("operacional.acesso")


def _destino_seguro(destino: str) -> str:
    """So aceita caminho interno.

    Sem isto, /entrar?destino=https://sitedogolpe... mandaria a pessoa para
    fora logo depois de ela entrar, com cara de que foi o site que levou.
    Barato de evitar, caro de descobrir depois.
    """
    destino = (destino or "/").strip()
    if not destino.startswith("/") or destino.startswith("//"):
        return "/"
    return destino


def _humano(segundos: int) -> str:
    if segundos < 60:
        return f"{segundos} segundos"
    minutos = round(segundos / 60)
    return "1 minuto" if minutos == 1 else f"{minutos} minutos"


def _tela(request: Request, etapa: str, destino: str = "/", erro=None, aviso=None,
          email: str = "", status_code: int = 200, motivo: str = ""):
    """As telas de entrada saem todas do mesmo template.

    etapa: senha | navegador | primeiro | codigo | definir
    motivo: so na etapa "codigo" -- primeiro (criar senha) ou navegador
            (liberar dispositivo novo). Muda o texto, nao o caminho.
    """
    return _render(request, "login.html", "login", status_code=status_code,
                   etapa=etapa, destino=destino, erro=erro, aviso=aviso,
                   email=email, motivo=motivo,
                   google_aqui=acesso.google_disponivel_aqui(request),
                   smtp_ligado=CONFIG.smtp_ligado,
                   minimo_senha=acesso.TAMANHO_MINIMO_SENHA,
                   validade_codigo=acesso.VALIDADE_CODIGO_MIN)


# --- navegadores conhecidos ------------------------------------------------
# O cookie guarda so uma marca aleatoria. Quem decide se ela vale e o cadastro,
# que guarda o sha256 dela -- roubar o arquivo nao da para forjar o cookie.
COOKIE_NAVEGADOR = "operacional_navegador"

_SISTEMAS = (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
             ("Windows", "Windows"), ("Macintosh", "Mac"), ("Linux", "Linux"))
_PROGRAMAS = (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox", "Firefox"),
              ("Chrome", "Chrome"), ("Safari", "Safari"))


def _descricao_navegador(request: Request) -> str:
    """Um rotulo legivel para a pessoa reconhecer o aparelho na lista.

    E so etiqueta: nada aqui entra na decisao de confiar ou nao, porque o
    User-Agent e escolhido por quem chama e pode dizer o que quiser.
    """
    ua = request.headers.get("user-agent", "")
    programa = next((nome for marca, nome in _PROGRAMAS if marca in ua), "")
    sistema = next((nome for marca, nome in _SISTEMAS if marca in ua), "")
    if programa and sistema:
        return f"{programa} no {sistema}"
    return (programa or sistema or ua[:60] or "desconhecido")


def _confiar_no_navegador(request: Request, resposta, email: str) -> None:
    """Marca este navegador como conhecido e devolve o cookie correspondente."""
    marca = request.cookies.get(COOKIE_NAVEGADOR, "")
    try:
        if acesso.USUARIOS.navegador_conhecido(email, marca):
            acesso.USUARIOS.renovar_navegador(email, marca)
        else:
            marca = marca or acesso.nova_marca_de_navegador()
            acesso.USUARIOS.registrar_navegador(
                email, marca, _descricao_navegador(request))
    except Exception as falha:
        # perder o "lembrar deste navegador" custa um codigo a mais no proximo
        # login; nao vale barrar quem ja provou quem e
        porteiro.warning("não consegui lembrar o navegador de %s: %s", email, falha)
        return
    resposta.set_cookie(
        COOKIE_NAVEGADOR, marca, max_age=acesso.DIAS_NAVEGADOR * 86400,
        httponly=True, samesite="lax", secure=CONFIG.cookie_seguro, path="/")


def _abrir_sessao(request: Request, email: str, nome: str, por: str,
                  ip: str, destino: str) -> RedirectResponse:
    request.session.clear()
    request.session["ok"] = True
    request.session["email"] = email
    request.session["nome"] = nome or email
    try:
        acesso.USUARIOS.registrar_entrada(email, por, nome)
    except Exception as falha:
        # nao impedir a entrada por causa do cadastro: aqui e so historico
        porteiro.warning("não consegui registrar a entrada de %s: %s", email, falha)
    porteiro.info("entrou por %s: %s (%s)", por, email, ip)
    resposta = RedirectResponse(destino, status_code=303)
    # So se chega aqui depois de senha + (navegador conhecido ou codigo), entao
    # todo caminho que abre sessao pode confiar na maquina.
    _confiar_no_navegador(request, resposta, email)
    return resposta


def _bloqueado(request: Request, etapa: str, destino: str, ip: str, email: str = ""):
    """Devolve a tela de espera se este IP estiver de castigo, senao None."""
    espera = acesso.PORTARIA.bloqueio_restante(ip)
    if not espera:
        return None
    porteiro.warning("%s bloqueado para %s por mais %ss", etapa, ip, espera)
    return _tela(request, etapa, destino, email=email, status_code=429,
                 erro=f"Número de tentativas excedido. Aguarde {_humano(espera)} "
                      f"antes de tentar novamente.")


def _punir(ip: str) -> int:
    """Conta o erro e cobra o custo fixo. Devolve os segundos de castigo.

    A rota e sincrona, entao o FastAPI ja a roda fora do laco de eventos --
    este sleep segura quem tenta em rajada sem travar o resto do site.
    """
    espera = acesso.PORTARIA.errou(ip)
    time.sleep(acesso.ATRASO_POR_ERRO_SEG)
    return espera


# --- e-mail e senha --------------------------------------------------------
@app.get("/entrar", response_class=HTMLResponse)
def tela_login(request: Request, destino: str = "/", erro: str = ""):
    destino = _destino_seguro(destino)
    if _autenticado(request):
        return RedirectResponse(destino, status_code=303)
    return _tela(request, "senha", destino, erro=erro or None)


@app.post("/entrar", response_class=HTMLResponse)
def fazer_login(request: Request, email: str = Form(""), senha: str = Form(""),
                destino: str = Form("/")):
    destino = _destino_seguro(destino)
    ip = acesso.ip_do_pedido(request)
    email = acesso.normalizar_email(email)

    if resposta := _bloqueado(request, "senha", destino, ip, email):
        return resposta

    if not email or not senha:
        return _tela(request, "senha", destino, email=email, status_code=400,
                     erro="Informe o e-mail e a senha.")

    # Quem esta na lista mas ainda nao definiu senha vai direto para o caminho
    # certo, em vez de bater de novo numa senha que nunca existiu.
    if acesso.autorizado(email) and not acesso.USUARIOS.tem_senha(email):
        return _tela(request, "primeiro", destino, email=email,
                     aviso="Este e-mail ainda não possui senha cadastrada. Solicite "
                           "um código de verificação para defini-la.")

    guardado = acesso.USUARIOS.buscar(email).get("senha")
    if acesso.autorizado(email) and guardado and acesso.senha_confere(senha, guardado):
        acesso.PORTARIA.acertou(ip)
        nome = acesso.USUARIOS.buscar(email).get("nome", "")
        if acesso.USUARIOS.navegador_conhecido(
                email, request.cookies.get(COOKIE_NAVEGADOR, "")):
            return _abrir_sessao(request, email, nome, "senha", ip, destino)
        # Senha certa, maquina que o site nunca viu -- que e exatamente o
        # desenho de uma senha vazada: quem rouba tenta do computador dele.
        # Antes de abrir, exigimos a prova de que a caixa de e-mail tambem e
        # desta pessoa.
        porteiro.info("navegador desconhecido para %s (%s)", email, ip)
        request.session.clear()
        request.session["pendente_email"] = email
        request.session["pendente_ate"] = time.time() + 600
        return _tela(request, "navegador", destino, email=email)

    espera = _punir(ip)
    porteiro.warning("senha errada para %r a partir de %s%s", email, ip,
                     f" (bloqueado por {espera}s)" if espera else "")
    if espera:
        return _tela(request, "senha", destino, email=email, status_code=429,
                     erro=f"Número de tentativas excedido. Aguarde {_humano(espera)} "
                     f"antes de tentar novamente.")
    return _tela(request, "senha", destino, email=email, status_code=401,
                 erro="E-mail ou senha incorretos.")


def _pendente(request: Request) -> str:
    """Quem acertou a senha agora e ainda deve a segunda prova. Vazio se nao ha.

    E esta janela que autoriza tanto o codigo quanto o Google a concluirem o
    login num navegador novo. Sem ela, o botao do Google entraria sozinho -- que
    e exatamente o que nao queremos.
    """
    email = acesso.normalizar_email(request.session.get("pendente_email", ""))
    if not email or time.time() > float(request.session.get("pendente_ate") or 0):
        return ""
    return email


def _enviar_codigo(request: Request, email: str, destino: str, motivo: str,
                   etapa_no_erro: str):
    """Gera o codigo, manda por e-mail e devolve a tela ja pronta.

    Serve aos dois usos do codigo -- criar senha (motivo "primeiro") e liberar
    um navegador novo (motivo "navegador"). O motivo fica na sessao porque e
    ele que decide, la na conferencia, se o certo e abrir a sessao ou pedir a
    senha nova.

    Guardar o motivo na sessao e seguro porque ele nao concede nada sozinho:
    sem o codigo, que so existe na caixa de e-mail da pessoa, ele nao abre
    porta nenhuma.
    """
    if not CONFIG.smtp_ligado:
        return _tela(request, etapa_no_erro, destino, email=email, status_code=503,
                     erro="O envio de e-mail não está configurado. Contate o "
                          "administrador do sistema.")

    request.session["codigo_email"] = email
    request.session["codigo_motivo"] = motivo

    if falta := acesso.CODIGOS.pode_pedir(email):
        return _tela(request, "codigo", destino, email=email, status_code=429,
                     motivo=motivo,
                     erro=f"Um código foi enviado recentemente. Caso não o tenha "
                          f"recebido, solicite outro em {_humano(falta)}.")

    codigo = acesso.CODIGOS.gerar(email)
    assunto, corpo = correio.texto_do_codigo(codigo, acesso.VALIDADE_CODIGO_MIN)
    try:
        correio.enviar(email, assunto, corpo)
    except correio.CorreioIndisponivel as falha:
        request.session.pop("codigo_email", None)
        request.session.pop("codigo_motivo", None)
        return _tela(request, etapa_no_erro, destino, email=email, status_code=502,
                     erro=str(falha))

    return _tela(request, "codigo", destino, email=email, motivo=motivo,
                 aviso=f"Um código de verificação foi enviado para {email}.")


# --- primeiro acesso e senha esquecida (mesmo caminho) ---------------------
@app.get("/entrar/primeiro", response_class=HTMLResponse)
def tela_primeiro(request: Request, destino: str = "/"):
    # quem ja esta dentro chega aqui pelo "Minha senha": adianta o e-mail dela
    ja_dentro = request.session.get("email", "") if _autenticado(request) else ""
    return _tela(request, "primeiro", _destino_seguro(destino), email=ja_dentro)


@app.post("/entrar/primeiro", response_class=HTMLResponse)
def pedir_codigo(request: Request, email: str = Form(""), destino: str = Form("/")):
    destino = _destino_seguro(destino)
    ip = acesso.ip_do_pedido(request)
    email = acesso.normalizar_email(email)

    if not acesso.autorizado(email):
        porteiro.warning("código pedido para e-mail fora da lista: %r (%s)", email, ip)
        # Dizer a verdade aqui e deliberado. E ferramenta interna, e a lista nao
        # e segredo; ja o silencio transformaria um e-mail digitado errado numa
        # espera sem fim, no meio de uma ocorrencia.
        return _tela(request, "primeiro", destino, email=email, status_code=403,
                     erro="Este e-mail não possui autorização de acesso.")

    return _enviar_codigo(request, email, destino, "primeiro", "primeiro")


@app.get("/entrar/codigo", response_class=HTMLResponse)
def tela_codigo(request: Request, destino: str = "/"):
    destino = _destino_seguro(destino)
    email = acesso.normalizar_email(request.session.get("codigo_email", ""))
    if not email:
        return _tela(request, "primeiro", destino)
    return _tela(request, "codigo", destino, email=email,
                 motivo=str(request.session.get("codigo_motivo") or "primeiro"))


@app.post("/entrar/navegador", response_class=HTMLResponse)
def confirmar_navegador_por_codigo(request: Request, destino: str = Form("/")):
    """A pessoa escolheu o código em vez do Google, no dispositivo novo."""
    destino = _destino_seguro(destino)
    email = _pendente(request)
    if not email:
        return _tela(request, "senha", destino, status_code=400,
                     erro="A sessão de acesso expirou. Informe o e-mail e a "
                          "senha novamente.")
    return _enviar_codigo(request, email, destino, "navegador", "navegador")


@app.post("/entrar/codigo/reenviar", response_class=HTMLResponse)
def reenviar_codigo(request: Request, destino: str = Form("/")):
    """Manda outro codigo mantendo o motivo do primeiro.

    O e-mail vem da sessao, nunca do formulario: assim este botao nao vira uma
    forma de disparar codigo para o endereco de outra pessoa.
    """
    destino = _destino_seguro(destino)
    email = acesso.normalizar_email(request.session.get("codigo_email", ""))
    motivo = str(request.session.get("codigo_motivo") or "primeiro")

    if not email or not acesso.autorizado(email):
        request.session.clear()
        return _tela(request, "primeiro", destino, status_code=400,
                     erro="A solicitação expirou. Informe o e-mail novamente.")

    return _enviar_codigo(request, email, destino, motivo,
                          "senha" if motivo == "navegador" else "primeiro")


@app.post("/entrar/codigo", response_class=HTMLResponse)
def conferir_codigo(request: Request, codigo: str = Form(""), destino: str = Form("/")):
    destino = _destino_seguro(destino)
    ip = acesso.ip_do_pedido(request)
    email = acesso.normalizar_email(request.session.get("codigo_email", ""))

    if not email:
        return _tela(request, "primeiro", destino, status_code=400,
                     erro="A solicitação expirou. Informe o e-mail novamente.")
    if resposta := _bloqueado(request, "codigo", destino, ip, email):
        return resposta

    porque = str(request.session.get("codigo_motivo") or "primeiro")

    certo, recusa = acesso.CODIGOS.conferir(email, codigo)
    if not certo:
        espera = _punir(ip)
        porteiro.warning("código errado para %s a partir de %s", email, ip)
        if espera:
            return _tela(request, "codigo", destino, email=email, status_code=429,
                         motivo=porque,
                         erro=f"Número de tentativas excedido. Aguarde "
                              f"{_humano(espera)} antes de tentar novamente.")
        return _tela(request, "codigo", destino, email=email, status_code=401,
                     motivo=porque, erro=recusa)

    acesso.PORTARIA.acertou(ip)
    request.session.pop("codigo_email", None)
    request.session.pop("codigo_motivo", None)

    # Navegador novo: a senha ja foi conferida antes de o codigo ser enviado, e
    # o codigo acabou de provar a caixa de e-mail. Os dois fatores fecharam.
    if porque == "navegador":
        if _pendente(request) != email:
            request.session.clear()
            porteiro.warning("código de navegador sem senha conferida: %s (%s)", email, ip)
            return _tela(request, "senha", destino, status_code=400,
                         erro="A sessão de acesso expirou. Informe o e-mail e a "
                              "senha novamente.")
        if not acesso.autorizado(email):
            request.session.clear()
            porteiro.warning("código conferido, mas %s perdeu o acesso (%s)", email, ip)
            return _tela(request, "senha", destino, status_code=403,
                         erro="Este e-mail não possui mais autorização de acesso.")
        nome = acesso.USUARIOS.buscar(email).get("nome", "")
        porteiro.info("navegador novo liberado para %s (%s)", email, ip)
        return _abrir_sessao(request, email, nome, "senha", ip, destino)

    # janela curta e separada: ter conferido o codigo autoriza definir a senha,
    # e so isso. Ainda nao e uma sessao aberta no site.
    request.session["pode_definir"] = email
    request.session["pode_definir_ate"] = time.time() + 600
    porteiro.info("código conferido por %s (%s)", email, ip)
    return _tela(request, "definir", destino, email=email,
                 aviso="Código validado. Defina sua senha de acesso.")


@app.post("/entrar/senha", response_class=HTMLResponse)
def gravar_senha(request: Request, senha: str = Form(""), repetir: str = Form(""),
                 destino: str = Form("/")):
    destino = _destino_seguro(destino)
    ip = acesso.ip_do_pedido(request)
    email = acesso.normalizar_email(request.session.get("pode_definir", ""))
    ate = float(request.session.get("pode_definir_ate") or 0)

    if not email or time.time() > ate:
        request.session.pop("pode_definir", None)
        request.session.pop("pode_definir_ate", None)
        return _tela(request, "primeiro", destino, status_code=400,
                     erro="O prazo para definição da senha expirou. Solicite um novo código.")

    if not acesso.autorizado(email):
        request.session.clear()
        porteiro.warning("tentativa de definir senha fora da lista: %s (%s)", email, ip)
        return _tela(request, "senha", destino, status_code=403,
                     erro="Este e-mail não possui mais autorização de acesso.")

    if problema := acesso.problema_na_senha(senha, repetir):
        return _tela(request, "definir", destino, email=email, status_code=400,
                     erro=problema)

    try:
        acesso.USUARIOS.definir_senha(email, senha)
    except OSError as falha:
        porteiro.error("não consegui gravar a senha de %s: %s", email, falha)
        return _tela(request, "definir", destino, email=email, status_code=500,
                     erro="Não foi possível gravar a senha. Tente novamente.")

    nome = acesso.USUARIOS.buscar(email).get("nome", "")
    request.session.pop("pode_definir", None)
    request.session.pop("pode_definir_ate", None)
    porteiro.info("senha criada por %s (%s)", email, ip)
    return _abrir_sessao(request, email, nome, "senha", ip, destino)


# --- entrada pelo Google ---------------------------------------------------
@app.get("/entrar/google")
def entrar_com_google(request: Request, destino: str = "/"):
    if not acesso.google_configurado():
        return RedirectResponse("/entrar?erro=" + quote(
            "A autenticação pelo Google não está configurada."), status_code=303)

    # o "state" e o que amarra a ida e a volta: sem ele, alguem poderia induzir
    # o navegador da vitima a completar um login que quem comecou foi o atacante
    estado = secrets.token_urlsafe(24)
    request.session["google_estado"] = estado
    request.session["google_destino"] = _destino_seguro(destino)
    return RedirectResponse(acesso.url_para_o_google(estado), status_code=303)


@app.get("/entrar/google/retorno")
def retorno_do_google(request: Request, code: str = "", state: str = "", error: str = ""):
    ip = acesso.ip_do_pedido(request)
    esperado = request.session.pop("google_estado", None)
    destino = _destino_seguro(request.session.pop("google_destino", "/"))

    def recusar(motivo: str, detalhe_log: str, status: int = 403):
        porteiro.warning("Google recusado (%s): %s", ip, detalhe_log)
        return _tela(request, "senha", destino, status_code=status, erro=motivo)

    if error:
        return recusar("A autenticação pelo Google foi cancelada.",
                       f"erro do Google: {error}", 400)
    if not code or not state:
        return recusar("A autenticação pelo Google não foi concluída. Tente novamente.",
                       "sem code/state", 400)
    if not esperado or not secrets.compare_digest(state, esperado):
        return recusar("A sessão de autenticação pelo Google expirou. Tente novamente.",
                       "state nao confere", 400)

    try:
        dados = acesso.identidade_do_codigo(code)
    except Exception as falha:
        return recusar("Não foi possível contatar o Google no momento. Tente novamente.",
                       f"{type(falha).__name__}: {falha}", 502)

    email, nome = acesso.conta_do_google(dados)
    if not email:
        return recusar("A conta Google informada não possui e-mail verificado.",
                       "sem e-mail verificado", 403)
    if not acesso.autorizado(email):
        return recusar(f"O e-mail {email} não possui autorização de acesso.",
                       f"fora da lista: {email}")

    porteiro.info("Google identificou %s (%s)", email, ip)

    # Dispositivo novo, senha ja conferida: o Google acabou de provar a mesma
    # coisa que o codigo provaria -- que aquela caixa de e-mail e desta pessoa.
    # So aqui ele conclui um login, e so porque a senha veio antes.
    if _pendente(request) == email:
        nome = acesso.USUARIOS.buscar(email).get("nome", "") or nome
        porteiro.info("dispositivo novo liberado pelo Google: %s (%s)", email, ip)
        return _abrir_sessao(request, email, nome, "senha e Google", ip, destino)

    # Fora dessa janela ele nao abre sessao. Diz QUEM e a pessoa, nao que ela
    # sabe a senha -- e e a senha que separa "ela" de "alguem com o celular
    # dela na mao". Entao identifica, e devolve ao caminho normal.
    if not acesso.USUARIOS.tem_senha(email):
        return _enviar_codigo(request, email, destino, "primeiro", "primeiro")
    return _tela(request, "senha", destino, email=email,
                 aviso=f"Conta confirmada: {email}. Informe a sua senha para "
                       "concluir o acesso.")


@app.get("/sair")
def sair(request: Request):
    quem = request.session.get("email") or request.session.get("nome") or "?"
    request.session.clear()
    porteiro.info("saiu: %s", quem)
    return RedirectResponse("/entrar", status_code=303)


# --------------------------------------------------------------------------
# usuários (só admin)
# --------------------------------------------------------------------------
def _tela_usuarios(request: Request, erro=None, aviso=None, status_code: int = 200):
    return _render(request, "usuarios.html", "usuarios", status_code=status_code,
                   pessoas=acesso.USUARIOS.listar(),
                   eu=acesso.normalizar_email(request.session.get("email", "")),
                   erro=erro, aviso=aviso)


def _so_admin(request: Request):
    """Devolve a resposta de recusa, ou None se pode seguir."""
    if not _autenticado(request):
        return _para_login(request)
    if not acesso.e_admin(request.session.get("email", "")):
        porteiro.warning("acesso negado à tela de usuários para %s",
                         request.session.get("email"))
        return _render(request, "erro_permissao.html", "usuarios", status_code=403)
    return None


@app.get("/usuarios", response_class=HTMLResponse)
def ver_usuarios(request: Request):
    if recusa := _so_admin(request):
        return recusa
    return _tela_usuarios(request)


def _mexer_no_cadastro(request: Request, acao, sucesso: str):
    """Casca comum das ações: valida admin, executa e devolve a tela de novo."""
    if recusa := _so_admin(request):
        return recusa
    try:
        acao()
    except (ValueError, acesso.SemAdmin) as falha:
        return _tela_usuarios(request, erro=str(falha), status_code=400)
    except OSError as falha:
        porteiro.error("falha ao gravar o cadastro: %s", falha)
        return _tela_usuarios(request, erro="Não foi possível gravar o cadastro.",
                              status_code=500)
    porteiro.info("cadastro alterado por %s: %s",
                  request.session.get("email"), sucesso)
    return _tela_usuarios(request, aviso=sucesso)


@app.post("/usuarios/novo", response_class=HTMLResponse)
def usuario_novo(request: Request, email: str = Form(""), nome: str = Form(""),
                 papel: str = Form(acesso.PADRAO)):
    email = acesso.normalizar_email(email)
    if "@" not in email or "." not in email.split("@")[-1]:
        if recusa := _so_admin(request):
            return recusa
        return _tela_usuarios(request, status_code=400,
                              erro="Informe um endereço de e-mail válido.")
    quem = request.session.get("email", "")
    return _mexer_no_cadastro(
        request,
        lambda: acesso.USUARIOS.criar(email, nome, papel, quem),
        f"Usuário {email} cadastrado. A senha será definida pelo próprio "
        f"usuário no primeiro acesso.")


@app.post("/usuarios/papel", response_class=HTMLResponse)
def usuario_papel(request: Request, email: str = Form(""), papel: str = Form(acesso.PADRAO)):
    email = acesso.normalizar_email(email)
    eu = acesso.normalizar_email(request.session.get("email", ""))
    if email == eu and papel != acesso.ADMIN:
        if recusa := _so_admin(request):
            return recusa
        return _tela_usuarios(
            request, status_code=400,
            erro="Não é permitido remover o próprio perfil de administrador. "
                 "Solicite a alteração a outro administrador.")
    nome_papel = "administrador" if papel == acesso.ADMIN else "padrão"
    return _mexer_no_cadastro(
        request,
        lambda: acesso.USUARIOS.definir_papel(email, papel),
        f"O perfil de {email} foi alterado para {nome_papel}.")


@app.post("/usuarios/ativo", response_class=HTMLResponse)
def usuario_ativo(request: Request, email: str = Form(""), ativo: str = Form("")):
    email = acesso.normalizar_email(email)
    eu = acesso.normalizar_email(request.session.get("email", ""))
    ligar = str(ativo).strip() == "1"
    if email == eu and not ligar:
        if recusa := _so_admin(request):
            return recusa
        return _tela_usuarios(request, status_code=400,
                              erro="Não é permitido revogar o próprio acesso.")
    return _mexer_no_cadastro(
        request,
        lambda: acesso.USUARIOS.definir_ativo(email, ligar),
        f"O acesso de {email} foi {'reativado' if ligar else 'revogado'}.")


@app.post("/usuarios/senha", response_class=HTMLResponse)
def usuario_senha(request: Request, email: str = Form("")):
    email = acesso.normalizar_email(email)
    return _mexer_no_cadastro(
        request,
        lambda: acesso.USUARIOS.esquecer_senha(email),
        f"A senha de {email} foi removida. O usuário deverá defini-la "
        f"novamente pelo primeiro acesso.")


@app.post("/usuarios/apagar", response_class=HTMLResponse)
def usuario_apagar(request: Request, email: str = Form("")):
    email = acesso.normalizar_email(email)
    eu = acesso.normalizar_email(request.session.get("email", ""))
    if email == eu:
        if recusa := _so_admin(request):
            return recusa
        return _tela_usuarios(request, status_code=400,
                              erro="Não é permitido excluir a própria conta.")
    return _mexer_no_cadastro(
        request,
        lambda: acesso.USUARIOS.apagar(email),
        f"O usuário {email} foi excluído do cadastro.")


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
