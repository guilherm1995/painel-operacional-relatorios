"""As 4 telas do painel: lista as imagens de saida/ e gera novas a partir de um upload."""

from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

from ..config import CONFIG

TELAS = [
    ("01_painel_acompanhamento", "Painel de Acompanhamento"),
    ("02_gestao_prazos", "Gestão de Prazos"),
    ("03_tempo_execucao", "Tempo de Execução"),
    ("04_carga_servicos", "Carga de Serviços"),
]

EXTENSOES_ACEITAS = {".xlsx", ".xlsm", ".csv", ".txt"}


def listar() -> list[dict]:
    """Telas geradas, na ordem do painel, com o horario de cada uma."""
    saida = []
    for nome, titulo in TELAS:
        arquivo = CONFIG.pasta_saida / f"{nome}.png"
        if not arquivo.exists():
            saida.append({"nome": nome, "titulo": titulo, "existe": False})
            continue
        ts = dt.datetime.fromtimestamp(arquivo.stat().st_mtime)
        saida.append({
            "nome": nome,
            "titulo": titulo,
            "existe": True,
            "quando": ts.strftime("%d/%m/%Y %H:%M"),
            "minutos": round((dt.datetime.now() - ts).total_seconds() / 60),
        })
    return saida


def caminho(nome: str) -> Path | None:
    """Resolve o PNG de uma tela, sem permitir sair da pasta de saida."""
    if not re.fullmatch(r"[\w\-]+", nome or ""):
        return None
    alvo = (CONFIG.pasta_saida / f"{nome}.png").resolve()
    try:
        alvo.relative_to(CONFIG.pasta_saida.resolve())
    except ValueError:
        return None
    return alvo if alvo.is_file() else None


def gerar(arquivo_enviado: Path, dia: str | None = None) -> dict:
    """Roda o motor do painel sobre a extracao enviada e regrava saida/.

    `dia` sobrescreve so esta geracao; sem ele, usa o `tipo_dia` salvo em
    config/painel.json (o mesmo que a tela de Configurações grava).
    """
    from operacional import (capa_envio_grupo, carga_servicos, carregar_config,
                        carregar_extracao, gestao_prazos, preparar, tempo_execucao)
    from operacional.analitico import diagnosticar, momento_painel
    from operacional.render import (gravar_imagens, html_capa, html_carga,
                               html_prazos, html_tempo)

    config = carregar_config()
    bruto = carregar_extracao(arquivo_enviado)
    df = preparar(bruto,
                  somente_servicos=config.get("somente_servicos", True),
                  clusters_excluidos=config.get("clusters_excluidos"),
                  servicos_excluidos=config.get("servicos_excluidos"))
    carimbo = momento_painel(df)

    paginas = {
        "01_painel_acompanhamento": html_capa(capa_envio_grupo(df, config), carimbo),
        "02_gestao_prazos": html_prazos(gestao_prazos(df, config), carimbo),
        "03_tempo_execucao": html_tempo(tempo_execucao(df, config), carimbo),
        "04_carga_servicos": html_carga(carga_servicos(df, config, dia=dia), carimbo),
    }
    gravar_imagens(paginas, CONFIG.pasta_saida)

    return {
        "atividades": len(df),
        "carimbo": carimbo.strftime("%d/%m/%Y %H:%M"),
        "avisos": diagnosticar(df),
    }


def guardar_upload(nome_original: str, conteudo: bytes) -> Path:
    """Grava a extracao enviada em uploads/, com data e hora no nome."""
    extensao = Path(nome_original).suffix.lower()
    if extensao not in EXTENSOES_ACEITAS:
        raise ValueError(
            f"Formato {extensao or '(sem extensão)'} não aceito. "
            f"Envie {', '.join(sorted(EXTENSOES_ACEITAS))}."
        )
    if not conteudo:
        raise ValueError("O arquivo chegou vazio.")

    CONFIG.pasta_uploads.mkdir(parents=True, exist_ok=True)
    destino = CONFIG.pasta_uploads / f"extracao_{dt.datetime.now():%Y%m%d_%H%M%S}{extensao}"
    destino.write_bytes(conteudo)
    _limpar_antigos()
    return destino


def _limpar_antigos(manter: int = 20) -> None:
    """Guarda so os ultimos envios, para a pasta nao crescer sem limite."""
    arquivos = sorted(CONFIG.pasta_uploads.glob("extracao_*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    for antigo in arquivos[manter:]:
        try:
            antigo.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# configuracao editavel pela tela (dia da semana, clusters ativos, M.O.)
# --------------------------------------------------------------------------
DIAS_CARGA = {"auto": "Automático", "util": "Dia útil",
              "sabado": "Sábado", "domingo": "Domingo"}


def _arquivo_config() -> Path:
    from operacional.relatorios import ARQUIVO_PAINEL
    return ARQUIVO_PAINEL


def configuracao_atual() -> dict:
    """O que a tela de Configurações precisa: valores salvos + clusters conhecidos."""
    from operacional import carregar_config
    from operacional.parametros import carregar as carregar_parametros

    config = carregar_config()
    par = carregar_parametros()
    conhecidos = par.clusters_conhecidos()
    excluidos = {c.upper() for c in (config.get("clusters_excluidos") or [])}
    carga_atuais = {c.upper() for c in (config.get("carga_clusters") or [])}
    mo_manual = config.get("mo_ativa_manual") or {}

    return {
        "tipo_dia": config.get("tipo_dia") or "auto",
        "dias_opcoes": DIAS_CARGA,
        "clusters": [
            {"codigo": c, "ativo": c.upper() not in excluidos,
             "na_carga": c.upper() in carga_atuais,
             "mo_ativa": mo_manual.get(c, "")}
            for c in conhecidos
        ],
        "mo_ativa_operacional": mo_manual.get("OPERACIONAL", ""),
    }


def salvar_configuracao(tipo_dia: str, clusters_ativos: list[str],
                        carga_clusters: list[str], mo_ativa: dict[str, str]) -> None:
    """Grava dia da semana, clusters excluidos, colunas da Carga de Servicos e M.O.

    Mantem intactas as demais chaves do arquivo (metas, limites de linha etc.) -
    so estas quatro sao geridas pela tela de Configurações.
    """
    import json

    arquivo = _arquivo_config()
    dados = json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}

    ordem_conhecidos: list[str] = []
    try:
        from operacional.parametros import carregar as carregar_parametros
        ordem_conhecidos = carregar_parametros().clusters_conhecidos()
    except Exception:
        pass

    ativos_norm = {c.upper() for c in clusters_ativos}
    dados["clusters_excluidos"] = sorted(c for c in ordem_conhecidos if c.upper() not in ativos_norm)
    dados["tipo_dia"] = tipo_dia if tipo_dia in DIAS_CARGA else "auto"

    # um cluster desativado no bloco de cima nunca pode sobrar aqui, mesmo que
    # o formulario tenha mandado o campo marcado (ex.: estado antigo da pagina).
    # Se nada sobrar, cai para todos os ativos - nunca para um nome fixo, que
    # poderia reintroduzir um cluster que acabou de ser desativado.
    carga_norm = {c.upper() for c in carga_clusters} & ativos_norm
    dados["carga_clusters"] = ([c for c in ordem_conhecidos if c.upper() in carga_norm]
                               or [c for c in ordem_conhecidos if c.upper() in ativos_norm])

    limpo = {}
    for cluster, valor in mo_ativa.items():
        texto = str(valor).strip()
        if not texto:
            continue
        if not texto.isdigit():
            raise ValueError(f"Quantidade de técnicos inválida para {cluster}: '{texto}'.")
        limpo[cluster.upper()] = int(texto)
    dados["mo_ativa_manual"] = limpo

    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def ultima_extracao() -> dict | None:
    if not CONFIG.pasta_uploads.exists():
        return None
    arquivos = sorted(CONFIG.pasta_uploads.glob("extracao_*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if not arquivos:
        return None
    ts = dt.datetime.fromtimestamp(arquivos[0].stat().st_mtime)
    return {"nome": arquivos[0].name, "quando": ts.strftime("%d/%m/%Y %H:%M")}
