# -*- coding: utf-8 -*-
"""Confere o middleware `guarda` e o que ele exige dos templates.

Tres perguntas:
  1. A CSP monta certo e prende o script ao nonce?
  2. A conferencia de origem aceita o que e nosso e recusa o que nao e --
     inclusive durante a migracao, quando ha DOIS enderecos validos?
  3. Todo <script> embutido carrega o nonce? (sem isso o navegador se recusa
     a executa-lo, e a tela quebra calada)
"""
import ast
import pathlib
import re
import sys
import types
from html.parser import HTMLParser
from urllib.parse import urlsplit

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Argumento opcional: e assim que se prova que este ensaio enxerga. Copie a
# pasta web/ para um canto, estrague um template de proposito (um <script> sem
# nonce, um <script src> de outro dominio) e aponte para a copia -- tem de
# FALHAR. Verificador que nunca falhou nao foi verificado.
WEB = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                   else r"C:\caminho\para\Documents\migracao_linux\site\web")
falhas = []


def diz(ok, rotulo, extra=""):
    print(f"  [{'ok ' if ok else 'FALHA'}] {rotulo}{extra}")
    if not ok:
        falhas.append(rotulo)


# ---------------------------------------------------------------------------
# extrai _CSP e _origens_aceitas do app.py real, sem importar o modulo inteiro
# (importar puxaria playwright, pandas e a configuracao de producao)
# ---------------------------------------------------------------------------
fonte = (WEB / "app.py").read_text(encoding="utf-8")
arvore = ast.parse(fonte)

pedacos = []
for no in arvore.body:
    if isinstance(no, ast.Assign) and any(
            getattr(a, "id", "") == "_CSP" for a in no.targets):
        pedacos.append(ast.get_source_segment(fonte, no))
    if isinstance(no, ast.FunctionDef) and no.name == "_origens_aceitas":
        pedacos.append(ast.get_source_segment(fonte, no))

if len(pedacos) != 2:
    print("nao consegui extrair _CSP e _origens_aceitas do app.py")
    sys.exit(1)


class _ConfigFalsa:
    endereco_publico = "https://painel.example.com"


ambiente = {"urlsplit": urlsplit, "CONFIG": _ConfigFalsa(), "Request": object}
exec("\n\n".join(pedacos), ambiente)
_CSP = ambiente["_CSP"]
_origens_aceitas = ambiente["_origens_aceitas"]


print("\n1. A politica de conteudo")
print("-" * 64)
montada = _CSP.format(nonce="ABC123")


def diretivas(politica: str) -> dict[str, list[str]]:
    """Quebra a politica em diretiva -> valores.

    A versao anterior fazia `politica.split("script-src")[1]` e procurava
    'unsafe-inline' no que sobrava. Isso NAO le a diretiva script-src: le tudo
    o que vem depois dela na string. Hoje script-src e a ultima e a conta bate
    por acaso; basta alguem reordenar a politica para o teste passar a ler o
    style-src -- que tem 'unsafe-inline' de proposito -- e gritar por nada. E,
    ao contrario, um 'unsafe-inline' que aparecesse ANTES de script-src
    passaria despercebido.
    """
    quebrado: dict[str, list[str]] = {}
    for parte in politica.split(";"):
        campos = parte.split()
        if campos:
            quebrado[campos[0]] = campos[1:]
    return quebrado


d = diretivas(montada)
diz("{" not in montada and "}" not in montada, "a CSP monta sem chave sobrando")
diz("'nonce-ABC123'" in d.get("script-src", []),
    "o script fica preso ao nonce do pedido")
diz("'unsafe-inline'" not in d.get("script-src", []),
    "script-src NAO libera inline solto", f"  ({' '.join(d.get('script-src', ['—']))})")
diz(d.get("frame-ancestors") == ["'none'"], "clickjacking barrado")
diz(d.get("default-src") == ["'self'"], "nada de fora por omissao")
diz(d.get("object-src") == ["'none'"] and d.get("base-uri") == ["'none'"],
    "object-src e base-uri fechados")
print(f"       {montada[:78]}…")


print("\n2. A conferencia de origem")
print("-" * 64)


def pedido(host):
    return types.SimpleNamespace(headers={"host": host})


aceitas = _origens_aceitas(pedido("painel.example.com"))
diz("https://painel.example.com" in aceitas, "aceita o dominio novo")

# durante a migracao os dois enderecos respondem ao mesmo tempo
aceitas_antigo = _origens_aceitas(pedido("operacional.sua-tailnet.ts.net"))
diz("https://operacional.sua-tailnet.ts.net" in aceitas_antigo,
    "aceita o endereco antigo pelo Host")
diz("https://painel.example.com" in aceitas_antigo,
    "e o configurado junto, para a transicao nao quebrar formulario")

diz("https://sitedogolpe.example" not in aceitas,
    "recusa origem de fora")


print("\n3. Os <script> embutidos carregam o nonce")
print("-" * 64)

env = Environment(loader=FileSystemLoader(str(WEB / "templates")),
                  undefined=ChainableUndefined)

NONCE = "n0nc3DeEnsaio"
contexto = dict(
    nonce=NONCE, sou_admin=True, logado=True, titulo_site="T", assinatura="x 2026",
    credito_nome="x", credito_ano="2026", autenticador_ativo=False, versao_estatica="1",
    usuario="a@b.c", pagina="backlog", marca="m", minutos_entre_pedidos=10,
    imagens=[], imagens_por_regiao={}, gerado_em="", arquivos=[], telas=[],
    log=[], caminho_log="", pasta_relatorios="/opt/operacional/bot/relatorios",
    est={"disponivel": True, "capex_fresco": True, "erros_log_hoje": 0,
         "capex_pendente_litoral_sp": 1, "capex_pendente_sul_rj": 2,
         "capex_pendente_total": 3, "capex_notificadas_hoje": 0,
         "garantias_notificadas_hoje": 0, "os_analisadas_hoje": 10,
         "data_referencia": "2026-08-16", "minutos": 1, "quando": ""},
    bot={"rodando": True, "log_em": "", "lock": True},
    entrantes={"total": 0, "linhas": [], "serie": [], "maximo_serie": 0},
    ofs={"vale": True, "existe": True, "quando": "", "horas": 1, "limite_horas": 8},
    dados={}, painel={}, imagens_painel=[], volta="/garantias",
    config={"clusters": [{"codigo": "CL1", "rotulo": "CL1", "ativo": True,
                          "carga": 10}],
            "dias_opcoes": {"auto": "Automático"}, "mo_ativa_operacional": "",
            "tipo_dia": "auto"},
    ultima={"quando": ""}, config_salva="", config_erro="", resultado=None,
    erro=None, dia="d0", enviado="", falhou="", problemas=[], agora="",
    pessoas=[], eu="", aviso=None,
)

class ColetorDeManipulador(HTMLParser):
    """Junta todo atributo `on*` -- onclick, onsubmit, onchange...

    Sob esta CSP eles NAO executam, e o nonce nao os salva: nonce vale para a
    tag <script>, nao para atributo de evento. Pior que nao executar: num
    <form onsubmit="return confirm(...)"> o navegador bloqueia o manipulador e
    ENVIA O FORMULARIO ASSIM MESMO -- a confirmacao some e a acao acontece. Foi
    o que aconteceu com o "Confirma a exclusao?" do botao Excluir, calado desde
    que a CSP entrou.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.achados: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        for nome, _valor in attrs:
            if nome.startswith("on") and len(nome) > 2:
                self.achados.append((tag, nome))


class ColetorDeScript(HTMLParser):
    """Junta cada <script> que o navegador veria, com seus atributos.

    Contar `html.count("<script>")` -- como era aqui -- so enxerga a tag
    escrita EXATAMENTE assim. Nao ve <script src=...>, nem
    <script type="module">, nem <script nonce="x" defer>, nem um espaco a
    mais. Foi por isso que o <script src> do base.html, sem nonce nenhum,
    atravessou este ensaio desde sempre: nao e a string que se procurava.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


def scripts_de(html: str) -> list[dict]:
    coletor = ColetorDeScript()
    coletor.feed(html)
    return coletor.scripts


# Antes de confiar no coletor, prova que ele acusa o caso ruim. Um verificador
# quebrado e indistinguivel de um template correto: os dois calam.
_prova = scripts_de('<script>alerta()</script><script src="/a.js"></script>'
                    '<script nonce="X" defer>x</script>')
if len(_prova) != 3 or _prova[0] or _prova[1].get("src") != "/a.js" \
        or _prova[2].get("nonce") != "X":
    print("  [FALHA] o proprio coletor de <script> nao enxerga; "
          f"o resto desta secao nao vale nada -> {_prova}")
    falhas.append("coletor de <script> quebrado")

# A lista vem do disco, nao escrita a mao: era uma lista fixa de 4 nomes, e
# template novo com script novo nascia fora do ensaio, sem ninguem notar.
PAGINAS = sorted(p.name for p in (WEB / "templates").glob("*.html")
                 if not p.name.startswith("_"))

total_scripts = 0
for nome in PAGINAS:
    try:
        html = env.get_template(nome).render(**contexto)
    except Exception as e:
        # Pedaco carregado por fetch (confirmacao_dados, garantias_dados) so
        # renderiza com um conjunto de dados de verdade, e montar um aqui seria
        # mais uma copia do formato para manter. Se o fonte nao tem <script>,
        # nao ha o que conferir e o ensaio nao mente dizendo "ok". Se TEM, a
        # falta de render vira falha: sem renderizar, ninguem confere o nonce.
        fonte_tpl = (WEB / "templates" / nome).read_text(encoding="utf-8")
        tem_script = "<script" in fonte_tpl
        diz(not tem_script, f"{nome} nao renderizou",
            f" -> {type(e).__name__}: {e}" if tem_script
            else "  (sem <script> no fonte, nada a conferir)")
        continue
    achados = scripts_de(html)
    total_scripts += len(achados)

    # embutido: sem nonce o navegador se recusa a executar, e a tela quebra
    # calada -- nenhum erro no servidor, nenhuma linha no log
    sem_nonce = [s for s in achados
                 if "src" not in s and s.get("nonce") != NONCE]
    # externo: a CSP tem 'self', entao caminho relativo passa. Endereco
    # absoluto para outra maquina e bloqueado pelo navegador -- de novo, calado
    de_fora = [s["src"] for s in achados
               if s.get("src", "").startswith(("http://", "https://", "//"))]

    leitor = ColetorDeManipulador()
    leitor.feed(html)
    manipuladores = leitor.achados

    embutidos = sum(1 for s in achados if "src" not in s)
    diz(not sem_nonce and not de_fora and not manipuladores, f"{nome}",
        f"  ({embutidos} embutido(s), {len(achados) - embutidos} externo(s))"
        + (f"  SEM NONCE: {sem_nonce}" if sem_nonce else "")
        + (f"  DE FORA: {de_fora}" if de_fora else "")
        + (f"  MANIPULADOR EMBUTIDO: {manipuladores}" if manipuladores else ""))

diz(total_scripts > 0, "os templates renderizaram com script de verdade",
    f"  ({total_scripts} tags no total)")


print("\n4. O CDN do Google saiu do caminho")
print("-" * 64)
base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
diz("fonts.googleapis.com" not in base and "fonts.gstatic.com" not in base,
    "nenhum link para o CDN de fontes no base.html")
css = (WEB / "static" / "estilo.css").read_text(encoding="utf-8")
# "system-ui" solto no arquivo nao diz nada: podia estar num comentario, ou
# numa regra que nao vale para o texto da pagina. O que interessa e a CADEIA
# que pede a Inter -- sem CDN, a Inter so existe em quem ja a tem instalada, e
# quem nao tem cai no proximo nome da lista. Se esse proximo nome nao for uma
# fonte de sistema, o site sai com a fonte padrao do navegador.
cadeia = re.search(r"font-family:\s*([^;}]*Inter[^;}]*)", css, re.S)
seguintes = [n.strip().strip('"\'')
             for n in (cadeia.group(1).split(",")[1:] if cadeia else [])]
diz(bool(cadeia) and any(n in seguintes for n in
                         ("system-ui", "-apple-system", "sans-serif")),
    "quem nao tem a Inter cai numa fonte de sistema",
    f"  ({', '.join(seguintes[:3]) or 'nenhuma alternativa'})")


print("\n5. Caminho de servidor so para administrador")
print("-" * 64)
sem_admin = dict(contexto, sou_admin=False, pasta_relatorios="", caminho_log="")
html = env.get_template("backlog.html").render(**sem_admin)
diz("/opt/operacional" not in html, "quem nao e admin nao ve caminho do servidor")
html_admin = env.get_template("backlog.html").render(**contexto)
diz("/opt/operacional/bot/relatorios" in html_admin, "admin continua vendo o caminho")


print()
print("-" * 64)
if falhas:
    print("FALHOU:\n  " + "\n  ".join(falhas))
else:
    print("OK: todas as asserções passaram.")
sys.exit(1 if falhas else 0)
