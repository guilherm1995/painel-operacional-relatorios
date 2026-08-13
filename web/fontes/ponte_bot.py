"""Chamadas HTTP para a ponte que o bot de monitoramento expoe (so localhost).

Usada por qualquer acao do site que precise que o PROCESSO do bot faca algo -
ele tem estado em memoria (lista de chamados) e a sessao do WhatsApp/Telegram
que o site nao tem. Botao "Gerar backlog novo" e "Enviar não confirmados no
grupo" passam por aqui.
"""

from __future__ import annotations

PORTA = 3940

# Quanto esperar pela RESPOSTA da ponte. Nao e o tempo do trabalho: o bot
# responde o aceite e segue gerando por conta dele. Ficou em 15s ate 05/08/2026,
# e nesse dia o aceite nao chegou a tempo, a tela disse "bot fora do ar" e o
# operador clicou de novo -- ver o comentario de `demorou` em chamar().
SEGUNDOS_RESPOSTA = 30


def chamar(rota: str, corpo: dict | None = None) -> dict:
    """POSTa na ponte do bot. Devolve sempre {'ok': bool, ...}.

    Quando falha, `demorou` separa os dois casos que a tela precisa tratar de
    formas opostas:

    - demorou=False -> nao houve conexao. O bot esta fora do ar, nada foi feito
      e tentar de novo e o certo.
    - demorou=True  -> conectou, entregou o pedido, e a resposta nao veio a
      tempo. O bot provavelmente esta trabalhando: pedir de novo abre uma
      SEGUNDA rodada e o grupo recebe as mesmas imagens duplicadas.
    """
    import json

    import requests

    try:
        if corpo is not None:
            resposta = requests.post(
                f"http://127.0.0.1:{PORTA}{rota}",
                data=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=(3, SEGUNDOS_RESPOSTA),
            )
        else:
            resposta = requests.post(
                f"http://127.0.0.1:{PORTA}{rota}", timeout=(3, SEGUNDOS_RESPOSTA)
            )
    except requests.exceptions.ReadTimeout:
        # Conectou (o connect timeout de 3s passou) e o pedido foi entregue.
        return {"ok": False, "demorou": True, "erro": (
            f"O bot recebeu o pedido, mas não respondeu em {SEGUNDOS_RESPOSTA}s — "
            "quase sempre é porque ainda está executando. Aguarde e NÃO repita: "
            "repetir faz a ação acontecer duas vezes no grupo."
        )}
    except Exception:
        return {"ok": False, "demorou": False, "erro": (
            "Não consegui falar com o bot de monitoramento. Ele precisa estar "
            "rodando nesta máquina para executar essa ação."
        )}

    try:
        resultado = resposta.json()
    except ValueError:
        resultado = {}

    if resposta.status_code in (200, 202):
        resultado.setdefault("ok", True)
        return resultado
    return {"ok": False, "erro": resultado.get("erro") or f"O bot respondeu {resposta.status_code}."}
