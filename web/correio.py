"""Envio de e-mail do site. Serve so para mandar o codigo de acesso.

E o unico ponto do login que depende de algo fora desta maquina, entao ele
falha de forma explicita: se o e-mail nao sair, a pessoa ve isso na tela em vez
de ficar esperando um codigo que nunca vem.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from web.config import CONFIG

logger = logging.getLogger("operacional.correio")

ESPERA_SEG = 20


class CorreioIndisponivel(RuntimeError):
    """Nao deu para enviar. A mensagem ja vem pronta para mostrar na tela."""


def configurado() -> bool:
    return bool(CONFIG.smtp_servidor and CONFIG.smtp_usuario and CONFIG.smtp_senha)


def remetente() -> str:
    return str(CONFIG.smtp_remetente or CONFIG.smtp_usuario or "").strip()


def enviar(para: str, assunto: str, corpo: str) -> None:
    if not configurado():
        raise CorreioIndisponivel(
            "O envio de e-mail não está configurado.")

    mensagem = EmailMessage()
    mensagem["From"] = f"{CONFIG.titulo} <{remetente()}>"
    mensagem["To"] = para
    mensagem["Subject"] = assunto
    mensagem.set_content(corpo)

    porta = int(CONFIG.smtp_porta or 587)
    contexto = ssl.create_default_context()
    try:
        if porta == 465:
            # 465 e TLS desde o primeiro byte; 587 comeca em claro e sobe com
            # STARTTLS. Trocar os dois da erro de handshake, nao de senha --
            # vale lembrar disso quando alguem mudar a porta no config.
            with smtplib.SMTP_SSL(CONFIG.smtp_servidor, porta,
                                  timeout=ESPERA_SEG, context=contexto) as servidor:
                servidor.login(CONFIG.smtp_usuario, CONFIG.smtp_senha)
                servidor.send_message(mensagem)
        else:
            with smtplib.SMTP(CONFIG.smtp_servidor, porta, timeout=ESPERA_SEG) as servidor:
                servidor.starttls(context=contexto)
                servidor.login(CONFIG.smtp_usuario, CONFIG.smtp_senha)
                servidor.send_message(mensagem)
    except smtplib.SMTPAuthenticationError as erro:
        logger.error("SMTP recusou o login de %s: %s", CONFIG.smtp_usuario, erro)
        raise CorreioIndisponivel(
            "O servidor de e-mail recusou as credenciais configuradas. Contate "
            "o administrador do sistema."
        ) from erro
    except Exception as erro:
        logger.error("falha ao enviar e-mail para %s: %s: %s",
                     para, type(erro).__name__, erro)
        raise CorreioIndisponivel(
            "Não foi possível enviar o e-mail. Tente novamente em instantes."
        ) from erro

    logger.info("código enviado para %s", para)


def texto_do_codigo(codigo: str, minutos: int) -> tuple[str, str]:
    assunto = f"{CONFIG.titulo} — código de verificação"
    corpo = (
        "Prezado(a),\n\n"
        "Foi solicitado o cadastro ou a redefinição de senha de acesso ao "
        f"sistema {CONFIG.titulo}.\n\n"
        f"Código de verificação: {codigo}\n\n"
        f"O código é válido por {minutos} minutos e permite um único uso.\n\n"
        "Caso não tenha realizado esta solicitação, desconsidere esta "
        "mensagem. Nenhuma alteração é efetuada sem a informação do código.\n\n"
        "Esta é uma mensagem automática. Não responda a este e-mail.\n"
    )
    return assunto, corpo
