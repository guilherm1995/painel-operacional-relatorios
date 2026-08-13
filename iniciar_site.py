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
import re
import shutil
import socket
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

# O site publica aqui onde ele esta. O bot de monitoramento le este arquivo
# para responder o comando /painel no grupo - assim o endereco publico mudar a
# cada reinicio deixa de ser problema. Nao ha mais PIN a publicar: quem entra
# usa o proprio e-mail.
ARQUIVO_ENDERECO = "painel_endereco.json"


def publicar_endereco(porta: int, publico: str = "") -> None:
    """Grava o endereco atual do site na pasta de dados do bot."""
    import json

    destino = CONFIG.pasta_bot / "dados"
    if not destino.exists():
        return
    conteudo = {
        "atualizado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "local": f"http://localhost:{porta}",
        "rede_local": f"http://{ip_local()}:{porta}",
        "publico": publico,
    }
    try:
        (destino / ARQUIVO_ENDERECO).write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as erro:
        print(f"  [aviso] não consegui publicar o endereço para o bot: {erro}")


def ip_local() -> str:
    """IP da maquina na rede, para acessar do celular no mesmo Wi-Fi."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


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
            publicar_endereco(porta, novo)
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
        publicar_endereco(porta, "")   # nao entregar link morto pelo /painel
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

    print(LINHA)
    print(f"  {CONFIG.titulo}")
    print(LINHA)
    print(f"  Neste notebook : http://localhost:{args.porta}")
    print(f"  Na rede local  : http://{ip_local()}:{args.porta}")
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
    publicar_endereco(args.porta, fixo)

    uvicorn.run("web.app:app", host="0.0.0.0", port=args.porta, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
