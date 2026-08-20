"""Lista de garantias — a MESMA que o bot manda para os grupos regionais.

Até 20/08/2026 este módulo montava a própria lista: 309 linhas cruzando
`chamados_abertos_field_service.xlsx` (exportado à mão do Field Service) com a
Base OFS. O bot montava a dele, do que a varredura do CAMPO viu aberto. Duas
implementações da mesma pergunta dão duas respostas — e davam: a tela mostrava
um número, o grupo recebia outro, e nada explicava a diferença.

Agora a regra vive num lugar só, no bot (`garantias_lista.py`), e ele publica o
resultado em `<pasta do bot>/dados/garantias_lista.json` toda vez que monta a
lista. Aqui é só leitura: o que a tela mostra é, literalmente, o que foi
enviado aos grupos.

Duas consequências para quem opera:

- a lista passa a depender de o bot ter concluído uma varredura. Sem isso ela
  vem vazia, com o motivo escrito na tela — e vazia é a resposta certa, porque
  é exatamente o que os grupos receberiam;
- o endereço da rua saiu. Ele vinha do Field Service; do CAMPO o bot recebe
  unidade, bairro e cidade. A coluna passou a mostrar o BAIRRO, e a cidade
  continua sendo o título de cada bloco.
"""

from __future__ import annotations

import datetime as dt
import json

from ..config import CONFIG
from . import planilhas

# Onde o bot publica -- o outro lado é garantias_envio.ARQUIVO_PUBLICADO.
ARQUIVO_LISTA = "garantias_lista.json"

# A partir de quanto tempo a lista publicada deixa de ser "agora".
#
# O bot remonta de hora em hora. Duas horas e meia sem atualizar significa que
# ele parou, perdeu a VPN ou não terminou uma varredura -- e a tela precisa
# dizer isso, porque lista velha se lê exatamente igual a lista atual.
MINUTOS_ATE_ENVELHECER = 150

def status_arquivos() -> list[dict]:
    """Planilhas que a lista de garantias consome."""
    return planilhas.status_arquivos("garantias")


def guardar_planilha(nome: str, conteudo: bytes) -> dict:
    return planilhas.guardar(nome, conteudo)


# O `consultar_autenticador` daqui foi removido em 20/08/2026, junto com o cálculo
# próprio da garantia. Ele dependia de dois ajudantes que existiam só para a
# antiga leitura de planilha (`pd`, `_contrato`) e, sem eles, importava mas
# estourava NameError ao ser chamado -- função morta que parece viva é pior do
# que função ausente, porque alguém a chama confiando que funciona.
#
# Quem consulta o Autenticador agora é o bot, UMA vez, quando monta a lista: o status
# já chega pronto dentro do JSON. Ver garantias_envio.montar_dados.

# --------------------------------------------------------------------------
# a lista, lida de onde o bot publicou
# --------------------------------------------------------------------------
def _idade_em_minutos(publicado_em: str) -> float | None:
    try:
        quando = dt.datetime.strptime(publicado_em, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return (dt.datetime.now() - quando).total_seconds() / 60.0


def _vazio(erro: str) -> dict:
    return {"erro": erro, "erro_autenticador": "", "regioes": [], "invalidos": [],
            "total": 0, "online": 0, "offline": 0, "gerado_em": ""}


def calcular(com_autenticador: bool = True) -> dict:
    """Lê a lista publicada pelo bot e devolve no formato que a tela espera.

    `com_autenticador` é aceito e ignorado de propósito. O status de conexão já vem
    dentro da lista, consultado UMA vez pelo bot no instante em que ela foi
    montada. Consultar de novo aqui daria um segundo retrato, de outro
    momento, para as mesmas linhas — e a tela voltaria a discordar do grupo,
    pela porta dos fundos.
    """
    caminho = CONFIG.dados_bot / ARQUIVO_LISTA
    if not caminho.is_file():
        return _vazio("O bot ainda não publicou a lista de garantias. Ela é "
                      "montada a cada hora, junto com o envio aos grupos.")

    try:
        with caminho.open(encoding="utf-8") as arquivo:
            bruto = json.load(arquivo)
    except (OSError, ValueError) as falha:
        return _vazio(f"Não consegui ler a lista publicada pelo bot: {falha}")

    aviso = ""
    idade = _idade_em_minutos(bruto.get("publicado_em", ""))
    if idade is not None and idade > MINUTOS_ATE_ENVELHECER:
        aviso = (f"Esta lista foi publicada há {idade / 60.0:.1f}h e o bot "
                 "remonta de hora em hora — provavelmente ele parou ou está "
                 "sem varredura completa. Os grupos também não receberam nada "
                 "novo nesse tempo.")

    return {
        "erro": "",
        # Reaproveita a faixa de aviso do Autenticador: mesmo lugar da tela, mesmo
        # sentido -- "a lista vale, com esta ressalva".
        "erro_autenticador": aviso,
        "regioes": bruto.get("regioes", []),
        "invalidos": bruto.get("sem_regiao", []),
        "total": bruto.get("total", 0),
        "online": bruto.get("online", 0),
        "offline": bruto.get("offline", 0),
        "gerado_em": bruto.get("gerado_em", ""),
    }
