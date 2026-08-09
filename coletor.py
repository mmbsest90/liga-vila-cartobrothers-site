#!/usr/bin/env python3
"""
Liga Vila Cartobrothers — coletor automático
=============================================
Roda no VPS, sem ninguém clicar em nada.

O que ele faz, nesta ordem:
  1. pergunta à API do Cartola qual é a rodada atual
  2. coleta apenas as rodadas que ainda faltam (a última é sempre refeita)
  3. calcula classificações, mitão, Capitão Mito, Mais Rico e as copas
  4. gera a página a partir de pagina-modelo.html
  5. publica no GitHub Pages, se estiver configurado

Só lê a API e escreve dentro da própria pasta. Não toca em mais nada do servidor.

Uso:
    python3 coletor.py              # coleta o que falta e a rodada em andamento
    python3 coletor.py --revisar    # relê também a última rodada já fechada
    python3 coletor.py --publicar   # coleta, gera e publica
    python3 coletor.py --forcar     # recoleta tudo do zero
"""

import csv
import hashlib
import json
import os
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------- configuração

API = "https://api.cartola.globo.com"
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
PAUSA = 0.12          # segundos entre consultas, para não abusar da API
TENTATIVAS = 3

BASE = os.path.dirname(os.path.abspath(__file__))
DADOS = os.path.join(BASE, "dados")
SAIDA = os.path.join(BASE, "saida")
SITE = os.path.join(BASE, "site")

TEMPORADA = 2026

# Premiação — ajuste a cada temporada. Os potes já descontam a taxa de 12%.
PREMIOS = {
    "anual":  {"pote": 2300, "pc": [0.60, 0.25, 0.15]},
    "turno1": {"pote": 1936, "pc": [0.60, 0.25, 0.15]},
    "turno2": {"pote": 2200, "pc": [0.60, 0.25, 0.15]},
    "mitada": 100, "capitao": 100, "rico": 100, "mensal": 100,
    "libertadores": 250, "sulamericana": 200, "recopa": 150,
}

# Copas — faixas da classificação do ANUAL ao fim do 1º turno.
# O ideal do regulamento é 32+32; encolhe conforme o número de inscritos.
COPAS_FAIXAS = {"libertadores": (1, 16), "sulamericana": (17, 48)}
SEED16 = [1, 16, 8, 9, 4, 13, 5, 12, 2, 15, 7, 10, 3, 14, 6, 11]
SEED32 = [1, 32, 16, 17, 8, 25, 9, 24, 4, 29, 13, 20, 5, 28, 12, 21,
          2, 31, 15, 18, 7, 26, 10, 23, 3, 30, 14, 19, 6, 27, 11, 22]
FASES = {
    "libertadores": [("Oitavas", 20), ("Quartas", 21), ("Semifinal", 22), ("Final", 23)],
    "sulamericana": [("Primeira fase", 20), ("Oitavas", 21), ("Quartas", 22),
                     ("Semifinal", 23), ("Final", 24)],
}
RODADA_RECOPA = 25

CAPITAO_MULT = 1.5    # no Cartola o capitão vale 1,5x, não 2x

# posições do Cartola e quantos entram na escalação mais popular (4-3-3)
POSICOES = {1: "GOL", 2: "LAT", 3: "ZAG", 4: "MEI", 5: "ATA", 6: "TEC"}
FORMACAO = [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 1)]

TURNOS = {"anual": (1, 38), "turno1": (1, 19), "turno2": (20, 38)}

FUSO = timezone(timedelta(hours=-3))


# ------------------------------------------------------------------ utilidades

def log(msg=""):
    print(msg, flush=True)


def agora():
    return datetime.now(FUSO).strftime("%d/%m/%Y às %H:%M")


def buscar(caminho):
    """Consulta a API com algumas tentativas. Devolve None se não conseguir."""
    for tentativa in range(TENTATIVAS):
        try:
            req = urllib.request.Request(API + caminho, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if tentativa == TENTATIVAS - 1:
                return None
            time.sleep(1.5 * (tentativa + 1))
    return None


def ler_csv(caminho):
    if not os.path.exists(caminho):
        return []
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def gravar_csv(caminho, linhas, colunas):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas, delimiter=";")
        w.writeheader()
        for l in linhas:
            w.writerow({c: l.get(c, "") for c in colunas})


def n2(v):
    return None if v is None else round(float(v), 2)


def br(v):
    """Número no formato brasileiro, para gravar nos CSVs."""
    return "" if v is None else f"{v:.2f}".replace(".", ",")


def de_br(s):
    """Aceita '51,71' (vírgula decimal), '0.7' (ponto decimal) e '1.234,56'.

    O PowerShell gravou pontuações com vírgula e capitães com ponto, então
    o leitor precisa aguentar os dois formatos sem confundir milhar com decimal.
    """
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    if "," in s:                      # tem vírgula: ela é o decimal, ponto é milhar
        s = s.replace(".", "").replace(",", ".")
    try:                              # sem vírgula: o ponto (se houver) é decimal
        return float(s)
    except ValueError:
        return None


# ------------------------------------------------------------------- cadastro

def carregar_times():
    caminho = os.path.join(DADOS, "times_cadastro.csv")
    times = ler_csv(caminho)
    if not times:
        log(f"  ERRO: não encontrei {caminho}")
        log("  Coloque o times_cadastro.csv na pasta dados/ e rode de novo.")
        sys.exit(1)
    for t in times:
        t["anual"] = bool((t.get("anual") or "").strip())
        t["turno1"] = bool((t.get("turno1") or "").strip())
        t["turno2"] = bool((t.get("turno2") or "").strip())
    return times


# --------------------------------------------------------------------- coleta

def carregar_cache(times, escal):
    """Lê o que já foi coletado, para não pedir de novo à API."""
    pts = {t["time_id"]: {} for t in times}
    caps = {t["time_id"]: {} for t in times}
    for r in ler_csv(os.path.join(SAIDA, "pontuacoes.csv")):
        tid = r.get("time_id")
        if tid not in pts:
            continue
        for k, v in r.items():
            if k and k.startswith("R") and k[1:].isdigit() and v:
                val = de_br(v)
                if val is not None:
                    pts[tid][int(k[1:])] = val
    for r in ler_csv(os.path.join(SAIDA, "escalados.csv")):
        try:
            rod = int(r["rodada"]); aid = int(r["atleta_id"])
        except (KeyError, ValueError, TypeError):
            continue
        escal.setdefault(rod, {})[aid] = {
            "apelido": r.get("apelido", ""), "posicao": r.get("posicao", "?"),
            "clube": r.get("clube", ""), "pontos": de_br(r.get("pontos")),
            "n": int(r.get("escalacoes") or 0),
        }
    for r in ler_csv(os.path.join(SAIDA, "capitaes.csv")):
        tid = r.get("time_id")
        if tid not in caps:
            continue
        for k, v in r.items():
            if k and k.startswith("R") and k[1:].isdigit() and v and "|" in v:
                nome, p = v.rsplit("|", 1)
                caps[tid][int(k[1:])] = (nome, de_br(p))
    return pts, caps


def coletar(times, pts, caps, patr, rodadas, escal=None, clubes=None):
    total = len(rodadas) * len(times)
    feito = falhas = 0
    for rod in rodadas:
        ok = 0
        if escal is not None:
            escal[rod] = {}          # recontagem limpa desta rodada
        for t in times:
            feito += 1
            d = buscar(f"/time/id/{t['time_id']}/{rod}")
            if d and d.get("pontos") is not None:
                pts[t["time_id"]][rod] = n2(d["pontos"])
                ok += 1
                # capitão
                cid = d.get("capitao_id")
                if cid:
                    for a in d.get("atletas", []):
                        if a.get("atleta_id") == cid:
                            caps[t["time_id"]][rod] = (a.get("apelido", ""), n2(a.get("pontos_num")))
                            break
                # patrimônio (para o prêmio Mais Rico)
                pv = None
                if isinstance(d.get("time"), dict):
                    pv = d["time"].get("patrimonio")
                if pv is None:
                    pv = d.get("patrimonio")
                if pv is not None:
                    patr[t["time_id"]] = {"valor": n2(pv), "rodada": rod}
                # escalação: conta quantas vezes cada atleta foi escalado (só o anual)
                if escal is not None and t["anual"]:
                    alvo = escal.setdefault(rod, {})
                    for a in d.get("atletas", []):
                        aid = a.get("atleta_id")
                        if not aid:
                            continue
                        e = alvo.setdefault(aid, {
                            "apelido": a.get("apelido", ""),
                            "posicao": POSICOES.get(a.get("posicao_id"), "?"),
                            "clube": (clubes or {}).get(str(a.get("clube_id")), ""),
                            "pontos": n2(a.get("pontos_num")),
                            "n": 0,
                        })
                        e["n"] += 1
                        if e["pontos"] is None:
                            e["pontos"] = n2(a.get("pontos_num"))
            else:
                falhas += 1
            time.sleep(PAUSA)
        log(f"    rodada {rod:2d}: {ok}/{len(times)} times")
    return feito, falhas


# ----------------------------------------------------------------- cálculos

def classificar(times, pts, filtro, de, ate, maxrod):
    ate = min(ate, maxrod)
    if ate < de:
        return []
    lista = []
    for t in times:
        if not t[filtro]:
            continue
        vals = [pts[t["time_id"]][r] for r in range(de, ate + 1) if r in pts[t["time_id"]]]
        soma = round(sum(vals), 2) if vals else 0.0
        lista.append({
            "time_id": t["time_id"], "time": t["nome_time"], "pontos": soma,
            "rodadas": len(vals),
            "media": round(soma / len(vals), 2) if vals else 0.0,
        })
    lista.sort(key=lambda x: -x["pontos"])
    for i, x in enumerate(lista, 1):
        x["posicao"] = i
    return lista


def calc_mitao(times_anual, pts, maxrod):
    saida = []
    for rod in range(1, maxrod + 1):
        melhor = None
        for t in times_anual:
            p = pts[t["time_id"]].get(rod)
            if p is not None and (melhor is None or p > melhor["pontos"]):
                melhor = {"rodada": rod, "time": t["nome_time"], "pontos": p}
        if melhor:
            saida.append(melhor)
    return saida


def calc_capitao_mito(times_anual, caps, maxrod):
    lista = []
    for t in times_anual:
        soma = 0.0
        n = 0
        for rod in range(1, maxrod + 1):
            c = caps[t["time_id"]].get(rod)
            if c and c[1] is not None:
                soma += c[1] * CAPITAO_MULT
                n += 1
        lista.append({"time_id": t["time_id"], "time": t["nome_time"],
                      "pontos": round(soma, 2), "rodadas": n})
    lista.sort(key=lambda x: -x["pontos"])
    for i, x in enumerate(lista, 1):
        x["posicao"] = i
    return lista


def montar_escalacao(escal, total_times):
    """Para cada rodada, monta o time mais escalado seguindo a FORMACAO."""
    saida = {}
    for rod, atletas in escal.items():
        porpos = {}
        for a in atletas.values():
            porpos.setdefault(a["posicao"], []).append(a)
        time = []
        for pid, quantos in FORMACAO:
            pos = POSICOES[pid]
            cand = sorted(porpos.get(pos, []), key=lambda x: (-x["n"], -(x["pontos"] or -99)))
            for a in cand[:quantos]:
                time.append({"apelido": a["apelido"], "posicao": pos, "clube": a["clube"],
                             "n": a["n"], "pontos": a["pontos"],
                             "pct": round(100 * a["n"] / total_times) if total_times else 0})
        if time:
            saida[str(rod)] = time
    return saida


def calc_mensais(times_anual, pts, maxrod):
    """Prêmios mensais. Quais rodadas contam em cada mês vem de dados/meses.csv,
    porque o agrupamento da liga não segue o calendário à risca.
    Só valem os inscritos no ANUAL. O mês fica 'parcial' até a última rodada dele
    ser disputada."""
    linhas = ler_csv(os.path.join(DADOS, "meses.csv"))
    if not linhas:
        return []
    saida = []
    for l in linhas:
        nome = (l.get("mes") or "").strip()
        brutas = (l.get("rodadas") or "").replace(" ", "")
        rods = sorted({int(x) for x in brutas.split(",") if x.isdigit()})
        if not nome or not rods:
            continue
        jogadas = [r for r in rods if r <= maxrod]
        lista = []
        if jogadas:                       # mês que ainda não começou não tem ranking
            for t in times_anual:
                vals = [pts[t["time_id"]][r] for r in jogadas if r in pts[t["time_id"]]]
                if not vals:
                    continue
                lista.append({"time_id": t["time_id"], "time": t["nome_time"],
                              "pontos": round(sum(vals), 2), "rodadas": len(vals)})
            lista.sort(key=lambda x: -x["pontos"])
            for i, x in enumerate(lista, 1):
                x["posicao"] = i
        saida.append({
            "mes": nome, "rodadas": rods, "jogadas": jogadas,
            "fechado": bool(rods) and max(rods) <= maxrod,
            "comecou": bool(jogadas),
            "classificacao": lista,
        })
    return saida


def calc_mais_rico(times_anual, patr):
    lista = [{"time_id": t["time_id"], "time": t["nome_time"],
              "patrimonio": patr[t["time_id"]]["valor"],
              "rodada": patr[t["time_id"]]["rodada"]}
             for t in times_anual if t["time_id"] in patr]
    lista.sort(key=lambda x: -x["patrimonio"])
    for i, x in enumerate(lista, 1):
        x["posicao"] = i
    return lista


def montar_copas(times, times_anual, pts, maxrod, parcial=None):
    """Chaveamento das copas. chaveamento-manual.csv, se existir, tem prioridade."""
    if maxrod < 19:
        return {}
    apos19 = classificar(times, pts, "anual", 1, 19, maxrod)
    seeds = {}
    for comp, (ini, fim) in COPAS_FAIXAS.items():
        seeds[comp] = {}
        for pos in range(ini, fim + 1):
            if pos <= len(apos19):
                x = apos19[pos - 1]
                seeds[comp][pos - ini + 1] = {"id": x["time_id"], "time": x["time"]}

    manual = ler_csv(os.path.join(DADOS, "chaveamento-manual.csv"))
    if manual:
        por_id = {t["time_id"]: t["nome_time"] for t in times}
        novo = {}
        for l in manual:
            comp = (l.get("competicao") or "").strip()
            if comp not in seeds:
                continue
            tid = (l.get("time_id") or "").strip()
            if tid in por_id:
                novo.setdefault(comp, {})[int(l["seed"])] = {"id": tid, "time": por_id[tid]}
        for comp, mapa in novo.items():
            if mapa:
                seeds[comp] = mapa
        if novo:
            log("  chaveamento manual aplicado")

    copas = {}
    for comp, mapa in seeds.items():
        n = len(mapa)
        if n < 2:
            continue
        ordem = [s for s in (SEED16 if comp == "libertadores" else SEED32) if s <= n]
        vivos = [{"seed": s, **mapa[s]} for s in ordem]
        fases = []
        vagas = len(ordem)          # quantos times a fase comporta
        # percorre TODAS as fases ate a final: as que ainda nao tem adversario
        # definido entram como vaga em aberto, para o chaveamento aparecer inteiro
        for nome, rod in FASES[comp]:
            if vagas < 2:
                break
            duelos, prox = [], []
            for k in range(0, vagas, 2):
                a = vivos[k]     if k     < len(vivos) else None
                b = vivos[k + 1] if k + 1 < len(vivos) else None
                if a and b:
                    pa = pts[a["id"]].get(rod)
                    pb = pts[b["id"]].get(rod)
                    venc = None
                    if pa is not None and pb is not None:
                        if pa > pb:
                            venc = a
                        elif pb > pa:
                            venc = b
                        else:                   # empate: melhor colocado no anual
                            venc = a if a["seed"] < b["seed"] else b
                    duelos.append({"a": a["time"], "aSeed": a["seed"], "aPts": pa,
                                   "b": b["time"], "bSeed": b["seed"], "bPts": pb,
                                   "venc": venc["time"] if venc else None})
                    if venc:
                        prox.append(venc)
                else:
                    duelos.append({"a": None, "aSeed": None, "aPts": None,
                                   "b": None, "bSeed": None, "bPts": None, "venc": None})
            fases.append({"nome": nome, "rodada": rod, "duelos": duelos,
                          "disputada": rod <= maxrod and rod != parcial
                                        and len(prox) == vagas // 2})
            vivos = prox
            vagas //= 2
        campeao = None
        if fases and fases[-1]["nome"] == "Final" and fases[-1]["disputada"] and len(vivos) == 1:
            campeao = vivos[0]["time"]
        copas[comp] = {"times": n, "fases": fases, "campeao": campeao}

    if copas:
        cl = copas.get("libertadores", {}).get("campeao")
        cs = copas.get("sulamericana", {}).get("campeao")
        copas["recopa"] = {"a": cl, "b": cs, "rodada": RODADA_RECOPA,
                           "pronta": bool(cl and cs)}
    return copas


# ------------------------------------------------------------------- saídas

def gravar_saidas(times, pts, caps, maxrod, classif, mitao, capmito, rico):
    cols_rod = [f"R{r}" for r in range(1, maxrod + 1)]

    linhas = []
    for t in times:
        l = {"time_id": t["time_id"], "nome_time": t["nome_time"]}
        vals = []
        for r in range(1, maxrod + 1):
            v = pts[t["time_id"]].get(r)
            l[f"R{r}"] = br(v)
            if v is not None:
                vals.append(v)
        l["total"] = br(round(sum(vals), 2) if vals else None)
        l["rodadas"] = len(vals)
        linhas.append(l)
    gravar_csv(os.path.join(SAIDA, "pontuacoes.csv"), linhas,
               ["time_id", "nome_time"] + cols_rod + ["total", "rodadas"])

    linhas = []
    for t in times:
        l = {"time_id": t["time_id"], "nome_time": t["nome_time"]}
        for r in range(1, maxrod + 1):
            c = caps[t["time_id"]].get(r)
            l[f"R{r}"] = f"{c[0]}|{br(c[1])}" if c else ""
        linhas.append(l)
    gravar_csv(os.path.join(SAIDA, "capitaes.csv"), linhas,
               ["time_id", "nome_time"] + cols_rod)

    for chave, arq in (("anual", "classificacao-anual.csv"),
                       ("turno1", "classificacao-turno1.csv"),
                       ("turno2", "classificacao-turno2.csv")):
        gravar_csv(os.path.join(SAIDA, arq),
                   [{**x, "pontos": br(x["pontos"]), "media": br(x["media"])} for x in classif[chave]],
                   ["posicao", "time", "pontos", "media", "rodadas", "time_id"])

    gravar_csv(os.path.join(SAIDA, "mitao.csv"),
               [{**x, "pontos": br(x["pontos"])} for x in mitao],
               ["rodada", "time", "pontos"])
    gravar_csv(os.path.join(SAIDA, "capitao-mito.csv"),
               [{**x, "pontos": br(x["pontos"])} for x in capmito],
               ["posicao", "time", "pontos", "rodadas", "time_id"])
    if rico:
        gravar_csv(os.path.join(SAIDA, "mais-rico.csv"),
                   [{**x, "patrimonio": br(x["patrimonio"])} for x in rico],
                   ["posicao", "time", "patrimonio", "rodada", "time_id"])


def texto_whatsapp(maxrod, classif, mitao, capmito, rico, parcial=None):
    L = ["*LIGA VILA CARTOBROTHERS*"]
    if parcial:
        L += [f"⏱ RODADA {parcial} EM ANDAMENTO — números parciais", ""]
    else:
        L += [f"Classificação após a rodada {maxrod}", ""]
    m = ["1º", "2º", "3º", "4º", "5º"]
    L.append("*ANUAL*")
    for i, x in enumerate(classif["anual"][:5]):
        L.append(f"{m[i]} {x['time']} - {br(x['pontos'])}")
    if classif["turno2"]:
        L += ["", "*2º TURNO*"]
        for i, x in enumerate(classif["turno2"][:3]):
            L.append(f"{m[i]} {x['time']} - {br(x['pontos'])}")
    ult = next((x for x in mitao if x["rodada"] == maxrod), None)
    if ult:
        L += ["", f"⭐ Mitão da rodada: {ult['time']} com {br(ult['pontos'])}"]
    rec = max(mitao, key=lambda x: x["pontos"]) if mitao else None
    if rec:
        L.append(f"🏅 Mitada do Ano (parcial): {rec['time']} - {br(rec['pontos'])} na rodada {rec['rodada']}")
    if capmito:
        L.append(f"🎩 Capitão Mito (parcial): {capmito[0]['time']} - {br(capmito[0]['pontos'])}")
    if rico:
        L.append(f"💰 Mais Rico (parcial): {rico[0]['time']} - C$ {br(rico[0]['patrimonio'])}")
    return "\n".join(L) + "\n"


def parcial_nao_vale(times, pts, parcial):
    """Diz por que a rodada em andamento NÃO deve entrar no site, ou None se pode.

    Duas armadilhas da API, as duas já vistas na prática:

    1. O mercado fecha algumas horas antes de a bola rolar. Nesse intervalo
       ninguém pontuou e a rodada não existe ainda.
    2. Pior: nesse mesmo intervalo a API responde /time/id/X/{rodada} com o
       resultado da rodada ANTERIOR, igualzinho. Sem esta checagem, a rodada
       passada entraria duas vezes e inflaria o anual inteiro.
    """
    tem = [t for t in times if pts[t["time_id"]].get(parcial) is not None]
    if not tem:
        return "a API não devolveu pontuação para nenhum time"
    if not any(pts[t["time_id"]][parcial] for t in tem):
        return "ninguém pontuou ainda"
    if parcial > 1:
        repetidos = sum(1 for t in tem
                        if pts[t["time_id"]].get(parcial - 1) == pts[t["time_id"]][parcial])
        if repetidos >= len(tem) * 0.9:
            return (f"a API repetiu a pontuação da rodada {parcial - 1} "
                    f"em {repetidos} de {len(tem)} times")
    return None


def sinalizar(nome, valor):
    """Devolve um resultado para o GitHub Actions, quando estiver rodando lá."""
    caminho = os.environ.get("GITHUB_OUTPUT")
    if caminho:
        with open(caminho, "a", encoding="utf-8") as f:
            f.write(f"{nome}={valor}\n")


def gerar_pagina(maxrod, times, pts, caps, classif, mitao, capmito, rico,
                 copas, mensais, escalacao=None, total_anual=0, parcial=None):
    modelo = os.path.join(BASE, "pagina-modelo.html")
    if not os.path.exists(modelo):
        log("  aviso: pagina-modelo.html não encontrado — página não gerada")
        return False

    detalhe = {}
    for t in times:
        tid = t["time_id"]
        p = {str(r): v for r, v in sorted(pts[tid].items()) if v is not None}
        c = {str(r): [x[0], round(x[1] * CAPITAO_MULT, 2)]
             for r, x in sorted(caps[tid].items()) if x[1] is not None}
        detalhe[tid] = {"pts": p, "cap": c}

    def enxugar(lista):
        return [{"posicao": x["posicao"], "time": x["time"], "pontos": x["pontos"],
                 "media": x.get("media"), "id": x["time_id"]} for x in lista]

    rec = max(mitao, key=lambda x: x["pontos"]) if mitao else None
    dados = {
        "temporada": TEMPORADA,
        "rodada": maxrod,
        "parcial": parcial,
        "atualizado": agora(),
        "detalhe": detalhe,
        "anual": enxugar(classif["anual"]),
        "turno1": enxugar(classif["turno1"]),
        "turno2": enxugar(classif["turno2"]),
        "mitao": [{"rodada": x["rodada"], "time": x["time"], "pontos": x["pontos"]} for x in mitao],
        "mitadaAno": {"time": rec["time"], "pontos": rec["pontos"], "rodada": rec["rodada"]} if rec else None,
        "capitaoMito": [{"posicao": x["posicao"], "time": x["time"], "pontos": x["pontos"],
                         "id": x["time_id"]} for x in capmito],
        "maisRico": [{"posicao": x["posicao"], "time": x["time"], "patrimonio": x["patrimonio"],
                      "id": x["time_id"]} for x in rico],
        "copas": copas,
        "escalacao": escalacao or {},
        "totalAnual": total_anual,
        "mensais": [{"mes": m["mes"], "rodadas": m["rodadas"], "jogadas": m["jogadas"],
                     "fechado": m["fechado"], "comecou": m["comecou"],
                     "classificacao": [{"posicao": x["posicao"], "time": x["time"],
                                        "pontos": x["pontos"], "id": x["time_id"]}
                                       for x in m["classificacao"]]}
                    for m in mensais],
        "premios": {
            "anual":  [round(PREMIOS["anual"]["pote"] * p, 2) for p in PREMIOS["anual"]["pc"]],
            "turno1": [round(PREMIOS["turno1"]["pote"] * p, 2) for p in PREMIOS["turno1"]["pc"]],
            "turno2": [round(PREMIOS["turno2"]["pote"] * p, 2) for p in PREMIOS["turno2"]["pc"]],
            "pcts":   [int(p * 100) for p in PREMIOS["anual"]["pc"]],
            "mitada": PREMIOS["mitada"], "capitao": PREMIOS["capitao"],
            "rico": PREMIOS["rico"], "mensal": PREMIOS["mensal"],
            "libertadores": PREMIOS["libertadores"], "sulamericana": PREMIOS["sulamericana"],
            "recopa": PREMIOS["recopa"],
        },
    }

    # A hora da atualização muda a cada execução, então ela fica de fora da
    # assinatura: o que interessa é se algum NÚMERO mudou.
    assinado = dict(dados); assinado.pop("atualizado", None)
    marca = hashlib.sha256(
        json.dumps(assinado, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    antes = os.path.join(SAIDA, "assinatura.txt")
    igual = (os.path.exists(antes)
             and open(antes, encoding="utf-8").read().strip() == marca
             and os.path.exists(os.path.join(SITE, "index.html")))
    if igual:
        return "igual"

    with open(modelo, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__DADOS__", json.dumps(dados, ensure_ascii=False, separators=(",", ":")))

    os.makedirs(SITE, exist_ok=True)
    with open(os.path.join(SITE, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    os.makedirs(SAIDA, exist_ok=True)
    with open(antes, "w", encoding="utf-8") as f:
        f.write(marca + "\n")
    return True


# ---------------------------------------------------------------- publicação

def publicar():
    """Envia a pasta site/ para o repositório do GitHub Pages, se configurado."""
    repo = os.path.join(BASE, "site")
    if not os.path.isdir(os.path.join(repo, ".git")):
        log("  publicação: site/ ainda não é um repositório git — pulei")
        return False
    try:
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True,
                       capture_output=True, timeout=60)
        r = subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet"],
                           capture_output=True, timeout=60)
        if r.returncode == 0:
            log("  publicação: nada mudou na página")
            return True
        subprocess.run(["git", "-C", repo, "commit", "-q", "-m",
                        f"Atualiza classificacao - {agora()}"],
                       check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "-C", repo, "push", "-q", "origin", "main"],
                       check=True, capture_output=True, timeout=120)
        log("  publicação: enviada ao GitHub Pages")
        return True
    except subprocess.CalledProcessError as e:
        saida = (e.stderr or b"").decode("utf-8", "ignore")[:300]
        log(f"  publicação FALHOU: {saida}")
        return False
    except Exception as e:
        log(f"  publicação FALHOU: {e}")
        return False


# --------------------------------------------------------------------- main

def main():
    forcar = "--forcar" in sys.argv
    revisar = "--revisar" in sys.argv
    quer_publicar = "--publicar" in sys.argv

    log("")
    log("=" * 54)
    log("   LIGA VILA CARTOBROTHERS — atualização automática")
    log("=" * 54)
    log(f"   {agora()}")

    times = carregar_times()
    st = buscar("/mercado/status")
    if not st:
        log("  ERRO: a API do Cartola não respondeu. Encerrando sem alterar nada.")
        sys.exit(2)

    rodada_atual = int(st.get("rodada_atual") or 0)
    status = int(st.get("status_mercado") or 0)
    estado = {1: "aberto", 2: "fechado", 3: "manutenção", 4: "encerrado"}.get(status, "?")

    # Mercado aberto significa que a rodada ainda vai comecar, entao a ultima
    # valida e a anterior. Fechado significa jogos rolando: a rodada entra como
    # PARCIAL e volta a ser coletada a cada execucao ate o mercado reabrir.
    if status == 1:
        maxrod = max(0, rodada_atual - 1)
        parcial = None
    else:
        maxrod = max(0, rodada_atual)
        parcial = rodada_atual if status in (2, 3) else None

    log("")
    log(f"  Rodada atual....: {rodada_atual} (mercado {estado})")
    log(f"  Considerando....: 1 a {maxrod}" + (f"  (a {parcial} é parcial)" if parcial else ""))
    log(f"  Times cadastrados: {len(times)}")

    if maxrod < 1:
        log("  Nenhuma rodada disputada ainda.")
        return

    escal = {}
    pts, caps = ({t["time_id"]: {} for t in times}, {t["time_id"]: {} for t in times}) \
        if forcar else carregar_cache(times, escal)
    patr = {}

    # A rodada em andamento é sempre refeita. As já fechadas só voltam à API se
    # ficou time sem pontuação ou se for a revisão diária (--revisar), que pega
    # eventual correção de placar feita pelo Cartola depois do apito final.
    faltando = [r for r in range(1, maxrod + 1)
                if r == parcial
                or (revisar and r == maxrod)
                or sum(1 for t in times if r in pts[t["time_id"]]) < len(times)]

    if not faltando:
        log("  Nada a coletar: tudo em dia.")
    else:
        log(f"  A coletar.......: rodada(s) {', '.join(map(str, faltando))}")
        log("")
        clubes = {}
        dc = buscar("/clubes")
        if isinstance(dc, dict):
            for cid, c in dc.items():
                clubes[str(cid)] = (c.get("abreviacao") or c.get("nome") or "").strip()
        feito, falhas = coletar(times, pts, caps, patr, faltando, escal, clubes)
        if falhas:
            log(f"  Consultas sem resposta: {falhas}")

    if parcial:
        motivo = parcial_nao_vale(times, pts, parcial)
        if motivo:
            log(f"  A rodada {parcial} não entra: {motivo}.")
            log(f"  Mostrando até a rodada {parcial - 1}.")
            for t in times:
                pts[t["time_id"]].pop(parcial, None)
                caps[t["time_id"]].pop(parcial, None)
            escal.pop(parcial, None)
            maxrod = max(0, parcial - 1)
            parcial = None
            if maxrod < 1:
                log("  Nenhuma rodada disputada ainda.")
                return

    times_anual = [t for t in times if t["anual"]]
    classif = {k: classificar(times, pts, k, de, ate, maxrod) for k, (de, ate) in TURNOS.items()}
    mitao = calc_mitao(times_anual, pts, maxrod)
    capmito = calc_capitao_mito(times_anual, caps, maxrod)
    rico = calc_mais_rico(times_anual, patr)
    mensais = calc_mensais(times_anual, pts, maxrod)
    copas = montar_copas(times, times_anual, pts, maxrod, parcial)

    gravar_saidas(times, pts, caps, maxrod, classif, mitao, capmito, rico)
    if mensais:
        linhas_m = []
        for m in mensais:
            for x in m["classificacao"]:
                linhas_m.append({"mes": m["mes"], "posicao": x["posicao"], "time": x["time"],
                                 "pontos": br(x["pontos"]), "rodadas": x["rodadas"],
                                 "situacao": "fechado" if m["fechado"] else "parcial",
                                 "time_id": x["time_id"]})
        gravar_csv(os.path.join(SAIDA, "mensais.csv"), linhas_m,
                   ["mes", "posicao", "time", "pontos", "rodadas", "situacao", "time_id"])
    if escal:
        linhas_e = []
        for rod in sorted(escal):
            for aid, a in sorted(escal[rod].items(), key=lambda kv: -kv[1]["n"]):
                linhas_e.append({"rodada": rod, "atleta_id": aid, "apelido": a["apelido"],
                                 "posicao": a["posicao"], "clube": a["clube"],
                                 "escalacoes": a["n"], "pontos": br(a["pontos"])})
        gravar_csv(os.path.join(SAIDA, "escalados.csv"), linhas_e,
                   ["rodada", "atleta_id", "apelido", "posicao", "clube", "escalacoes", "pontos"])

    with open(os.path.join(SAIDA, "resumo-whatsapp.txt"), "w", encoding="utf-8") as f:
        f.write(texto_whatsapp(maxrod, classif, mitao, capmito, rico, parcial))

    escalacao = montar_escalacao(escal, len(times_anual))
    ok = gerar_pagina(maxrod, times, pts, caps, classif, mitao, capmito, rico,
                      copas, mensais, escalacao, len(times_anual), parcial)

    log("")
    log("  " + "-" * 50)
    if classif["anual"]:
        log("  ANUAL — top 3")
        for x in classif["anual"][:3]:
            log(f"    {x['posicao']}. {x['time'][:34]:<34} {x['pontos']:>9.2f}")
    if capmito:
        log(f"  Capitão Mito: {capmito[0]['time']} ({capmito[0]['pontos']:.2f})")
    if rico:
        log(f"  Mais Rico...: {rico[0]['time']} (C$ {rico[0]['patrimonio']:.2f})")
    if mensais:
        atual = [m for m in mensais if m["comecou"] and not m["fechado"]]
        if atual and atual[-1]["classificacao"]:
            m = atual[-1]
            log(f"  Mensal {m['mes']} (parcial): {m['classificacao'][0]['time']} "
                f"({m['classificacao'][0]['pontos']:.2f})")
    if copas:
        for comp in ("libertadores", "sulamericana"):
            if comp in copas:
                c = copas[comp]
                feitas = sum(1 for f in c["fases"] if f["disputada"])
                log(f"  {comp.capitalize():<14}: {feitas}/{len(c['fases'])} fases"
                    + (f" — campeão: {c['campeao']}" if c["campeao"] else ""))
    log("  " + "-" * 50)
    if ok == "igual":
        log("  Nenhum número mudou desde a última execução. Página mantida como está.")
    elif ok:
        log("  Página gerada em site/index.html")
    sinalizar("mudou", "nao" if ok == "igual" else "sim")
    if quer_publicar:
        publicar()
    log("")


if __name__ == "__main__":
    main()
