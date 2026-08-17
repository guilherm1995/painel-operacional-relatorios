# -*- coding: utf-8 -*-
"""Prova o que a identidade do Cloudflare Access dispensa -- e o que nao.

Importa o acesso.py de verdade (com um duble de configuracao) em vez de
reimplementar a regra aqui: ensaio que reescreve o que testa acaba testando a
si mesmo.

O caso que mais importa e o do Funnel: ele chega pelo MESMO 127.0.0.1 que a
Cloudflare, mas nao passa pelo Access. Se a regra confiasse so no par e no
cabecalho, qualquer pessoa com o endereco do Funnel viraria qualquer usuario.
"""
import os
import pathlib
import sys
import tempfile
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ_SITE = pathlib.Path(r"C:\caminho\para\Documents\migracao_linux\site")
sys.path.insert(0, str(RAIZ_SITE))

PUBLICO = "https://painel.example.com"
FUNNEL_HOST = "operacional.sua-tailnet.ts.net"

pacote = types.ModuleType("web")
pacote.__path__ = [str(RAIZ_SITE / "web")]
sys.modules["web"] = pacote

cfg = types.ModuleType("web.config")


class _ConfigFalsa:
    google_client_id = ""
    google_client_secret = ""
    endereco_publico = PUBLICO
    usuarios_iniciais = {}


cfg.CONFIG = _ConfigFalsa()
cfg.RAIZ = pathlib.Path(tempfile.mkdtemp(prefix="ensaio_access_"))
cfg.SOMENTE_LEITURA = "ENSAIO_SO_LEITURA"
sys.modules["web.config"] = cfg
os.environ["ENSAIO_SO_LEITURA"] = "1"

from web import acesso  # noqa: E402

EU = "operador@provedor.example"
falhas = []


class PedidoFalso:
    def __init__(self, par, **cabecalhos):
        self.headers = {k.lower().replace("_", "-"): v
                        for k, v in cabecalhos.items()}
        self.client = types.SimpleNamespace(host=par)


def confere(rotulo, obtido, esperado):
    ok = obtido == esperado
    print(f"  [{'ok ' if ok else 'FALHA'}] {rotulo}: {obtido!r}")
    if not ok:
        falhas.append(f"{rotulo}: esperava {esperado!r}, veio {obtido!r}")


print("\n1. O caminho legitimo")
print("-" * 66)

confere("pela Cloudflare, com Host do dominio publico",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", host="painel.example.com",
            cf_access_authenticated_user_email=EU)),
        EU)

confere("o e-mail vem normalizado",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", host="painel.example.com",
            cf_access_authenticated_user_email="  operador@provedor.example  ")),
        EU)


print("\n2. O que NAO pode passar")
print("-" * 66)

# Este e o motivo de a funcao olhar o Host. O Funnel chega pelo mesmo
# 127.0.0.1 e nao passa pelo Access: sem esta barreira, bastaria saber o
# endereco do Funnel para virar qualquer pessoa da lista.
confere("pelo FUNNEL, mesmo forjando o cabecalho",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", host=FUNNEL_HOST,
            cf_access_authenticated_user_email=EU)),
        "")

confere("conexao direta de fora (nao passa pelo proxy local)",
        acesso.identidade_do_access(PedidoFalso(
            "203.0.113.7", host="painel.example.com",
            cf_access_authenticated_user_email=EU)),
        "")

confere("sem o cabecalho",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", host="painel.example.com")),
        "")

confere("cabecalho vazio",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", host="painel.example.com",
            cf_access_authenticated_user_email="   ")),
        "")

confere("sem Host nenhum",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", cf_access_authenticated_user_email=EU)),
        "")

confere("Host parecido, mas nao igual",
        acesso.identidade_do_access(PedidoFalso(
            "127.0.0.1", host="painel.example.com.golpe.example",
            cf_access_authenticated_user_email=EU)),
        "")


print("\n3. A identidade dispensa o CODIGO, nunca a SENHA")
print("-" * 66)

# A regra do app.py e `identidade_do_access(request) == email`: ela so age
# depois de a senha ja ter sido conferida, e so libera o dispositivo novo.
# Aqui se prova que ela nao serve de identidade para OUTRA pessoa.
outro = "operador@provedor.example"
lido = acesso.identidade_do_access(PedidoFalso(
    "127.0.0.1", host="painel.example.com", cf_access_authenticated_user_email=EU))
ok = lido == EU and lido != outro
print(f"  [{'ok ' if ok else 'FALHA'}] o Access identifica {lido!r}, "
      f"entao NAO libera o login de {outro!r}")
if not ok:
    falhas.append("a identidade do Access nao esta presa ao e-mail que entra")

# E o mais importante: sem senha certa, o codigo do app.py nem chega aqui.
# Este ensaio cobre a funcao; a ordem (senha primeiro) esta no fazer_login.
print("  [nota] a senha e conferida ANTES deste ponto no fazer_login() --")
print("         esta funcao nunca e consultada para quem errou a senha.")


print()
print("-" * 66)
if falhas:
    print("FALHOU:\n  " + "\n  ".join(falhas))
else:
    print("OK: todas as asserções passaram.")
sys.exit(1 if falhas else 0)
