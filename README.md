# Liga Vila Cartobrothers

Apuração automática da liga de Cartola FC, temporada 2026.

**Classificação ao vivo:** https://mmbsest90.github.io/liga-vila-cartobrothers-site/

---

## Como funciona

Todo dia às 6h da manhã, o GitHub roda o `coletor.py`. Ele pergunta à API do
Cartola qual é a rodada atual, coleta apenas o que ainda falta, recalcula tudo
e publica a página. Nos dias sem rodada nova, encerra em segundos sem fazer nada.

Também dá para atualizar na hora: aba **Actions** → **Atualizar classificação**
→ botão **Run workflow**.

## O que é calculado

| Competição | Regra |
|---|---|
| Anual | soma das 38 rodadas |
| 1º e 2º Turno | rodadas 1–19 e 20–38, com listas de inscritos próprias |
| Time mais escalado | jogador mais escolhido em cada posição, entre os inscritos no anual |
| Mitada do Ano | maior pontuação em rodada única, só entre inscritos no anual |
| Capitão Mito | soma dos pontos dos capitães (o capitão vale 1,5×) |
| Mais Rico | maior patrimônio em cartoletas |
| Libertadores | 1º ao 16º do anual ao fim do 1º turno |
| Sul-Americana | 17º ao 48º do anual ao fim do 1º turno |
| Recopa | campeões das duas copas |

Nos mata-matas, quem faz mais pontos na rodada avança. Empate: passa o melhor
colocado no anual.

## Arquivos

    coletor.py                  o programa
    pagina-modelo.html          o desenho da página (dados entram no lugar de __DADOS__)
    dados/times_cadastro.csv    os 67 times e em quais competições cada um está
    dados/chaveamento-manual.csv  chaveamento das copas como foi divulgado ao grupo
    saida/                      CSVs gerados e o texto pronto para o WhatsApp
    site/index.html             a página publicada

## Ajustes por temporada

Tudo que muda de ano para ano está no topo do `coletor.py`:
valores de premiação, faixas de classificação das copas e rodadas de cada fase.

Se `dados/chaveamento-manual.csv` existir, ele tem prioridade sobre o
chaveamento calculado. Para 2027, basta apagar esse arquivo.

## Privacidade

Este repositório contém apenas nomes de **times** e pontuações — as mesmas
informações que a página mostra. Nomes de participantes, controle de pagamentos
e o histórico de inscrições ficam fora daqui.

## Rodar no seu computador

    python3 coletor.py              # coleta e gera a página
    python3 coletor.py --forcar     # recoleta tudo do zero

Não precisa instalar nada além do Python 3.
