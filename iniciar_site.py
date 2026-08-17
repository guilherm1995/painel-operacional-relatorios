"""Sobe o site do painel.

O acesso de fora vem de 'endereco_publico' no site.json -- em producao, o
Tailscale Funnel, que publica a porta 8800 num endereco fixo e roda fora deste
processo. O caminho do cloudflared abaixo so entra em acao quando nao ha
endereco fixo configurado; e o modo antigo, de tunel efemero.

    python iniciar_site.py            # rede local + endereco publico fixo, se configurado
    python iniciar_site.py --local    # so rede local, sem tunel
    python iniciar_site.py --porta 9000
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# o console do Windows nem sempre esta em UTF-8; sem isso o banner sai com lixo
for fluxo in (sys.stdout, sys.stderr):
    try:
        fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from web.config import CONFIG  # noqa: E402

LINHA = "=" * 64


def ligar_a_trilha() -> None:
    """Faz os registros do site aparecerem no journal.

    O uvicorn sobe com log_level="warning", e isso engolia TODO porteiro.info:
    quem entrou, por qual caminho, dispositivo novo, revogacao. O sistema
    rodava sem trilha de acesso nenhuma -- e foi essa cegueira que fez a
    identidade do Cloudflare Access ser descartada em silencio por horas, sem
    uma linha sequer dizendo o que estava acontecendo.

    Subir o nivel do uvicorn inteiro resolveria e traria junto uma linha por
    requisicao, afogando justamente o que interessa. Entao a familia "operacional"
    ganha o proprio destino, com propagate=False para nao depender de como o
    uvicorn configurou a raiz -- e para nao ser desligada quando ele mudar de
    ideia numa versao futura.
    """
    saida = logging.StreamHandler(sys.stdout)
    saida.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    nosso = logging.getLogger("operacional")
    nosso.setLevel(logging.INFO)
    nosso.handlers.clear()
    nosso.addHandler(saida)
    nosso.propagate = False

# O site publica aqui onde ele esta. O bot de monitoramento le este arquivo
# para responder o comando /painel no grupo - assim o endereco publico mudar a
# cada reinicio deixa de ser problema. Nao ha mais PIN a publicar: quem entra
# usa o proprio e-mail.
ARQUIVO_ENDERECO = "painel_endereco.json"


def publicar_endereco(publico: str = "") -> None:
    """Grava o endereco atual do site na pasta de dados do bot."""
    import json

    destino = CONFIG.pasta_bot / "dados"
    if not destino.exists():
        return
    # Um endereco so, e cifrado. O site escuta apenas em 127.0.0.1: "localhost"
    # e o IP da rede nao respondem mais a ninguem fora desta maquina, entao
    # anuncia-los seria mandar a equipe a uma porta fechada. E, enquanto
    # respondiam, respondiam em texto claro, por fora do tunel -- ou seja, por
    # fora do certificado, da conferencia de origem e do registro de acesso.
    conteudo = {
        "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "publico": publico,
    }
    try:
        (destino / ARQUIVO_ENDERECO).write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as erro:
        print(f"  [aviso] não consegui publicar o endereço para o bot: {erro}")


# ip_local() vivia aqui: descobria o IP da maquina na rede para anunciar o site
# ao celular no mesmo Wi-Fi. Saiu junto com o bind em 0.0.0.0 -- sem ninguem
# escutando naquele endereco, a funcao so sabia responder um caminho morto.


def achar_cloudflared() -> str | None:
    if caminho := shutil.which("cloudflared"):
        return caminho
    candidatos = [
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
        RAIZ / "cloudflared.exe",
    ]
    return next((str(c) for c in candidatos if c.exists()), None)


def subir_tunel(porta: int, executavel: str) -> None:
    """Mantem o tunel publico no ar e o endereco publicado sempre atual.

    O tunel do trycloudflare e efemero e nao tem endereco fixo. Nesta maquina
    a VPN corporativa e a rota padrao, entao toda reconexao de VPN derruba a
    conexao do cloudflared -- e, quando ele se re-registra, pode voltar com
    OUTRO endereco.

    A versao anterior publicava a primeira URL vista e parava de olhar
    (`anunciado = True`). Depois de uma queda de VPN, o /painel seguia
    entregando o link antigo, que ja nao respondia, ate alguem reiniciar o
    site na mao. Tambem nao havia supervisao: se o cloudflared morresse, o
    site continuava de pe sem tunel nenhum e sem dizer nada.

    Agora: toda URL nova vista na saida e republicada, o processo e
    ressuscitado se morrer, e enquanto nao ha tunel o endereco publicado fica
    VAZIO -- o /painel ja sabe responder "sem endereco externo agora", que e
    melhor que entregar um link morto.
    """
    padrao = re.compile(r"https://[\w-]+\.trycloudflare\.com")
    atual = ""

    while True:
        try:
            processo = subprocess.Popen(
                [executavel, "tunnel", "--url", f"http://localhost:{porta}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
        except Exception as erro:
            print(f"  [tunel] nao consegui iniciar o cloudflared: {erro}", flush=True)
            time.sleep(15)
            continue

        for linha in processo.stdout:
            achado = padrao.search(linha)
            if not achado or achado.group(0) == atual:
                continue
            novo = achado.group(0)
            trocou = bool(atual)
            atual = novo
            publicar_endereco(novo)
            print()
            print(LINHA)
            if trocou:
                print("  O TUNEL RECONECTOU COM OUTRO ENDERECO:")
            else:
                print("  ENDERECO PUBLICO (compartilhe so com quem deve ver):")
            print(f"  {novo}")
            print("  Publicado para o bot - no grupo, digite /painel")
            print(LINHA, flush=True)

        processo.wait()
        atual = ""
        publicar_endereco("")   # nao entregar link morto pelo /painel
        print(
            f"  [tunel] cloudflared encerrou (codigo {processo.returncode}); "
            "subindo de novo em 10s",
            flush=True,
        )
        time.sleep(10)


def main() -> int:
    p = argparse.ArgumentParser(description="Sobe o site do painel OPERACIONAL.")
    p.add_argument("--porta", type=int, default=CONFIG.porta)
    p.add_argument("--local", action="store_true", help="nao tenta subir o tunel publico")
    args = p.parse_args()

    import uvicorn

    ligar_a_trilha()

    print(LINHA)
    print(f"  {CONFIG.titulo}")
    print(LINHA)
    print(f"  Escutando em   : 127.0.0.1:{args.porta} (so quem esta nesta maquina)")
    print("  Acesso         : e-mail e senha, ou conta Google")

    for problema in CONFIG.problemas():
        print(f"  [aviso] {problema}")

    fixo = str(CONFIG.endereco_publico or "").strip().rstrip("/")

    if fixo:
        # Endereco fixo configurado (Tailscale Funnel). Nao ha tunel a subir:
        # quem publica a porta 8800 na internet e o `tailscale funnel`, que
        # roda fora deste processo e sobrevive a reinicios do site.
        print(f"  Endereco publico: {fixo}")
    elif args.local:
        print("  Tunel publico  : desligado (--local)")
    else:
        executavel = achar_cloudflared()
        if executavel:
            print("  Tunel publico  : subindo, o endereco aparece em instantes...")
            threading.Thread(target=subir_tunel, args=(args.porta, executavel),
                             daemon=True).start()
        else:
            print("  Tunel publico  : cloudflared nao encontrado.")
            print("                   Configure 'endereco_publico' no site.json para")
            print("                   usar um endereco fixo (Tailscale Funnel).")
    print(LINHA, flush=True)

    # Com endereco fixo, o /painel ja recebe o valor definitivo aqui. Sem ele,
    # publica o que se sabe e o tunel atualiza quando a URL sair.
    publicar_endereco(fixo)

    # 127.0.0.1 e nao 0.0.0.0: quem fala com o site e o cloudflared, na mesma
    # maquina. Escutando em todas as interfaces, qualquer micro da rede da
    # empresa alcancava http://<ip>:8800 em texto claro e contornava a borda
    # inteira -- certificado, conferencia de origem, e o Access.
    #
    # proxy_headers=False, e este e o ponto sutil: por padrao o uvicorn
    # REESCREVE request.client.host com o que vem no X-Forwarded-For. O IP
    # resultante ate era o certo, mas o efeito colateral era grave -- o codigo
    # deste site pergunta "o par e o proxy local?" para decidir em quem
    # confiar, e essa pergunta passava a responder "nao" para TODO pedido
    # vindo da Cloudflare. Foi o que fez a identidade do Access ser ignorada
    # em silencio, e o que impedia acesso.ip_do_pedido() de sequer consultar o
    # CF-Connecting-IP que ele foi escrito para preferir.
    #
    # Com False, client.host volta a ser o par de verdade (127.0.0.1) e cada
    # decisao passa a ser tomada por quem tem contexto para toma-la, em vez de
    # por um middleware generico que nao sabe qual cabecalho e confiavel aqui.
    uvicorn.run("web.app:app", host="127.0.0.1", port=args.porta,
                log_level="warning", proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
