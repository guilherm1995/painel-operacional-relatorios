# -*- coding: utf-8 -*-
"""Confere o token da tela de usuarios -- a unica que apaga conta.

Extrai as funcoes reais do app.py em vez de reescreve-las aqui: ensaio que
reimplementa o que testa passa a testar a si mesmo.
"""
import ast
import pathlib
import secrets
import sys
import types
from html.parser import HTMLParser

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WEB = pathlib.Path(r"C:\caminho\para\Documents\migracao_linux\site\web")
falhas = []


def diz(ok, rotulo, extra=""):
    print(f"  [{'ok ' if ok else 'FALHA'}] {rotulo}{extra}")
    if not ok:
        falhas.append(rotulo)


fonte = (WEB / "app.py").read_text(encoding="utf-8")
arvore = ast.parse(fonte)

pedacos = []
for no in arvore.body:
    if isinstance(no, ast.Assign) and any(
            getattr(a, "id", "") in ("CAMPO_CSRF", "TELAS_COM_CSRF")
            for a in no.targets):
        pedacos.append(ast.get_source_segment(fonte, no))
    if isinstance(no, ast.FunctionDef) and no.name in ("_csrf", "_csrf_confere"):
        pedacos.append(ast.get_source_segment(fonte, no))

if len(pedacos) != 4:
    print("esperava 4 pedacos (CAMPO_CSRF, TELAS_COM_CSRF, _csrf, "
          f"_csrf_confere), achei {len(pedacos)}")
    sys.exit(1)

ambiente = {"secrets": secrets, "Request": object}
exec("\n\n".join(pedacos), ambiente)
_csrf = ambiente["_csrf"]
_csrf_confere = ambiente["_csrf_confere"]
TELAS_COM_CSRF = ambiente["TELAS_COM_CSRF"]


def sessao_nova():
    return types.SimpleNamespace(session={})


print("\n1. O token em si")
print("-" * 62)

pedido = sessao_nova()
t1 = _csrf(pedido)
t2 = _csrf(pedido)
diz(t1 == t2, "o mesmo token vale para a sessao inteira")
diz(len(t1) >= 32, "tem tamanho de token", f"  ({len(t1)} caracteres)")

diz(_csrf_confere(pedido, t1), "o token certo passa")
diz(not _csrf_confere(pedido, ""), "vazio nao passa")
diz(not _csrf_confere(pedido, t1[:-1] + "x"), "token adulterado nao passa")
diz(not _csrf_confere(pedido, t1 + " "), "token com sujeira nao passa")

outro = sessao_nova()
t_outro = _csrf(outro)
diz(t_outro != t1, "cada sessao tem o seu")
diz(not _csrf_confere(pedido, t_outro),
    "token de OUTRA sessao nao serve -- e isto que barra o site de fora")

vazia = sessao_nova()
diz(not _csrf_confere(vazia, t1),
    "sessao sem token nao aceita token nenhum (nem o certo de outrem)")


print("\n2. Todos os formularios de /usuarios carregam o campo")
print("-" * 62)

env = Environment(loader=FileSystemLoader(str(WEB / "templates")),
                  undefined=ChainableUndefined)

TOKEN = "t0k3nDeEnsaio"
html = env.get_template("usuarios.html").render(
    csrf=TOKEN, nonce="n", sou_admin=True, logado=True, titulo_site="T",
    assinatura="x 2026", credito_nome="x", credito_ano="2026",
    autenticador_ativo=False, versao_estatica="1", usuario="a@b.c", pagina="usuarios",
    eu="operador@provedor.example", erro=None, aviso=None,
    pessoas=[
        {"email": "operador@provedor.example", "nome": "Admin", "papel": "admin",
         "ativo": True, "tem_senha": True, "criado_em": "", "ultimo_acesso": "",
         "entrou_por": "senha"},
        {"email": "operador@provedor.example", "nome": "Outro", "papel": "padrao",
         "ativo": True, "tem_senha": True, "criado_em": "", "ultimo_acesso": "",
         "entrou_por": "senha"},
    ],
)

# Este trecho contava a ocorrencia do TEXTO `name="csrf" value="..."` no HTML,
# e foi por isso que ele passou com um <form> quebrado: a tag de abertura tinha
# ficado `<form action="..." <input name="csrf" value="...">`, o navegador lia o
# input como ATRIBUTO do form, e o campo nunca chegava a existir. O texto estava
# la; o campo, nao. Contar string nao ve isso -- analisar a estrutura ve.
class Formularios(HTMLParser):
    def __init__(self):
        super().__init__()
        self.achados = []      # [action, [nomes de input filhos], [atributos]]
        self._atual = None

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "form":
            self._atual = [d.get("action", "?"), [], sorted(d)]
            self.achados.append(self._atual)
        elif tag == "input" and self._atual is not None:
            self._atual[1].append(d.get("name", ""))

    def handle_endtag(self, tag):
        if tag == "form":
            self._atual = None


leitor = Formularios()
leitor.feed(html)
alvo = [f for f in leitor.achados if str(f[0]).startswith("/usuarios/")]
com_token = [f for f in alvo if "csrf" in f[1]]

diz(len(alvo) > 0 and len(com_token) == len(alvo),
    "todo formulario tem um campo csrf DE VERDADE (filho, nao texto)",
    f"  ({len(com_token)} de {len(alvo)})")
for action, nomes, _ in alvo:
    if "csrf" not in nomes:
        print(f"         sem token -> {action}  (campos: {nomes or 'nenhum'})")

# Um <form> bem formado nao tem atributo chamado "<input", nem "value".
# Se tiver, uma tag de abertura engoliu um campo.
sujos = [f for f in alvo
         if any(a.startswith("<") or a in ("value", "type") for a in f[2])]
diz(not sujos, "nenhuma tag <form> engoliu um <input> como atributo",
    f"  ({len(sujos)} suspeita(s))")
for action, _, attrs in sujos:
    print(f"         tag suja -> {action}  (atributos: {attrs})")

# a tela nao pode entregar o token a quem nao e admin -- ela nem abre para eles,
# mas conferir e barato e o custo de errar aqui e alto
diz("{{ csrf }}" not in html, "o campo foi de fato preenchido, nao literal")


print("\n3. O token so nasce na tela que o usa")
print("-" * 62)

# Criar o token GRAVA na sessao, e gravar na sessao faz o Starlette responder
# com Set-Cookie. Enquanto isso acontecia em toda tela, quem so abria /entrar
# -- sem se identificar -- ja saia marcado com cookie de sessao.
#
# O acerto criou um acoplamento novo: `_render` so entrega o token as telas de
# TELAS_COM_CSRF. Um template que peca o campo e fique de fora da lista renderiza
# `{{ csrf }}` VAZIO, e a acao responde 403 sem uma palavra de explicacao. Por
# isso a conferencia vai nos DOIS sentidos: nem template pedindo sem receber,
# nem tela na lista sem precisar.
pedem = {p.name for p in (WEB / "templates").glob("*.html")
         if 'name="csrf"' in p.read_text(encoding="utf-8")}

diz(pedem == set(TELAS_COM_CSRF),
    "a lista do app.py bate com os templates que pedem o campo",
    f"  (app.py: {sorted(TELAS_COM_CSRF)} · templates: {sorted(pedem)})")

for nome in sorted(pedem - set(TELAS_COM_CSRF)):
    print(f"         pede e NAO recebe -> {nome}  (vai renderizar vazio e dar 403)")
for nome in sorted(set(TELAS_COM_CSRF) - pedem):
    print(f"         recebe e nao usa   -> {nome}  (cookie de sessao a toa)")

diz("login.html" not in TELAS_COM_CSRF,
    "a tela de entrar NAO ganha token -- e ela que visitante anonimo abre")


print()
print("-" * 62)
if falhas:
    print("FALHOU:\n  " + "\n  ".join(falhas))
else:
    print("OK: todas as asserções passaram.")
sys.exit(1 if falhas else 0)
