"""Configuracao do site: le config/site.json e sorteia o PIN no primeiro boot."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

# O instalador liga isto no modo --conferir: o PIN ainda e sorteado para a
# configuracao carregar, mas nada e escrito em disco.
SOMENTE_LEITURA = "OPERACIONAL_CONFIG_SOMENTE_LEITURA"

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = RAIZ / "config" / "site.json"

PADRAO = {
    "pin": "",
    "porta": 8800,
    "titulo": "OPERACIONAL OPERAÇÕES",
    "assinatura": "Desenvolvido por operador Santos 2026",
    "pasta_bot": r"C:\caminho\para\Desktop\bot_campo_monitoramento_dist",
    "pasta_database": r"C:\caminho\para\Desktop\painel_desktop_operacao.py",
    "autenticador_ativo": True,
    "autenticador_cache_segundos": 120,
    "sessao_horas": 12,
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

    def problemas(self) -> list[str]:
        """Avisos de configuracao para mostrar na tela de status."""
        avisos = []
        if not self.pasta_bot.exists():
            avisos.append(f"Pasta do bot de monitoramento não encontrada: {self.pasta_bot}")

        for arquivo, para_que in [
            ("chamados_abertos_field_service.xlsx", "os chamados em aberto"),
            ("base OFS ok.xlsx", "o histórico de serviços concluídos"),
        ]:
            if self.localizar_dado(arquivo) is None:
                avisos.append(
                    f"Não encontrei '{arquivo}' ({para_que}) — a lista de garantias "
                    f"fica indisponível. Procurei em: "
                    + " · ".join(str(p) for p in self.pastas_de_dados())
                )
        return avisos


def carregar() -> Config:
    dados = json.loads(ARQUIVO.read_text(encoding="utf-8")) if ARQUIVO.exists() else {}

    # primeiro boot: sorteia o PIN e grava, para nao existir instalacao sem senha
    if not str(dados.get("pin") or "").strip():
        dados["pin"] = f"{secrets.randbelow(900000) + 100000}"
        if os.environ.get(SOMENTE_LEITURA) != "1":
            ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
            ARQUIVO.write_text(json.dumps(dados, ensure_ascii=False, indent=2),
                               encoding="utf-8")
            print("=" * 58)
            print(f"  PIN de acesso gerado: {dados['pin']}")
            print(f"  Guardado em {ARQUIVO}")
            print("=" * 58)

    return Config(dados)


CONFIG = carregar()
