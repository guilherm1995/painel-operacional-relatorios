"""A portaria do site: quem entra, por onde, e o que fazer com quem insiste errado.

Enquanto o endereco publico era um sorteio da Cloudflare que mudava a cada
reinicio, o PIN sozinho bastava -- ninguem achava o site sem receber o link.
Com o endereco fixo do Tailscale isso acaba: o nome do servidor vai parar no
Certificate Transparency, que e um registro publico, e varredura automatica
acha em minutos. Dai este modulo.

O PIN unico que todo mundo sabia saiu de cena. No lugar, duas portas -- e as
duas passam pela mesma lista de e-mails autorizados:

  Google  - um toque, porque o celular ja esta logado. So funciona no endereco
            publico HTTPS: o Google se recusa a redirecionar para http:// que
            nao seja localhost.
  Senha   - e-mail e senha propria. Funciona em qualquer endereco, inclusive
            pela rede local, onde o Google nao alcanca. E por isso que tirar o
            PIN nao deixou ninguem trancado do lado de fora.

Quem ainda nao tem senha faz o primeiro acesso com um codigo enviado ao
proprio e-mail. O mesmo caminho serve para quem esqueceu a senha.

Nada disso vale se der para tentar a noite inteira, entao a Portaria fecha a
porta progressivamente por IP.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode, urlsplit

import requests

from web.config import CONFIG, RAIZ, SOMENTE_LEITURA

logger = logging.getLogger("operacional.acesso")

# Endpoints do Google. Estao no documento de descoberta OpenID Connect
# (https://accounts.google.com/.well-known/openid-configuration) e nao mudam.
GOOGLE_AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_QUEM_E = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_ESCOPO = "openid email profile"
ESPERA_REDE_SEG = 15

CAMINHO_RETORNO = "/entrar/google/retorno"


# ---------------------------------------------------------------------------
# chave da sessao
# ---------------------------------------------------------------------------
ARQUIVO_CHAVE = RAIZ / "config" / "chave_sessao"


def chave_de_sessao() -> str:
    """Chave que assina o cookie de sessao, estavel entre reinicios.

    Antes era sorteada a cada boot, o que deslogava todo mundo sempre que o
    servico reiniciava -- e ele reinicia sozinho (Restart=always). Com endereco
    fixo as pessoas passam a deixar a aba aberta, entao isso incomodaria muito
    mais. Guardar em disco resolve; se o arquivo sumir, sorteia outra e o unico
    efeito e um logout geral.
    """
    try:
        guardada = ARQUIVO_CHAVE.read_text(encoding="utf-8").strip()
        if len(guardada) >= 32:
            return guardada
    except OSError:
        pass

    nova = secrets.token_hex(32)
    try:
        ARQUIVO_CHAVE.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_CHAVE.write_text(nova, encoding="utf-8")
        # 0600: quem tem esta chave forja uma sessao de qualquer pessoa.
        ARQUIVO_CHAVE.chmod(0o600)
    except OSError as erro:
        logger.warning("nao consegui guardar a chave de sessao (%s); "
                       "as sessoes vao cair a cada reinicio", erro)
    return nova


# ---------------------------------------------------------------------------
# escada de castigo
# ---------------------------------------------------------------------------
# Quantos IPs a portaria lembra. Alem disso o mais antigo sai, para um atacante
# trocando de IP nao conseguir inchar a memoria do processo.
LIMITE_MEMORIA = 4000

# (tentativas erradas, segundos de porta fechada). Em ordem decrescente: vale a
# primeira que casar, entao a partir de 20 erros e uma hora por tentativa.
ESCADA = ((20, 3600), (15, 1800), (10, 300), (5, 30))

# Custo fixo de cada erro, mesmo antes do primeiro bloqueio. Segura o volume de
# quem tenta em rajada sem atrapalhar quem so errou de dedo.
ATRASO_POR_ERRO_SEG = 1.0


class Portaria:
    """Conta tentativas erradas por IP e fecha a porta progressivamente.

    Vale para senha e para codigo. A partir de 20 erros e uma tentativa por
    hora vinda daquele IP -- o que torna inviavel varrer senhas, mesmo curtas,
    e limita muito o chute do codigo de 6 digitos dentro dos 15 minutos em que
    ele vale.
    """

    def __init__(self) -> None:
        self._erros: OrderedDict[str, list] = OrderedDict()
        self._trava = Lock()

    def bloqueio_restante(self, ip: str) -> int:
        """Segundos que ainda faltam para este IP poder tentar de novo."""
        with self._trava:
            registro = self._erros.get(ip)
            if not registro:
                return 0
            return max(0, int(round(registro[1] - time.monotonic())))

    def errou(self, ip: str) -> int:
        """Registra um erro. Devolve quantos segundos o IP fica de castigo."""
        with self._trava:
            registro = self._erros.pop(ip, None) or [0, 0.0]
            registro[0] += 1
            for limite, castigo in ESCADA:
                if registro[0] >= limite:
                    registro[1] = time.monotonic() + castigo
                    break
            # reinserir joga para o fim da fila: quem some ha mais tempo e o
            # primeiro a ser esquecido quando a memoria estoura
            self._erros[ip] = registro
            while len(self._erros) > LIMITE_MEMORIA:
                self._erros.popitem(last=False)
            return max(0, int(round(registro[1] - time.monotonic())))

    def acertou(self, ip: str) -> None:
        with self._trava:
            self._erros.pop(ip, None)


PORTARIA = Portaria()


def ip_do_pedido(request) -> str:
    """De onde veio o pedido, para efeito de contagem.

    Sob o Funnel o tailscaled entrega tudo em 127.0.0.1 e poe o IP real em
    X-Forwarded-For. Esse cabecalho e forjavel, entao ele NAO serve para
    autorizar nada -- mas para contar tentativas so ajuda: quem forja esta
    apenas trocando de balde, e o balde de quem nao forja continua certo.
    """
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()[:45] or "?"
    cliente = getattr(request, "client", None)
    return getattr(cliente, "host", None) or "?"


# ---------------------------------------------------------------------------
# Google
# ---------------------------------------------------------------------------
def endereco_de_retorno() -> str:
    """A URL exata que precisa estar cadastrada no console do Google."""
    base = str(CONFIG.endereco_publico or "").strip().rstrip("/")
    return f"{base}{CAMINHO_RETORNO}" if base else ""


def google_configurado() -> bool:
    return bool(CONFIG.google_client_id and CONFIG.google_client_secret
                and endereco_de_retorno())


def google_disponivel_aqui(request) -> bool:
    """Se vale a pena mostrar o botao do Google nesta tela.

    So no endereco publico: clicar nele pela rede local levaria a pessoa para o
    dominio publico e ela acabaria logada la, nao aqui. E pura decisao de tela
    -- nao e barreira de seguranca, e nem precisa ser.
    """
    if not google_configurado():
        return False
    esperado = urlsplit(str(CONFIG.endereco_publico).strip()).netloc.lower()
    return bool(esperado) and request.headers.get("host", "").lower() == esperado


def url_para_o_google(estado: str) -> str:
    parametros = {
        "client_id": CONFIG.google_client_id,
        "redirect_uri": endereco_de_retorno(),
        "response_type": "code",
        "scope": GOOGLE_ESCOPO,
        "state": estado,
        # sempre perguntar de qual conta se trata: muita gente tem a pessoal e a
        # do trabalho no mesmo celular, e entrar com a errada so gera confusao
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTORIZAR}?{urlencode(parametros)}"


def identidade_do_codigo(codigo: str) -> dict:
    """Troca o codigo de uso unico pela identidade de quem entrou.

    Nao verifico a assinatura do id_token de proposito: o token nao veio pelo
    navegador, veio de uma chamada HTTPS que este processo fez direto ao
    Google, autenticada com o client_secret. Quem pode responder isso e o
    Google. Por isso perguntar o e-mail no /userinfo e suficiente, e uma
    dependencia a menos para dar errado.
    """
    resposta = requests.post(GOOGLE_TOKEN, timeout=ESPERA_REDE_SEG, data={
        "code": codigo,
        "client_id": CONFIG.google_client_id,
        "client_secret": CONFIG.google_client_secret,
        "redirect_uri": endereco_de_retorno(),
        "grant_type": "authorization_code",
    })
    resposta.raise_for_status()
    token = resposta.json().get("access_token")
    if not token:
        raise ValueError("o Google nao devolveu access_token")

    quem = requests.get(GOOGLE_QUEM_E, timeout=ESPERA_REDE_SEG,
                        headers={"Authorization": f"Bearer {token}"})
    quem.raise_for_status()
    return quem.json()


def conta_do_google(dados: dict) -> tuple[str, str]:
    """Extrai (email, nome) do que o Google respondeu. Vazio se nao servir."""
    email = str(dados.get("email") or "").strip().lower()
    if not email or not dados.get("email_verified"):
        return "", ""
    return email, str(dados.get("name") or email)


# ---------------------------------------------------------------------------
# quem tem direito de entrar
# ---------------------------------------------------------------------------
def normalizar_email(email: str) -> str:
    return str(email or "").strip().lower()


ADMIN = "admin"
PADRAO = "padrao"
PAPEIS = (ADMIN, PADRAO)


def autorizado(email: str) -> bool:
    """Quem esta no cadastro e ativo. Conferido em TODO pedido, nao so no login.

    E o que faz revogar alguem valer na hora: a sessao dela morre no proximo
    clique, em vez de sobreviver ate vencer -- o que pode ser meio dia.
    """
    registro = USUARIOS.buscar(email)
    return bool(registro) and registro.get("ativo", True)


def e_admin(email: str) -> bool:
    registro = USUARIOS.buscar(email)
    return bool(registro) and registro.get("ativo", True) and registro.get("papel") == ADMIN


# ---------------------------------------------------------------------------
# senhas
# ---------------------------------------------------------------------------
# scrypt e da biblioteca padrao -- nada para instalar num servidor atras de VPN
# corporativa. Estes parametros sao os recomendados pelo proprio modulo e
# custam ~100ms por verificacao, que e o ponto: encarece o teste em massa.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
TAMANHO_MINIMO_SENHA = 10


def cifrar_senha(senha: str) -> dict:
    sal = secrets.token_bytes(16)
    bruto = hashlib.scrypt(senha.encode("utf-8"), salt=sal,
                           n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return {"algoritmo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
            "sal": sal.hex(), "hash": bruto.hex()}


def senha_confere(senha: str, guardado: dict) -> bool:
    if not guardado or guardado.get("algoritmo") != "scrypt":
        return False
    try:
        bruto = hashlib.scrypt(
            senha.encode("utf-8"), salt=bytes.fromhex(guardado["sal"]),
            n=int(guardado["n"]), r=int(guardado["r"]), p=int(guardado["p"]))
    except (KeyError, ValueError):
        return False
    return secrets.compare_digest(bruto.hex(), str(guardado.get("hash", "")))


def _lista(itens: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b e c'."""
    if len(itens) == 1:
        return itens[0]
    return ", ".join(itens[:-1]) + f" e {itens[-1]}"


def problema_na_senha(senha: str, repetida: str) -> str:
    """Devolve o que ha de errado com a senha, ou string vazia se esta boa.

    Exigimos as quatro familias de caractere e um comprimento minimo. O
    comprimento e o que mais pesa contra quem tenta adivinhar em massa; as
    familias existem para impedir a senha obvia -- o nome da pessoa, o nome da
    empresa, uma data.
    """
    if senha != repetida:
        return "As senhas informadas não coincidem."
    if len(senha) < TAMANHO_MINIMO_SENHA:
        return f"A senha deve conter no mínimo {TAMANHO_MINIMO_SENHA} caracteres."
    if senha.strip() != senha:
        return "A senha não pode iniciar ou terminar com espaço."

    faltando = []
    if not any(c.isupper() for c in senha):
        faltando.append("letra maiúscula")
    if not any(c.islower() for c in senha):
        faltando.append("letra minúscula")
    if not any(c.isdigit() for c in senha):
        faltando.append("número")
    # qualquer coisa que nao seja letra, digito ou espaco -- assim vale o que a
    # pessoa tiver no teclado dela, sem uma lista que sempre esquece um simbolo
    if not any(not c.isalnum() and not c.isspace() for c in senha):
        faltando.append("caractere especial")

    if faltando:
        return f"A senha deve conter também: {_lista(faltando)}."
    return ""


# ---------------------------------------------------------------------------
# navegadores conhecidos
# ---------------------------------------------------------------------------
# A senha sozinha nao basta num navegador que o site nunca viu: senha vaza, e
# quando vaza o atacante tenta do computador dele. Entao, em maquina
# desconhecida, alem da senha exigimos a prova de que a caixa de e-mail e da
# pessoa -- pelo codigo ou pelo Google, que provam a mesma coisa.
#
# No navegador de sempre nao pedimos nada disso. Nao e preguica: segundo fator
# cobrado todo dia vira senha anotada em papel e gente compartilhando conta,
# que e pior do que o problema que ele resolvia.
DIAS_NAVEGADOR = 90
MAXIMO_NAVEGADORES = 10
# so regravo a data se ela ja estiver velha; sem isso todo login reescreve o
# arquivo inteiro sem necessidade
FOLGA_PARA_REGRAVAR_SEG = 12 * 3600


def nova_marca_de_navegador() -> str:
    """O valor que vai no cookie do navegador."""
    return secrets.token_urlsafe(24)


def _resumo_da_marca(marca: str) -> str:
    """Do cookie guardo o sha256, nunca o valor.

    Mesma logica do hash de senha: se o usuarios.json vazar, o que sai dali
    nao serve para montar um cookie e se passar por navegador conhecido.
    """
    return hashlib.sha256(str(marca or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# cadastro
# ---------------------------------------------------------------------------
ARQUIVO_USUARIOS = RAIZ / "config" / "usuarios.json"


class SemAdmin(RuntimeError):
    """A mudanca deixaria o site sem nenhum administrador ativo."""


class Usuarios:
    """Cadastro em arquivo. Sao poucas pessoas, nao precisa de banco.

    Le e grava o arquivo inteiro a cada mudanca, sob trava, e grava por
    substituicao atomica -- um desligamento no meio da escrita nao deixa um
    JSON pela metade que trancaria todo mundo para fora.

    Toda alteracao passa por _mudar(), que recusa a gravacao se o resultado
    ficaria sem administrador ativo. Nao existe caminho pela tela que leve a
    isso; a trava esta aqui porque o custo de errar e perder o site.
    """

    def __init__(self, caminho: Path = ARQUIVO_USUARIOS) -> None:
        self._caminho = caminho
        self._trava = Lock()

    # -- disco -----------------------------------------------------------
    def _ler(self) -> dict:
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
            return dados if isinstance(dados, dict) else {}
        except (OSError, ValueError):
            return {}

    def _gravar(self, dados: dict) -> None:
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        provisorio = self._caminho.with_name(self._caminho.name + ".novo")
        provisorio.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        try:
            provisorio.chmod(0o600)
        except OSError:
            pass
        provisorio.replace(self._caminho)

    def _mudar(self, funcao) -> None:
        """Aplica uma mudanca no cadastro, recusando ficar sem admin."""
        with self._trava:
            dados = self._ler()
            funcao(dados)
            if not any(r.get("papel") == ADMIN and r.get("ativo", True)
                       for r in dados.values()):
                raise SemAdmin(
                    "A operação deixaria o sistema sem nenhum administrador ativo.")
            self._gravar(dados)

    # -- consulta --------------------------------------------------------
    def buscar(self, email: str) -> dict:
        return self._ler().get(normalizar_email(email)) or {}

    def tem_senha(self, email: str) -> bool:
        return bool(self.buscar(email).get("senha"))

    def listar(self) -> list[dict]:
        """Todos, em ordem alfabetica, sem os hashes de senha."""
        saida = []
        for email, registro in sorted(self._ler().items()):
            saida.append({
                "email": email,
                "nome": registro.get("nome") or "",
                "papel": registro.get("papel") or PADRAO,
                "ativo": registro.get("ativo", True),
                "tem_senha": bool(registro.get("senha")),
                "criado_em": registro.get("criado_em") or "",
                "ultimo_acesso": registro.get("ultimo_acesso") or "",
                "entrou_por": registro.get("entrou_por") or "",
            })
        return saida

    def quantos_admins_ativos(self) -> int:
        return sum(1 for r in self._ler().values()
                   if r.get("papel") == ADMIN and r.get("ativo", True))

    # -- administracao ---------------------------------------------------
    def criar(self, email: str, nome: str, papel: str, por: str) -> None:
        email = normalizar_email(email)
        papel = papel if papel in PAPEIS else PADRAO

        def aplicar(dados):
            if email in dados:
                raise ValueError("Este e-mail já consta no cadastro.")
            dados[email] = {
                "nome": nome.strip(),
                "papel": papel,
                "ativo": True,
                "criado_em": _agora(),
                "criado_por": por,
            }
        self._mudar(aplicar)

    def definir_papel(self, email: str, papel: str) -> None:
        email = normalizar_email(email)
        papel = papel if papel in PAPEIS else PADRAO

        def aplicar(dados):
            if email not in dados:
                raise ValueError("Este e-mail não consta no cadastro.")
            dados[email]["papel"] = papel
        self._mudar(aplicar)

    def definir_ativo(self, email: str, ativo: bool) -> None:
        email = normalizar_email(email)

        def aplicar(dados):
            if email not in dados:
                raise ValueError("Esse e-mail não está no cadastro.")
            dados[email]["ativo"] = bool(ativo)
        self._mudar(aplicar)

    def apagar(self, email: str) -> None:
        email = normalizar_email(email)

        def aplicar(dados):
            if email not in dados:
                raise ValueError("Esse e-mail não está no cadastro.")
            del dados[email]
        self._mudar(aplicar)

    def esquecer_senha(self, email: str) -> None:
        """Tira a senha da pessoa: ela refaz o primeiro acesso pelo e-mail."""
        email = normalizar_email(email)

        def aplicar(dados):
            if email not in dados:
                raise ValueError("Esse e-mail não está no cadastro.")
            dados[email].pop("senha", None)
            dados[email].pop("senha_em", None)
            dados[email].pop("navegadores", None)
        self._mudar(aplicar)

    # -- navegadores conhecidos ------------------------------------------
    def navegador_conhecido(self, email: str, marca: str) -> bool:
        if not marca:
            return False
        guardado = (self.buscar(email).get("navegadores") or {})
        visto = guardado.get(_resumo_da_marca(marca))
        if not visto:
            return False
        idade = time.time() - float(visto.get("epoca") or 0)
        return idade < DIAS_NAVEGADOR * 86400

    def registrar_navegador(self, email: str, marca: str, descricao: str = "") -> None:
        """Passa a confiar neste navegador para os proximos logins."""
        if not marca:
            return
        email = normalizar_email(email)
        resumo = _resumo_da_marca(marca)

        def aplicar(dados):
            registro = dados.setdefault(email, {"papel": PADRAO, "ativo": True,
                                                "criado_em": _agora()})
            conhecidos = registro.setdefault("navegadores", {})
            conhecidos[resumo] = {
                "descricao": descricao[:120],
                "visto_em": _agora(),
                "epoca": time.time(),
            }
            # o mais velho sai quando passa do teto; sem isso o registro de
            # quem troca de celular todo mes cresce para sempre
            if len(conhecidos) > MAXIMO_NAVEGADORES:
                sobrando = sorted(conhecidos.items(),
                                  key=lambda par: float(par[1].get("epoca") or 0))
                for chave, _ in sobrando[:len(conhecidos) - MAXIMO_NAVEGADORES]:
                    conhecidos.pop(chave, None)
        self._mudar(aplicar)

    def renovar_navegador(self, email: str, marca: str) -> None:
        """Adia o vencimento de quem usa o site todo dia, sem regravar sempre."""
        if not marca:
            return
        resumo = _resumo_da_marca(marca)
        visto = (self.buscar(email).get("navegadores") or {}).get(resumo)
        if not visto:
            return
        if time.time() - float(visto.get("epoca") or 0) < FOLGA_PARA_REGRAVAR_SEG:
            return
        self.registrar_navegador(email, marca, visto.get("descricao", ""))

    def esquecer_navegadores(self, email: str) -> None:
        """Nenhum navegador e mais confiavel: todos voltam a pedir codigo."""
        email = normalizar_email(email)

        def aplicar(dados):
            if email in dados:
                dados[email].pop("navegadores", None)
        self._mudar(aplicar)

    def quantos_navegadores(self, email: str) -> int:
        return len(self.buscar(email).get("navegadores") or {})

    # -- uso normal ------------------------------------------------------
    def definir_senha(self, email: str, senha: str, nome: str = "") -> None:
        email = normalizar_email(email)

        def aplicar(dados):
            registro = dados.setdefault(email, {"papel": PADRAO, "ativo": True,
                                                "criado_em": _agora()})
            registro["senha"] = cifrar_senha(senha)
            registro["senha_em"] = _agora()
            # Trocar a senha derruba todos os navegadores confiaveis. Quem
            # redefine senha ou esqueceu, ou desconfia -- nos dois casos a lista
            # de "pode entrar so com a senha" e justamente o que nao deve
            # sobreviver. Fica aqui, e nao na rota, para nao ter como esquecer.
            registro.pop("navegadores", None)
            if nome and not registro.get("nome"):
                registro["nome"] = nome
        self._mudar(aplicar)

    def registrar_entrada(self, email: str, por: str, nome: str = "") -> None:
        email = normalizar_email(email)

        def aplicar(dados):
            registro = dados.setdefault(email, {"papel": PADRAO, "ativo": True,
                                                "criado_em": _agora()})
            registro["ultimo_acesso"] = _agora()
            registro["entrou_por"] = por
            if nome:
                registro["nome"] = nome
        self._mudar(aplicar)

    # -- primeira execucao -----------------------------------------------
    def semear(self, iniciais: dict) -> None:
        """Cria o cadastro inicial a partir do site.json, se ele estiver vazio.

        So roda com o cadastro vazio. Depois disso quem manda e a tela de
        usuarios, e mexer no site.json nao devolve acesso a ninguem -- e por
        isso que a semente precisa ter pelo menos um admin.
        """
        if self._ler():
            return
        # o instalador em modo --conferir nao pode criar arquivo nenhum
        if os.environ.get(SOMENTE_LEITURA) == "1":
            return
        dados = {}
        for email, papel in (iniciais or {}).items():
            email = normalizar_email(email)
            if not email:
                continue
            dados[email] = {
                "nome": "",
                "papel": papel if papel in PAPEIS else PADRAO,
                "ativo": True,
                "criado_em": _agora(),
                "criado_por": "config/site.json",
            }
        if not any(r["papel"] == ADMIN for r in dados.values()):
            logger.error("usuarios_iniciais nao tem nenhum admin -- cadastro nao semeado")
            return
        with self._trava:
            if not self._ler():
                self._gravar(dados)
                logger.info("cadastro semeado com %d pessoa(s)", len(dados))


def _agora() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


USUARIOS = Usuarios()
USUARIOS.semear(CONFIG.usuarios_iniciais)


# ---------------------------------------------------------------------------
# codigo de uso unico
# ---------------------------------------------------------------------------
VALIDADE_CODIGO_MIN = 15
TENTATIVAS_POR_CODIGO = 5
# Nao adianta pedir codigo em rajada para inundar a caixa de alguem.
INTERVALO_ENTRE_PEDIDOS_SEG = 60


class Codigos:
    """Codigos de acesso na memoria do processo.

    De proposito nao vao para disco: se o site reiniciar, o codigo perde a
    validade e a pessoa pede outro. Isso custa um clique e evita ter codigo
    valido dormindo em arquivo.
    """

    def __init__(self) -> None:
        self._itens: dict[str, dict] = {}
        self._trava = Lock()

    def pode_pedir(self, email: str) -> int:
        """Segundos que faltam para poder pedir outro codigo. 0 = pode agora."""
        with self._trava:
            item = self._itens.get(normalizar_email(email))
            if not item:
                return 0
            falta = item["pedido_em"] + INTERVALO_ENTRE_PEDIDOS_SEG - time.monotonic()
            return max(0, int(round(falta)))

    def gerar(self, email: str) -> str:
        codigo = f"{secrets.randbelow(1000000):06d}"
        with self._trava:
            self._itens[normalizar_email(email)] = {
                "codigo": codigo,
                "pedido_em": time.monotonic(),
                "vence_em": time.monotonic() + VALIDADE_CODIGO_MIN * 60,
                "tentativas": 0,
            }
        return codigo

    def conferir(self, email: str, informado: str) -> tuple[bool, str]:
        informado = str(informado or "").strip()
        with self._trava:
            chave = normalizar_email(email)
            item = self._itens.get(chave)
            if not item:
                return False, "Código inválido. Solicite um novo código."
            if time.monotonic() > item["vence_em"]:
                del self._itens[chave]
                return False, "O código expirou. Solicite um novo código."
            item["tentativas"] += 1
            if item["tentativas"] > TENTATIVAS_POR_CODIGO:
                del self._itens[chave]
                return False, "Número de tentativas excedido. Solicite um novo código."
            if not secrets.compare_digest(informado, item["codigo"]):
                faltam = TENTATIVAS_POR_CODIGO - item["tentativas"]
                if faltam <= 0:
                    del self._itens[chave]
                    return False, "Errou o código vezes demais. Peça outro."
                return False, f"Código incorreto. Restam {faltam} tentativas."
            del self._itens[chave]          # uso unico
            return True, ""


CODIGOS = Codigos()
