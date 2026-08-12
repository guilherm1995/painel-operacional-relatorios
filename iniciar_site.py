"""Sobe o site do painel e, se o cloudflared estiver instalado, o tunel publico.

    python iniciar_site.py            # rede local + tunel Cloudflare (se disponivel)
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

# O site publica aqui onde ele esta e qual o PIN. O bot de monitoramento le
# este arquivo para responder o comando /painel no grupo - assim o endereco
# publico mudar a cada reinicio deixa de ser problema.
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
        "pin": str(CONFIG.pin),
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
    """Roda o cloudflared e imprime a URL publica assim que ela aparecer."""
    processo = subprocess.Popen(
        [executavel, "tunnel", "--url", f"http://localhost:{porta}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    padrao = re.compile(r"https://[\w-]+\.trycloudflare\.com")
    anunciado = False
    for linha in processo.stdout:
        if not anunciado and (achado := padrao.search(linha)):
            anunciado = True
            publicar_endereco(porta, achado.group(0))
            print()
            print(LINHA)
            print("  ENDERECO PUBLICO (compartilhe so com quem deve ver):")
            print(f"  {achado.group(0)}")
            print("  Publicado para o bot - no grupo, digite /painel")
            print(LINHA, flush=True)


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
    print(f"  PIN de acesso  : {CONFIG.pin}")

    for problema in CONFIG.problemas():
        print(f"  [aviso] {problema}")

    if args.local:
        print("  Tunel publico  : desligado (--local)")
    else:
        executavel = achar_cloudflared()
        if executavel:
            print("  Tunel publico  : subindo, o endereco aparece em instantes...")
            threading.Thread(target=subir_tunel, args=(args.porta, executavel),
                             daemon=True).start()
        else:
            print("  Tunel publico  : cloudflared nao encontrado.")
            print("                   Para acessar de fora da rede, instale com:")
            print("                   winget install --id Cloudflare.cloudflared")
            print("                   (depois feche e abra o terminal de novo)")
    print(LINHA, flush=True)

    # publica ja com o que se sabe; o tunel atualiza depois que a URL sair
    publicar_endereco(args.porta)

    uvicorn.run("web.app:app", host="0.0.0.0", port=args.porta, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
