# -*- coding: utf-8 -*-
"""Prova que a escada de castigo voltou a contar por IP de verdade.

Nao testa so a funcao: monta o ataque real -- N tentativas erradas variando o
X-Forwarded-For a cada uma -- e passa pela Portaria de verdade, que e quem
decide o bloqueio. O criterio e o comportamento, nao a implementacao.
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

# web.config le config/site.json e escreve em disco. Aqui interessa so o
# acesso.py, entao entra um dublê -- assim o ensaio nao depende de configuracao
# nem cria arquivo nenhum na arvore de producao.
pacote = types.ModuleType("web")
pacote.__path__ = [str(RAIZ_SITE / "web")]
sys.modules["web"] = pacote

cfg = types.ModuleType("web.config")


class _ConfigFalsa:
    google_client_id = ""
    google_client_secret = ""
    endereco_publico = ""
    usuarios_iniciais = {}


cfg.CONFIG = _ConfigFalsa()
cfg.RAIZ = pathlib.Path(tempfile.mkdtemp(prefix="ensaio_xff_"))
cfg.SOMENTE_LEITURA = "ENSAIO_SO_LEITURA"
sys.modules["web.config"] = cfg
os.environ["ENSAIO_SO_LEITURA"] = "1"

from web import acesso  # noqa: E402


class PedidoFalso:
    """O minimo que ip_do_pedido consulta: headers e client.host."""

    def __init__(self, par, **cabecalhos):
        self.headers = {k.lower().replace("_", "-"): v
                        for k, v in cabecalhos.items()}
        self.client = types.SimpleNamespace(host=par)


falhas = []


def confere(rotulo, obtido, esperado):
    ok = obtido == esperado
    print(f"  [{'ok ' if ok else 'FALHA'}] {rotulo}: {obtido!r}")
    if not ok:
        falhas.append(f"{rotulo}: esperava {esperado!r}, veio {obtido!r}")


print("\n1. De onde o IP e lido")
print("-" * 62)

confere("conexao direta ignora o XFF forjado",
        acesso.ip_do_pedido(PedidoFalso("203.0.113.7",
                                        x_forwarded_for="192.0.2.4")),
        "203.0.113.7")

confere("pelo proxy, vale o CF-Connecting-IP",
        acesso.ip_do_pedido(PedidoFalso("127.0.0.1",
                                        cf_connecting_ip="198.51.100.9",
                                        x_forwarded_for="192.0.2.4")),
        "198.51.100.9")

confere("sem Cloudflare, vale o ULTIMO salto do XFF",
        acesso.ip_do_pedido(PedidoFalso(
            "127.0.0.1", x_forwarded_for="192.0.2.4, 192.0.2.8, 198.51.100.9")),
        "198.51.100.9")

confere("XFF com um salto so",
        acesso.ip_do_pedido(PedidoFalso("127.0.0.1",
                                        x_forwarded_for="198.51.100.9")),
        "198.51.100.9")


print("\n2. O ataque: 40 tentativas erradas trocando o cabecalho a cada uma")
print("-" * 62)

TENTATIVAS = 40

# O atacante controla o que escreve no XFF, mas nao controla o que a borda da
# Cloudflare escreve no CF-Connecting-IP: ela sobrescreve em todo pedido.
portaria = acesso.Portaria()
ip_real = "203.0.113.66"
for n in range(TENTATIVAS):
    pedido = PedidoFalso("127.0.0.1",
                         cf_connecting_ip=ip_real,
                         x_forwarded_for=f"10.0.{n // 256}.{n % 256}")
    portaria.errou(acesso.ip_do_pedido(pedido))

castigo = portaria.bloqueio_restante(ip_real)
print(f"  baldes usados          : {len(portaria._erros)}")
print(f"  castigo do IP real     : {castigo}s")

if len(portaria._erros) != 1:
    falhas.append(f"o atacante conseguiu {len(portaria._erros)} baldes; "
                  f"deveria cair sempre no mesmo")
else:
    print("  [ok ] as 40 tentativas cairam no MESMO balde")

if castigo < 3000:
    falhas.append(f"depois de {TENTATIVAS} erros o castigo deveria ser de 1h "
                  f"(3600s); veio {castigo}s")
else:
    print(f"  [ok ] a escada disparou: {castigo}s de porta fechada")


print("\n3. O mesmo ataque contra a regra ANTIGA, para efeito de comparacao")
print("-" * 62)


def ip_do_pedido_antigo(request):
    """Como era antes: primeiro elemento do XFF, escrito pelo cliente."""
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()[:45] or "?"
    return getattr(request.client, "host", None) or "?"


antiga = acesso.Portaria()
for n in range(TENTATIVAS):
    pedido = PedidoFalso("127.0.0.1",
                         cf_connecting_ip=ip_real,
                         x_forwarded_for=f"10.0.{n // 256}.{n % 256}")
    antiga.errou(ip_do_pedido_antigo(pedido))

print(f"  baldes usados          : {len(antiga._erros)}")
print(f"  castigo do IP real     : {antiga.bloqueio_restante(ip_real)}s")
print(f"  -> {TENTATIVAS} chutes sem nenhum bloqueio: era este o furo")


print()
print("-" * 62)
if falhas:
    print("FALHOU:\n  " + "\n  ".join(falhas))
else:
    print("OK: todas as asserções passaram.")
sys.exit(1 if falhas else 0)
