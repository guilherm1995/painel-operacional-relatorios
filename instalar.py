"""Prepara esta maquina para rodar o painel e o site.

Feito para ser executado na maquina que vai hospedar (inclusive por acesso
remoto): confere o Python, instala as dependencias, valida os caminhos dos
outros projetos, define o PIN e testa se tudo funciona de ponta a ponta.

    python instalar.py                     # instala e testa
    python instalar.py --pin 123456        # ja define o PIN
    python instalar.py --porta 8800
    python instalar.py --bot "D:/campo_bot_telegram" --database "D:/painel_desktop_operacao.py"
    python instalar.py --auto-start        # sobe junto com o Windows
    python instalar.py --conferir          # so diagnostica, nao altera nada

Pode rodar quantas vezes quiser - ele nao desfaz nada que ja esteja pronto.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CONFIG_SITE = RAIZ / "config" / "site.json"
PYTHON_MINIMO = (3, 10)
# Acima disto, pandas/numpy costumam ainda nao ter wheel publicado e o pip tenta
# compilar do zero - o que exige compilador C e quase sempre falha no Windows.
PYTHON_TESTADO_ATE = (3, 14)
PYTHON_SUGERIDO = "3.13"

DEPENDENCIAS = [
    "pandas", "openpyxl", "playwright",
    "fastapi", "uvicorn[standard]", "jinja2", "python-multipart", "itsdangerous",
    # consulta de status no Autenticador: requests faz a chamada, lxml e o parser que
    # o pandas usa para ler a tabela HTML da resposta
    "requests", "lxml",
    # planilha de confirmacao de agenda, quando vem do Google Sheets ao vivo
    "gspread", "oauth2client",
]
# nome do pacote -> modulo que precisa importar
IMPORTS = {
    "pandas": "pandas", "openpyxl": "openpyxl", "playwright": "playwright",
    "fastapi": "fastapi", "uvicorn[standard]": "uvicorn", "jinja2": "jinja2",
    "python-multipart": "multipart", "itsdangerous": "itsdangerous",
    "requests": "requests", "lxml": "lxml",
    "gspread": "gspread", "oauth2client": "oauth2client",
}

for fluxo in (sys.stdout, sys.stderr):
    try:
        fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

falhas: list[str] = []
avisos: list[str] = []


def titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


def ok(texto: str) -> None:
    print(f"  [ok]    {texto}")


def aviso(texto: str) -> None:
    avisos.append(texto)
    print(f"  [aviso] {texto}")


def erro(texto: str) -> None:
    falhas.append(texto)
    print(f"  [ERRO]  {texto}")


# --------------------------------------------------------------------------
def conferir_python() -> None:
    titulo("1. Python")
    versao = sys.version_info
    print(f"  versão: {versao.major}.{versao.minor}.{versao.micro}")
    print(f"  executável: {sys.executable}")
    if versao < PYTHON_MINIMO:
        erro(f"Precisa do Python {PYTHON_MINIMO[0]}.{PYTHON_MINIMO[1]} ou mais novo.")
        return
    ok("versão compatível")

    if (versao.major, versao.minor) > PYTHON_TESTADO_ATE:
        aviso(f"Python {versao.major}.{versao.minor} é mais novo do que o pandas "
              f"costuma acompanhar. Se o passo 2 falhar citando numpy ou pandas, "
              f"instale o Python {PYTHON_SUGERIDO} e rode com "
              f"'py -{PYTHON_SUGERIDO} instalar.py ...'")


def _instalado(modulo: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(modulo) is not None
    except (ImportError, ValueError):
        return False


def instalar_dependencias(somente_conferir: bool) -> None:
    titulo("2. Dependências Python")
    faltando = [p for p in DEPENDENCIAS if not _instalado(IMPORTS[p])]

    if not faltando:
        ok("todas as bibliotecas já estão instaladas")
        return
    if somente_conferir:
        aviso(f"faltam: {', '.join(faltando)}")
        return

    print(f"  faltando: {', '.join(faltando)}")

    # um pacote por vez: assim a falta de wheel do pandas nao impede o fastapi
    # e o diagnostico aponta exatamente quem quebrou
    quebrados: list[tuple[str, str]] = []
    for pacote in faltando:
        print(f"  instalando {pacote}...", flush=True)
        resultado = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", pacote],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        saida = (resultado.stderr or "") + (resultado.stdout or "")
        if resultado.returncode != 0 or not _instalado(IMPORTS[pacote]):
            quebrados.append((pacote, saida))
        else:
            ok(pacote)

    if not quebrados:
        ok(f"{len(faltando)} biblioteca(s) instalada(s)")
        return

    versao = f"{sys.version_info.major}.{sys.version_info.minor}"
    sem_wheel = any(
        marca in saida
        for _, saida in quebrados
        for marca in ("metadata-generation-failed", "Failed to build",
                      "Microsoft Visual C++", "meson", "error: subprocess-exited")
    )

    nomes = ", ".join(p for p, _ in quebrados)
    if sem_wheel:
        erro(f"Não foi possível instalar: {nomes}.\n"
             f"          O pip tentou compilar do zero porque não existe versão "
             f"pronta para o Python {versao}.\n"
             f"          Solução: instale o Python {PYTHON_SUGERIDO} "
             f"(winget install --id Python.Python.{PYTHON_SUGERIDO}) e rode\n"
             f"          py -{PYTHON_SUGERIDO} instalar.py (com os mesmos parâmetros).\n"
             f"          Não precisa desinstalar o Python {versao}.")
    else:
        erro(f"Não foi possível instalar: {nomes}\n"
             + "          " + quebrados[0][1].strip()[-500:])


def instalar_navegador(somente_conferir: bool) -> None:
    titulo("3. Chromium (renderiza as imagens do painel)")
    if not _instalado("playwright"):
        erro("playwright não está instalado; rode o instalador sem --conferir")
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            navegador.close()
        ok("Chromium pronto")
        return
    except Exception:
        pass

    if somente_conferir:
        aviso("Chromium não instalado (python -m playwright install chromium)")
        return

    print("  baixando o Chromium do Playwright (uma vez só, ~150 MB)...")
    resultado = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if resultado.returncode != 0:
        erro("falha ao instalar o Chromium:\n" + (resultado.stderr or "")[-700:])
        return
    ok("Chromium instalado")


def conferir_caminhos(bot: str | None, database: str | None,
                      somente_conferir: bool) -> None:
    titulo("4. Caminhos dos outros projetos")
    dados = json.loads(CONFIG_SITE.read_text(encoding="utf-8")) if CONFIG_SITE.exists() else {}

    if bot:
        dados["pasta_bot"] = str(Path(bot).resolve())
    if database:
        dados["pasta_database"] = str(Path(database).resolve())

    pasta_bot = Path(dados.get("pasta_bot", ""))
    pasta_db = Path(dados.get("pasta_database", ""))

    print(f"  bot de monitoramento: {pasta_bot}")
    if not pasta_bot.exists():
        aviso("pasta do bot não encontrada — use --bot para apontar o caminho certo")
    else:
        ok("pasta do bot encontrada")
        # relatorios/ e logs/ nascem vazios numa instalacao nova e enchem
        # conforme o bot roda, entao ausencia aqui e informativa, nao erro
        for sub, oque in [("relatorios", "imagens de backlog"),
                          ("logs", "log do bot")]:
            destino = pasta_bot / sub
            if not destino.exists():
                aviso(f"{sub}/ ainda não existe — {oque} aparecem quando o bot rodar")
            elif not any(destino.iterdir()):
                print(f"          {sub}/ está vazia (normal antes do bot rodar)")

    # As planilhas das garantias podem estar em varios lugares: na pasta do
    # Operacional Database, ou em <bot>/dados na versao empacotada.
    candidatas = [pasta_db, pasta_db / "dados", pasta_bot / "dados", pasta_bot,
                  *(Path(p) for p in dados.get("pastas_dados_extra") or [])]
    print(f"  Operacional Database: {pasta_db}")

    for arquivo in ("chamados_abertos_field_service.xlsx", "base OFS ok.xlsx"):
        achado = next((c / arquivo for c in candidatas if (c / arquivo).is_file()), None)
        if achado:
            ok(f"{arquivo} → {achado.parent}")
        else:
            aviso(f"{arquivo} não encontrado; sem ele a lista de garantias não abre. "
                  f"Coloque em uma destas pastas ou acrescente a pasta em "
                  f"'pastas_dados_extra' no config/site.json")

    if not somente_conferir and (bot or database):
        CONFIG_SITE.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        ok("caminhos gravados em config/site.json")


def configurar_site(pin: str | None, porta: int | None,
                    somente_conferir: bool) -> dict:
    titulo("5. Configuração do site")
    dados = json.loads(CONFIG_SITE.read_text(encoding="utf-8")) if CONFIG_SITE.exists() else {}

    if pin:
        if not (pin.isdigit() and 4 <= len(pin) <= 12):
            erro("o PIN precisa ter de 4 a 12 dígitos")
        else:
            dados["pin"] = pin
    elif not str(dados.get("pin") or "").strip():
        if somente_conferir:
            # em modo conferir nada e gravado, entao nao da para prometer um PIN:
            # quem sorteia e grava e o primeiro boot do site
            print("  PIN: nenhum definido — será sorteado ao subir o site")
        else:
            dados["pin"] = f"{secrets.randbelow(900000) + 100000}"
            print("  PIN sorteado (nenhum estava definido)")

    if porta:
        dados["porta"] = int(porta)

    if not somente_conferir:
        CONFIG_SITE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_SITE.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                               encoding="utf-8")

    print(f"  porta: {dados.get('porta', 8800)}")
    if dados.get("pin"):
        print(f"  PIN:   {dados['pin']}")
    ok("configuração pronta")
    return dados


def ensinar_bot_onde_esta_o_site(porta: int) -> None:
    """Deixa na pasta do bot como iniciar o site.

    E assim que o comando /painel consegue subir o site quando ele esta
    desligado: o bot le este arquivo para saber o caminho e o interpretador.
    """
    dados_bot = Path(json.loads(CONFIG_SITE.read_text(encoding="utf-8"))
                     .get("pasta_bot", "")) / "dados"
    if not dados_bot.is_dir():
        aviso("não achei a pasta dados/ do bot; o comando /painel não vai "
              "conseguir iniciar o site sozinho")
        return

    conteudo = {
        "pasta": str(RAIZ),
        "python": sys.executable,
        "porta": porta,
        "_comentario": "Gravado pelo instalador do painel. O bot usa isto no comando /painel.",
    }
    try:
        (dados_bot / "painel_config.json").write_text(
            json.dumps(conteudo, ensure_ascii=False, indent=2), encoding="utf-8")
        ok(f"o bot aprendeu a iniciar o site ({dados_bot / 'painel_config.json'})")
    except OSError as erro:
        aviso(f"não consegui gravar o painel_config.json para o bot: {erro}")


def gravar_interpretador() -> None:
    """Anota qual python foi usado, para os .bat subirem o site com o mesmo.

    Numa maquina com varias versoes instaladas, 'python' no PATH pode ser outra
    - justamente a que nao tem as bibliotecas.
    """
    marcador = RAIZ / "config" / "python.txt"
    marcador.parent.mkdir(parents=True, exist_ok=True)
    marcador.write_text(sys.executable, encoding="utf-8")
    ok(f"o site vai subir com este Python: {sys.executable}")


def ler_config() -> dict:
    """Le o site.json do disco - a verdade final depois de todas as etapas."""
    if not CONFIG_SITE.exists():
        return {}
    try:
        return json.loads(CONFIG_SITE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def conferir_tunel() -> None:
    titulo("6. Acesso de fora da rede (Cloudflare)")
    achado = shutil.which("cloudflared") or next(
        (str(c) for c in [
            Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
            Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
            RAIZ / "cloudflared.exe",
        ] if c.exists()), None)

    if achado:
        ok(f"cloudflared encontrado em {achado}")
        return
    aviso("cloudflared não instalado — o site funciona na rede local, mas não "
          "sai para a internet.")
    print("          Para habilitar, rode nesta máquina:")
    print("            winget install --id Cloudflare.cloudflared")


def testar(somente_conferir: bool) -> None:
    titulo("7. Teste de ponta a ponta")
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    if somente_conferir:
        # importar o site dispara o sorteio de PIN do primeiro boot; em modo
        # conferir isso nao pode virar escrita em disco
        os.environ["OPERACIONAL_CONFIG_SOMENTE_LEITURA"] = "1"

    try:
        from web.app import app
        rotas = sum(1 for r in app.routes if hasattr(r, "methods"))
        ok(f"site carrega ({rotas} rotas)")
    except Exception as falha:
        erro(f"o site não carregou: {type(falha).__name__} — {falha}")
        return

    exemplo = RAIZ / "exemplo_extracao.csv"
    if somente_conferir or not exemplo.exists():
        if not exemplo.exists():
            aviso("exemplo_extracao.csv não está aqui; pulei o teste de geração")
        return

    try:
        from operacional import carregar_extracao, preparar
        df = preparar(carregar_extracao(exemplo))
        ok(f"motor do painel calcula ({len(df)} atividades no exemplo)")
    except Exception as falha:
        erro(f"o motor do painel falhou: {type(falha).__name__} — {falha}")


def registrar_auto_start(porta: int) -> None:
    titulo("8. Subir junto com o Windows")
    if os.name != "nt":
        aviso("só disponível no Windows")
        return

    nome = "OperacionalPainelSite"
    comando = f'"{sys.executable}" "{RAIZ / "iniciar_site.py"}" --porta {porta}'
    resultado = subprocess.run(
        ["schtasks", "/Create", "/TN", nome, "/TR", comando,
         "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if resultado.returncode == 0:
        ok(f"tarefa '{nome}' criada — o site sobe sozinho ao ligar a máquina")
        print(f"          para remover: schtasks /Delete /TN {nome} /F")
    else:
        aviso("não consegui criar a tarefa agendada (precisa de terminal como "
              "administrador):\n          " + (resultado.stderr or "").strip()[:300])


# --------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Prepara esta máquina para o painel OPERACIONAL.")
    p.add_argument("--pin", help="PIN de acesso ao site (4 a 12 dígitos)")
    p.add_argument("--porta", type=int, help="porta local do site")
    p.add_argument("--bot", help="pasta do bot de monitoramento")
    p.add_argument("--database", help="pasta do Operacional Database")
    p.add_argument("--auto-start", action="store_true",
                   help="registra o site para subir junto com o Windows")
    p.add_argument("--conferir", action="store_true",
                   help="só diagnostica, sem instalar nem gravar nada")
    args = p.parse_args()

    print("=" * 66)
    print("  INSTALADOR - Painel e Site OPERACIONAL")
    print(f"  pasta: {RAIZ}")
    if args.conferir:
        print("  modo: apenas conferindo (nada será alterado)")
    print("=" * 66)

    conferir_python()
    if falhas:
        print("\nPython incompatível — resolva isso antes de seguir.")
        return 1

    instalar_dependencias(args.conferir)
    instalar_navegador(args.conferir)
    conferir_caminhos(args.bot, args.database, args.conferir)
    dados = configurar_site(args.pin, args.porta, args.conferir)
    conferir_tunel()
    testar(args.conferir)

    if not args.conferir and not falhas:
        gravar_interpretador()
        ensinar_bot_onde_esta_o_site(int(dados.get("porta", 8800)))

    if args.auto_start and not args.conferir:
        registrar_auto_start(int(dados.get("porta", 8800)))

    # o autoteste importa o site, que pode ter sorteado e gravado um PIN proprio -
    # entao o resumo le o arquivo de novo, para nao anunciar um PIN que nao vale
    dados = ler_config() or dados

    print("\n" + "=" * 66)
    if falhas:
        print(f"  {len(falhas)} problema(s) impedem o funcionamento:")
        for f in falhas:
            print(f"    - {f}")
        print("=" * 66)
        return 1

    print("  TUDO PRONTO")
    if avisos:
        print(f"\n  {len(avisos)} aviso(s) que não impedem o site de rodar:")
        for a in avisos:
            print(f"    - {a}")
    print(f"\n  Para subir:  clique em 'INICIAR SITE.bat'")
    print(f"  PIN:         {dados.get('pin') or '(será mostrado ao subir o site)'}")
    print(f"  Porta:       {dados.get('porta', 8800)}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
