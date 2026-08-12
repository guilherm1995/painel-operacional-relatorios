# Painel Operacional — relatórios diários de campo

> Transforma a extração diária de um sistema de field service em quatro painéis PNG prontos para envio, replicando um layout que antes era montado à mão em planilha.

![status](https://img.shields.io/badge/status-portfolio-blue)
![licença](https://img.shields.io/badge/licen%C3%A7a-MIT-green)

## O problema

O acompanhamento diário da operação de campo era feito numa planilha com quatro
abas. Todo dia alguém colava a extração do sistema, conferia fórmula por fórmula,
tirava print de cada aba e mandava no grupo. Quarenta minutos por dia, e o
número mudava dependendo de quem fez.

## A solução

Uma única fonte de verdade — a extração do dia — e quatro imagens geradas por
código, com o mesmo visual da planilha original:

| Saída | Tela |
|---|---|
| `01_painel_acompanhamento.png` | Painel de acompanhamento |
| `02_gestao_prazos.png` | Gestão de prazos |
| `03_tempo_execucao.png` | Tempo de execução |
| `04_carga_servicos.png` | Carga de serviços |

```bash
python gerar.py extracao_do_dia.xlsx
```

Aceita `.xlsx` (aba `ANALITICO`, ou a primeira disponível) e `.csv`. Precisa
apenas das 32 colunas brutas do export. Todo o resto é calculado.

## Como está organizado

```
gerar.py                  ponto de entrada da CLI
extrair_parametros.py     lê a aba BASE da planilha e gera config/parametros.json
operacional/
  analitico.py            as métricas: produtividade, prazo, tempo, carga
  parametros.py           os de/para (cidade -> cluster, status, pontuação)
  estilo.py               paleta, fontes e grid — o visual da planilha original
  relatorios.py           monta cada uma das quatro telas
  render.py               desenha em PNG (Pillow)
web/                      site FastAPI para upload da extração e ajuste dos parâmetros
integracao_bot/           gancho para o monitor disparar a geração sozinho
```

## Decisões que valem comentário

**Separar parâmetro de código.** O `config/parametros.json` sai da própria planilha,
pelo `extrair_parametros.py`. Cidade nova ou mudança de baremo não exige mexer no
Python — roda o extrator de novo. E o `gerar.py` avisa no fim quando encontra algo
fora do de/para, em vez de silenciosamente jogar no "outros".

**Horário determinístico.** A opção `--agora` fixa o instante usado nos tempos
decorridos. Sem isso, rodar a mesma extração duas vezes dava números diferentes,
e ficava impossível comparar o resultado com o da planilha para validar a
migração.

**Replicar antes de melhorar.** O layout copia a planilha de propósito, inclusive
onde ela era estranha. Quem recebia a imagem no grupo não precisou reaprender a
ler. As melhorias vieram depois, já com a saída automatizada aceita.

## Rodando

```bash
pip install -r requirements.txt
python extrair_parametros.py sua_planilha.xlsx   # gera config/parametros.json
python gerar.py extracao_do_dia.xlsx
```

Opções úteis:

```bash
python gerar.py extracao.xlsx --apenas capa --apenas prazos
python gerar.py extracao.xlsx --agora "2026-06-11 18:11"
python gerar.py extracao.xlsx --dia sabado
python gerar.py extracao.xlsx --escala 3
```

Site local:

```bash
python iniciar_site.py
```

## Aviso

Este repositório é uma versão de portfólio, extraída de um sistema que rodou em
produção. Foi anonimizado antes da publicação: nomes de empresas, domínios
internos, credenciais, sessões de mensageria e dados de clientes foram
substituídos por valores de exemplo. Os arquivos de configuração são gabaritos,
não os valores reais de operação.

O código está aqui como referência técnica. Para rodar de verdade, é preciso
apontar as variáveis de ambiente e os configs para um ambiente próprio.

## Licença

MIT — veja [LICENSE](LICENSE). Copyright (c) 2026 Guilherme da Silva dos Santos.
