"""Gera as 3 imagens do painel a partir da extracao do dia.

Uso:
    python gerar.py "C:/caminho/extracao.xlsx"
    python gerar.py "extracao.xlsx" --saida saida --aba ANALITICO
    python gerar.py "extracao.xlsx" --agora "2026-06-11 18:11"   (fixa o horario)
    python gerar.py "extracao.xlsx" --apenas capa

Saida (pasta ./saida por padrao):
    01_painel_acompanhamento.png
    02_gestao_prazos.png
    03_tempo_execucao.png
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from operacional import (capa_envio_grupo, carga_servicos, carregar_config,
                    carregar_extracao, gestao_prazos, preparar, tempo_execucao)
from operacional.analitico import diagnosticar, momento_painel
from operacional.render import (gravar_imagens, html_capa, html_carga, html_prazos,
                           html_tempo)

NOMES = {
    "capa": "01_painel_acompanhamento",
    "prazos": "02_gestao_prazos",
    "tempo": "03_tempo_execucao",
    "carga": "04_carga_servicos",
}


def montar_argumentos() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gera as imagens do painel OPERACIONAL.")
    p.add_argument("extracao", help="Arquivo .xlsx ou .csv com a extracao do dia")
    p.add_argument("--aba", default=None, help="Aba da extracao (padrao: ANALITICO ou a primeira)")
    p.add_argument("--saida", default="saida", help="Pasta de destino das imagens")
    p.add_argument("--agora", default=None,
                   help="Horario de referencia 'AAAA-MM-DD HH:MM' (padrao: relogio do sistema)")
    p.add_argument("--apenas", choices=list(NOMES), action="append",
                   help="Gera so o relatorio indicado (pode repetir)")
    p.add_argument("--dia", choices=["util", "sabado", "domingo"], default=None,
                   help="Tipo de dia da CARGA DE SERVICOS (padrao: pelo dia da extracao)")
    p.add_argument("--escala", type=int, default=2, help="Fator de nitidez da imagem (padrao 2)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = montar_argumentos().parse_args(argv)

    caminho = Path(args.extracao)
    if not caminho.exists():
        print(f"Extracao nao encontrada: {caminho}", file=sys.stderr)
        return 1

    agora = dt.datetime.now()
    if args.agora:
        for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                agora = dt.datetime.strptime(args.agora, formato)
                break
            except ValueError:
                continue
        else:
            print(f"Nao entendi o horario: {args.agora}", file=sys.stderr)
            return 1

    config = carregar_config()
    bruto = carregar_extracao(caminho, aba=args.aba)
    df = preparar(bruto, agora=agora,
                  somente_servicos=config.get("somente_servicos", True),
                  clusters_excluidos=config.get("clusters_excluidos"),
                  servicos_excluidos=config.get("servicos_excluidos"))

    # O carimbo do cabecalho segue a planilha: data da extracao + hora do ultimo
    # evento. Com --agora, o horario informado manda em tudo.
    carimbo = agora if args.agora else momento_painel(df)

    escolhidos = args.apenas or list(NOMES)
    paginas: dict[str, str] = {}
    if "capa" in escolhidos:
        paginas[NOMES["capa"]] = html_capa(capa_envio_grupo(df, config), carimbo)
    if "prazos" in escolhidos:
        paginas[NOMES["prazos"]] = html_prazos(gestao_prazos(df, config), carimbo)
    if "tempo" in escolhidos:
        paginas[NOMES["tempo"]] = html_tempo(tempo_execucao(df, config), carimbo)
    if "carga" in escolhidos:
        paginas[NOMES["carga"]] = html_carga(carga_servicos(df, config, dia=args.dia), carimbo)

    gerados = gravar_imagens(paginas, args.saida, escala=args.escala)

    data_ref = df.attrs.get("data_referencia")
    print(f"Extracao: {caminho.name}  |  {len(df)} atividades"
          + (f"  |  data {data_ref:%d/%m/%Y}" if data_ref is not None else ""))
    print(f"Carimbo do painel: {carimbo:%d/%m/%Y %H:%M:%S}"
          f"  |  relogio para tempos decorridos: {agora:%d/%m/%Y %H:%M}")
    for destino in gerados:
        print(f"  gerado: {destino}")

    avisos = diagnosticar(df)
    if avisos:
        print("\nATENCAO:")
        for aviso in avisos:
            print(f"  - {aviso}")
        print("  Atualize a aba BASE do Excel e rode extrair_parametros.py de novo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
