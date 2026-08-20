"""Configuracao do site: le config/site.json e sorteia o PIN no primeiro boot."""

from __future__ import annotations

import json
import os
from pathlib import Path

# O instalador liga isto no modo --conferir: o PIN ainda e sorteado para a
# configuracao carregar, mas nada e escrito em disco.
SOMENTE_LEITURA = "OPERACIONAL_CONFIG_SOMENTE_LEITURA"

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = RAIZ / "config" / "site.json"

PADRAO = {
    "porta": 8800,
    "titulo": "OPERACIONAL OPERAÇÕES",
    "assinatura": "Desenvolvido por operador Santos 2026",
    "pasta_bot": r"C:\caminho\para\Desktop\bot_campo_monitoramento_dist",
    "pasta_database": r"C:\caminho\para\Desktop\painel_desktop_operacao.py",
    "autenticador_ativo": True,
    "autenticador_cache_segundos": 120,
    "sessao_horas": 12,
    # --- portaria (ver web/acesso.py) ---
    # Semente do cadastro, usada UMA vez: se config/usuarios.json ainda nao
    # existe, ele nasce com estas pessoas. Dai em diante quem manda e a tela de
    # usuarios, e mexer aqui nao devolve acesso a ninguem. Precisa ter ao menos
    # um "admin", senao ninguem conseguiria administrar o cadastro depois.
    "usuarios_iniciais": {},
    # Endereco fixo pelo qual o site e alcancado de fora. E dele que sai a URL
    # de retorno cadastrada no console do Google, entao tem que bater letra por
    # letra. Vazio = login com Google desligado.
    "endereco_publico": "",
    "google_client_id": "",
    "google_client_secret": "",
    # Envio do codigo de primeiro acesso. No Gmail a senha aqui e a "senha de
    # app", nao a senha da conta.
    "smtp_servidor": "smtp.gmail.com",
    "smtp_porta": 587,
    "smtp_usuario": "",
    "smtp_senha": "",
    "smtp_remetente": "",
    # Cookie de sessao so viaja em HTTPS. Ligar isto quebra o acesso pela rede
    # local, que e HTTP puro -- ligue quando o Funnel for a unica porta.
    "cookie_seguro": False,
    "forms_sheet_id": "SEU_ID_DA_PLANILHA_GOOGLE",
    "forms_aba": "Respostas ao formulário 1",
}


class Config:
    def __init__(self, dados: dict):
        self._dados = dados
        for chave, valor in PADRAO.items():
            setattr(self, chave, dados.get(chave, valor) or valor)
        self.porta = int(self.porta)
        self.sessao_horas = int(self.sessao_horas)
        self.autenticador_cache_segundos = int(self.autenticador_cache_segundos)
        self.autenticador_ativo = bool(dados.get("autenticador_ativo", True))
        # Estes precisam vir do dicionario direto: o laco acima usa
        # "valor or padrao", que para um padrao verdadeiro engoliria um False
        # escrito de proposito no site.json.
        self.cookie_seguro = bool(dados.get("cookie_seguro", False))
        self.usuarios_iniciais = dict(dados.get("usuarios_iniciais") or {})
        self.smtp_porta = int(dados.get("smtp_porta") or 587)
        self.pasta_bot = Path(self.pasta_bot)
        self.pasta_database = Path(self.pasta_database)

    # -- pastas derivadas ------------------------------------------------
    @property
    def pasta_saida(self) -> Path:
        return RAIZ / "saida"

    @property
    def pasta_uploads(self) -> Path:
        return RAIZ / "uploads"

    @property
    def pasta_dados(self) -> Path:
        """Planilhas enviadas pelo site. Tem prioridade sobre as do disco."""
        return RAIZ / "dados"

    @property
    def relatorios_bot(self) -> Path:
        return self.pasta_bot / "relatorios"

    @property
    def dados_bot(self) -> Path:
        return self.pasta_bot / "dados"

    @property
    def log_bot(self) -> Path:
        return self.pasta_bot / "logs" / "monitor_campo.log"

    def pastas_de_dados(self) -> list[Path]:
        """Onde procurar as planilhas das garantias, em ordem de preferencia.

        Os arquivos nao ficam sempre no mesmo lugar: na versao empacotada do bot,
        'base OFS ok.xlsx' fica em <bot>/dados, enquanto o app do Operacional Database
        guarda tudo na propria raiz. Procurar em varios lugares evita ter que
        duplicar planilha de 1 MB so para agradar o caminho configurado.
        """
        candidatas = [
            self.pasta_dados,            # o que foi enviado pelo site manda
            self.pasta_database,
            self.pasta_database / "dados",
            self.pasta_bot / "dados",
            self.pasta_bot,
            *(Path(p) for p in self._dados.get("pastas_dados_extra") or []),
        ]
        vistas, ordenadas = set(), []
        for pasta in candidatas:
            chave = str(pasta).lower()
            if chave not in vistas:
                vistas.add(chave)
                ordenadas.append(pasta)
        return ordenadas

    def localizar_dado(self, nome: str) -> Path | None:
        """Primeiro caminho existente para um arquivo de dados, ou None."""
        for pasta in self.pastas_de_dados():
            alvo = pasta / nome
            if alvo.is_file():
                return alvo
        return None

    @property
    def google_ligado(self) -> bool:
        return bool(self.endereco_publico and self.google_client_id
                    and self.google_client_secret)

    @property
    def smtp_ligado(self) -> bool:
        return bool(self.smtp_servidor and self.smtp_usuario and self.smtp_senha)

    def problemas(self) -> list[str]:
        """Avisos de configuracao para mostrar na tela de status."""
        avisos = []
        if not self.pasta_bot.exists():
            avisos.append(f"Pasta do bot de monitoramento não encontrada: {self.pasta_bot}")

        if not self.smtp_ligado:
            avisos.append(
                "O envio de e-mail não está configurado — quem ainda não tem senha "
                "não consegue fazer o primeiro acesso, e ninguém consegue recuperar "
                "senha esquecida. Preencha os campos 'smtp_' em config/site.json."
            )
        if not self.google_ligado and not self.smtp_ligado:
            avisos.append(
                "ATENÇÃO: sem Google e sem e-mail configurados, só entra quem já "
                "tem senha definida. Se ninguém tiver, o site fica inacessível."
            )

        # 'chamados_abertos_field_service.xlsx' saiu desta lista em 20/08/2026,
        # junto com o cálculo próprio da garantia: hoje a lista vem pronta do
        # bot. Cobrar um arquivo que ninguém lê treina a equipe a ignorar avisos.
        for arquivo, para_que in [
            ("base OFS ok.xlsx", "o histórico de serviços concluídos"),
        ]:
            if self.localizar_dado(arquivo) is None:
                avisos.append(
                    f"Não encontrei '{arquivo}' ({para_que}) — a lista de garantias "
                    f"fica indisponível. Procurei em: "
                    + " · ".join(str(p) for p in self.pastas_de_dados())
                )

        if not (self.dados_bot / "garantias_lista.json").is_file():
            avisos.append(
                "O bot ainda não publicou 'garantias_lista.json' — a tela de "
                "garantias fica vazia até a próxima montagem da lista (de hora "
                f"em hora). Procurei em: {self.dados_bot}"
            )

        # Aviso à parte, e mais brando: sem esta base o site continua inteiro
        # e a garantia também. O que para é o alerta de reincidência do bot --
        # e ele para EM SILÊNCIO, que é justamente o motivo de o aviso existir.
        if self.localizar_dado("base improdutivas 60 dias.xlsx") is None:
            avisos.append(
                "Não encontrei 'base improdutivas 60 dias.xlsx' — o bot não vai "
                "avisar quando um entrante já tiver sido improdutiva nos "
                "últimos 60 dias. "
                "Envie pela página Garantias (exportação do OFS dos últimos 30 "
                "dias, SEM o filtro de status concluído)."
            )
        return avisos


def carregar() -> Config:
    dados = json.loads(ARQUIVO.read_text(encoding="utf-8")) if ARQUIVO.exists() else {}

    # O PIN unico saiu: agora cada pessoa tem a propria senha, e quem manda em
    # quem entra e a lista 'emails_autorizados'. Deixar o campo antigo no
    # arquivo nao quebra nada, mas tambem nao vale mais nada -- entao ele e
    # removido na primeira gravacao, para ninguem achar que ainda abre o site.
    if "pin" in dados and os.environ.get(SOMENTE_LEITURA) != "1":
        dados.pop("pin", None)
        try:
            ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            ARQUIVO.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        except OSError:
            pass

    return Config(dados)


CONFIG = carregar()
