"""Monta um .zip enxuto do projeto para levar ate o servidor.

Leva o codigo, a configuracao e o logo. Fica de fora o que a maquina de destino
gera sozinha (imagens, cache) e o que nao deve sair daqui: a pasta uploads/,
que guarda extracoes com nome, endereco e telefone de cliente.

    python preparar_envio.py
    python preparar_envio.py --destino "D:/pendrive"
    python preparar_envio.py --pasta          # copia para uma pasta, sem zipar
"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent

# Vai junto - tudo que o servidor precisa para rodar
INCLUIR_PASTAS = ["operacional", "web", "config", "assets", "integracao_bot"]
INCLUIR_ARQUIVOS = [
    "instalar.py",
    "INSTALAR.bat",
    "iniciar_site.py",
    "INICIAR SITE.bat",
    "gerar.py",
    "extrair_parametros.py",
    "gerar_logo_site.py",
    "preparar_envio.py",
    "vigia_anydesk.ps1",
    "instalar_vigia_anydesk.ps1",
    "README.md",
    "exemplo_extracao.csv",   # usado pelo autoteste do instalador
]

# Nunca vai junto, mesmo se estiver dentro de uma pasta incluida
IGNORAR = {"__pycache__", ".claude", ".git", ".venv", "node_modules"}

# Arquivos que sao especificos desta maquina e o destino recria sozinho
IGNORAR_ARQUIVOS = {
    Path("config") / "python.txt",   # caminho do interpretador local
}

# Fica de fora porque o destino gera sozinho, ou porque tem dado de cliente
EXPLICAR_AUSENCIA = {
    "saida": "imagens do painel — o servidor gera de novo no primeiro envio",
    "uploads": "extrações já enviadas — contêm dados de cliente, não saem daqui",
    "dados": "planilhas enviadas pelo site — o servidor recebe as dele",
}


def _limpa(pasta: Path) -> list[Path]:
    """Arquivos de uma pasta, sem os diretorios ignorados."""
    saida = []
    for caminho in pasta.rglob("*"):
        if not caminho.is_file():
            continue
        relativo = caminho.relative_to(RAIZ)
        if IGNORAR & set(relativo.parts):
            continue
        if relativo in IGNORAR_ARQUIVOS:
            continue
        saida.append(caminho)
    return saida


def _config_para_envio() -> str | None:
    """site.json limpo do que é só desta máquina - pin, e os caminhos do bot e
    do Operacional Database (são pastas de instalação que existem AQUI, não no
    servidor de destino; deixar preenchido faz o instalador achar que já está
    tudo certo e o site sobe silenciosamente sem achar bot nem garantias)."""
    arquivo = RAIZ / "config" / "site.json"
    if not arquivo.exists():
        return None
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    dados["pin"] = ""
    dados["pasta_bot"] = ""
    dados["pasta_database"] = ""
    return json.dumps(dados, ensure_ascii=False, indent=2)


def reunir() -> list[Path]:
    itens: list[Path] = []
    for nome in INCLUIR_PASTAS:
        pasta = RAIZ / nome
        if pasta.is_dir():
            itens.extend(_limpa(pasta))
    for nome in INCLUIR_ARQUIVOS:
        arquivo = RAIZ / nome
        if arquivo.is_file():
            itens.append(arquivo)
    return itens


def main() -> int:
    p = argparse.ArgumentParser(description="Empacota o projeto para o servidor.")
    p.add_argument("--destino", default=str(RAIZ.parent), help="onde gravar")
    p.add_argument("--pasta", action="store_true",
                   help="copia para uma pasta em vez de gerar um .zip")
    args = p.parse_args()

    itens = reunir()
    if not itens:
        print("Nada para empacotar — rode de dentro da pasta do projeto.")
        return 1

    destino = Path(args.destino)
    destino.mkdir(parents=True, exist_ok=True)
    config_limpa = _config_para_envio()
    relativo_config = Path("config") / "site.json"

    if args.pasta:
        alvo = destino / "painel_operacional"
        if alvo.exists():
            shutil.rmtree(alvo)
        for arquivo in itens:
            relativo = arquivo.relative_to(RAIZ)
            copia = alvo / relativo
            copia.parent.mkdir(parents=True, exist_ok=True)
            if relativo == relativo_config and config_limpa:
                copia.write_text(config_limpa, encoding="utf-8")
            else:
                shutil.copy2(arquivo, copia)
        total = sum(f.stat().st_size for f in alvo.rglob("*") if f.is_file())
    else:
        alvo = destino / "painel_operacional.zip"
        with zipfile.ZipFile(alvo, "w", zipfile.ZIP_DEFLATED) as zip_saida:
            for arquivo in itens:
                relativo = arquivo.relative_to(RAIZ)
                if relativo == relativo_config and config_limpa:
                    zip_saida.writestr(str(relativo), config_limpa)
                else:
                    zip_saida.write(arquivo, relativo)
        total = alvo.stat().st_size

    print(f"Gerado: {alvo}")
    print(f"{len(itens)} arquivos · {total / 1024:.0f} KB")
    print("\nNão foi junto (de propósito):")
    for nome, porque in EXPLICAR_AUSENCIA.items():
        if (RAIZ / nome).exists():
            print(f"  {nome}/ — {porque}")
    print("  __pycache__/ — bytecode, recriado sozinho")
    print("\nNo servidor, depois de descompactar:")
    print('  python instalar.py --bot "CAMINHO/DO/BOT" --database "CAMINHO/DO/DATABASE" --pin SEUPIN')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
