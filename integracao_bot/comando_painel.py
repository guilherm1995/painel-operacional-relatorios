"""Comando /painel para o bot_campo_monitoramento.py.

O site publica onde ele esta em dados/painel_endereco.json toda vez que sobe.
Este comando le esse arquivo e responde no grupo - por isso o endereco publico
mudar a cada reinicio deixa de ser problema: quem precisar digita /painel e
recebe o link atual junto com o PIN.

COMO INSTALAR (duas colagens no bot_campo_monitoramento.py):

1) Cole a funcao `montar_mensagem_painel()` abaixo em qualquer lugar do bot,
   antes de `escutar_comandos_telegram()`.

2) Dentro de `escutar_comandos_telegram()`, logo depois do bloco do /status
   (a linha `if comando == "/status":` ... `continue`), cole:

        if comando == "/painel":
            logger.info("Comando /painel recebido no grupo. Enviando endereco do site...")
            try:
                enviar_alerta_telegram(montar_mensagem_painel())
            except Exception:
                logger.exception("Falha ao responder o comando /painel.")
            continue

Se o grupo do WhatsApp tambem interpreta comandos, use a mesma chamada de
`montar_mensagem_painel()` no ponto onde os outros comandos sao tratados.
"""

import json
import os


def montar_mensagem_painel():
    """Le o endereco publicado pelo site e monta a resposta do /painel."""
    caminho = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "dados", "painel_endereco.json"
    )

    if not os.path.exists(caminho):
        return (
            "⚠️ O painel não está no ar no momento.\n"
            "Suba o site na máquina e digite /painel de novo."
        )

    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, ValueError):
        return "⚠️ Não consegui ler o endereço do painel. Verifique se o site está no ar."

    linhas = ["📊 *PAINEL OPERACIONAL*", ""]

    if dados.get("publico"):
        linhas.append(f"🌐 {dados['publico']}")
    else:
        linhas.append("🌐 Sem endereço externo agora (o túnel não subiu).")

    if dados.get("rede_local"):
        linhas.append(f"🏠 Na rede da empresa: {dados['rede_local']}")

    linhas += [
        "",
        "🔐 Entre com o seu e-mail. No primeiro acesso, o site envia um código "
        "para você criar a sua senha.",
        "",
        f"_Atualizado em {dados.get('atualizado_em', '?')}_",
        "_Sem acesso? Peça a um administrador para cadastrar o seu e-mail._",
    ]
    return "\n".join(linhas)


if __name__ == "__main__":
    # teste rapido: python comando_painel.py
    print(montar_mensagem_painel())
