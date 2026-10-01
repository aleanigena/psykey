#!/usr/bin/env python3
"""
PsyKey 5.3 (formerly BPM Renamer)

Aba 1 - Renomear por BPM
    Detecta o BPM (2 casas decimais) de músicas mp3, flac e wav, otimizado para
    música eletrônica de andamento estável e BPM alto (psytrance, darkpsy, hi-tech,
    forest, psycore... de 135 a 500 BPM), e renomeia colocando o BPM na frente do nome.

        "Minha Musica.mp3"  ->  "148,00 - Minha Musica.mp3"

    Botão direito em uma música:
        - copiar para a pasta de sets/apresentações
        - abrir a pasta original da música
        - ver espectro e tom

Aba 2 - Espectro e Tom
    Forma de onda colorida em 3 bandas (estilo CDJ/Rekordbox), espectrograma,
    tom (nota + maior/menor) e código na roda Camelot com cor e sigla da nota.

Instalação para uso direto do código:
    pip install -r requirements.txt
    python bpm_renamer_gui.py

Para gerar o instalador do Windows, veja o LEIAME.txt (build_windows.bat).
"""

import colorsys
import json
import math
import multiprocessing
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from collections import namedtuple
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

VERSAO = "5.3.0"

# Em executável "sem console" (PyInstaller) stdout/stderr podem ser None; algumas bibliotecas escrevem neles.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

EXTENSOES = {".mp3", ".flac", ".wav"}
SEP_DECIMAL = ","  # troque por "." se preferir 148.00

# Reconhece nomes que já começam com BPM: "148 - ", "148,5 - ", "148,53 - ", "148.53 - "
PADRAO_JA_RENOMEADO = re.compile(r"^\d{2,3}(?:[.,]\d{1,2})?\s*-\s")

# Estilos: faixa de BPM esperada (ajuda a escolher a oitava certa: 150 x 300 x 75)
ESTILOS = {
    "Psytrance geral (135–200)": (135, 200),
    "Forest / Full-on (135–155)": (135, 155),
    "Darkpsy (145–175)": (145, 175),
    "Hi-Tech (165–195)": (165, 195),
    "Psycore / Hardcore (190–350)": (190, 350),
    "Techno (115–145)": (115, 145),
    "Extremo (200–500)": (200, 500),
    "Personalizado": None,
}

# Modo -> duração (s) do trecho analisado (do meio da música)
MODOS = {
    "Preciso (recomendado)": "preciso",
    "Máximo (música inteira)": "maximo",
    "Rápido": "rapido",
}
JANELAS = {"rapido": 60.0, "preciso": 180.0, "maximo": 480.0}
JANELAS_TOM = {"rapido": 90.0, "preciso": 240.0, "maximo": 480.0}   # trecho central usado no tom

# Como o tom aparece no nome do arquivo:  148,00 - 8A Am - Nome.mp3
FORMATOS_TOM = {
    "Camelot e nota (8A Am)": "ambos",
    "Só Camelot (8A)": "camelot",
    "Só nota (Am)": "nota",
    "Sem tom no nome": "nenhum",
}

# Colunas da tabela (a ordem daqui é a ordem na tela) e ordem de confiança para classificar
TITULOS = {"arquivo": "Arquivo", "pasta": "Pasta", "bpm": "BPM", "cbpm": "Conf. BPM",
           "tom": "Tom", "ctom": "Conf. tom", "novo": "Novo nome", "status": "Status"}
ORDEM_CONF = {"Alta": 0, "Média": 1, "Baixa": 2, "Manual": 3}

# Ordenação da lista para montar sets: colunas usadas, por ordem de prioridade
ORDENACOES = {
    "Tom → BPM (roda Camelot)": ("tom", "bpm"),
    "BPM → Tom": ("bpm", "tom"),
    "Só tom": ("tom",),
    "Só BPM": ("bpm",),
    "Nome do arquivo": ("arquivo",),
}

# Espectro visível de 20 Hz a 35 kHz (escala logarítmica). Só existe informação até a frequência de
# Nyquist do arquivo (metade da taxa de amostragem): 22,05 kHz em 44,1 kHz; 35 kHz exige arquivos de 96 kHz.
FMIN_ESPECTRO, FMAX_ESPECTRO, LINHAS_ESPECTRO = 20.0, 35000.0, 640

HOP = 128        # ~5,8 ms por quadro a 22050 Hz
N_FFT = 1024
PESOS_HARMONICOS = (1.0, 0.6, 0.4, 0.3)


def fmt_bpm(bpm):
    return f"{bpm:.2f}".replace(".", SEP_DECIMAL)


# --------------------------------------------------------------------------- #
#  Detecção de BPM
#
#  Em vez de rastrear batida por batida, o programa mede a periodicidade do
#  "envelope de ataques" (onde estão os bumbos e transientes) com uma FFT bem
#  interpolada. Em música eletrônica, feita em DAW com andamento fixo, isso
#  chega a centésimos de BPM e funciona bem em andamentos muito altos.
# --------------------------------------------------------------------------- #
def calcular_envelope(y, sr):
    """Envelope de ataques: fluxo espectral da faixa grave (bumbo) + faixa total."""
    import librosa
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    sel = freqs <= 8000
    f_sel = freqs[sel]
    grave = f_sel <= 250
    total = f_sel >= 30

    bloco = HOP * 8000  # processa em blocos (~46 s) para economizar memória
    fl_grave, fl_total = [], []
    prev = None
    pos = 0
    while pos + N_FFT <= len(y):
        seg = y[pos: pos + bloco + N_FFT - HOP]
        if len(seg) < N_FFT:
            break
        S = np.abs(librosa.stft(seg, n_fft=N_FFT, hop_length=HOP, center=False))[sel]
        L = 20.0 * np.log10(np.maximum(S, 1e-4))          # dB, referência fixa
        ext = np.concatenate([L[:, :1] if prev is None else prev, L], axis=1)
        d = np.maximum(np.diff(ext, axis=1), 0.0)          # só aumentos de energia
        fl_grave.append(d[grave].mean(axis=0))
        fl_total.append(d[total].mean(axis=0))
        prev = L[:, -1:]
        pos += L.shape[1] * HOP

    g = np.concatenate(fl_grave)
    t = np.concatenate(fl_total)
    return g / (g.std() + 1e-9) + 0.5 * t / (t.std() + 1e-9)


def _passa_alta(x, janela):
    import numpy as np
    janela = max(3, int(janela) | 1)
    return x - np.convolve(x, np.ones(janela) / janela, mode="same")


def estimar_tempo_espectral(env, fs, minimo, maximo):
    """
    Acha o andamento dentro de [minimo, maximo] BPM pelo pico do espectro do
    envelope, somando os harmônicos (2x, 3x, 4x) para firmar o resultado.

    Retorna (bpm, proeminência do pico, ambiguidade de oitava).
    """
    import numpy as np

    x = _passa_alta(np.asarray(env, dtype=float), 1.5 * fs)
    x = x * np.hanning(len(x))
    N = 1 << int(np.ceil(np.log2(len(x) * 32)))   # zero-padding: ~0,01 BPM por ponto
    X = np.abs(np.fft.rfft(x, N))
    df = fs / N * 60.0                             # BPM por ponto do espectro

    def H_bin(i):
        return sum(w * X[i * h] for h, w in enumerate(PESOS_HARMONICOS, 1) if i * h < len(X))

    i0 = max(1, int(np.floor(minimo / df)))
    i1 = min(int(np.ceil(maximo / df)), len(X) - 1)
    idx = np.arange(i0, i1 + 1)
    H = np.zeros(len(idx))
    for h, w in enumerate(PESOS_HARMONICOS, 1):
        j = idx * h
        ok = j < len(X)
        H[ok] += w * X[j[ok]]

    i = int(idx[int(np.argmax(H))])
    # Faixa larga: prefere a oitava mais baixa (ritmo do bumbo) se tiver energia comparável
    while i // 2 >= i0 and H_bin(i // 2) >= 0.5 * H_bin(i):
        i //= 2

    # Refinamento parabólico em torno do pico
    a, b, c = H_bin(i - 1), H_bin(i), H_bin(i + 1)
    den = a - 2 * b + c
    delta = float(np.clip(0.5 * (a - c) / den, -1, 1)) if den != 0 else 0.0
    bpm = (i + delta) * df

    # Há outra oitava (metade/dobro) dentro da faixa com energia relevante?
    alt = []
    if i // 2 >= i0:
        alt.append(H_bin(i // 2) / b)
    if i * 2 <= i1:
        alt.append(H_bin(i * 2) / b)
    ambiguidade = max(alt) if alt else 0.0
    return bpm, float(b / (np.median(H) + 1e-12)), ambiguidade


def analisar_envelope(env, fs, minimo, maximo, segmentos=True):
    """Retorna (bpm, confiança, aviso). Confiança: 'Alta' | 'Média' | 'Baixa'."""
    bpm, prom, amb = estimar_tempo_espectral(env, fs, minimo, maximo)

    # Verificação cruzada: o mesmo andamento aparece em 3 trechos da música?
    spread = None
    n = len(env) // 3
    if segmentos and n >= int(20 * fs):
        ests = [
            estimar_tempo_espectral(env[k * n:(k + 1) * n], fs, bpm * 0.985, bpm * 1.015)[0]
            for k in range(3)
        ]
        spread = max(ests) - min(ests)

    aviso = "verificar oitava" if amb > 0.3 else ""
    if amb > 0.3 or prom < 3:
        conf = "Baixa"
    elif spread is None:
        conf = "Média" if prom >= 4 else "Baixa"
    elif spread <= 0.2 and prom >= 6:
        conf = "Alta"
    elif spread <= 0.6:
        conf = "Média"
    else:
        conf = "Baixa"
    return bpm, conf, aviso


def encaixar_valor(bpm, tolerancia=0.06):
    """Produtores costumam usar andamentos redondos (145,00 / 145,50): encaixa se estiver muito perto."""
    cand = round(bpm * 2) / 2
    return cand if abs(bpm - cand) <= tolerancia else bpm


def detectar_bpm(caminho, minimo, maximo, modo="preciso", encaixar=True):
    """Retorna (bpm com 2 casas, confiança, aviso)."""
    import librosa

    janela = JANELAS.get(modo, 180.0)
    try:
        dur = float(librosa.get_duration(path=str(caminho)))
    except TypeError:  # versões antigas do librosa
        dur = float(librosa.get_duration(filename=str(caminho)))

    if dur > janela:  # trecho do meio: evita introdução/final sem batida marcada
        y, sr = librosa.load(str(caminho), sr=22050, mono=True,
                             offset=(dur - janela) / 2, duration=janela)
    else:
        y, sr = librosa.load(str(caminho), sr=22050, mono=True)

    if len(y) < sr * 15:
        raise ValueError("arquivo muito curto para medir o BPM (mínimo ~15 s)")

    env = calcular_envelope(y, sr)
    bpm, conf, aviso = analisar_envelope(env, sr / HOP, minimo, maximo, segmentos=(modo != "rapido"))
    if encaixar:
        bpm = encaixar_valor(bpm)
    return round(bpm, 2), conf, aviso


# --------------------------------------------------------------------------- #
#  Tom musical, roda Camelot e cores
#
#  Detector de tom - motor FFT com parâmetros no estilo KeyFinder
#  (implementação própria; NÃO é o algoritmo do Rekordbox nem do KeyFinder):
#    1. reamostra para ~4,4 kHz e calcula FFTs longas (ex.: 16384) com janela Blackman;
#    2. estima a afinação (método tipo Harte: histograma dos desvios dos picos);
#    3. soma o espectro nas 12 notas (27,5 Hz + 6 oitavas), dando peso reduzido às
#       frequências "desafinadas" (borda de cada semitom);
#    4. compara com perfis de tom (Sha'ath, Gomez, Temperley, Krumhansl);
#    5. a confiança vem da concordância entre os perfis e entre trechos da música.
# --------------------------------------------------------------------------- #
KK_MAIOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MENOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
TEMPERLEY_MAIOR = (5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0)
TEMPERLEY_MENOR = (5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0)
PERFIS_TOM = ((KK_MAIOR, KK_MENOR), (TEMPERLEY_MAIOR, TEMPERLEY_MENOR))
PESO_GRAVE = 0.12          # influência da nota mais grave (tônica) na decisão

NOMES_MAIOR = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
NOMES_MENOR = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]
NOMES_PT = {"C": "Dó", "D": "Ré", "E": "Mi", "F": "Fá", "G": "Sol", "A": "Lá", "B": "Si"}
NOTAS_BARRAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Número Camelot de cada tônica (índice 0 = Dó). Maior = letra B, menor = letra A.
CAMELOT_MAIOR = [8, 3, 10, 5, 12, 7, 2, 9, 4, 11, 6, 1]
CAMELOT_MENOR = [5, 12, 7, 2, 9, 4, 11, 6, 1, 8, 3, 10]

# Matiz (graus) de cada número da roda, do 1 (turquesa) ao 12 (azul claro)
CAMELOT_MATIZ = {1: 170, 2: 135, 3: 100, 4: 60, 5: 35, 6: 10,
                 7: 350, 8: 325, 9: 295, 10: 265, 11: 235, 12: 205}


def codigo_camelot(pc, menor):
    return f"{(CAMELOT_MENOR if menor else CAMELOT_MAIOR)[pc]}{'A' if menor else 'B'}"


def nome_tom(pc, menor):
    """Retorna ('Lá menor', 'Am')."""
    letra = (NOMES_MENOR if menor else NOMES_MAIOR)[pc]
    pt = NOMES_PT[letra[0]] + letra[1:]
    return f"{pt} {'menor' if menor else 'maior'}", f"{letra}{'m' if menor else ''}"


# Sigla da nota de cada código da roda: '8A' -> 'Am', '8B' -> 'C', '11A' -> 'F#m'...
SIGLA_POR_CODIGO = {codigo_camelot(pc, m): nome_tom(pc, m)[1] for pc in range(12) for m in (False, True)}


def cor_camelot(codigo):
    """Cor (hex) da chave: mesmo matiz para A e B do mesmo número; B mais clara, A mais intensa."""
    num, letra = int(codigo[:-1]), codigo[-1]
    s, v = (0.55, 0.97) if letra == "B" else (0.80, 0.86)
    r, g, b = colorsys.hsv_to_rgb(CAMELOT_MATIZ[num] / 360.0, s, v)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def texto_sobre(cor_hex):
    """Preto ou branco, o que ficar mais legível sobre a cor."""
    r, g, b = (int(cor_hex[i:i + 2], 16) for i in (1, 3, 5))
    return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def misturar(cor_hex, fracao_branco):
    r, g, b = (int(cor_hex[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (round(c + (255 - c) * fracao_branco) for c in (r, g, b))
    return "#%02x%02x%02x" % (r, g, b)


def compativeis(codigo):
    """Chaves que combinam na mixagem harmônica: relativa (A/B) e vizinhas (±1)."""
    num, letra = int(codigo[:-1]), codigo[-1]
    outra = "B" if letra == "A" else "A"
    return {f"{num}{outra}", f"{num % 12 + 1}{letra}", f"{(num - 2) % 12 + 1}{letra}"}


SHAATH_MAIOR = (6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4)
SHAATH_MENOR = (6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2)
GOMEZ_MAIOR = (0.82, 0.0, 0.55, 0.0, 0.53, 0.30, 0.08, 1.0, 0.0, 0.38, 0.0, 0.47)
GOMEZ_MENOR = (0.81, 0.0, 0.53, 0.54, 0.0, 0.27, 0.07, 1.0, 0.27, 0.07, 0.10, 0.36)
PERFIS = {
    "shaath": (SHAATH_MAIOR, SHAATH_MENOR),
    "gomez": (GOMEZ_MAIOR, GOMEZ_MENOR),
    "temperley": (TEMPERLEY_MAIOR, TEMPERLEY_MENOR),
    "krumhansl": (KK_MAIOR, KK_MENOR),
}
NOMES_PERFIS = {"shaath": "Sha'ath", "gomez": "Gomez", "temperley": "Temperley", "krumhansl": "Krumhansl"}

# Parâmetros do detector. bandas = 1 banda por semitom (fixo); "Offset to C" está sempre ativo (nota 0 = Dó).
PRESET_BASE = dict(
    fmin=27.5, oitavas=6, bandas=1, fft=16384, hops=4, janela="blackman", stretch=0.8,
    afinacao="harte", desafinado=0.20, segmentacao="nenhuma", segmentos=3, suavizacao=0.0,
    limiar_picos=0.0, perfil="shaath", comparar=("gomez", "temperley"),
    peso_grave=PESO_GRAVE, enfase_grave=False,
)


def _preset(descricao, **kw):
    p = dict(PRESET_BASE)
    p.update(kw)
    p["descricao"] = descricao
    return p


PRESET_PADRAO = "PSY KEY · Psytrance / Full-On (recomendado)"
PRESET_PERSONALIZADO = "Personalizado (ajustes avançados)"
PRESETS_TOM = {
    PRESET_PADRAO: _preset(
        "Tom geral da faixa (preset A): FFT longa, janela Blackman e perfil Sha'ath, feito para música eletrônica."),
    "Forest": _preset(
        "Como o PSY KEY, mas compara Sha'ath com Gomez e Temperley: se divergirem, a confiança cai.",
        comparar=("gomez", "temperley")),
    "Darkpsy": _preset(
        "Material modal/ambíguo: compara Sha'ath com Temperley e Gomez (relativa maior/menor conta como dúvida).",
        comparar=("temperley", "gomez")),
    "Psycore (250–350+ BPM)": _preset(
        "Ataques muito rápidos: FFT 8192 e mais peso para a linha de baixo do que para o kick.",
        fft=8192, peso_grave=0.25),
    "Experimental (compara 4 perfis, com segmentação)": _preset(
        "Sem 'preset mágico': compara os 4 perfis e usa segmentação em 3 trechos com suavização.",
        segmentacao="arbitraria", segmentos=3, suavizacao=1.0, comparar=("gomez", "temperley", "krumhansl")),
    "PSY PITCH / TOM (nota individual)": _preset(
        "Preset B: FFT 8192 e desafinado 0,15, para investigar notas/toms individuais.",
        fft=8192, desafinado=0.15),
    "PSY BASS (foco no grave)": _preset(
        "Preset C: dá mais peso aos trechos em que o baixo domina e à nota grave (tônica).",
        desafinado=0.15, enfase_grave=True, peso_grave=0.25),
}
PRESET_TECHNO = "TECHNO · 115–145 BPM (Sha'ath)"
PRESETS_TOM[PRESET_TECHNO] = _preset(
    "Techno 115–145 BPM: perfil Sha'ath (feito para EDM) comparado com Gomez e Temperley; FFT 16384, "
    "janela Blackman e mais peso para o baixo/drone, que definem o centro tonal do techno.",
    comparar=("gomez", "temperley"), peso_grave=0.20)
NOMES_PRESETS_TOM = list(PRESETS_TOM) + [PRESET_PERSONALIZADO]

# Ao escolher o estilo (faixa de BPM), o preset de tom correspondente é selecionado junto ("setup")
ESTILO_PARA_PRESET_TOM = {
    "Psytrance geral (135–200)": PRESET_PADRAO,
    "Forest / Full-on (135–155)": "Forest",
    "Darkpsy (145–175)": "Darkpsy",
    "Hi-Tech (165–195)": PRESET_PADRAO,
    "Psycore / Hardcore (190–350)": "Psycore (250–350+ BPM)",
    "Techno (115–145)": PRESET_TECHNO,
    "Extremo (200–500)": "Psycore (250–350+ BPM)",
}

# Campos da janela "Ajustes avançados": (chave, rótulo, tipo, opções)
CAMPOS_AVANCADOS = [
    ("fmin", "Frequência inicial (Hz)", "num", (20.0, 200.0, 0.5)),
    ("oitavas", "Número de oitavas", "num", (3, 8, 1)),
    ("fft", "FFT (tamanho)", "combo", ["2048", "4096", "8192", "16384", "32768"]),
    ("hops", "Hops por frame", "num", (1, 16, 1)),
    ("janela", "Janela temporal", "combo", ["blackman", "hann", "hamming", "retangular"]),
    ("stretch", "Direct SK stretch", "num", (0.5, 1.0, 0.05)),
    ("afinacao", "Método de afinação", "combo", ["harte", "nenhuma"]),
    ("desafinado", "Detuned band weighting", "num", (0.0, 1.0, 0.05)),
    ("segmentacao", "Segmentação", "combo", ["nenhuma", "arbitraria"]),
    ("segmentos", "Nº de segmentos arbitrários", "num", (2, 12, 1)),
    ("suavizacao", "Smoothing Gaussian size", "num", (0.0, 10.0, 0.5)),
    ("limiar_picos", "Peak-picking threshold", "num", (0.0, 0.9, 0.05)),
    ("perfil", "Perfil de timbre (principal)", "combo", ["shaath", "gomez", "temperley", "krumhansl"]),
    ("peso_grave", "Peso da nota grave (tônica)", "num", (0.0, 0.6, 0.02)),
]


def obter_preset(nome, custom=None):
    """Parâmetros do preset escolhido (ou dos ajustes avançados, se 'Personalizado')."""
    if nome == PRESET_PERSONALIZADO:
        p = dict(PRESET_BASE)
        if custom:
            p.update(custom)
        p["descricao"] = "Ajustes avançados definidos por você."
        return p
    return dict(PRESETS_TOM.get(nome) or PRESETS_TOM[PRESET_PADRAO])


def resumo_preset(p):
    perfis = [NOMES_PERFIS[p["perfil"]]] + [NOMES_PERFIS[c] for c in p["comparar"] if c != p["perfil"]]
    seg = "nenhuma" if p["segmentacao"] == "nenhuma" else f"{p['segmentos']} trechos"
    comp = f" (compara: {', '.join(perfis[1:])})" if len(perfis) > 1 else ""
    return (f"FFT {p['fft']} · {p['hops']} hops · janela {p['janela']} · {p['fmin']:g} Hz + {p['oitavas']} oitavas · "
            f"afinação {p['afinacao']} · desafinado {p['desafinado']:.2f} · SK {p['stretch']:.2f} · "
            f"perfil {perfis[0]}{comp} · segmentação: {seg}")


def sr_para_preset(p):
    """Taxa de amostragem da análise: ~4,4 kHz cobre 27,5 Hz + 6 oitavas (até 1760 Hz)."""
    fmax = p["fmin"] * 2 ** p["oitavas"]
    return 4410 if fmax * 2.2 <= 4410 else int(math.ceil(fmax * 2.5 / 10.0) * 10)


def pontuar_tons(chroma, grave=None, perfil="shaath", peso_grave=PESO_GRAVE):
    """Correlação do perfil de notas com os 24 tons (índice = tônica + 12 se menor), para um perfil de tom."""
    import numpy as np

    c = np.asarray(chroma, dtype=float)
    pont = np.zeros(24)
    if c.std() < 1e-12:
        return pont
    perfil_maior, perfil_menor = PERFIS[perfil]
    for menor in (0, 1):
        base = np.asarray(perfil_menor if menor else perfil_maior, dtype=float)
        for pc in range(12):
            pont[pc + 12 * menor] = np.corrcoef(c, np.roll(base, pc))[0, 1]
    if grave is not None:
        g = np.asarray(grave, dtype=float)
        if g.max() > 0:
            g = g / g.max()
            for pc in range(12):
                prior = (g[pc] + 0.5 * g[(pc + 7) % 12]) / 1.5   # tônica + quinta no grave
                pont[pc] += peso_grave * prior
                pont[pc + 12] += peso_grave * prior
    return np.nan_to_num(pont)


def _relativas(a, b):
    """(tônica, menor?) de duas tonalidades: uma é a relativa maior/menor da outra?"""
    if a[1] == b[1]:
        return False
    menor, maior = (a, b) if a[1] else (b, a)
    return maior[0] == (menor[0] + 3) % 12


def confianca_combinada(votos_perfis, primario, concordancia):
    """
    Confiança pela concordância entre perfis (Sha'ath x Gomez x Temperley...):
      todos iguais = Alta; o principal bate com algum outro (ou com sua relativa) = Média; senão Baixa.
    Cai um nível se menos da metade dos trechos da música concorda.
    """
    v0 = votos_perfis[primario]
    outros = [v for k, v in votos_perfis.items() if k != primario]
    if not outros:
        nivel = 0 if concordancia >= 0.7 else (1 if concordancia >= 0.5 else 2)
        return ["Alta", "Média", "Baixa"][nivel]
    if all(v == v0 for v in outros):
        nivel = 0
    elif any(v == v0 or _relativas(v0, v) for v in outros):
        nivel = 1
    else:
        nivel = 2
    if concordancia < 0.5:
        nivel = min(2, nivel + 1)
    return ["Alta", "Média", "Baixa"][nivel]


def resumo_votos(votos_perfis):
    return " · ".join(f"{NOMES_PERFIS[p]} {codigo_camelot(pc, m)}" for p, (pc, m) in votos_perfis.items())


def janela_fft(nome, n):
    import numpy as np
    if nome == "blackman":
        return np.blackman(n)
    if nome == "hann":
        return np.hanning(n)
    if nome == "hamming":
        return np.hamming(n)
    return np.ones(n)


def espectro_frames(y, sr, tam_fft, hops, janela, fmin, oitavas):
    """Magnitudes (quadros x bins) das FFTs com sobreposição, só na faixa [fmin, fmin*2^oitavas]."""
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    passo = max(1, tam_fft // max(1, hops))
    if len(y) < tam_fft:
        y = np.pad(y, (0, tam_fft - len(y)))
    n = 1 + (len(y) - tam_fft) // passo
    jan = janela_fft(janela, tam_fft).astype(np.float32)
    freqs = np.arange(tam_fft // 2 + 1) * (sr / tam_fft)
    fmax = min(fmin * 2.0 ** oitavas, 0.95 * sr / 2)
    idx = np.nonzero((freqs >= fmin * 2.0 ** (-0.5 / 12)) & (freqs <= fmax))[0]
    mags = np.empty((n, len(idx)), dtype=np.float32)
    for a in range(0, n, 32):                       # em blocos, para economizar memória
        b = min(n, a + 32)
        quadros = np.stack([y[i * passo: i * passo + tam_fft] for i in range(a, b)]) * jan
        mags[a:b] = np.abs(np.fft.rfft(quadros, axis=1))[:, idx]
    return mags, freqs[idx]


def aplicar_picos(mags, limiar):
    """Peak-picking: mantém só os máximos locais acima de 'limiar' x o maior valor do quadro."""
    import numpy as np
    if limiar <= 0:
        return mags
    pico = np.zeros(mags.shape, dtype=bool)
    m = mags
    pico[:, 1:-1] = (m[:, 1:-1] > m[:, :-2]) & (m[:, 1:-1] >= m[:, 2:]) & \
                    (m[:, 1:-1] >= limiar * m.max(axis=1, keepdims=True))
    return np.where(pico, m, 0.0)


def estimar_afinacao(mags, freqs):
    """
    Desvio de afinação (em semitons, -0,5..+0,5) em relação a A=440 Hz: histograma circular dos desvios
    dos picos espectrais (método tipo Harte & Sandler), ponderado pela magnitude.
    """
    import numpy as np

    hist = np.zeros(100)
    df = float(freqs[1] - freqs[0])
    for m in mags:
        mx = float(m.max())
        if mx <= 0:
            continue
        pk = np.nonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:]) & (m[1:-1] > 0.1 * mx))[0] + 1
        if len(pk) == 0:
            continue
        a, b, c = m[pk - 1].astype(float), m[pk].astype(float), m[pk + 1].astype(float)
        den = a - 2 * b + c
        delta = np.clip(np.where(den != 0, 0.5 * (a - c) / np.where(den == 0, 1, den), 0.0), -0.5, 0.5)
        midi = 69 + 12 * np.log2((freqs[pk] + delta * df) / 440.0)
        dev = midi - np.round(midi)
        np.add.at(hist, np.clip(((dev + 0.5) * 100).astype(int), 0, 99), b)
    if hist.sum() <= 0:
        return 0.0
    x = np.arange(-8, 9)
    k = np.exp(-0.5 * (x / 2.0) ** 2)
    suave = np.convolve(np.tile(hist, 3), k / k.sum(), mode="same")[100:200]
    return float((int(np.argmax(suave)) + 0.5) / 100.0 - 0.5)


def chroma_frames(comp, freqs, offset, stretch, desafinado, fmax_hz=None):
    """
    Perfil das 12 notas (12 x quadros). Em cada semitom pega o MAIOR valor entre os bins da FFT (e não a soma,
    que favoreceria as oitavas altas, onde cada semitom tem centenas de bins de ruído) e depois junta as oitavas.
    Bins no núcleo da nota (±stretch/2 semitom) pesam 1; os da borda ("desafinados") pesam 'desafinado'.
    """
    import numpy as np

    sel = np.arange(len(freqs)) if fmax_hz is None else np.nonzero(freqs <= fmax_hz)[0]
    if len(sel) == 0:
        return np.zeros((12, comp.shape[0]))
    midi = 69 + 12 * np.log2(freqs[sel] / 440.0) - offset
    nota = np.round(midi).astype(int)
    peso = np.where(np.abs(midi - nota) <= 0.5 * stretch, 1.0, desafinado)
    inicios = np.flatnonzero(np.diff(nota, prepend=nota[0] - 1))          # 1º bin de cada semitom
    faixas = np.maximum.reduceat(comp[:, sel] * peso[None, :], inicios, axis=1)
    M = np.zeros((len(inicios), 12))
    M[np.arange(len(inicios)), nota[inicios] % 12] = 1.0                  # midi 60 (Dó) % 12 = 0
    return (faixas @ M).T


def suavizar_tempo(X, sigma):
    """Suavização gaussiana de cada linha de X (12 x T) ao longo do tempo."""
    import numpy as np
    if sigma <= 0 or X.shape[1] < 3:
        return X
    raio = max(1, int(np.ceil(4 * sigma)))
    x = np.arange(-raio, raio + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    Xp = np.pad(X, ((0, 0), (raio, raio)), mode="edge")
    return np.stack([np.convolve(Xp[i], k, mode="valid") for i in range(X.shape[0])])


def decidir_tom(C, B, pesos, params):
    """
    C: chroma (12 x T); B: chroma do grave (12 x T); pesos: energia por quadro.
    Decide o tom com o perfil principal e compara com os demais perfis do preset.
    """
    import numpy as np

    C, B = np.asarray(C, dtype=float), np.asarray(B, dtype=float)
    if params.get("suavizacao", 0) > 0:
        C, B = suavizar_tempo(C, params["suavizacao"]), suavizar_tempo(B, params["suavizacao"])
    T = C.shape[1]
    w = np.asarray(pesos, dtype=float)[:T] + 1e-12
    chroma_glob = (C * w).sum(axis=1) / w.sum()
    grave_glob = (B * w).sum(axis=1) / w.sum()

    usar_seg = params.get("segmentacao", "nenhuma") != "nenhuma"
    n_seg = max(2, int(params.get("segmentos", 3))) if usar_seg else 3   # sem segmentação: só p/ medir a concordância
    n_seg = max(1, min(n_seg, T))
    bordas = np.linspace(0, T, n_seg + 1).astype(int)
    pg = params.get("peso_grave", PESO_GRAVE)
    perfis = [params["perfil"]] + [p for p in params.get("comparar", ()) if p != params["perfil"]]

    totais, votos_seg = {}, {}
    for perfil in perfis:
        p_glob = pontuar_tons(chroma_glob, grave_glob, perfil, pg)
        soma, peso_total, votos = np.zeros(24), 0.0, []
        for a, b in zip(bordas[:-1], bordas[1:]):
            ws = w[a:b]
            if b - a < 1 or ws.sum() < 1e-9:
                continue
            p = pontuar_tons((C[:, a:b] * ws).sum(axis=1) / ws.sum(),
                             (B[:, a:b] * ws).sum(axis=1) / ws.sum(), perfil, pg)
            soma += p * ws.sum()
            peso_total += ws.sum()
            votos.append(int(np.argmax(p)))
        totais[perfil] = 0.5 * p_glob + 0.5 * soma / peso_total if (usar_seg and peso_total > 0) else p_glob
        votos_seg[perfil] = votos

    primario = perfis[0]
    total = totais[primario]
    ordem = np.argsort(total)[::-1]
    vencedor = int(ordem[0])
    votos = votos_seg[primario]
    concordancia = float(np.mean([v == vencedor for v in votos])) if votos else 1.0
    votos_perfis = {p: (int(np.argmax(t) % 12), bool(np.argmax(t) >= 12)) for p, t in totais.items()}
    return {
        "ranking": [(float(total[i]), int(i % 12), bool(i >= 12)) for i in ordem[:4]],
        "concordancia": concordancia,
        "chroma": chroma_glob,
        "votos_perfis": votos_perfis,
        "confianca": confianca_combinada(votos_perfis, primario, concordancia),
        "resumo": resumo_votos(votos_perfis),
    }


def analisar_tom_sinal(y, sr, params):
    """Detecção de tom em um sinal já carregado (mono, taxa 'sr'). Só usa numpy."""
    import numpy as np

    mags, freqs = espectro_frames(y, sr, params["fft"], params["hops"], params["janela"],
                                  params["fmin"], params["oitavas"])
    offset = estimar_afinacao(mags, freqs) if params["afinacao"] == "harte" else 0.0
    comp = np.sqrt(aplicar_picos(mags, params["limiar_picos"]))      # compressão: o kick não domina
    C = chroma_frames(comp, freqs, offset, params["stretch"], params["desafinado"])
    grave = freqs <= 130.81                                          # até Dó3: baixo e tônica
    B = chroma_frames(comp, freqs, offset, params["stretch"], params["desafinado"], fmax_hz=130.81)

    energia = (mags.astype(float) ** 2).sum(axis=1)
    energia_grave = (mags[:, grave].astype(float) ** 2).sum(axis=1) if grave.any() else np.zeros_like(energia)
    pesos = np.sqrt(energia) / (np.sqrt(energia).max() + 1e-12)
    if params.get("enfase_grave"):
        pesos = pesos * (energia_grave / (energia + 1e-12)) ** 2

    r = decidir_tom(C, B, pesos, params)
    r["afinacao"] = float(offset)
    r["pc"], r["menor"] = r["ranking"][0][1], r["ranking"][0][2]
    r["conf"] = r["confianca"]
    return r


def detectar_tom_arquivo(caminho, modo="preciso", params=None):
    """Carrega o trecho central da música (já na taxa da análise) e detecta o tom."""
    import librosa

    params = params or obter_preset(PRESET_PADRAO)
    sr = sr_para_preset(params)
    janela = JANELAS_TOM.get(modo, 240.0)
    try:
        dur = float(librosa.get_duration(path=str(caminho)))
    except TypeError:  # versões antigas do librosa
        dur = float(librosa.get_duration(filename=str(caminho)))
    if dur > janela:
        y, _ = librosa.load(str(caminho), sr=sr, mono=True, offset=(dur - janela) / 2, duration=janela)
    else:
        y, _ = librosa.load(str(caminho), sr=sr, mono=True)
    if len(y) < sr * 10:
        raise ValueError("arquivo muito curto para medir o tom (mínimo ~10 s)")
    r = analisar_tom_sinal(y, sr, params)
    return {"pc": r["pc"], "menor": r["menor"], "conf": r["conf"], "concordancia": r["concordancia"],
            "resumo": r["resumo"], "afinacao": r["afinacao"]}


#  Espectrograma e forma de onda colorida (3 bandas)
# --------------------------------------------------------------------------- #
_LUT = None

# graves = azul, médios = âmbar, agudos = branco (estilo "3 Band" dos CDJs/Rekordbox)
CORES_ONDA = ((30, 90, 255), (255, 170, 30), (255, 255, 255))
FUNDO_ONDA = (11, 11, 16)


def criar_lut():
    """Paleta do espectrograma (preto -> roxo -> laranja -> amarelo claro) com 256 níveis."""
    global _LUT
    if _LUT is None:
        import numpy as np
        pontos = np.linspace(0, 1, 8)
        cores = np.array([(0, 0, 4), (40, 11, 84), (101, 21, 110), (159, 42, 99),
                          (212, 72, 66), (245, 125, 21), (250, 193, 39), (252, 255, 164)], dtype=float)
        x = np.linspace(0, 1, 256)
        _LUT = np.stack([np.interp(x, pontos, cores[:, i]) for i in range(3)], axis=1).astype(np.uint8)
    return _LUT


def calcular_espectrograma(y, sr, linhas=LINHAS_ESPECTRO, max_colunas=2600, so_ondas=False):
    """
    Espectrograma em escala logarítmica de 20 Hz a 35 kHz, na taxa de amostragem original do arquivo.
    Acima da frequência de Nyquist não há informação: essa área fica escura.
    Retorna (spec uint8 [linhas x colunas] ou None, ondas 3 bandas [3 x colunas] em 0..1, nyquist em Hz).
    """
    import numpy as np

    y = np.asarray(y, dtype=np.float32)
    alvo_hop = max(512, int(np.ceil(len(y) / max_colunas)))
    n_fft = 8192
    while n_fft < alvo_hop and n_fft < 32768:
        n_fft *= 2
    hop = min(alvo_hop, n_fft)
    if len(y) < n_fft:
        y = np.pad(y, (0, n_fft - len(y)))
    n_q = 1 + (len(y) - n_fft) // hop
    jan = np.blackman(n_fft).astype(np.float32)
    norm = 2.0 / float(jan.sum())                 # 0 dBFS = senoide de amplitude 1,0
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    bandas = (freqs < 200, (freqs >= 200) & (freqs < 2500), freqs >= 2500)
    mapa = None if so_ondas else preparar_mapa_log(freqs, np.geomspace(FMIN_ESPECTRO, FMAX_ESPECTRO, linhas + 1))

    ondas = np.zeros((3, n_q), dtype=np.float32)
    partes = []
    for a in range(0, n_q, 16):
        b = min(n_q, a + 16)
        quadros = np.stack([y[i * hop: i * hop + n_fft] for i in range(a, b)]) * jan
        mag = (np.abs(np.fft.rfft(quadros, axis=1)) * norm).astype(np.float32)
        pot = mag.astype(np.float64) ** 2
        for k, m in enumerate(bandas):
            ondas[k, a:b] = np.sqrt(pot[:, m].sum(axis=1))
        if mapa is not None:
            partes.append(20.0 * np.log10(np.maximum(aplicar_mapa_log(mag, mapa), 1e-7)))

    escala = (1.0, 0.85, 0.7)                      # graves mais altos, agudos por cima (visual de CDJ)
    for k in range(3):
        ref = np.percentile(ondas[k], 99.5) + 1e-12
        ondas[k] = np.clip(ondas[k] / ref, 0.0, 1.0) ** 0.7 * escala[k]
    if mapa is None:
        return None, ondas, sr / 2.0

    db = np.concatenate(partes, axis=0).T          # linhas x colunas
    validas = mapa["validas"]
    teto = float(np.percentile(db[validas], 99.9)) if validas.any() else 0.0
    spec = (np.clip((db - (teto - 96.0)) / 96.0, 0.0, 1.0) * 255).astype(np.uint8)   # 96 dB de faixa dinâmica
    spec[~validas, :] = 0
    return spec, ondas, sr / 2.0


def renderizar_ppm(spec, largura, altura):
    """Redimensiona (interpolação bilinear) o espectrograma para largura x altura e devolve bytes PPM."""
    import numpy as np

    n_l, n_c = spec.shape
    y = np.linspace(n_l - 1, 0, altura)                          # graves embaixo
    x = np.linspace(0, n_c - 1, largura)
    y0 = np.floor(y).astype(int)
    y1 = np.minimum(y0 + 1, n_l - 1)
    x0 = np.floor(x).astype(int)
    x1 = np.minimum(x0 + 1, n_c - 1)
    wy, wx = (y - y0)[:, None], (x - x0)[None, :]
    s = spec.astype(np.float32)
    topo = s[y0][:, x0] * (1 - wx) + s[y0][:, x1] * wx
    base = s[y1][:, x0] * (1 - wx) + s[y1][:, x1] * wx
    img = (topo * (1 - wy) + base * wy).round().astype(np.uint8)
    rgb = criar_lut()[img]
    return b"P6 %d %d 255\n" % (largura, altura) + rgb.tobytes()


def renderizar_onda_ppm(ondas, largura, altura):
    """Desenha a forma de onda espelhada em 3 bandas coloridas e devolve bytes PPM."""
    import numpy as np

    n = ondas.shape[1]
    if n >= largura:                         # agrupa colunas pelo máximo (preserva picos)
        bordas = np.linspace(0, n, largura + 1).astype(int)
        bordas[1:] = np.maximum(bordas[1:], bordas[:-1] + 1)
        bordas = np.minimum(bordas, n - 1)[:-1]
        a = np.maximum.reduceat(ondas, bordas, axis=1)
    else:
        a = ondas[:, np.linspace(0, n - 1, largura).round().astype(int)]
    a = a[:, :largura]

    img = np.empty((altura, largura, 3), dtype=np.uint8)
    img[:] = FUNDO_ONDA
    dist = np.abs(np.arange(altura) - (altura - 1) / 2.0)[:, None]
    for banda in range(3):                   # graves primeiro; médios e agudos sobrepõem
        mascara = dist <= a[banda][None, :] * (altura / 2.0)
        img[mascara] = CORES_ONDA[banda]
    return b"P6 %d %d 255\n" % (largura, altura) + img.tobytes()


def analisar_faixa(caminho, params=None, progresso=None):
    """Carrega a faixa e devolve espectro, forma de onda e tom. 'progresso' recebe mensagens de texto."""
    import librosa

    params = params or obter_preset(PRESET_PADRAO)

    def avisar(msg):
        if progresso:
            progresso(msg)

    avisar("Carregando o áudio...")
    y, sr = librosa.load(str(caminho), sr=None, mono=True, duration=600)   # taxa original: espectro até Nyquist
    if len(y) < sr * 10:
        raise ValueError("arquivo muito curto (mínimo ~10 s)")

    avisar("Gerando espectro e forma de onda...")
    spec, ondas, nyquist = calcular_espectrograma(y, sr)

    avisar("Analisando o tom...")
    janela = int(JANELAS_TOM["preciso"] * sr)                 # trecho central
    ini = max(0, (len(y) - janela) // 2)
    sr_tom = sr_para_preset(params)
    ytom = librosa.resample(y[ini:ini + janela], orig_sr=sr, target_sr=sr_tom)
    r = analisar_tom_sinal(ytom, sr_tom, params)
    return {"spec": spec, "nyquist": nyquist, "sr": sr, "ondas": ondas, "dur": len(y) / sr,
            "chroma": r["chroma"], "ranking": r["ranking"], "concordancia": r["concordancia"],
            "confianca": r["conf"], "resumo": r["resumo"], "afinacao": r["afinacao"]}


# --------------------------------------------------------------------------- #
#  Backend de áudio (sounddevice / PortAudio): dispositivos, taxa, teste, diagnóstico e erros
#  Toda a dependência do sounddevice fica nesta seção; o player só fala com AudioBackend.
#  Não existe backend "falso": se o áudio não está disponível, isso é dito claramente.
# --------------------------------------------------------------------------- #
SISTEMA_PADRAO = "Sistema padrão"
REQUISITO_SOUNDDEVICE = "sounddevice>=0.5.1,<0.6"

DispositivoSaida = namedtuple("DispositivoSaida", "indice nome api canais taxa_padrao padrao")


# --------------------------------------------------------------------------- #
#  Erros
# --------------------------------------------------------------------------- #
class AudioIndisponivel(Exception):
    """Falha de áudio: 'resumo' é para o usuário, 'detalhe' é técnico (vai para o log), 'codigo' classifica."""

    def __init__(self, resumo, detalhe="", codigo="desconhecido"):
        super().__init__(detalhe or resumo)
        self.resumo = resumo
        self.detalhe = detalhe or resumo
        self.codigo = codigo


def classificar_erro(exc):
    """Devolve (codigo, resumo em linguagem simples) para qualquer exceção vinda do áudio."""
    texto = f"{type(exc).__name__}: {exc}"
    baixo = texto.lower()
    if isinstance(exc, ImportError) or "no module named" in baixo:
        return "sem_sounddevice", "O componente de reprodução de áudio (sounddevice) não está disponível."
    if "portaudio library not found" in baixo or "could not load portaudio" in baixo or "portaudio not initialized" in baixo:
        return "sem_portaudio", "A biblioteca de áudio do sistema (PortAudio) não foi encontrada."
    if "no default output device" in baixo or "error querying device -1" in baixo:
        return "sem_saida", "Nenhuma saída de áudio foi encontrada no computador."
    if "invalid sample rate" in baixo:
        return "taxa", "A saída de áudio não aceita a taxa de amostragem pedida."
    if any(p in baixo for p in ("invalid device", "device unavailable", "unanticipated host error",
                                "error opening", "invalid number of channels", "stream is stopped",
                                "internal portaudio error")):
        return "dispositivo", ("Não foi possível abrir o dispositivo de áudio. Ele pode estar em uso, "
                               "desconectado ou sem suporte.")
    return "desconhecido", "Não foi possível iniciar o áudio."


def traduzir_erro(exc):
    """Converte qualquer exceção em AudioIndisponivel (mensagem simples + detalhe técnico)."""
    if isinstance(exc, AudioIndisponivel):
        return exc
    codigo, resumo = classificar_erro(exc)
    return AudioIndisponivel(resumo, f"{type(exc).__name__}: {exc}", codigo)


def pode_instalar_componente():
    """Só faz sentido oferecer 'instalar' quando rodando pelo código-fonte (no executável tudo já vai embutido)."""
    return not getattr(sys, "frozen", False)


def comando_instalar_sounddevice():
    return [sys.executable, "-m", "pip", "install", REQUISITO_SOUNDDEVICE]


# --------------------------------------------------------------------------- #
#  Interface
# --------------------------------------------------------------------------- #
class AudioBackend:
    """Interface do backend de áudio. Implementação principal: SoundDeviceBackend."""

    nome = "base"

    def disponivel(self):
        raise NotImplementedError

    def listar_saidas(self):
        raise NotImplementedError

    def resolver_dispositivo(self, nome_salvo):
        raise NotImplementedError

    def escolher_taxa(self, indice, taxa_arquivo):
        raise NotImplementedError

    def criar_saida(self, indice, taxa, callback, finalizado=None):
        raise NotImplementedError

    def testar_saida(self, indice=None, freq=440.0, dur=0.5):
        raise NotImplementedError

    def diagnostico(self, nome_dispositivo=None, taxa_arquivo=None):
        raise NotImplementedError


class SoundDeviceBackend(AudioBackend):
    nome = "sounddevice"

    def __init__(self, log=None, importar=None):
        self._log = log or (lambda msg: None)
        self._importar = importar or self._importar_padrao      # injetável (testes)
        self._sd = None
        self._erro = None
        self._trava = threading.RLock()

    @staticmethod
    def _importar_padrao():
        import sounddevice
        return sounddevice

    # ---- carregamento do módulo ----
    def modulo(self):
        with self._trava:
            if self._sd is not None:
                return self._sd
            if self._erro is not None:
                raise self._erro
            try:
                self._sd = self._importar()
                self._log(f"backend: sounddevice {getattr(self._sd, '__version__', '?')} carregado")
            except Exception as e:                # ImportError, OSError (PortAudio ausente), etc.
                self._erro = traduzir_erro(e)
                self._log(f"backend: falha ao carregar sounddevice [{self._erro.codigo}] {self._erro.detalhe}")
                raise self._erro
            return self._sd

    def disponivel(self):
        try:
            self.modulo()
            return True, ""
        except AudioIndisponivel as e:
            return False, e.detalhe

    def recarregar(self):
        """Esquece falhas anteriores (usado em 'Testar novamente', p.ex. depois de instalar o componente)."""
        with self._trava:
            self._sd, self._erro = None, None
            sys.modules.pop("sounddevice", None)
            try:
                import importlib
                importlib.invalidate_caches()
            except Exception:
                pass

    def atualizar_dispositivos(self):
        """Relê a lista de dispositivos do Windows (p.ex. fone Bluetooth ligado depois). Não usar tocando."""
        sd = self.modulo()
        try:
            sd._terminate()
            sd._initialize()
            self._log("backend: lista de dispositivos atualizada")
        except Exception as e:
            self._log(f"backend: não foi possível atualizar dispositivos: {e!r}")

    # ---- dispositivos ----
    def listar_saidas(self):
        sd = self.modulo()
        try:
            dispositivos = list(sd.query_devices())
            apis = list(sd.query_hostapis())
            try:
                padrao = sd.default.device[1]
            except Exception:
                padrao = -1
            try:
                api_padrao = sd.default.hostapi
            except Exception:
                api_padrao = None
        except Exception as e:
            raise traduzir_erro(e)

        todas = []
        for i, d in enumerate(dispositivos):
            if d.get("max_output_channels", 0) <= 0:
                continue
            h = d.get("hostapi")
            api = apis[h]["name"] if isinstance(h, int) and 0 <= h < len(apis) else ""
            todas.append((h, DispositivoSaida(i, d.get("name", f"Dispositivo {i}"), api, d["max_output_channels"],
                                              int(d.get("default_samplerate") or 0), i == padrao)))
        # só a API padrão do Windows: evita a mesma placa repetida em MME/DirectSound/WASAPI
        filtradas = [d for h, d in todas if api_padrao is None or h == api_padrao]
        return filtradas or [d for _, d in todas]

    def resolver_dispositivo(self, nome_salvo):
        """Nome salvo nas configurações -> (índice do PortAudio ou None = sistema padrão, aviso ou None)."""
        if not nome_salvo or nome_salvo == SISTEMA_PADRAO:
            return None, None
        for d in self.listar_saidas():
            if d.nome == nome_salvo:
                return d.indice, None
        return None, f'O dispositivo "{nome_salvo}" não está mais disponível. Voltei para "{SISTEMA_PADRAO}".'

    def escolher_taxa(self, indice, taxa_arquivo):
        """
        Taxa de amostragem a usar na saída: a do arquivo, se a placa aceitar; senão 48000, 44100 ou a padrão da placa.
        Retorna (taxa, precisa_reamostrar).
        """
        sd = self.modulo()
        candidatas = [int(taxa_arquivo), 48000, 44100]
        try:
            info = sd.query_devices(indice if indice is not None else sd.default.device[1])
            candidatas.append(int(info["default_samplerate"]))
        except Exception:
            pass
        vistas = []
        for t in candidatas:
            if t > 0 and t not in vistas:
                vistas.append(t)
        ultimo = None
        for t in vistas:
            try:
                sd.check_output_settings(device=indice, channels=2, dtype="float32", samplerate=t)
                return t, t != int(taxa_arquivo)
            except Exception as e:
                ultimo = e
        raise traduzir_erro(ultimo or RuntimeError("nenhuma taxa de amostragem suportada"))

    def parar_callback(self):
        """Exceção que o callback levanta para encerrar a reprodução (sd.CallbackStop)."""
        return self.modulo().CallbackStop

    # ---- stream ----
    def criar_saida(self, indice, taxa, callback, finalizado=None):
        sd = self.modulo()
        try:
            stream = sd.OutputStream(samplerate=taxa, channels=2, dtype="float32", device=indice,
                                     callback=callback, finished_callback=finalizado)
        except Exception as e:
            err = traduzir_erro(e)
            self._log(f"backend: erro ao abrir OutputStream [{err.codigo}] {err.detalhe}")
            raise err
        self._log(f"backend: stream aberto (dispositivo={'padrão' if indice is None else indice}, "
                  f"taxa={taxa}, canais=2)")
        return stream

    def fechar_seguro(self, stream):
        """abort() + close() tolerando stream já parado/fechado."""
        if stream is None:
            return
        for acao in ("abort", "close"):
            try:
                getattr(stream, acao)()
            except Exception as e:
                self._log(f"backend: {acao}() ignorado ({type(e).__name__})")
        self._log("backend: stream fechado")

    # ---- teste de áudio ----
    def testar_saida(self, indice=None, freq=440.0, dur=0.5):
        """Toca um tom curto (440 Hz, ~0,5 s) na saída escolhida. Retorna dict(ok, resumo, detalhe, taxa)."""
        import numpy as np
        try:
            sd = self.modulo()
            taxa, _ = self.escolher_taxa(indice, 48000)
        except AudioIndisponivel as e:
            return {"ok": False, "resumo": e.resumo, "detalhe": e.detalhe, "taxa": None}

        total = int(taxa * dur)
        estado = {"n": 0}
        terminou = threading.Event()

        def callback(outdata, frames, tempo, status):
            k = np.arange(frames) + estado["n"]
            env = np.clip(np.minimum(k / (0.02 * taxa), (total - k) / (0.03 * taxa)), 0.0, 1.0)   # fade in/out
            y = 0.25 * np.sin(2 * np.pi * freq * k / taxa) * env
            y[k >= total] = 0.0
            outdata[:] = y[:, None]
            estado["n"] += frames
            if estado["n"] >= total:
                raise sd.CallbackStop

        stream = None
        try:
            stream = self.criar_saida(indice, taxa, callback, terminou.set)
            stream.start()
            ok = terminou.wait(timeout=dur + 3.0)
            return {"ok": ok, "resumo": "" if ok else "O teste não terminou no tempo esperado.",
                    "detalhe": "" if ok else "timeout aguardando finished_callback", "taxa": taxa}
        except Exception as e:
            err = traduzir_erro(e)
            self._log(f"backend: teste de áudio falhou [{err.codigo}] {err.detalhe}")
            return {"ok": False, "resumo": err.resumo, "detalhe": err.detalhe, "taxa": taxa}
        finally:
            self.fechar_seguro(stream)

    # ---- diagnóstico ----
    def diagnostico(self, nome_dispositivo=None, taxa_arquivo=None):
        d = {"backend": self.nome, "player": "INDISPONÍVEL", "sounddevice": "AUSENTE", "portaudio": "INDISPONÍVEL",
             "dispositivo": "NENHUM", "canais": "—", "samplerate": "—", "api": "—", "estado": "indisponível",
             "saidas": [], "problema": "", "codigo": "", "detalhe": "", "aviso": ""}
        try:
            sd = self.modulo()
        except AudioIndisponivel as e:
            d.update(problema=e.resumo, codigo=e.codigo, detalhe=e.detalhe)
            return d
        d["sounddevice"] = f"OK ({getattr(sd, '__version__', '?')})"
        try:
            d["portaudio"] = f"OK ({sd.get_portaudio_version()[1]})"
        except Exception as e:
            d["portaudio"] = "INDISPONÍVEL"
            d.update(problema="A biblioteca de áudio do sistema (PortAudio) não respondeu.", codigo="sem_portaudio",
                     detalhe=repr(e))
            return d
        try:
            saidas = self.listar_saidas()
            d["saidas"] = [s.nome for s in saidas]
            if not saidas:
                d.update(problema="Nenhuma saída de áudio foi encontrada no computador.", codigo="sem_saida",
                         detalhe="query_devices não retornou dispositivos de saída")
                return d
            indice, aviso = self.resolver_dispositivo(nome_dispositivo)
            d["aviso"] = aviso or ""
            escolhido = next((s for s in saidas if (s.indice == indice if indice is not None else s.padrao)),
                             saidas[0])
            taxa, _ = self.escolher_taxa(escolhido.indice if indice is not None else None, taxa_arquivo or 44100)
            stream = self.criar_saida(escolhido.indice if indice is not None else None, taxa,
                                      lambda outdata, frames, t, s: outdata.fill(0))
            self.fechar_seguro(stream)                      # abriu e fechou: a saída está utilizável
            d.update(dispositivo=escolhido.nome, canais="2", samplerate=f"{taxa} Hz",
                     api=escolhido.api or "—", estado="disponível", player="OK")
        except AudioIndisponivel as e:
            d.update(problema=e.resumo, codigo=e.codigo, detalhe=e.detalhe)
        return d


# --------------------------------------------------------------------------- #
#  Texto do diagnóstico
# --------------------------------------------------------------------------- #
def formatar_diagnostico(d):
    linhas = [
        f"Player interno: {d['player']}",
        f"sounddevice: {d['sounddevice']}",
        f"PortAudio: {d['portaudio']}",
        f"Dispositivo: {d['dispositivo']}",
        f"Canais: {d['canais']}",
        f"Sample rate: {d['samplerate']}",
        f"Backend: {d['api']}",
        f"Estado: {d['estado']}",
    ]
    if d.get("saidas"):
        linhas.append("Saídas encontradas: " + "; ".join(d["saidas"]))
    if d.get("aviso"):
        linhas.append("Aviso: " + d["aviso"])
    if d.get("problema"):
        linhas.append("Problema: " + d["problema"])
    if d.get("detalhe"):
        linhas.append("Detalhe técnico: " + d["detalhe"])
    linhas.append(f"Versão do Python: {sys.version.split()[0]} · executável empacotado: "
                  f"{'sim' if getattr(sys, 'frozen', False) else 'não'}")
    return "\n".join(linhas)


def resumo_estado(d):
    return "✓ Áudio disponível" if d.get("estado") == "disponível" else "⚠ Verifique Configurações → Áudio"


# --------------------------------------------------------------------------- #
#  Medidores (VU, pico, LUFS), analisador de espectro em tempo real e player interno
# --------------------------------------------------------------------------- #
VU_REFERENCIA_DBFS = -18.0        # 0 VU = -18 dBFS
# Posição do ponteiro (0..1) para cada marca da escala do VU (a escala real é comprimida à esquerda)
VU_PARADAS = ((-20, 0.0), (-10, 0.20), (-7, 0.32), (-5, 0.42), (-3, 0.54), (-2, 0.62), (-1, 0.71),
              (0, 0.80), (1, 0.87), (2, 0.94), (3, 1.0))


def dbfs(x):
    import numpy as np
    return float(20.0 * np.log10(max(float(x), 1e-9)))


def posicao_vu(vu):
    import numpy as np
    vs, ps = zip(*VU_PARADAS)
    return float(np.interp(vu, vs, ps))


def ordenar_itens(iids, chave_fn, colunas, desc=False):
    """
    Ordena por várias colunas (a 1ª é a principal), crescente ou decrescente. Itens sem valor vão sempre
    para o fim. 'chave_fn(iid, coluna)' devolve o valor de ordenação ou None.
    """
    ordem = list(iids)
    for coluna in reversed(colunas):        # ordenações estáveis: primeiro a chave secundária
        com = [i for i in ordem if chave_fn(i, coluna) is not None]
        sem = [i for i in ordem if chave_fn(i, coluna) is None]
        com.sort(key=lambda i: chave_fn(i, coluna), reverse=desc)
        ordem = com + sem
    return ordem


# ---- LUFS (ITU-R BS.1770-4 / EBU R128) ----
def sos_kweight(sr):
    """Filtro de ponderação K (shelf de agudos + passa-altas RLB) para qualquer taxa de amostragem."""
    import numpy as np

    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10.0 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    est1 = [(Vh + Vb * K / Q + K * K) / a0, 2.0 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0,
            1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    a0 = 1.0 + K / Q + K * K
    est2 = [1.0, -2.0, 1.0, 1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0]
    return np.array([est1, est2])


def ms_blocos_100ms(dados, sr):
    """Média dos quadrados (com ponderação K) de cada bloco de 100 ms, por canal: (n_blocos x canais)."""
    import numpy as np
    from scipy.signal import sosfilt

    n, canais = dados.shape
    bloco = int(round(0.1 * sr))
    sos = sos_kweight(sr)
    nb = n // bloco
    saida = np.zeros((nb, canais))
    zi = [np.zeros((sos.shape[0], 2)) for _ in range(canais)]
    for b0 in range(0, nb, 100):                       # ~10 s por vez, mantendo o estado do filtro
        b1 = min(nb, b0 + 100)
        seg = dados[b0 * bloco:b1 * bloco]
        for c in range(canais):
            y, zi[c] = sosfilt(sos, seg[:, c].astype(np.float64), zi=zi[c])
            saida[b0:b1, c] = (y.reshape(b1 - b0, bloco) ** 2).mean(axis=1)
    return saida


def lufs_de_energia(e):
    import numpy as np
    return -0.691 + 10.0 * np.log10(np.maximum(e, 1e-12))


def integrado(E4):
    """LUFS integrado com dupla porta: absoluta (-70 LUFS) e relativa (-10 LU). E4 = energia de janelas de 400 ms."""
    import numpy as np
    if len(E4) == 0:
        return -120.0
    L = lufs_de_energia(E4)
    m = L > -70.0
    if not m.any():
        return -120.0
    limite = float(lufs_de_energia(E4[m].mean())) - 10.0
    m2 = m & (L > limite)
    return float(lufs_de_energia(E4[m2].mean())) if m2.any() else -120.0


class MedidorLoudness:
    """Loudness pré-calculado da faixa: consulta instantânea (momentâneo, curto prazo, integrado) em qualquer ponto."""

    def __init__(self, dados, sr):
        import numpy as np
        self.ms = ms_blocos_100ms(dados, sr)
        e = self.ms.sum(axis=1)                          # canais L e R com peso 1,0
        self.cs = np.concatenate([[0.0], np.cumsum(e)])
        self.E4 = (self.cs[4:] - self.cs[:-4]) / 4.0 if len(e) >= 4 else np.zeros(0)
        self.integrado_total = integrado(self.E4)

    def _janela(self, k, largura):
        k = int(min(max(k, 0), len(self.cs) - 1))
        w = min(largura, k)
        if w <= 0:
            return -120.0
        return float(lufs_de_energia((self.cs[k] - self.cs[k - w]) / w))

    def momentaneo(self, k):            # 400 ms
        return self._janela(k, 4)

    def curto_prazo(self, k):           # 3 s
        return self._janela(k, 30)

    def integrado_ate(self, k):
        return integrado(self.E4[:max(0, int(k) - 3)])


def medir_instantaneo(dados, fim, sr):
    """Pico (dBFS), pico verdadeiro (dBTP) e VU de cada canal na janela que termina em 'fim' (amostras)."""
    import numpy as np
    from scipy.signal import resample_poly

    canais = dados.shape[1]
    fim = int(min(max(fim, 0), len(dados)))
    j_pico, j_vu = max(0, fim - int(0.05 * sr)), max(0, fim - int(0.3 * sr))
    if fim - j_pico < 4:
        return {"pico": [-120.0] * canais, "vu": [-120.0] * canais, "tp": -120.0}
    seg = dados[j_pico:fim].astype(np.float64)
    pico = np.abs(seg).max(axis=0)
    sobre = np.abs(resample_poly(seg, 4, 1, axis=0))                 # 4x: detecta picos entre amostras
    corte = 4 * 24                                                   # descarta as bordas, onde o filtro "toca" (Gibbs)
    if len(sobre) > 2 * corte + 8:
        sobre = sobre[corte:-corte]
    tp = max(float(sobre.max()), float(pico.max()))
    rms = np.sqrt((dados[j_vu:fim].astype(np.float64) ** 2).mean(axis=0))
    return {"pico": [dbfs(p) for p in pico], "vu": [dbfs(r) - VU_REFERENCIA_DBFS for r in rms], "tp": dbfs(tp)}


# ---- espectro em escala logarítmica (usado no analisador e no espectrograma) ----
def preparar_mapa_log(freqs, bordas):
    """Prepara o mapeamento dos bins da FFT para faixas de frequência logarítmicas (bordas em Hz)."""
    import numpy as np

    df = float(freqs[1] - freqs[0])
    nb = len(freqs)
    centros = np.sqrt(bordas[:-1] * bordas[1:])
    pos = centros / df
    i0 = np.clip(pos.astype(int), 0, nb - 2)
    s = np.ceil(bordas[:-1] / df).astype(int)
    e = np.floor(bordas[1:] / df).astype(int)
    validas = centros < freqs[-1]
    return {"i0": i0, "w": pos - i0, "s": s, "e": e, "validas": validas, "centros": centros,
            "multi": np.nonzero((e > s) & (e < nb) & validas)[0]}


def aplicar_mapa_log(mags, mapa):
    """(quadros x bins) -> (quadros x faixas): interpola nas faixas estreitas (graves) e usa o máximo nas largas."""
    import numpy as np

    out = mags[:, mapa["i0"]] * (1.0 - mapa["w"]) + mags[:, mapa["i0"] + 1] * mapa["w"]
    for r in mapa["multi"]:
        out[:, r] = np.maximum(out[:, r], mags[:, mapa["s"][r]: mapa["e"][r] + 1].max(axis=1))
    out[:, ~mapa["validas"]] = 0.0
    return out


def janela_blackman_harris(n):
    import numpy as np
    k = np.arange(n) * 2.0 * np.pi / (n - 1)
    return 0.35875 - 0.48829 * np.cos(k) + 0.14128 * np.cos(2 * k) - 0.01168 * np.cos(3 * k)


class AnalisadorEspectro:
    """Analisador em tempo real: FFT com janela Blackman-Harris, 20 Hz - 35 kHz em bandas logarítmicas."""

    def __init__(self, sr, n_fft=8192, n_bandas=160):
        import numpy as np
        self.sr, self.n_fft, self.n_bandas = sr, n_fft, n_bandas
        self.jan = janela_blackman_harris(n_fft)
        self.norm = 2.0 / float(self.jan.sum())
        self.mapa = preparar_mapa_log(np.fft.rfftfreq(n_fft, 1.0 / sr),
                                      np.geomspace(FMIN_ESPECTRO, FMAX_ESPECTRO, n_bandas + 1))

    def bandas_db(self, mono):
        """dBFS de cada banda (0 dBFS = senoide de amplitude 1,0). Bandas acima de Nyquist = -200."""
        import numpy as np
        mono = np.asarray(mono, dtype=np.float64)[-self.n_fft:]
        x = np.zeros(self.n_fft)
        x[self.n_fft - len(mono):] = mono
        mag = np.abs(np.fft.rfft(x * self.jan)) * self.norm
        linhas = aplicar_mapa_log(mag[None, :], self.mapa)[0]
        db = 20.0 * np.log10(np.maximum(linhas, 1e-9))
        db[~self.mapa["validas"]] = -200.0
        return db


# ---- player interno ----
def ler_audio_estereo(caminho):
    """Decodifica o arquivo em float32 (n x 1 ou n x 2) na taxa original. Retorna (dados, taxa)."""
    import numpy as np

    try:
        import soundfile as sf
        with sf.SoundFile(str(caminho)) as f:
            sr, canais, n = f.samplerate, min(f.channels, 2), f.frames
            if n > 0 and n * canais * 4 > 1.6e9:
                raise MemoryError("arquivo longo demais para o player interno (use o player padrão)")
            partes = []
            while True:
                bloco = f.read(1 << 20, dtype="float32", always_2d=True)
                if len(bloco) == 0:
                    break
                partes.append(bloco[:, :canais].copy())
        if not partes:
            raise ValueError("arquivo vazio")
        return np.concatenate(partes, axis=0), int(sr)
    except MemoryError:
        raise
    except Exception:
        import librosa               # plano B: outros decodificadores (ffmpeg/audioread)
        y, sr = librosa.load(str(caminho), sr=None, mono=False)
        y = np.atleast_2d(y)[:2]
        return np.ascontiguousarray(y.T, dtype=np.float32), int(sr)


class Reprodutor:
    """
    Player interno: decodifica em float32, toca via AudioBackend (sounddevice/PortAudio) e expõe a posição
    para os medidores. O stream é sempre fechado ao pausar/trocar de faixa/de dispositivo (nada fica aberto).
    """

    def __init__(self, backend=None, dispositivo=None):
        self.backend = backend or SoundDeviceBackend(log=registrar_info)
        self.dispositivo = dispositivo or SISTEMA_PADRAO         # nome do dispositivo (salvo nas configurações)
        self.dados = None
        self.sr = 44100
        self.canais = 2
        self._buf = None                # o que vai para a placa: os próprios dados ou uma cópia reamostrada
        self.sr_buf = 44100
        self.pos = 0                    # posição em amostras DO BUFFER já entregues à placa
        self.tocando = False
        self.fim_chegou = False
        self.volume = 0.8
        self.stream = None
        self.caminho = None
        self.loud = None
        self.ondas = None
        self.analisador = None
        self.pico_faixa = -120.0
        self.aviso = ""                 # ex.: "o dispositivo salvo não existe mais"
        self._parar_cb = None
        self._quer_tocar = False
        self._interrompido = False
        self._geracao = 0
        self._trava = threading.RLock()

    @property
    def duracao(self):
        return 0.0 if self.dados is None else len(self.dados) / float(self.sr)

    # ---- carregar / descarregar ----
    def carregar(self, caminho, progresso=None):
        import numpy as np

        def avisar(m):
            if progresso:
                progresso(m)

        self.descarregar()
        avisar("Decodificando o áudio...")
        dados, sr = ler_audio_estereo(caminho)
        if len(dados) < sr:
            raise ValueError("arquivo muito curto")
        avisar("Medindo o loudness (LUFS)...")
        loud = MedidorLoudness(dados, sr)
        avisar("Gerando a forma de onda...")
        _, ondas, _ = calcular_espectrograma(dados.mean(axis=1), sr, so_ondas=True)
        self.loud, self.ondas = loud, ondas
        self.pico_faixa = dbfs(np.abs(dados).max())
        self.analisador = AnalisadorEspectro(sr)
        self.caminho, self.pos, self.fim_chegou = Path(caminho), 0, False
        self.sr, self.canais, self.sr_buf, self._buf = sr, dados.shape[1], sr, None
        self.dados = dados                     # por último: a interface só lê depois que tudo está pronto

    def descarregar(self):
        with self._trava:
            self._quer_tocar = False
            self.tocando = False
            self._fechar_stream()
            self.dados = self.loud = self.ondas = self.analisador = self._buf = None
            self.pos = 0

    # ---- stream ----
    def _fechar_stream(self):
        s, self.stream = self.stream, None
        self._geracao += 1                  # invalida callbacks "de fim" atrasados de streams antigos
        self.backend.fechar_seguro(s)

    @staticmethod
    def _reamostrar(dados, sr, taxa):
        import numpy as np
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(sr), int(taxa))
        return resample_poly(dados, int(taxa) // g, int(sr) // g, axis=0).astype(np.float32)

    def _preparar_saida(self):
        """Abre o stream no dispositivo escolhido; reamostra o buffer só se a placa não aceitar a taxa do arquivo."""
        indice, aviso = self.backend.resolver_dispositivo(self.dispositivo)
        if aviso:
            self.aviso, self.dispositivo = aviso, SISTEMA_PADRAO
            registrar_info("dispositivo salvo indisponível; usando o sistema padrão")
        taxa, _ = self.backend.escolher_taxa(indice, self.sr)
        if self._buf is None or taxa != self.sr_buf:
            seg = self.pos / float(self.sr_buf)            # mantém a posição ao trocar de buffer
            self._buf = self.dados if taxa == self.sr else self._reamostrar(self.dados, self.sr, taxa)
            self.sr_buf = taxa
            self.pos = int(seg * taxa)
            if taxa != self.sr:
                registrar_info(f"buffer reamostrado de {self.sr} Hz para {taxa} Hz")
        self._parar_cb = self.backend.parar_callback()
        self._geracao += 1
        g = self._geracao
        self.stream = self.backend.criar_saida(indice, taxa, self._callback, lambda: self._terminou(g))

    def _callback(self, outdata, frames, tempo, status):
        """Roda na thread de áudio: só copia amostras (nada de widgets, locks pesados ou cálculos)."""
        buf = self._buf
        if buf is None:
            outdata.fill(0)
            raise self._parar_cb
        pos = self.pos
        fim = min(len(buf), pos + frames)
        m = fim - pos
        if m > 0:
            trecho = buf[pos:fim] * self.volume
            outdata[:m] = trecho if trecho.shape[1] == 2 else trecho[:, [0, 0]]     # mono -> estéreo
        if m < frames:
            outdata[max(m, 0):] = 0
        self.pos = fim
        if fim >= len(buf):
            raise self._parar_cb

    def _terminou(self, geracao):
        """Chamado pelo PortAudio quando o stream para: fim da faixa, pausa, ou falha/remoção do dispositivo."""
        if geracao != self._geracao:
            return
        buf = self._buf
        fim_normal = buf is not None and self.pos >= len(buf) - 1
        if self._quer_tocar and not fim_normal:
            self._interrompido = True                     # parou antes do fim sem ninguém pedir: dispositivo perdido
        self.tocando = False
        self.fim_chegou = bool(fim_normal and self._quer_tocar)
        if fim_normal:
            self._quer_tocar = False

    def consumir_interrupcao(self):
        """True (uma única vez) se a reprodução parou sozinha por falha/remoção do dispositivo de áudio."""
        if self._interrompido:
            self._interrompido = False
            return True
        return False

    # ---- controles ----
    def tocar(self):
        if self.dados is None:
            return
        with self._trava:
            if self.tocando and self.stream is not None and self.stream.active:
                return
            if self.stream is not None:                   # stream parado/terminado: começa limpo
                self._fechar_stream()
            self._preparar_saida()
            if self.pos >= len(self._buf) - 1:
                self.pos = 0
            try:
                self.stream.start()
            except Exception as e:
                err = traduzir_erro(e)
                self._fechar_stream()
                raise err
            self.tocando, self.fim_chegou, self._interrompido, self._quer_tocar = True, False, False, True

    def pausar(self):
        if self.dados is None:
            return
        with self._trava:
            self._quer_tocar = False
            self.pos = self._pos_buf_ouvida()         # descarta o que ainda estava no buffer da placa
            self.tocando = False
            self._fechar_stream()
            self.fim_chegou = False

    def parar(self):
        self.pausar()
        self.pos = 0

    def buscar(self, segundos):
        if self.dados is None:
            return
        self.pos = int(min(max(segundos, 0.0), max(0.0, self.duracao - 0.05)) * self.sr_buf)

    def trocar_dispositivo(self, nome):
        """Muda a saída (e retoma de onde parou, se estava tocando)."""
        if self.dados is None:
            self.dispositivo = nome or SISTEMA_PADRAO
            return
        with self._trava:
            tocava = self._quer_tocar
            seg = self.posicao_ouvida() / float(self.sr)
            self._quer_tocar = False
            self.tocando = False
            self._fechar_stream()
            self.dispositivo = nome or SISTEMA_PADRAO
            self._buf, self.sr_buf = None, self.sr
            self.pos = int(seg * self.sr)
            self._interrompido = False
        if tocava:
            self.tocar()

    # ---- posição e medidores ----
    def _pos_buf_ouvida(self):
        """Posição realmente audível, em amostras do buffer (desconta a latência de saída da placa)."""
        lat = 0.0
        if self.tocando and self.stream is not None:
            try:
                lat = float(self.stream.latency)
            except Exception:
                lat = 0.0
        n = len(self._buf) if self._buf is not None else int(len(self.dados) * self.sr_buf / self.sr)
        return int(max(0, min(n, self.pos - lat * self.sr_buf)))

    def posicao_ouvida(self):
        """Posição audível em amostras do arquivo original (é nela que os medidores leem)."""
        if self.dados is None:
            return 0
        return int(min(len(self.dados), self._pos_buf_ouvida() * self.sr / float(self.sr_buf)))

    def medir(self):
        """Instantâneo dos medidores (pico, true peak, VU, LUFS M/S/I) na posição que está sendo ouvida."""
        if self.dados is None:
            return None
        pos = self.posicao_ouvida()
        r = medir_instantaneo(self.dados, pos, self.sr)
        k = int(pos / (0.1 * self.sr))
        r.update(lufs_m=self.loud.momentaneo(k), lufs_s=self.loud.curto_prazo(k),
                 lufs_i=self.loud.integrado_ate(k), pos=pos)
        return r

    def espectro_bandas(self):
        if self.dados is None:
            return None
        pos = self.posicao_ouvida()
        seg = self.dados[max(0, pos - self.analisador.n_fft):pos].mean(axis=1)
        return self.analisador.bandas_db(seg) if len(seg) >= 64 else None


# --------------------------------------------------------------------------- #
#  Utilitários: configuração, arquivos, pastas, erros, autoteste
# --------------------------------------------------------------------------- #
def pasta_dados():
    """Pasta de dados do programa. No Windows: %APPDATA%\\PsyKey (a antiga %APPDATA%\\BPMRenamer é migrada)."""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "PsyKey"
    primeira_vez = not p.exists()
    p.mkdir(parents=True, exist_ok=True)
    antiga = Path(base) / "BPMRenamer" / "config.json"
    if primeira_vez and antiga.is_file():
        try:
            shutil.copy2(antiga, p / "config.json")          # mantém as configurações de quem já usava o BPM Renamer
        except Exception:
            pass
    return p


def carregar_config():
    try:
        return json.loads((pasta_dados() / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def salvar_config(cfg):
    try:
        (pasta_dados() / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def registrar_info(texto):
    """Log técnico (sem dados pessoais nem caminhos de arquivos): %APPDATA%\\PsyKey\\psykey.log, limitado a ~1 MB."""
    try:
        arq = pasta_dados() / "psykey.log"
        if arq.exists() and arq.stat().st_size > 1_000_000:
            arq.replace(arq.with_name("psykey.old.log"))
        with open(arq, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {texto}\n")
    except Exception:
        pass


def registrar_erro(texto):
    try:
        with open(pasta_dados() / "erro.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] v{VERSAO}\n{texto}\n\n")
    except Exception:
        pass


def recurso(nome):
    """Caminho de um arquivo de recurso, tanto rodando o .py quanto empacotado (PyInstaller)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / nome


def caminho_livre(pasta, nome):
    """Evita sobrescrever: 'musica.mp3' -> 'musica (2).mp3' se já existir."""
    pasta = Path(pasta)
    alvo = pasta / nome
    if not alvo.exists():
        return alvo
    base, ext = alvo.stem, alvo.suffix
    n = 2
    while (pasta / f"{base} ({n}){ext}").exists():
        n += 1
    return pasta / f"{base} ({n}){ext}"


def abrir_pasta(pasta):
    pasta = Path(pasta)
    if sys.platform.startswith("win"):
        os.startfile(str(pasta))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(pasta)])
    else:
        subprocess.Popen(["xdg-open", str(pasta)])


def abrir_pasta_do_arquivo(caminho):
    """Abre a pasta onde a música está, já com o arquivo selecionado (Windows/macOS)."""
    caminho = Path(caminho)
    if sys.platform.startswith("win"):
        if caminho.exists():
            subprocess.Popen(f'explorer /select,"{caminho}"')
        else:
            abrir_pasta(caminho.parent)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(caminho)] if caminho.exists() else ["open", str(caminho.parent)])
    else:
        subprocess.Popen(["xdg-open", str(caminho.parent)])


PC_MENOR_POR_CODIGO = {codigo_camelot(pc, m): (pc, m) for pc in range(12) for m in (False, True)}


def texto_tom(tom, formato):
    """'8A Am' / '8A' / 'Am' / '' conforme o formato escolhido para o nome do arquivo."""
    if not tom or formato == "nenhum":
        return ""
    cod = codigo_camelot(tom["pc"], tom["menor"])
    sigla = nome_tom(tom["pc"], tom["menor"])[1]
    return {"ambos": f"{cod} {sigla}", "camelot": cod, "nota": sigla}.get(formato, "")


def montar_nome(bpm, tom, formato, nome):
    """Novo nome do arquivo: '148,00 - 8A Am - Nome.mp3' (ou sem o tom)."""
    t = texto_tom(tom, formato)
    return f"{fmt_bpm(bpm)} - {t} - {nome}" if t else f"{fmt_bpm(bpm)} - {nome}"


def chave_ordenacao(coluna, item, texto):
    """Valor usado para ordenar a tabela por uma coluna (None = vazio, vai sempre para o fim)."""
    if coluna == "bpm":
        return item.get("bpm")
    if coluna == "tom":
        t = item.get("tom")
        if not t:
            return None
        cod = codigo_camelot(t["pc"], t["menor"])
        return (int(cod[:-1]), cod[-1])                 # 1A, 1B, 2A, 2B ... (ordem da roda)
    if coluna in ("cbpm", "ctom"):
        return ORDEM_CONF.get(item.get(coluna))
    t = (texto or "").strip().lower()
    return t or None


def reproduzir_no_player_padrao(caminho):
    """Abre a música no reprodutor de áudio padrão do sistema."""
    caminho = Path(caminho)
    if sys.platform.startswith("win"):
        os.startfile(str(caminho))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(caminho)])
    else:
        subprocess.Popen(["xdg-open", str(caminho)])


# Tarefas executadas em PROCESSOS separados (BPM e tom ao mesmo tempo). Precisam ficar no nível do módulo.
def tarefa_ping():
    return os.getpid()


def tarefa_bpm(caminho, minimo, maximo, modo, encaixar):
    _preparar_ambiente()
    return detectar_bpm(caminho, minimo, maximo, modo, encaixar)


def tarefa_tom(caminho, modo, params):
    _preparar_ambiente()
    return detectar_tom_arquivo(caminho, modo, params)


def _sinal_teste(sr=22050, dur=45.0, bpm=148.0):
    """Faixa sintética: bumbo a 148 BPM + acorde de Lá menor (A2, C3, E3)."""
    import numpy as np

    n = int(sr * dur)
    y = np.zeros(n, dtype=np.float32)
    passo = 60.0 / bpm
    kn = int(0.15 * sr)
    tt = np.arange(kn) / sr
    kick = (np.sin(2 * np.pi * (50 + 110 * np.exp(-tt * 45)) * tt) * np.exp(-tt * 22)).astype(np.float32)
    k = 0
    while True:
        i = int(round(k * passo * sr))
        if i >= n:
            break
        fim = min(n, i + kn)
        y[i:fim] += kick[:fim - i]
        k += 1
    t = np.arange(n) / sr
    for f in (110.0, 130.81, 164.81):
        y += (0.12 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    y /= max(1.0, float(np.abs(y).max()))
    return y, sr


def autoteste():
    """Verifica se todas as bibliotecas carregam e o pipeline completo funciona. Grava um log no TEMP."""
    log = [f"PsyKey v{VERSAO} - autoteste"]
    ok = True
    try:
        import numpy
        import scipy
        import soundfile
        import librosa
        log.append(f"numpy {numpy.__version__} | scipy {scipy.__version__} | "
                   f"soundfile {soundfile.__version__} | librosa {librosa.__version__}")

        y, sr = _sinal_teste()
        caminho = Path(tempfile.gettempdir()) / "bpmrenamer_teste.wav"
        soundfile.write(str(caminho), y, sr)                     # testa a DLL do libsndfile

        bpm, conf, aviso = detectar_bpm(caminho, 135, 200, "preciso", True)
        log.append(f"BPM detectado: {bpm} (esperado 148,00) confianca={conf}")
        if abs(bpm - 148.0) > 0.5:
            ok = False
            log.append("FALHA: BPM fora do esperado")

        preset = obter_preset(PRESET_PADRAO)
        t = detectar_tom_arquivo(caminho, "preciso", preset)
        log.append(f"Tom (varredura): {codigo_camelot(t['pc'], t['menor'])} {nome_tom(t['pc'], t['menor'])[1]} "
                   f"(esperado 8A Am) confianca={t['conf']} | {t['resumo']}")

        # BPM e tom ao mesmo tempo, em processos separados (como no programa)
        ex = None
        try:
            ex = ProcessPoolExecutor(max_workers=2)
            fb = ex.submit(tarefa_bpm, str(caminho), 135, 200, "preciso", True)
            ft = ex.submit(tarefa_tom, str(caminho), "preciso", preset)
            rb, rt = fb.result(timeout=240), ft.result(timeout=240)
            log.append(f"Paralelo por processos: OK (BPM {rb[0]}, tom {codigo_camelot(rt['pc'], rt['menor'])})")
        except Exception as e:
            log.append(f"AVISO: paralelo por processos indisponivel ({e!r}); o programa usara threads.")
        finally:
            if ex is not None:
                ex.shutdown(wait=False, cancel_futures=True)

        # medidor de loudness (BS.1770): senoide estéreo de 1 kHz a -23 dBFS deve medir -23,0 LUFS
        t48 = np.arange(10 * 48000) / 48000.0
        s = (10 ** (-23 / 20.0) * np.sin(2 * np.pi * 1000.0 * t48)).astype(np.float32)
        lufs = MedidorLoudness(np.stack([s, s], axis=1), 48000).integrado_total
        log.append(f"LUFS de teste: {lufs:.2f} (esperado -23,00)")
        if abs(lufs + 23.0) > 0.3:
            ok = False
            log.append("FALHA: medidor de LUFS fora do esperado")
        # Áudio: carregar o sounddevice/PortAudio é OBRIGATÓRIO (se faltar, o player não toca no executável).
        # O restante depende do hardware desta máquina e só gera AVISO.
        backend = SoundDeviceBackend()
        disponivel, det = backend.disponivel()
        if not disponivel:
            ok = False
            log.append(f"FALHA: componente de audio (sounddevice/PortAudio) indisponivel: {det}")
        else:
            d = backend.diagnostico()
            log.append("Audio: " + " | ".join(f"{k}={d[k]}" for k in
                                              ("sounddevice", "portaudio", "dispositivo", "samplerate", "estado")))
            if d["estado"] != "disponível":
                log.append(f"AVISO: nenhuma saida de audio utilizavel nesta maquina ({d['problema']}); "
                           "o teste de Play fica para o teste manual (Player > Audio > Testar audio).")

        res = analisar_faixa(caminho)
        pc, menor = res["ranking"][0][1], res["ranking"][0][2]
        log.append(f"Tom (aba Espectro): {codigo_camelot(pc, menor)} {nome_tom(pc, menor)[1]} "
                   f"confianca={res['confianca']} concordancia={res['concordancia']:.0%}")
        log.append(f"Espectro {res['spec'].shape} | onda {res['ondas'].shape}")
        try:
            caminho.unlink()
        except OSError:
            pass
    except Exception:
        import traceback
        ok = False
        log.append("FALHA:\n" + traceback.format_exc())
    log.append("RESULTADO: " + ("OK" if ok else "FALHOU"))
    try:
        (Path(tempfile.gettempdir()) / "bpmrenamer_selftest.log").write_text("\n".join(log), encoding="utf-8")
    except Exception:
        pass
    return ok


def bibliotecas_faltando():
    """Bibliotecas necessárias que não estão instaladas (só relevante ao rodar pelo código-fonte)."""
    import importlib.util
    return [m for m in ("numpy", "scipy", "librosa", "soundfile", "sounddevice") if importlib.util.find_spec(m) is None]


def _preparar_ambiente():
    """Cache do numba numa pasta gravável (importante quando instalado em Arquivos de Programas)."""
    try:
        os.environ.setdefault("NUMBA_CACHE_DIR", str(pasta_dados() / "numba_cache"))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Interface
# --------------------------------------------------------------------------- #
def fmt_tempo(seg):
    seg = max(0, int(seg))
    return f"{seg // 60}:{seg % 60:02d}"


def fmt_lufs(v):
    return "—" if v <= -100 else f"{v:.1f}".replace(".", SEP_DECIMAL)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"PsyKey — BPM & Harmonic Library Analyzer  {VERSAO}")
        self.geometry("1240x800")
        self.minsize(1020, 660)
        try:
            self.iconbitmap(str(recurso("icone.ico")))
        except Exception:
            pass

        self.cfg = carregar_config()
        self.items = {}          # iid -> {"path": Path, "bpm": float|None, "state": str}
        self.paths_set = set()
        self.undo_stack = []     # cada lote = [(iid, novo_path, antigo_path)]
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self._aplicando_estilo = False
        self._sort_cols = ORDENACOES["Tom → BPM (roda Camelot)"]
        self._sort_desc = False
        self._ordem_id = None
        self.player = Reprodutor(SoundDeviceBackend(log=registrar_info),
                                 self.cfg.get("audio_dispositivo") or SISTEMA_PADRAO)
        self._dlg_audio = None
        self._audio_diag = None
        self._carregando_player = False
        self._arrastando = False
        self._ultima_pos_medida = None
        self._vu_suave = [-20.0, -20.0]
        self._pico_hold = [(-120.0, 0.0), (-120.0, 0.0)]
        self._clip = [False, False]
        self._an_suave = None
        self._an_hold = None
        self._erro_tick = False
        self.img_onda_player = None
        self._pool = None
        self._fechando = False

        # aba de espectro
        self.esp_caminho = None
        self.esp_rodando = False
        self.spec = None
        self.spec_nyq = None
        self.spec_dur = 0.0
        self.ondas = None
        self.img_spec = None
        self.img_onda = None
        self._titulo_spec = ""
        self._redesenho_id = None

        self._estilo()
        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.after(100, self._processar_fila)
        self.after(300, self._tick_player)
        self.after(900, self._verificar_audio_inicial)

    def report_callback_exception(self, exc, val, tb):
        import traceback
        registrar_erro("".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror(
            "Erro inesperado",
            f"Ocorreu um erro:\n\n{val}\n\nOs detalhes foram salvos em:\n{pasta_dados() / 'erro.log'}",
        )

    # ---------------------------- construção da UI ------------------------- #
    def _estilo(self):
        st = ttk.Style(self)
        for tema in ("vista", "clam"):
            if tema in st.theme_names():
                st.theme_use(tema)
                break
        st.configure("Titulo.TLabel", font=("Segoe UI", 16, "bold"))
        st.configure("Sub.TLabel", foreground="#555555")
        st.configure("Acao.TButton", font=("Segoe UI", 10, "bold"), padding=8)
        st.configure("Treeview", rowheight=26)
        self.cor_fundo = st.lookup("TFrame", "background") or "#f0f0f0"

    def _montar(self):
        raiz = ttk.Frame(self, padding=12)
        raiz.pack(fill="both", expand=True)

        ttk.Label(raiz, text="PsyKey", style="Titulo.TLabel").pack(anchor="w")
        ttk.Label(raiz, text="BPM • KEY • CAMELOT • AUDIO ANALYZER", style="Sub.TLabel").pack(anchor="w", pady=(0, 6))

        self.abas = ttk.Notebook(raiz)
        self.abas.pack(fill="both", expand=True)
        tab1 = ttk.Frame(self.abas, padding=(2, 10, 2, 2))
        tab2 = ttk.Frame(self.abas, padding=(2, 10, 2, 2))
        self.abas.add(tab1, text="  🎚  Renomear por BPM  ")
        self.abas.add(tab2, text="  🎼  Espectro e Tom  ")
        tab3 = ttk.Frame(self.abas, padding=(2, 10, 2, 2))
        self.abas.add(tab3, text="  🎧  Player e Medidores  ")

        self._montar_aba_bpm(tab1)
        self._montar_aba_espectro(tab2)
        self._montar_aba_player(tab3)

        self._montar_barra_player(raiz)

        # Progresso / status (compartilhado)
        self.progresso = ttk.Progressbar(raiz, mode="determinate")
        self.progresso.pack(fill="x", pady=(10, 2))
        self.var_status = tk.StringVar(value="Pronto. Adicione músicas para começar.")
        ttk.Label(raiz, textvariable=self.var_status, style="Sub.TLabel").pack(anchor="w")
        self._sincronizar_barra_ordem()
        self._aplicar_ordenacao()

    # -------------------------- aba 1: renomear por BPM -------------------- #
    def _montar_aba_bpm(self, raiz):
        ttk.Label(
            raiz,
            text="1) Adicione músicas   →   2) Analisar BPM   →   3) Confira e Renomear      "
                 "(botão direito em uma música: copiar para a pasta de sets, abrir pasta, ver tom)",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        # Passo 1
        f1 = ttk.LabelFrame(raiz, text=" 1. Adicionar músicas e ajustes ", padding=8)
        f1.pack(fill="x")

        l1 = ttk.Frame(f1)
        l1.pack(fill="x")
        self.btn_pasta = ttk.Button(l1, text="📁  Adicionar pasta...", command=self.adicionar_pasta)
        self.btn_pasta.pack(side="left")
        self.btn_arqs = ttk.Button(l1, text="🎵  Adicionar arquivos...", command=self.adicionar_arquivos)
        self.btn_arqs.pack(side="left", padx=6)
        self.var_sub = tk.BooleanVar(value=True)
        ttk.Checkbutton(l1, text="Incluir subpastas", variable=self.var_sub).pack(side="left", padx=10)

        l2 = ttk.Frame(f1)
        l2.pack(fill="x", pady=(8, 0))
        ttk.Label(l2, text="Estilo").pack(side="left")
        self.var_estilo = tk.StringVar(value="Psytrance geral (135–200)")
        cb = ttk.Combobox(l2, textvariable=self.var_estilo, values=list(ESTILOS), state="readonly", width=28)
        cb.pack(side="left", padx=(4, 10))
        cb.bind("<<ComboboxSelected>>", self._aplicar_estilo)

        ttk.Label(l2, text="BPM entre").pack(side="left")
        self.var_min = tk.IntVar(value=135)
        self.var_max = tk.IntVar(value=200)
        ttk.Spinbox(l2, from_=30, to=800, width=5, textvariable=self.var_min).pack(side="left", padx=4)
        ttk.Label(l2, text="e").pack(side="left")
        ttk.Spinbox(l2, from_=30, to=800, width=5, textvariable=self.var_max).pack(side="left", padx=4)
        self.var_min.trace_add("write", self._faixa_editada)
        self.var_max.trace_add("write", self._faixa_editada)

        ttk.Separator(l2, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(l2, text="Modo").pack(side="left")
        self.var_modo = tk.StringVar(value="Preciso (recomendado)")
        ttk.Combobox(l2, textvariable=self.var_modo, values=list(MODOS), state="readonly",
                     width=22).pack(side="left", padx=4)

        l3 = ttk.Frame(f1)
        l3.pack(fill="x", pady=(8, 0))
        self.var_snap = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            l3, text="Encaixar em valores redondos próximos (ex.: 148,02 → 148,00; 150,48 → 150,50)",
            variable=self.var_snap,
        ).pack(side="left")
        l4 = ttk.Frame(f1)
        l4.pack(fill="x", pady=(6, 0))
        self.var_com_tom = tk.BooleanVar(value=True)
        ttk.Checkbutton(l4, text="Detectar o tom junto com o BPM (em paralelo)",
                        variable=self.var_com_tom).pack(side="left")
        ttk.Label(l4, text="     Tom no nome:").pack(side="left")
        self.var_fmt_tom = tk.StringVar(value="Camelot e nota (8A Am)")
        cbt = ttk.Combobox(l4, textvariable=self.var_fmt_tom, values=list(FORMATOS_TOM), state="readonly", width=24)
        cbt.pack(side="left", padx=4)
        cbt.bind("<<ComboboxSelected>>", lambda e: self._refrescar_nomes())
        self.var_exemplo = tk.StringVar()
        ttk.Label(l4, textvariable=self.var_exemplo, style="Sub.TLabel").pack(side="left", padx=8)
        self._atualizar_exemplo()

        l5 = ttk.Frame(f1)
        l5.pack(fill="x", pady=(6, 0))
        ttk.Label(l5, text="Preset de tom").pack(side="left")
        salvo = self.cfg.get("preset_tom")
        self.var_preset_tom = tk.StringVar(value=salvo if salvo in NOMES_PRESETS_TOM else PRESET_PADRAO)
        cbp = ttk.Combobox(l5, textvariable=self.var_preset_tom, values=NOMES_PRESETS_TOM, state="readonly", width=46)
        cbp.pack(side="left", padx=4)
        cbp.bind("<<ComboboxSelected>>", self._preset_tom_mudou)
        ttk.Button(l5, text="Ajustes avançados...", command=self.abrir_ajustes_tom).pack(side="left", padx=4)
        ttk.Label(l5, text="     Núcleos de processamento").pack(side="left")
        self.var_nucleos = tk.IntVar(value=int(self.cfg.get("nucleos") or max(2, min(4, os.cpu_count() or 2))))
        ttk.Spinbox(l5, from_=1, to=16, width=4, textvariable=self.var_nucleos).pack(side="left", padx=4)
        self.var_resumo_preset = tk.StringVar()
        ttk.Label(f1, textvariable=self.var_resumo_preset, style="Sub.TLabel", wraplength=1080,
                  justify="left").pack(anchor="w", pady=(4, 0))
        self._atualizar_resumo_preset()
        ttk.Label(
            f1,
            text="Dica: escolha o estilo da sua música. A faixa de BPM ajuda o programa a distinguir 150 de 300 ou 75.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        # Ordenação para montar sets (tom e BPM em sequência)
        fo = ttk.Frame(raiz)
        fo.pack(fill="x", pady=(10, 0))
        ttk.Label(fo, text="Ordenar a lista para o set:").pack(side="left")
        self.var_ordem = tk.StringVar(value="Tom → BPM (roda Camelot)")
        cbo = ttk.Combobox(fo, textvariable=self.var_ordem, values=list(ORDENACOES), state="readonly", width=26)
        cbo.pack(side="left", padx=6)
        cbo.bind("<<ComboboxSelected>>", self._ordem_escolhida)
        self.btn_sentido = ttk.Button(fo, text="▲ Crescente", width=14, command=self.alternar_sentido)
        self.btn_sentido.pack(side="left")
        self.var_manter_ordem = tk.BooleanVar(value=True)
        ttk.Checkbutton(fo, text="Manter sempre ordenado", variable=self.var_manter_ordem,
                        command=self._ao_marcar_manter).pack(side="left", padx=10)
        ttk.Label(fo, text="(ou clique no título de uma coluna)", style="Sub.TLabel").pack(side="left")

        # Tabela
        meio = ttk.Frame(raiz)
        meio.pack(fill="both", expand=True, pady=10)
        cols = tuple(TITULOS)
        self.tree = ttk.Treeview(meio, columns=cols, show="headings", selectmode="extended")
        larguras = {"arquivo": 230, "pasta": 140, "bpm": 80, "cbpm": 80, "tom": 85, "ctom": 80,
                    "novo": 300, "status": 125}
        for c in cols:   # clicar no título ordena (crescente / decrescente)
            self.tree.heading(c, text=TITULOS[c], command=lambda col=c: self.ordenar_por(col))
            self.tree.column(c, width=larguras[c],
                             anchor="center" if c in ("bpm", "cbpm", "tom", "ctom", "status") else "w")
        sb = ttk.Scrollbar(meio, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.tag_configure("ready", foreground="#0a6b2d")
        self.tree.tag_configure("low", foreground="#a86400")
        self.tree.tag_configure("done", foreground="#777777")
        self.tree.tag_configure("error", foreground="#b00020")
        self.tree.tag_configure("skip", foreground="#999999")
        self.tree.bind("<Double-1>", self.editar_bpm)
        self.tree.bind("<Delete>", lambda e: self.remover_selecionados())
        self.tree.bind("<Control-a>", lambda e: (self.selecionar_todos(), "break")[1])

        # Menu de contexto (botão direito)
        self.menu_ctx = tk.Menu(self, tearoff=0)
        self.menu_ctx.add_command(label="🎧  Copiar para a pasta de sets", command=self.ctx_copiar_sets)
        self.menu_ctx.add_command(label="📂  Abrir pasta original da música", command=self.ctx_abrir_pasta)
        self.menu_ctx.add_command(label="🎧  Tocar no player interno (com medidores)", command=self.ctx_tocar_interno)
        self.menu_ctx.add_command(label="▶  Reproduzir no player padrão", command=self.ctx_reproduzir)
        self.menu_ctx.add_separator()
        self.menu_ctx.add_command(label="🎼  Ver espectro e tom desta faixa", command=self.ctx_ver_espectro)
        self.menu_ctx.add_command(label="✏  Corrigir o tom (código Camelot)...", command=self.ctx_corrigir_tom)
        self.menu_ctx.add_command(label="⚙  Escolher a pasta de sets...", command=self.escolher_pasta_sets)
        self.menu_ctx.add_separator()
        self.menu_ctx.add_command(label="🗑  Remover da lista", command=self.remover_selecionados)
        if self.tk.call("tk", "windowingsystem") == "aqua":       # macOS
            self.tree.bind("<Button-2>", self._menu_contexto)
            self.tree.bind("<Control-Button-1>", self._menu_contexto)
        else:
            self.tree.bind("<Button-3>", self._menu_contexto)

        # Ferramentas da lista
        f2 = ttk.Frame(raiz)
        f2.pack(fill="x")
        ttk.Button(f2, text="Selecionar todos", command=self.selecionar_todos).pack(side="left")
        ttk.Button(f2, text="Remover selecionados", command=self.remover_selecionados).pack(side="left", padx=6)
        ttk.Button(f2, text="Limpar lista", command=self.limpar_lista).pack(side="left")
        ttk.Separator(f2, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(f2, text="BPM × 2", width=9, command=lambda: self.multiplicar(2.0)).pack(side="left")
        ttk.Button(f2, text="BPM ÷ 2", width=9, command=lambda: self.multiplicar(0.5)).pack(side="left", padx=6)
        ttk.Label(f2, text="Clique no título de uma coluna para ordenar  ·  dois cliques na linha = digitar o BPM.", style="Sub.TLabel").pack(side="right")

        # Pasta de sets
        fs = ttk.Frame(raiz)
        fs.pack(fill="x", pady=(8, 0))
        ttk.Label(fs, text="🎧  Pasta de sets:").pack(side="left")
        self.var_pasta_sets = tk.StringVar()
        self._atualizar_rotulo_pasta_sets()
        ttk.Label(fs, textvariable=self.var_pasta_sets, style="Sub.TLabel").pack(side="left", padx=8)
        ttk.Button(fs, text="Escolher...", command=self.escolher_pasta_sets).pack(side="left")
        ttk.Button(fs, text="Abrir", command=self.abrir_pasta_sets).pack(side="left", padx=6)

        # Passos 2 e 3
        f3 = ttk.Frame(raiz)
        f3.pack(fill="x", pady=(10, 0))
        self.btn_analisar = ttk.Button(f3, text="2.  ▶  Analisar BPM", style="Acao.TButton",
                                       command=self.analisar)
        self.btn_analisar.pack(side="left")
        self.btn_cancelar = ttk.Button(f3, text="⏹  Cancelar", command=self.cancelar, state="disabled")
        self.btn_cancelar.pack(side="left", padx=6)
        self.btn_renomear = ttk.Button(f3, text="3.  ✔  Renomear", style="Acao.TButton",
                                       command=self.renomear)
        self.btn_renomear.pack(side="left", padx=(24, 0))
        self.btn_desfazer = ttk.Button(f3, text="↩  Desfazer último lote", command=self.desfazer,
                                       state="disabled")
        self.btn_desfazer.pack(side="left", padx=6)

    # ------------------------ aba 2: espectro e tom ------------------------ #
    def _montar_aba_espectro(self, pai):
        topo = ttk.Frame(pai)
        topo.pack(fill="x")
        ttk.Button(topo, text="📂  Escolher arquivo...", command=self.esp_escolher).pack(side="left")
        ttk.Button(topo, text="Usar o selecionado na aba Renomear",
                   command=self.esp_usar_selecionado).pack(side="left", padx=6)
        self.btn_esp = ttk.Button(topo, text="▶  Analisar espectro e tom", style="Acao.TButton",
                                  command=self.esp_analisar)
        self.btn_esp.pack(side="left", padx=(14, 0))
        ttk.Label(topo, text="   Preset de tom").pack(side="left")
        cbp2 = ttk.Combobox(topo, textvariable=self.var_preset_tom, values=NOMES_PRESETS_TOM,
                            state="readonly", width=40)
        cbp2.pack(side="left", padx=4)
        cbp2.bind("<<ComboboxSelected>>", self._preset_tom_mudou)

        self.var_esp_arq = tk.StringVar(value="Nenhum arquivo escolhido.")
        ttk.Label(pai, textvariable=self.var_esp_arq, style="Sub.TLabel").pack(anchor="w", pady=(8, 6))

        self.pb_esp = ttk.Progressbar(pai, mode="indeterminate")
        self.pb_esp.pack(side="bottom", fill="x", pady=(8, 0))

        corpo = ttk.Frame(pai)
        corpo.pack(fill="both", expand=True)

        # Direita: cartão do tom + roda Camelot
        dir_ = ttk.Frame(corpo, padding=(14, 0, 0, 0))
        dir_.pack(side="right", fill="y")
        ttk.Label(dir_, text="Tom da faixa", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.lbl_camelot = tk.Label(dir_, text="—", font=("Segoe UI", 34, "bold"), width=6,
                                    bg="#c8c8c8", fg="#555555")
        self.lbl_camelot.pack(fill="x", pady=(4, 4))
        self.var_tom = tk.StringVar(value="Analise uma faixa para ver o tom.")
        ttk.Label(dir_, textvariable=self.var_tom, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.var_conf_tom = tk.StringVar(value="")
        ttk.Label(dir_, textvariable=self.var_conf_tom, style="Sub.TLabel", wraplength=280).pack(anchor="w")

        ttk.Label(dir_, text="Roda Camelot", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(10, 2))
        self.cv_roda = tk.Canvas(dir_, width=250, height=250, bg=self.cor_fundo, highlightthickness=0)
        self.cv_roda.pack()
        self.var_comp = tk.StringVar(value="")
        ttk.Label(dir_, textvariable=self.var_comp, style="Sub.TLabel", wraplength=280,
                  justify="left").pack(anchor="w", pady=(4, 0))

        # Esquerda: forma de onda, espectrograma e faixa inferior (notas + alternativas)
        esq = ttk.Frame(corpo)
        esq.pack(side="left", fill="both", expand=True)

        self.cv_onda = tk.Canvas(esq, height=120, bg="#0b0b10", highlightthickness=0)
        self.cv_onda.pack(side="top", fill="x")
        self.cv_onda.bind("<Configure>", self._agendar_redesenho)

        baixo = ttk.Frame(esq)
        baixo.pack(side="bottom", fill="x", pady=(8, 0))

        fc = ttk.Frame(baixo)
        fc.pack(side="left")
        ttk.Label(fc, text="Notas mais presentes", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.cv_chroma = tk.Canvas(fc, width=300, height=76, bg=self.cor_fundo, highlightthickness=0)
        self.cv_chroma.pack()

        col = ttk.Frame(baixo)
        col.pack(side="left", padx=(16, 0), fill="x", expand=True)
        ttk.Label(col, text="Outras possibilidades", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.chips = []
        for _ in range(3):
            lb = tk.Label(col, text="", anchor="w", padx=8, pady=1, font=("Segoe UI", 9, "bold"))
            lb.pack(fill="x", pady=1)
            self.chips.append(lb)
        ttk.Label(col, text="Em psytrance/darkpsy o tom pode ser ambíguo: confira as alternativas.",
                  style="Sub.TLabel").pack(anchor="w")

        self.cv_spec = tk.Canvas(esq, bg="#0b0b10", highlightthickness=0)
        self.cv_spec.pack(side="top", fill="both", expand=True, pady=(6, 0))
        self.cv_spec.bind("<Configure>", self._agendar_redesenho)

        self._desenhar_roda(None)
        self._desenhar_chroma(None)
        self._desenhar_visuais()

    # ------------------------------ ações da aba 2 ------------------------- #
    def esp_escolher(self):
        arq = filedialog.askopenfilename(
            title="Escolha uma música",
            filetypes=[("Áudio", "*.mp3 *.flac *.wav"), ("Todos os arquivos", "*.*")],
        )
        if arq:
            self.esp_caminho = Path(arq)
            self.var_esp_arq.set(f"Arquivo: {self.esp_caminho.name}")

    def esp_usar_selecionado(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Espectro e Tom", "Selecione uma música na lista da aba \"Renomear por BPM\" primeiro.")
            return
        self.esp_caminho = self.items[sel[0]]["path"]
        self.var_esp_arq.set(f"Arquivo: {self.esp_caminho.name}")

    def esp_analisar(self):
        if self.esp_rodando:
            return
        if self.esp_caminho is None:
            sel = self.tree.selection()
            if sel:
                self.esp_caminho = self.items[sel[0]]["path"]
                self.var_esp_arq.set(f"Arquivo: {self.esp_caminho.name}")
            else:
                messagebox.showinfo("Espectro e Tom", "Escolha um arquivo primeiro.")
                return
        if not self.esp_caminho.exists():
            messagebox.showwarning("Espectro e Tom", "Esse arquivo não foi encontrado (foi movido ou renomeado?).")
            return
        self.esp_rodando = True
        self.btn_esp.configure(state="disabled")
        self.pb_esp.start(12)
        self.var_status.set("Analisando espectro e tom...")
        threading.Thread(target=self._worker_espectro, args=(self.esp_caminho, self._params_tom()),
                         daemon=True).start()

    def _worker_espectro(self, caminho, params):
        try:
            import librosa  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.q.put(("esp_err", "Faltam bibliotecas.\nInstale com:\n\npip install -r requirements.txt"))
            return
        try:
            res = analisar_faixa(caminho, params, progresso=lambda m: self.q.put(("esp_status", m)))
            self.q.put(("esp_ok", res, caminho.name))
        except Exception as e:  # arquivo corrompido, sem decodificador, muito curto etc.
            import traceback
            registrar_erro(traceback.format_exc())
            self.q.put(("esp_err", str(e)))

    def _esp_finalizar(self):
        self.esp_rodando = False
        self.btn_esp.configure(state="normal")
        self.pb_esp.stop()

    def _mostrar_resultado_esp(self, res, nome):
        self.spec = res["spec"]
        self.spec_nyq = res["nyquist"]
        self.spec_dur = res["dur"]
        self.ondas = res["ondas"]
        self._desenhar_visuais(titulo=nome)

        ranking = res["ranking"]
        score0, pc, menor = ranking[0]
        conc = res["concordancia"]
        cod = codigo_camelot(pc, menor)
        cor = cor_camelot(cod)
        nome_pt, sigla = nome_tom(pc, menor)

        self.lbl_camelot.configure(text=cod, bg=cor, fg=texto_sobre(cor))
        self.var_tom.set(f"{nome_pt}  ({sigla})")
        conf = res["confianca"]
        self.var_conf_tom.set(f"Confiança: {conf}  ·  {conc * 100:.0f}% dos trechos concordam\n"
                              f"{res['resumo']}\nAfinação estimada: {res['afinacao'] * 100:+.0f} cents")

        self._desenhar_roda(cod)
        vizinhos = sorted(compativeis(cod), key=lambda c: (int(c[:-1]), c[-1]))
        self.var_comp.set("Combinam na mixagem:\n" + "    ".join(f"{c} ({SIGLA_POR_CODIGO[c]})" for c in vizinhos))
        self._desenhar_chroma(res["chroma"], pc, cor)

        for chip, (_, p, m) in zip(self.chips, ranking[1:4]):
            cd = codigo_camelot(p, m)
            cr = cor_camelot(cd)
            n_pt, sg = nome_tom(p, m)
            chip.configure(text=f"{cd}   {sg}   ·   {n_pt}", bg=cr, fg=texto_sobre(cr))
        self.var_status.set(f"Espectro e tom prontos: {cod} — {nome_pt} ({sigla}).")

    # ------------------------------- desenho ------------------------------- #
    def _agendar_redesenho(self, _evento=None):
        if self._redesenho_id:
            self.after_cancel(self._redesenho_id)
        self._redesenho_id = self.after(80, self._desenhar_visuais)

    def _desenhar_visuais(self, titulo=None):
        if titulo is not None:
            self._titulo_spec = titulo
        self._desenhar_onda()
        self._desenhar_espectro()

    def _desenhar_onda(self):
        cv = self.cv_onda
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 120 or H < 40:
            return
        if self.ondas is None:
            cv.create_text(W / 2, H / 2, fill="#9a9aa5", font=("Segoe UI", 10), justify="center",
                           text="Forma de onda colorida em 3 bandas\n(graves em azul, médios em âmbar, agudos em branco)")
            return
        ml, mr, mt, mb = 56, 12, 4, 4
        w, h = W - ml - mr, H - mt - mb
        if w < 50 or h < 20:
            return
        ppm = renderizar_onda_ppm(self.ondas, w, h)
        self.img_onda = tk.PhotoImage(width=w, height=h, data=ppm, format="PPM")  # manter referência!
        cv.create_image(ml, mt, image=self.img_onda, anchor="nw")
        x = ml + 8
        for texto, cor in (("Graves", "#5b8cff"), ("Médios", "#ffaa1e"), ("Agudos", "#ffffff")):
            cv.create_text(x, mt + 4, text=texto, anchor="nw", fill=cor, font=("Segoe UI", 8, "bold"))
            x += 58

    def _desenhar_espectro(self):
        cv = self.cv_spec
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 120 or H < 120:
            return
        if self.spec is None:
            cv.create_text(W / 2, H / 2, fill="#9a9aa5", font=("Segoe UI", 11), justify="center",
                           text="O espectrograma (20 Hz a 35 kHz) aparece aqui.\nEscolha um arquivo e clique em\n"
                                "\"Analisar espectro e tom\".")
            return

        ml, mr, mt, mb = 64, 12, 22, 28
        w, h = W - ml - mr, H - mt - mb
        if w < 50 or h < 50:
            return
        ppm = renderizar_ppm(self.spec, w, h)
        self.img_spec = tk.PhotoImage(width=w, height=h, data=ppm, format="PPM")  # manter referência!
        cv.create_image(ml, mt, image=self.img_spec, anchor="nw")
        cv.create_text(ml, 4, anchor="nw", fill="#d8d8e0", font=("Segoe UI", 9, "bold"), text=self._titulo_spec)

        # eixo de frequência logarítmico: 20 Hz embaixo, 35 kHz em cima
        fator = math.log(FMAX_ESPECTRO / FMIN_ESPECTRO)

        def y_de(f):
            return mt + h - (math.log(f / FMIN_ESPECTRO) / fator) * h

        for f, rot in ((20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
                       (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k"), (35000, "35k")):
            y = y_de(f)
            cv.create_line(ml, y, ml + w, y, fill="#3a3a48", dash=(2, 6))
            cv.create_text(ml - 6, y, text=f"{rot} Hz", anchor="e", fill="#c8c8d0", font=("Segoe UI", 8))
        nyq = self.spec_nyq or 0
        if 0 < nyq < FMAX_ESPECTRO:                 # acima disso o arquivo não tem informação
            yn = y_de(nyq)
            cv.create_line(ml, yn, ml + w, yn, fill="#ff5a5a", dash=(4, 3))
            cv.create_text(ml + w - 4, yn - 2, anchor="se", fill="#ff8a8a", font=("Segoe UI", 8),
                           text=f"Limite (Nyquist) deste arquivo: {nyq / 1000:.2f} kHz — acima não há informação")

        # eixo de tempo
        passo = next((p for p in (5, 10, 15, 30, 60, 120, 300, 600) if self.spec_dur / p <= max(2, w / 80)), 600)
        t = 0
        while t <= self.spec_dur:
            x = ml + (t / self.spec_dur) * w
            cv.create_line(x, mt + h, x, mt + h + 4, fill="#c8c8d0")
            cv.create_text(x, mt + h + 6, text=f"{int(t) // 60}:{int(t) % 60:02d}", anchor="n",
                           fill="#c8c8d0", font=("Segoe UI", 8))
            t += passo

    def _desenhar_roda(self, destaque=None):
        cv = self.cv_roda
        cv.delete("all")
        cx = cy = 125
        R, r, r0 = 122, 83, 45
        comp = compativeis(destaque) if destaque else set()

        def setor(r_in, r_out, theta):
            angulos = [theta - 15 + 30 * k / 8 for k in range(9)]
            pts = [(cx + r_out * math.sin(math.radians(a)), cy - r_out * math.cos(math.radians(a))) for a in angulos]
            pts += [(cx + r_in * math.sin(math.radians(a)), cy - r_in * math.cos(math.radians(a)))
                    for a in reversed(angulos)]
            return [c for p in pts for c in p]

        def desenhar(cod, contorno, largura):
            num, letra = int(cod[:-1]), cod[-1]
            theta = (num % 12) * 30
            r_in, r_out = (r, R) if letra == "B" else (r0, r)
            cor = cor_camelot(cod)
            if destaque and cod != destaque and cod not in comp:
                cor = misturar(cor, 0.65)
            cv.create_polygon(setor(r_in, r_out, theta), fill=cor, outline=contorno, width=largura)
            rr = (r_in + r_out) / 2
            cv.create_text(cx + rr * math.sin(math.radians(theta)), cy - rr * math.cos(math.radians(theta)),
                           text=f"{cod}\n{SIGLA_POR_CODIGO[cod]}", justify="center",
                           fill=texto_sobre(cor), font=("Segoe UI", 8, "bold"))

        todos = [f"{n}{letra}" for n in range(1, 13) for letra in ("B", "A")]
        for cod in todos:
            if cod != destaque and cod not in comp:
                desenhar(cod, "#ffffff", 1)
        for cod in comp:
            desenhar(cod, "#444444", 2)
        if destaque:
            desenhar(destaque, "#000000", 3)
        cv.create_text(cx, cy, text="B = maior\nA = menor", fill="#666666", font=("Segoe UI", 8), justify="center")

    def _desenhar_chroma(self, chroma=None, tonica=None, cor="#888888"):
        cv = self.cv_chroma
        cv.delete("all")
        if chroma is None:
            return
        W, H = 300, 76
        maior = float(max(chroma)) or 1.0
        larg = W / 12
        for i, v in enumerate(chroma):
            altura = (float(v) / maior) * (H - 24)
            x0, x1 = i * larg + 3, (i + 1) * larg - 3
            cv.create_rectangle(x0, H - 14 - altura, x1, H - 14, fill=cor if i == tonica else "#9aa0a6", outline="")
            cv.create_text((x0 + x1) / 2, H - 6, text=NOTAS_BARRAS[i], font=("Segoe UI", 7), fill="#333333")

    # --------------------------- estilos / faixa --------------------------- #
    def _aplicar_estilo(self, _evento=None):
        faixa = ESTILOS.get(self.var_estilo.get())
        if faixa:
            self._aplicando_estilo = True
            self.var_min.set(faixa[0])
            self.var_max.set(faixa[1])
            self._aplicando_estilo = False
            preset = ESTILO_PARA_PRESET_TOM.get(self.var_estilo.get())
            if preset:                                # o estilo escolhe também o preset de tom (setup completo)
                self.var_preset_tom.set(preset)
                self._preset_tom_mudou()

    def _faixa_editada(self, *_):
        if not self._aplicando_estilo:
            self.var_estilo.set("Personalizado")

    # ------------------------------ helpers -------------------------------- #
    def _travar(self, ocupado):
        self.running = ocupado
        normal = "disabled" if ocupado else "normal"
        for b in (self.btn_pasta, self.btn_arqs, self.btn_analisar, self.btn_renomear):
            b.configure(state=normal)
        self.btn_cancelar.configure(state="normal" if ocupado else "disabled")
        self.btn_desfazer.configure(state="disabled" if ocupado or not self.undo_stack else "normal")

    def _linha(self, iid, arquivo=None, pasta=None, bpm=None, cbpm=None, tom=None, ctom=None,
               novo=None, status=None, tag=None):
        vals = list(self.tree.item(iid, "values"))
        for i, v in enumerate((arquivo, pasta, bpm, cbpm, tom, ctom, novo, status)):
            if v is not None:
                vals[i] = v
        self.tree.item(iid, values=vals)
        if tag:
            self.tree.item(iid, tags=(tag,))

    def _formato_tom(self):
        return FORMATOS_TOM.get(self.var_fmt_tom.get(), "ambos")

    def _nome_novo(self, iid):
        it = self.items[iid]
        return montar_nome(it["bpm"], it.get("tom"), self._formato_tom(), it["path"].name)

    @staticmethod
    def _texto_tom_item(it):
        t = it.get("tom")
        if not t:
            return "—"
        return f"{codigo_camelot(t['pc'], t['menor'])} {nome_tom(t['pc'], t['menor'])[1]}"

    def _marcar_pronto(self, iid, bpm=None, conf_bpm=None, tom=None, conf_tom=None, status="Pronto"):
        it = self.items[iid]
        if bpm is not None:
            it["bpm"] = bpm
        if conf_bpm is not None:
            it["cbpm"] = conf_bpm
        if tom is not None:
            it["tom"] = tom
        if conf_tom is not None:
            it["ctom"] = conf_tom
        it["state"] = "ready"
        baixa = "Baixa" in (it.get("cbpm"), it.get("ctom"))
        self._linha(iid, bpm=fmt_bpm(it["bpm"]), cbpm=it.get("cbpm") or "—", tom=self._texto_tom_item(it),
                    ctom=it.get("ctom") or "—", novo=self._nome_novo(iid), status=status,
                    tag="low" if baixa else "ready")
        self._ordenar_automatico()

    def _selecionados_paths(self):
        return [self.items[i]["path"] for i in self.tree.selection() if i in self.items]

    # ------------------------- menu de contexto / sets --------------------- #
    def _menu_contexto(self, evento):
        iid = self.tree.identify_row(evento.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        n = len(self.tree.selection())
        self.menu_ctx.entryconfigure(
            0, label="🎧  Copiar para a pasta de sets" + (f"  ({n} músicas)" if n > 1 else ""))
        try:
            self.menu_ctx.tk_popup(evento.x_root, evento.y_root)
        finally:
            self.menu_ctx.grab_release()

    def _atualizar_rotulo_pasta_sets(self):
        p = self.cfg.get("pasta_sets")
        self.var_pasta_sets.set(p if p else "(não definida — escolha uma pasta ou use o botão direito em uma música)")

    def escolher_pasta_sets(self):
        opcoes = {"title": "Escolha a pasta de destino para seus sets e apresentações"}
        if self.cfg.get("pasta_sets") and Path(self.cfg["pasta_sets"]).exists():
            opcoes["initialdir"] = self.cfg["pasta_sets"]
        pasta = filedialog.askdirectory(**opcoes)
        if not pasta:
            return None
        self.cfg["pasta_sets"] = str(Path(pasta))
        salvar_config(self.cfg)
        self._atualizar_rotulo_pasta_sets()
        return Path(pasta)

    def _garantir_pasta_sets(self):
        p = self.cfg.get("pasta_sets")
        if p:
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
                return Path(p)
            except Exception:
                pass
        return self.escolher_pasta_sets()

    def abrir_pasta_sets(self):
        pasta = self._garantir_pasta_sets()
        if pasta:
            abrir_pasta(pasta)

    def ctx_copiar_sets(self):
        paths = self._selecionados_paths()
        if not paths:
            return
        destino = self._garantir_pasta_sets()
        if not destino:
            return
        self.var_status.set(f"Copiando {len(paths)} música(s) para a pasta de sets...")
        threading.Thread(target=self._worker_copia, args=(paths, destino), daemon=True).start()

    def _worker_copia(self, paths, destino):
        ok = erros = ja_la = 0
        ultimo_erro = ""
        for i, p in enumerate(paths, 1):
            try:
                if p.parent.resolve() == Path(destino).resolve():
                    ja_la += 1
                    continue
                shutil.copy2(p, caminho_livre(destino, p.name))
                ok += 1
            except Exception as e:
                erros += 1
                ultimo_erro = str(e)
            self.q.put(("msg", f"Copiando para a pasta de sets... {i}/{len(paths)}"))
        self.q.put(("copia_fim", ok, erros, ja_la, str(destino), ultimo_erro))

    def ctx_abrir_pasta(self):
        paths = self._selecionados_paths()
        if not paths:
            return
        vistos, alvo = set(), []
        for p in paths:                       # uma janela por pasta diferente
            if p.parent not in vistos:
                vistos.add(p.parent)
                alvo.append(p)
        if len(alvo) > 5:
            messagebox.showinfo("Abrir pasta", "As músicas selecionadas estão em muitas pastas diferentes.\n"
                                               "Vou abrir apenas as 5 primeiras.")
            alvo = alvo[:5]
        for p in alvo:
            try:
                abrir_pasta_do_arquivo(p)
            except Exception as e:
                messagebox.showwarning("Abrir pasta", f"Não foi possível abrir a pasta:\n{e}")

    def ctx_ver_espectro(self):
        paths = self._selecionados_paths()
        if not paths:
            return
        self.esp_caminho = paths[0]
        self.var_esp_arq.set(f"Arquivo: {paths[0].name}")
        self.abas.select(1)
        self.esp_analisar()

    # ------------------------------ adicionar ------------------------------ #
    def adicionar_pasta(self):
        pasta = filedialog.askdirectory(title="Escolha a pasta com as músicas")
        if not pasta:
            return
        p = Path(pasta)
        it = p.rglob("*") if self.var_sub.get() else p.glob("*")
        self._adicionar([a for a in sorted(it) if a.is_file() and a.suffix.lower() in EXTENSOES])

    def adicionar_arquivos(self):
        arqs = filedialog.askopenfilenames(
            title="Escolha as músicas",
            filetypes=[("Áudio", "*.mp3 *.flac *.wav"), ("Todos os arquivos", "*.*")],
        )
        self._adicionar([Path(a) for a in arqs])

    def _adicionar(self, caminhos):
        novos = 0
        for p in caminhos:
            if p in self.paths_set:
                continue
            iid = self.tree.insert("", "end", values=(p.name, str(p.parent), "", "", "", "", "", "Aguardando"))
            self.items[iid] = {"path": p, "bpm": None, "cbpm": None, "tom": None, "ctom": None, "state": "wait"}
            self.paths_set.add(p)
            if PADRAO_JA_RENOMEADO.match(p.name):
                self.items[iid]["state"] = "skip"
                self._linha(iid, status="Já tem BPM", tag="skip")
            novos += 1
        self.var_status.set(f"{novos} arquivo(s) adicionado(s). Total na lista: {len(self.items)}.")
        self._ordenar_automatico()

    # --------------------------- gerenciar lista --------------------------- #
    def selecionar_todos(self):
        self.tree.selection_set(self.tree.get_children())

    def remover_selecionados(self):
        if self.running:
            return
        for iid in self.tree.selection():
            self.paths_set.discard(self.items[iid]["path"])
            del self.items[iid]
            self.tree.delete(iid)
        self.var_status.set(f"Total na lista: {len(self.items)}.")

    def limpar_lista(self):
        if self.running:
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self.paths_set.clear()
        self.progresso["value"] = 0
        self.var_status.set("Lista limpa.")

    def editar_bpm(self, _evento):
        sel = self.tree.selection()
        if self.running or len(sel) != 1:
            return
        iid = sel[0]
        it = self.items[iid]
        if it["state"] not in ("ready", "error", "wait"):
            return
        txt = simpledialog.askstring(
            "Corrigir BPM",
            f"BPM de:\n{it['path'].name}\n\nDigite o valor (ex.: 148,50):",
            parent=self,
            initialvalue=fmt_bpm(it["bpm"]) if it["bpm"] else "148,00",
        )
        if not txt:
            return
        try:
            valor = round(float(txt.strip().replace(",", ".")), 2)
        except ValueError:
            messagebox.showwarning("BPM inválido", "Digite um número, por exemplo 148,50.")
            return
        if not 30 <= valor <= 800:
            messagebox.showwarning("BPM inválido", "O BPM deve estar entre 30 e 800.")
            return
        self._marcar_pronto(iid, bpm=valor, conf_bpm="Manual", status="Pronto (manual)")

    def multiplicar(self, fator):
        if self.running:
            return
        for iid in self.tree.selection():
            it = self.items[iid]
            if it["state"] != "ready" or it["bpm"] is None:
                continue
            novo = round(it["bpm"] * fator, 2)
            if 30 <= novo <= 800:
                self._marcar_pronto(iid, bpm=novo, conf_bpm="Manual", status="Pronto (ajustado)")

    # ------------------------------ player interno ------------------------- #
    def _montar_barra_player(self, pai):
        barra = ttk.Frame(pai)
        barra.pack(fill="x", pady=(8, 0))
        self.btn_play = ttk.Button(barra, text="▶", width=4, command=self.player_alternar)
        self.btn_play.pack(side="left")
        ttk.Button(barra, text="⏹", width=3, command=self.player_parar).pack(side="left", padx=(4, 8))
        self.var_tempo = tk.StringVar(value="0:00")
        ttk.Label(barra, textvariable=self.var_tempo, width=6).pack(side="left")
        self.escala_pos = ttk.Scale(barra, from_=0, to=1000, orient="horizontal")
        self.escala_pos.pack(side="left", fill="x", expand=True, padx=6)
        self.escala_pos.bind("<ButtonPress-1>", lambda e: setattr(self, "_arrastando", True))
        self.escala_pos.bind("<ButtonRelease-1>", self._soltou_barra)
        self.var_duracao = tk.StringVar(value="0:00")
        ttk.Label(barra, textvariable=self.var_duracao, width=6).pack(side="left")
        self.var_faixa = tk.StringVar(value="Nenhuma faixa no player (botão direito em uma música → Tocar no player interno)")
        ttk.Label(barra, textvariable=self.var_faixa, style="Sub.TLabel", width=52).pack(side="left", padx=8)
        ttk.Label(barra, text="🔊").pack(side="left")
        self.escala_vol = ttk.Scale(barra, from_=0, to=100, orient="horizontal", length=90, command=self._mudou_volume)
        self.escala_vol.pack(side="left")
        self.escala_vol.set(80)
        self.btn_audio = ttk.Button(barra, text="🔊 Áudio: verificando...", command=self.abrir_config_audio)
        self.btn_audio.pack(side="left", padx=(10, 0))

    def _montar_aba_player(self, pai):
        negrito = ("Segoe UI", 10, "bold")
        topo = ttk.Frame(pai)
        topo.pack(fill="x")
        self.var_player_titulo = tk.StringVar(value="Nenhuma faixa carregada")
        ttk.Label(topo, textvariable=self.var_player_titulo, font=("Segoe UI", 12, "bold")).pack(side="left")
        self.var_player_info = tk.StringVar(value="")
        ttk.Label(topo, textvariable=self.var_player_info, style="Sub.TLabel").pack(side="left", padx=14)
        self.var_analisar_tocar = tk.BooleanVar(value=True)
        ttk.Checkbutton(topo, text="Analisar BPM e tom ao tocar", variable=self.var_analisar_tocar).pack(side="right")

        self.cv_onda_player = tk.Canvas(pai, height=110, bg="#0b0b10", highlightthickness=0)
        self.cv_onda_player.pack(fill="x", pady=(8, 8))
        self.cv_onda_player.bind("<Button-1>", self._clique_onda_player)
        self.cv_onda_player.bind("<Configure>", lambda e: self._desenhar_onda_player())

        corpo = ttk.Frame(pai)
        corpo.pack(fill="both", expand=True)
        dir_ = ttk.Frame(corpo, padding=(12, 0, 0, 0))
        dir_.pack(side="right", fill="y")
        self.cv_analisador = tk.Canvas(corpo, bg="#0b0b10", highlightthickness=0)
        self.cv_analisador.pack(side="left", fill="both", expand=True)
        self.cv_analisador.bind("<Configure>", lambda e: self._desenhar_grade_analisador())

        ttk.Label(dir_, text="VU Meter  (0 VU = -18 dBFS)", font=negrito).pack(anchor="w")
        fv = ttk.Frame(dir_)
        fv.pack(anchor="w")
        self.cv_vu = []
        for rot in ("L", "R"):
            c = tk.Canvas(fv, width=210, height=124, bg="#f2eed8", highlightthickness=1)
            c.pack(side="left", padx=(0, 6))
            self._desenhar_vu_escala(c, rot)
            self._agulha_vu(c, -20.0)
            self.cv_vu.append(c)

        ttk.Label(dir_, text="Peak Meter  (clique para zerar)", font=negrito).pack(anchor="w", pady=(8, 2))
        self.cv_pico = tk.Canvas(dir_, width=430, height=84, bg="#101018", highlightthickness=0)
        self.cv_pico.pack(anchor="w")
        self.cv_pico.bind("<Button-1>", self._reset_picos)
        self._desenhar_escala_picos()
        self.var_tp = tk.StringVar(value="True peak: —")
        ttk.Label(dir_, textvariable=self.var_tp, style="Sub.TLabel").pack(anchor="w")

        ttk.Label(dir_, text="Loudness (LUFS, ITU-R BS.1770 / EBU R128)", font=negrito).pack(anchor="w", pady=(8, 2))
        self.var_lufs = tk.StringVar(value="M —    S —    I —")
        ttk.Label(dir_, textvariable=self.var_lufs, font=("Segoe UI", 15, "bold")).pack(anchor="w")
        self.cv_lufs = tk.Canvas(dir_, width=430, height=50, bg="#101018", highlightthickness=0)
        self.cv_lufs.pack(anchor="w", pady=(4, 0))
        self._desenhar_escala_lufs()
        self.var_lufs_faixa = tk.StringVar(value="")
        ttk.Label(dir_, textvariable=self.var_lufs_faixa, style="Sub.TLabel", wraplength=430).pack(anchor="w")
        ttk.Label(dir_, text="M = momentâneo (400 ms)  ·  S = curto prazo (3 s)  ·  I = integrado da posição",
                  style="Sub.TLabel", wraplength=430).pack(anchor="w", pady=(4, 0))

    # ---- controles ----
    def player_carregar(self, caminho, tocar=False):
        if self._carregando_player:
            return
        self._carregando_player = True
        self.btn_play.configure(state="disabled")
        self.var_status.set("Carregando a faixa no player...")
        threading.Thread(target=self._worker_player, args=(Path(caminho), tocar), daemon=True).start()

    def _worker_player(self, caminho, tocar):
        try:
            self.player.carregar(caminho, progresso=lambda m: self.q.put(("play_status", m)))
            self.q.put(("play_pronto", str(caminho), tocar))
        except Exception as e:
            import traceback
            registrar_erro(f"Player: {caminho}\n{traceback.format_exc()}")
            self.q.put(("play_erro", str(e)))

    def _player_pronto(self, caminho, tocar):
        rep = self.player
        self._carregando_player = False
        self.btn_play.configure(state="normal")
        self.var_faixa.set(rep.caminho.name)
        self.var_player_titulo.set(rep.caminho.name)
        self.var_duracao.set(fmt_tempo(rep.duracao))
        self.var_lufs_faixa.set(f"Faixa inteira: I = {fmt_lufs(rep.loud.integrado_total)} LUFS  ·  "
                                f"pico máximo = {fmt_lufs(rep.pico_faixa)} dBFS  ·  {rep.sr / 1000:.1f} kHz")
        self._pico_hold, self._clip = [(-120.0, 0.0), (-120.0, 0.0)], [False, False]
        self._an_suave = self._an_hold = None
        self._ultima_pos_medida = None
        self._desenhar_onda_player()
        self._desenhar_grade_analisador()
        self._info_faixa_tocando(rep.caminho)
        if tocar:
            self._tocar_seguro()
        self._atualizar_botao_play()
        self.var_status.set(f"Player: {rep.caminho.name}")

    def _tocar_seguro(self):
        try:
            self.player.tocar()
        except Exception as e:
            self._erro_player(e)

    def _erro_player(self, e):
        self._mostrar_erro_audio(e)

    # ---------------------- áudio: erros, diagnóstico e configurações ---------------------- #
    def _verificar_audio_inicial(self):
        threading.Thread(target=self._worker_diagnostico, daemon=True).start()

    def _worker_diagnostico(self):
        rep = self.player
        d = rep.backend.diagnostico(rep.dispositivo, rep.sr if rep.dados is not None else None)
        registrar_info("diagnóstico de áudio: " + " | ".join(
            f"{k}={d[k]}" for k in ("player", "sounddevice", "portaudio", "dispositivo", "samplerate", "estado", "codigo")))
        self.q.put(("audio_diag", d))

    def _dispositivo_perdido(self):
        rep = self.player
        registrar_info("reprodução interrompida: dispositivo de áudio indisponível")
        self.var_status.set("Dispositivo de áudio desconectado. Tentando o Sistema padrão...")
        try:
            rep.trocar_dispositivo(SISTEMA_PADRAO)             # retoma de onde parou
            self.cfg["audio_dispositivo"] = SISTEMA_PADRAO
            salvar_config(self.cfg)
            self._atualizar_botao_play()
            messagebox.showinfo("Áudio", "Dispositivo de áudio desconectado.\n"
                                         "Voltei para o Sistema padrão e continuei a reprodução.")
        except Exception as e:
            self._atualizar_botao_play()
            self._mostrar_erro_audio(e)

    def _mostrar_erro_audio(self, exc):
        err = traduzir_erro(exc)
        registrar_erro(f"Áudio [{err.codigo}]: {err.detalhe}")
        registrar_info(f"erro de áudio [{err.codigo}]: {err.detalhe}")
        dlg = tk.Toplevel(self)
        dlg.title("Áudio")
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.geometry(f"+{self.winfo_rootx() + 140}+{self.winfo_rooty() + 120}")
        quadro = ttk.Frame(dlg, padding=16)
        quadro.pack()
        ttk.Label(quadro, text="Não foi possível iniciar o áudio.", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(quadro, text=f"Problema:\n{err.resumo}", wraplength=430, justify="left").pack(anchor="w", pady=(8, 0))
        if err.codigo in ("sem_sounddevice", "sem_portaudio"):
            if getattr(sys, "frozen", False):
                dica = ("Esta instalação do PsyKey está incompleta (falta o componente de áudio). Reinstale o programa "
                        "ou gere o instalador de novo com o build_windows.bat.")
            else:
                dica = ("Você está executando pelo código-fonte. Clique em \"Instalar componente\" (usa a internet só "
                        "nesta instalação) ou rode:  pip install -r requirements.txt")
        else:
            dica = "O PsyKey verificou a configuração de áudio. Confira a saída de som do Windows e tente de novo."
        ttk.Label(quadro, text=f"Solução:\n{dica}", wraplength=430, justify="left").pack(anchor="w", pady=(8, 0))

        botoes = ttk.Frame(quadro)
        botoes.pack(fill="x", pady=(14, 0))

        def novamente():
            dlg.destroy()
            self.player.backend.recarregar()
            self._tocar_seguro()

        def diagnostico():
            dlg.destroy()
            self.abrir_config_audio()

        def instalar():
            dlg.destroy()
            self.var_status.set("Instalando o componente de áudio (sounddevice)...")
            threading.Thread(target=self._worker_pip, daemon=True).start()

        ttk.Button(botoes, text="Testar novamente", command=novamente).pack(side="left")
        ttk.Button(botoes, text="Abrir diagnóstico", command=diagnostico).pack(side="left", padx=6)
        if err.codigo == "sem_sounddevice" and pode_instalar_componente():
            ttk.Button(botoes, text="Instalar componente", style="Acao.TButton", command=instalar).pack(side="left")
        ttk.Button(botoes, text="Fechar", command=dlg.destroy).pack(side="right")

    def _worker_pip(self):
        try:
            r = subprocess.run(comando_instalar_sounddevice(), capture_output=True, text=True, timeout=900)
            ok, saida = r.returncode == 0, (r.stdout + r.stderr)[-1500:]
        except Exception as e:
            ok, saida = False, repr(e)
        registrar_info(f"instalação do sounddevice: {'ok' if ok else 'falhou'}")
        self.q.put(("audio_pip", ok, saida))

    def abrir_config_audio(self):
        if self._dlg_audio:
            try:
                self._dlg_audio["janela"].lift()
                return
            except Exception:
                self._dlg_audio = None
        dlg = tk.Toplevel(self)
        dlg.title("Áudio — dispositivo de saída e diagnóstico")
        dlg.transient(self)
        dlg.geometry(f"+{self.winfo_rootx() + 100}+{self.winfo_rooty() + 60}")
        quadro = ttk.Frame(dlg, padding=14)
        quadro.pack(fill="both", expand=True)
        ttk.Label(quadro, text="Dispositivo de saída", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        var_disp = tk.StringVar(value=self.player.dispositivo)
        combo = ttk.Combobox(quadro, textvariable=var_disp, values=[SISTEMA_PADRAO], state="readonly", width=58)
        combo.grid(row=1, column=0, columnspan=4, sticky="we", pady=(2, 8))
        var_teste = tk.StringVar(value="")
        texto = tk.Text(quadro, width=78, height=13, wrap="word", font=("Consolas", 9))
        texto.grid(row=3, column=0, columnspan=4, sticky="nsew", pady=(6, 0))
        texto.insert("1.0", "Verificando o áudio...")
        self._dlg_audio = {"janela": dlg, "combo": combo, "texto": texto, "var": var_disp, "var_teste": var_teste}

        def atualizar():
            if not self.player.tocando:                  # não reinicializa o PortAudio com música tocando
                try:
                    self.player.backend.atualizar_dispositivos()
                except Exception:
                    pass
            texto.delete("1.0", "end")
            texto.insert("1.0", "Verificando o áudio...")
            threading.Thread(target=self._worker_diagnostico, daemon=True).start()

        def testar():
            var_teste.set("Tocando o tom de teste (440 Hz)...")
            threading.Thread(target=self._worker_teste_audio, args=(var_disp.get(),), daemon=True).start()

        def copiar():
            self.clipboard_clear()
            self.clipboard_append(texto.get("1.0", "end").strip())
            var_teste.set("Diagnóstico copiado para a área de transferência.")

        def salvar():
            nome = var_disp.get() or SISTEMA_PADRAO
            self.cfg["audio_dispositivo"] = nome
            salvar_config(self.cfg)
            fechar()
            try:
                self.player.trocar_dispositivo(nome)
                self._atualizar_botao_play()
            except Exception as e:
                self._mostrar_erro_audio(e)
            threading.Thread(target=self._worker_diagnostico, daemon=True).start()

        def fechar():
            self._dlg_audio = None
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", fechar)
        ttk.Button(quadro, text="Atualizar dispositivos", command=atualizar).grid(row=2, column=0, sticky="w")
        ttk.Button(quadro, text="Testar áudio", command=testar).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(quadro, text="Copiar diagnóstico", command=copiar).grid(row=2, column=2, sticky="w")
        ttk.Label(quadro, textvariable=var_teste, style="Sub.TLabel").grid(row=4, column=0, columnspan=4, sticky="w",
                                                                           pady=(6, 0))
        rodape = ttk.Frame(quadro)
        rodape.grid(row=5, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(rodape, text="Fechar", command=fechar).pack(side="right")
        ttk.Button(rodape, text="Salvar dispositivo", style="Acao.TButton", command=salvar).pack(side="right", padx=8)
        if self._audio_diag:
            self._mostrar_diagnostico(self._audio_diag)
        else:
            threading.Thread(target=self._worker_diagnostico, daemon=True).start()

    def _mostrar_diagnostico(self, d):
        self._audio_diag = d
        self.btn_audio.configure(text="🔊 " + resumo_estado(d))
        if self._dlg_audio:
            self._dlg_audio["combo"].configure(values=[SISTEMA_PADRAO] + list(d.get("saidas", [])))
            t = self._dlg_audio["texto"]
            t.delete("1.0", "end")
            t.insert("1.0", formatar_diagnostico(d))

    def _worker_teste_audio(self, nome):
        be = self.player.backend
        try:
            indice, aviso = be.resolver_dispositivo(nome)
            r = be.testar_saida(indice)
        except AudioIndisponivel as e:
            r, aviso = {"ok": False, "resumo": e.resumo, "detalhe": e.detalhe}, None
        self.q.put(("audio_teste", r, aviso))

    def _resultado_teste_audio(self, r, aviso):
        if self._dlg_audio:
            if r["ok"]:
                self._dlg_audio["var_teste"].set("✓ Saída de áudio funcionando")
            else:
                self._dlg_audio["var_teste"].set(f"✗ Não foi possível abrir a saída: {r['resumo']}")
                self._dlg_audio["texto"].insert("end", f"\n\nTeste de áudio falhou.\nDetalhe técnico: {r['detalhe']}")
        registrar_info(f"teste de áudio: {'ok' if r['ok'] else 'falhou'} {r.get('detalhe', '')}")

    def player_alternar(self):
        rep = self.player
        if self._carregando_player:
            return
        if rep.dados is None:
            sel = self._selecionados_paths()
            alvo = sel[0] if sel else self.esp_caminho
            if not alvo:
                messagebox.showinfo("Player", "Selecione uma música na lista (ou escolha um arquivo na aba "
                                              "Espectro e Tom) e clique em play.")
                return
            self.player_carregar(alvo, tocar=True)
            return
        try:
            if rep.tocando:
                rep.pausar()
            else:
                rep.tocar()
        except Exception as e:
            self._erro_player(e)
        self._atualizar_botao_play()

    def player_parar(self):
        self.player.parar()
        self._ultima_pos_medida = None
        self._atualizar_botao_play()

    def _atualizar_botao_play(self):
        self.btn_play.configure(text="⏸" if self.player.tocando else "▶")

    def _mudou_volume(self, valor):
        self.player.volume = (float(valor) / 100.0) ** 2

    def _soltou_barra(self, _evento=None):
        self._arrastando = False
        rep = self.player
        if rep.dados is not None:
            rep.buscar(float(self.escala_pos.get()) / 1000.0 * rep.duracao)

    def _clique_onda_player(self, evento):
        rep = self.player
        if rep.dados is None:
            return
        w = max(1, self.cv_onda_player.winfo_width() - 64 - 12)
        rep.buscar(min(max((evento.x - 64) / w, 0.0), 1.0) * rep.duracao)

    def ctx_tocar_interno(self):
        paths = self._selecionados_paths()
        if not paths:
            return
        self.abas.select(2)
        self.player_carregar(paths[0], tocar=True)

    # ---- análise BPM + tom da faixa que está tocando (em paralelo) ----
    def _info_faixa_tocando(self, caminho):
        self.var_player_info.set("")
        for it in self.items.values():
            if it["path"] == caminho and it.get("bpm"):
                self.var_player_info.set(f"BPM {fmt_bpm(it['bpm'])}  ·  Tom {self._texto_tom_item(it)}")
                return
        if self.var_analisar_tocar.get():
            self.var_player_info.set("Analisando BPM e tom...")
            try:
                minimo, maximo = float(self.var_min.get()), float(self.var_max.get())
            except (tk.TclError, ValueError):
                minimo, maximo = 135.0, 200.0
            args = (str(caminho), minimo, maximo, MODOS.get(self.var_modo.get(), "preciso"),
                    bool(self.var_snap.get()), self._params_tom())
            threading.Thread(target=self._worker_analise_tocando, args=args, daemon=True).start()

    def _worker_analise_tocando(self, caminho, minimo, maximo, modo, encaixar, params):
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:      # BPM e tom ao mesmo tempo
                fb = ex.submit(detectar_bpm, caminho, minimo, maximo, modo, encaixar)
                ft = ex.submit(detectar_tom_arquivo, caminho, modo, params)
                bpm, conf, aviso = fb.result()
                t = ft.result()
            self.q.put(("play_analise", caminho, bpm, conf, {"pc": t["pc"], "menor": t["menor"], "conf": t["conf"]}))
        except Exception as e:
            registrar_erro(f"Análise ao tocar: {e!r}")
            self.q.put(("play_analise", caminho, None, None, None))

    def _resultado_analise_tocando(self, caminho, bpm, conf, tom):
        atual = self.player.caminho
        if bpm is None:
            if atual and str(atual) == caminho:
                self.var_player_info.set("Não foi possível analisar BPM/tom desta faixa.")
            return
        if atual and str(atual) == caminho:
            texto = f"{codigo_camelot(tom['pc'], tom['menor'])} {nome_tom(tom['pc'], tom['menor'])[1]}"
            self.var_player_info.set(f"BPM {fmt_bpm(bpm)}  ·  Tom {texto}")
        for iid, it in self.items.items():        # também preenche a lista, se a faixa ainda não tinha sido analisada
            if str(it["path"]) == caminho and it["state"] in ("wait", "error"):
                self._marcar_pronto(iid, bpm=bpm, conf_bpm=conf, tom={"pc": tom["pc"], "menor": tom["menor"]},
                                    conf_tom=tom["conf"], status="Pronto (ao tocar)")

    # ---- atualização em tempo real ----
    def _tick_player(self):
        rep = self.player
        try:
            if rep.consumir_interrupcao():
                self._dispositivo_perdido()
            if rep.dados is not None and rep.loud is not None:
                self._atualizar_player(rep)
        except Exception:
            if not self._erro_tick:
                import traceback
                registrar_erro("Player (tick): " + traceback.format_exc())
                self._erro_tick = True
        self.after(40 if rep.tocando else 150, self._tick_player)

    def _atualizar_player(self, rep):
        pos = rep.posicao_ouvida()
        seg, dur = pos / rep.sr, rep.duracao
        self.var_tempo.set(fmt_tempo(seg))
        if not self._arrastando:
            self.escala_pos.set(1000.0 * seg / dur if dur else 0)
        if rep.fim_chegou:
            rep.fim_chegou = False
            rep.pos = 0
            self._atualizar_botao_play()
        if self.abas.index("current") != 2:               # medidores só quando a aba está visível
            return
        self._desenhar_cabeca(seg / dur if dur else 0.0)
        if not rep.tocando and pos == self._ultima_pos_medida:
            return
        self._ultima_pos_medida = pos
        m = rep.medir()
        pico = (m["pico"] * 2)[:2]
        vu = (m["vu"] * 2)[:2]
        for i in range(2):
            self._vu_suave[i] += (vu[i] - self._vu_suave[i]) * 0.5
            self._agulha_vu(self.cv_vu[i], self._vu_suave[i])
        self._desenhar_picos(pico, m["tp"])
        self.var_lufs.set(f"M {fmt_lufs(m['lufs_m'])}    S {fmt_lufs(m['lufs_s'])}    I {fmt_lufs(m['lufs_i'])}")
        self._desenhar_barras_lufs(m["lufs_m"], m["lufs_s"])
        bandas = rep.espectro_bandas()
        if bandas is not None:
            self._desenhar_analisador(bandas)

    # ---- desenho: forma de onda com posição ----
    def _desenhar_onda_player(self):
        cv = self.cv_onda_player
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        rep = self.player
        if W < 120 or H < 40:
            return
        if rep.ondas is None:
            cv.create_text(W / 2, H / 2, fill="#9a9aa5", font=("Segoe UI", 10),
                           text="Forma de onda da faixa (clique para saltar para um ponto)")
            return
        ml, mr = 64, 12
        w, h = W - ml - mr, H - 8
        ppm = renderizar_onda_ppm(rep.ondas, w, h)
        self.img_onda_player = tk.PhotoImage(width=w, height=h, data=ppm, format="PPM")
        cv.create_image(ml, 4, image=self.img_onda_player, anchor="nw")
        self._desenhar_cabeca(rep.posicao_ouvida() / rep.sr / rep.duracao if rep.duracao else 0.0)

    def _desenhar_cabeca(self, frac):
        cv = self.cv_onda_player
        cv.delete("cabeca")
        W, H = cv.winfo_width(), cv.winfo_height()
        if self.player.ondas is None or W < 120:
            return
        x = 64 + min(max(frac, 0.0), 1.0) * (W - 64 - 12)
        cv.create_line(x, 0, x, H, fill="#ff3b3b", width=2, tags="cabeca")

    # ---- desenho: analisador de espectro em tempo real (20 Hz - 35 kHz) ----
    def _geo_analisador(self):
        cv = self.cv_analisador
        W, H = cv.winfo_width(), cv.winfo_height()
        ml, mr, mt, mb = 46, 10, 10, 24
        return ml, mt, W - ml - mr, H - mt - mb

    def _desenhar_grade_analisador(self):
        cv = self.cv_analisador
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 160 or H < 100:
            return
        ml, mt, w, h = self._geo_analisador()
        fator = math.log(FMAX_ESPECTRO / FMIN_ESPECTRO)
        for f, rot in ((20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
                       (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k"), (35000, "35k")):
            x = ml + math.log(f / FMIN_ESPECTRO) / fator * w
            cv.create_line(x, mt, x, mt + h, fill="#26262f")
            cv.create_text(x, mt + h + 4, text=rot, anchor="n", fill="#b8b8c4", font=("Segoe UI", 8))
        for db in range(0, -101, -20):
            y = mt + (-db / 100.0) * h
            cv.create_line(ml, y, ml + w, y, fill="#26262f")
            cv.create_text(ml - 5, y, text=f"{db}", anchor="e", fill="#b8b8c4", font=("Segoe UI", 8))
        cv.create_text(ml + 4, mt + 2, anchor="nw", fill="#8a8a96", font=("Segoe UI", 8),
                       text="dBFS  ·  Hz (escala logarítmica, FFT 8192, janela Blackman-Harris)")
        rep = self.player
        if rep.dados is not None and rep.sr / 2.0 < FMAX_ESPECTRO:
            xn = ml + math.log((rep.sr / 2.0) / FMIN_ESPECTRO) / fator * w
            cv.create_rectangle(xn, mt, ml + w, mt + h, fill="#1c1418", outline="")
            cv.create_text((xn + ml + w) / 2, mt + h / 2, fill="#9a6a6a", font=("Segoe UI", 8), justify="center",
                           text=f"acima de {rep.sr / 2000:.2f} kHz\n(limite do arquivo)")

    def _desenhar_analisador(self, bandas_db):
        import numpy as np
        cv = self.cv_analisador
        cv.delete("dyn")
        if cv.winfo_width() < 160:
            return
        ml, mt, w, h = self._geo_analisador()
        n = len(bandas_db)
        if self._an_suave is None or len(self._an_suave) != n:
            self._an_suave = np.full(n, -120.0)
            self._an_hold = np.full(n, -120.0)
        self._an_suave = np.where(bandas_db > self._an_suave, bandas_db, self._an_suave - 3.0)   # ataque rápido, queda suave
        self._an_hold = np.maximum(self._an_suave, self._an_hold - 0.5)
        xs = ml + (np.arange(n) + 0.5) / n * w

        def y_de(db):
            return mt + (1.0 - np.clip((db + 100.0) / 100.0, 0.0, 1.0)) * h

        pontos = [ml, mt + h]
        for x, y in zip(xs, y_de(self._an_suave)):
            pontos += [float(x), float(y)]
        pontos += [ml + w, mt + h]
        cv.create_polygon(pontos, fill="#1f6fd1", outline="#8fc8ff", tags="dyn")
        linha = []
        for x, y in zip(xs, y_de(self._an_hold)):
            linha += [float(x), float(y)]
        cv.create_line(linha, fill="#ffd166", tags="dyn")

    # ---- desenho: VU ----
    def _desenhar_vu_escala(self, cv, rotulo):
        cv.delete("all")
        cx, cy, R = 105, 116, 92

        def ponto(pos, r):
            a = math.radians(150 - 120 * pos)
            return cx + r * math.cos(a), cy - r * math.sin(a)

        p0 = posicao_vu(0)
        cv.create_arc(cx - R, cy - R, cx + R, cy + R, start=150 - 120 * p0, extent=120 * p0,
                      style="arc", outline="#333333", width=2)
        cv.create_arc(cx - R, cy - R, cx + R, cy + R, start=30, extent=120 * (1 - p0),
                      style="arc", outline="#c0392b", width=5)
        for vu, rot in ((-20, "-20"), (-10, "-10"), (-7, "-7"), (-5, "-5"), (-3, "-3"), (-2, ""), (-1, ""),
                        (0, "0"), (1, ""), (2, ""), (3, "+3")):
            p = posicao_vu(vu)
            x0, y0 = ponto(p, R - 2)
            x1, y1 = ponto(p, R - (13 if rot else 8))
            cv.create_line(x0, y0, x1, y1, fill="#c0392b" if vu > 0 else "#222222", width=2 if rot else 1)
            if rot:
                xt, yt = ponto(p, R - 25)
                cv.create_text(xt, yt, text=rot, fill="#c0392b" if vu > 0 else "#222222", font=("Segoe UI", 8, "bold"))
        cv.create_text(cx, cy - 38, text="VU", fill="#444444", font=("Segoe UI", 13, "bold"))
        cv.create_text(10, 10, text=rotulo, anchor="nw", fill="#444444", font=("Segoe UI", 10, "bold"))

    def _agulha_vu(self, cv, vu):
        cv.delete("agulha")
        cx, cy, R = 105, 116, 92
        a = math.radians(150 - 120 * posicao_vu(vu))
        cv.create_line(cx, cy, cx + (R - 6) * math.cos(a), cy - (R - 6) * math.sin(a),
                       fill="#111111", width=2, tags="agulha")
        cv.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill="#222222", outline="", tags="agulha")

    # ---- desenho: picos ----
    def _desenhar_escala_picos(self):
        cv = self.cv_pico
        cv.delete("estatico")
        for i, rot in enumerate(("L", "R")):
            cv.create_text(8, 10 + i * 30 + 9, text=rot, fill="#c8c8d0", font=("Segoe UI", 9, "bold"), tags="estatico")
        for db in (-60, -48, -36, -24, -18, -12, -6, -3, 0):
            x = 30 + (db + 60) / 60.0 * 340
            cv.create_line(x, 68, x, 72, fill="#8a8a96", tags="estatico")
            cv.create_text(x, 74, text=str(db), anchor="n", fill="#9a9aa6", font=("Segoe UI", 7), tags="estatico")

    def _desenhar_picos(self, picos, tp):
        cv = self.cv_pico
        cv.delete("dyn")
        agora = time.monotonic()

        def x_de(db):
            return 30 + (min(max(db, -60.0), 0.0) + 60.0) / 60.0 * 340

        for i, db in enumerate(picos):
            y = 10 + i * 30
            hold, t_hold = self._pico_hold[i]
            if db >= hold:
                hold, t_hold = db, agora
            elif agora - t_hold > 1.2:
                hold = max(db, hold - 0.9)                 # o marcador de pico cai depois de ~1 s
            self._pico_hold[i] = (hold, t_hold)
            if db >= -0.1:
                self._clip[i] = True
            for a, b, cor in ((-60, -18, "#2fbf4a"), (-18, -6, "#e6c229"), (-6, 0, "#e8552f")):
                if db > a:
                    cv.create_rectangle(x_de(a), y, x_de(min(db, b)), y + 18, fill=cor, outline="", tags="dyn")
            cv.create_line(x_de(hold), y - 1, x_de(hold), y + 19, fill="#ffffff", width=2, tags="dyn")
            cv.create_text(376, y + 9, text=fmt_lufs(hold), anchor="w", fill="#e0e0e8", font=("Segoe UI", 9), tags="dyn")
            cv.create_oval(412, y + 3, 424, y + 15, fill="#ff2d2d" if self._clip[i] else "#3a1a1a",
                           outline="", tags="dyn")
        self.var_tp.set(f"True peak (4x): {fmt_lufs(tp)} dBTP" + ("   ⚠ acima de 0 dBTP" if tp > 0 else ""))

    def _reset_picos(self, _evento=None):
        self._pico_hold, self._clip = [(-120.0, 0.0), (-120.0, 0.0)], [False, False]

    # ---- desenho: barras de LUFS ----
    def _desenhar_escala_lufs(self):
        cv = self.cv_lufs
        for v in (-40, -30, -23, -14, -9, 0):
            x = 10 + (v + 40) / 40.0 * 410
            cv.create_line(x, 4, x, 40, fill="#2c2c38")
            cv.create_text(x, 42, text=str(v), anchor="n", fill="#9a9aa6", font=("Segoe UI", 7))
        cv.create_text(10, 1, text="-23 = EBU R128  ·  -14 = streaming", anchor="nw", fill="#6a6a76", font=("Segoe UI", 7))

    def _desenhar_barras_lufs(self, m, s):
        cv = self.cv_lufs
        cv.delete("dyn")
        for j, v in enumerate((m, s)):
            y = 10 + j * 14
            x = 10 + (min(max(v, -40.0), 0.0) + 40.0) / 40.0 * 410
            cor = "#2fbf4a" if v <= -14 else ("#e6c229" if v <= -9 else "#e8552f")
            cv.create_rectangle(10, y, x, y + 10, fill=cor, outline="", tags="dyn")

    # ------------------------- nomes, ordenação e presets ------------------ #
    def _atualizar_exemplo(self):
        ex = montar_nome(148.0, {"pc": 9, "menor": True}, self._formato_tom(), "Nome.mp3")
        self.var_exemplo.set(f"Ex.: {ex}")

    def _refrescar_nomes(self):
        self._atualizar_exemplo()
        for iid, it in self.items.items():
            if it["state"] == "ready":
                self._linha(iid, novo=self._nome_novo(iid))

    def ordenar_por(self, coluna):
        """Clique no título: 1º clique = crescente, 2º = decrescente. Tom desempata por BPM e vice-versa."""
        cols = {"tom": ("tom", "bpm"), "bpm": ("bpm", "tom")}.get(coluna, (coluna,))
        if self._sort_cols and self._sort_cols[0] == coluna:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_cols, self._sort_desc = cols, False
        self._sincronizar_barra_ordem()
        self._aplicar_ordenacao()

    def _ordem_escolhida(self, _evento=None):
        self._sort_cols = ORDENACOES[self.var_ordem.get()]
        self._aplicar_ordenacao()

    def alternar_sentido(self):
        self._sort_desc = not self._sort_desc
        self._sincronizar_barra_ordem()
        self._aplicar_ordenacao()

    def _ao_marcar_manter(self):
        if self.var_manter_ordem.get():
            self._aplicar_ordenacao()

    def _sincronizar_barra_ordem(self):
        for nome, cols in ORDENACOES.items():
            if cols == self._sort_cols:
                self.var_ordem.set(nome)
        self.btn_sentido.configure(text="▼ Decrescente" if self._sort_desc else "▲ Crescente")

    def _ordenar_automatico(self):
        """Mantém a lista ordenada quando chegam resultados novos (com um pequeno atraso para agrupar)."""
        if not self.var_manter_ordem.get():
            return
        if self._ordem_id:
            self.after_cancel(self._ordem_id)
        self._ordem_id = self.after(200, self._aplicar_ordenacao)

    def _aplicar_ordenacao(self):
        self._ordem_id = None
        cols = self._sort_cols
        if cols:
            atual = list(self.tree.get_children())
            ordem = ordenar_itens(atual, lambda i, c: chave_ordenacao(c, self.items[i], self.tree.set(i, c)),
                                  cols, self._sort_desc)
            if ordem != atual:
                for pos, i in enumerate(ordem):
                    self.tree.move(i, "", pos)
        for c, titulo in TITULOS.items():
            seta = (" ▼" if self._sort_desc else " ▲") if cols and c == cols[0] else ""
            self.tree.heading(c, text=titulo + seta)

    def _params_tom(self):
        return obter_preset(self.var_preset_tom.get(), self.cfg.get("tom_custom"))

    def _atualizar_resumo_preset(self):
        p = self._params_tom()
        self.var_resumo_preset.set(f"{p['descricao']}\n{resumo_preset(p)}")

    def _preset_tom_mudou(self, _evento=None):
        self.cfg["preset_tom"] = self.var_preset_tom.get()
        salvar_config(self.cfg)
        self._atualizar_resumo_preset()

    def abrir_ajustes_tom(self):
        base = self._params_tom()
        dlg = tk.Toplevel(self)
        dlg.title("Ajustes avançados do tom")
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.geometry(f"+{self.winfo_rootx() + 80}+{self.winfo_rooty() + 40}")
        quadro = ttk.Frame(dlg, padding=14)
        quadro.pack(fill="both", expand=True)
        ttk.Label(quadro, text="Parâmetros do detector de tom (motor FFT)",
                  font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        vars_ = {}
        for i, (chave, rotulo, tipo, opc) in enumerate(CAMPOS_AVANCADOS, start=1):
            ttk.Label(quadro, text=rotulo).grid(row=i, column=0, sticky="w", pady=2, padx=(0, 14))
            var = tk.StringVar(value=str(base[chave]))
            if tipo == "combo":
                w = ttk.Combobox(quadro, textvariable=var, values=opc, state="readonly", width=14)
            else:
                w = ttk.Spinbox(quadro, from_=opc[0], to=opc[1], increment=opc[2], textvariable=var, width=14)
            w.grid(row=i, column=1, sticky="w", pady=2)
            vars_[chave] = var

        linha = len(CAMPOS_AVANCADOS) + 1
        ttk.Label(quadro, text="Comparar também com os perfis").grid(row=linha, column=0, sticky="nw", pady=(8, 2))
        caixa = ttk.Frame(quadro)
        caixa.grid(row=linha, column=1, sticky="w", pady=(8, 2))
        var_comp = {}
        for nome_p in PERFIS:
            v = tk.BooleanVar(value=nome_p in base["comparar"])
            ttk.Checkbutton(caixa, text=NOMES_PERFIS[nome_p], variable=v).pack(anchor="w")
            var_comp[nome_p] = v
        var_grave = tk.BooleanVar(value=bool(base["enfase_grave"]))
        ttk.Checkbutton(quadro, text="Dar mais peso aos trechos em que o grave domina (PSY BASS)",
                        variable=var_grave).grid(row=linha + 1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(quadro, text="Bandas por semitom: 1 (fixo)  ·  Offset to C: sempre ativo", style="Sub.TLabel"
                  ).grid(row=linha + 2, column=0, columnspan=2, sticky="w")

        def salvar():
            novo = {}
            try:
                for chave, rotulo, tipo, opc in CAMPOS_AVANCADOS:
                    txt = vars_[chave].get()
                    if tipo == "combo":
                        novo[chave] = int(txt) if chave == "fft" else txt
                        continue
                    v = float(txt.replace(",", "."))
                    if not opc[0] <= v <= opc[1]:
                        raise ValueError(f"{rotulo}: use um valor entre {opc[0]} e {opc[1]}.")
                    novo[chave] = int(v) if chave in ("oitavas", "hops", "segmentos") else v
            except ValueError as e:
                messagebox.showwarning("Ajustes avançados",
                                       str(e) if "use um valor" in str(e) else "Confira os valores digitados.",
                                       parent=dlg)
                return
            novo["comparar"] = [p for p, v in var_comp.items() if v.get() and p != novo["perfil"]]
            novo["enfase_grave"] = bool(var_grave.get())
            self.cfg["tom_custom"] = novo
            self.cfg["preset_tom"] = PRESET_PERSONALIZADO
            self.var_preset_tom.set(PRESET_PERSONALIZADO)
            salvar_config(self.cfg)
            self._atualizar_resumo_preset()
            dlg.destroy()

        botoes = ttk.Frame(quadro)
        botoes.grid(row=linha + 3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(botoes, text="Cancelar", command=dlg.destroy).pack(side="right")
        ttk.Button(botoes, text="Salvar como Personalizado", style="Acao.TButton", command=salvar
                   ).pack(side="right", padx=8)
        dlg.grab_set()

    def ctx_reproduzir(self):
        paths = self._selecionados_paths()
        if not paths:
            return
        try:
            reproduzir_no_player_padrao(paths[0])
            extra = "  (apenas a primeira das selecionadas)" if len(paths) > 1 else ""
            self.var_status.set(f"Abrindo no player padrão: {paths[0].name}{extra}")
        except Exception as e:
            messagebox.showwarning("Reproduzir", f"Não foi possível abrir o arquivo no player padrão:\n{e}")

    def ctx_corrigir_tom(self):
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showinfo("Corrigir tom", "Selecione uma única música.")
            return
        iid = sel[0]
        it = self.items[iid]
        if it["state"] not in ("ready", "wait", "error"):
            return
        atual = ""
        if it.get("tom"):
            atual = codigo_camelot(it["tom"]["pc"], it["tom"]["menor"])
        txt = simpledialog.askstring("Corrigir tom", "Digite o código Camelot (1A a 12B), por exemplo 8A:",
                                     parent=self, initialvalue=atual)
        if not txt:
            return
        cod = txt.strip().upper().replace(" ", "")
        if cod not in PC_MENOR_POR_CODIGO:
            messagebox.showwarning("Corrigir tom", "Código inválido. Use de 1A a 12B (A = menor, B = maior).")
            return
        pc, menor = PC_MENOR_POR_CODIGO[cod]
        it["tom"], it["ctom"] = {"pc": pc, "menor": menor}, "Manual"
        if it["state"] == "ready":
            self._marcar_pronto(iid, status="Pronto (tom manual)")
        else:
            self._linha(iid, tom=self._texto_tom_item(it), ctom="Manual")

    # ------------------------------- analisar ------------------------------ #
    def analisar(self):
        try:
            minimo, maximo = float(self.var_min.get()), float(self.var_max.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Faixa de BPM", "Informe números válidos para a faixa de BPM.")
            return
        if minimo < 30 or maximo <= minimo * 1.02:
            messagebox.showwarning(
                "Faixa de BPM",
                "Informe uma faixa válida (mínimo a partir de 30 e máximo maior que o mínimo).",
            )
            return

        sel = [i for i in self.tree.selection() if self.items[i]["state"] in ("wait", "ready", "error")]
        alvo = sel or [i for i, it in self.items.items() if it["state"] in ("wait", "error")]
        if not alvo:
            messagebox.showinfo("Analisar", "Não há músicas pendentes para analisar.")
            return

        self.cancel.clear()
        self.progresso.configure(maximum=len(alvo), value=0)
        self._travar(True)
        trabalho = [(i, self.items[i]["path"]) for i in alvo]
        modo = MODOS.get(self.var_modo.get(), "preciso")
        encaixar = bool(self.var_snap.get())
        com_tom = bool(self.var_com_tom.get())
        params = self._params_tom()
        try:
            nucleos = max(1, min(32, int(self.var_nucleos.get())))
        except (tk.TclError, ValueError):
            nucleos = 2
        self.cfg["nucleos"] = nucleos
        salvar_config(self.cfg)
        threading.Thread(target=self._worker,
                         args=(trabalho, minimo, maximo, modo, encaixar, com_tom, params, nucleos),
                         daemon=True).start()

    def _criar_executor(self, n):
        """Processos (paralelismo de verdade); se não funcionarem nesta máquina, usa threads."""
        ex = None
        try:
            ex = ProcessPoolExecutor(max_workers=n)
            ex.submit(tarefa_ping).result(timeout=120)
            return ex, "processos"
        except Exception as e:
            registrar_erro(f"Processos indisponíveis, usando threads: {e!r}")
            try:
                if ex is not None:
                    ex.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            return ThreadPoolExecutor(max_workers=n), "threads"

    def _encerrar_pool(self):
        ex, self._pool = self._pool, None
        if ex is None:
            return
        try:
            procs = list(getattr(ex, "_processes", {}).values())
            ex.shutdown(wait=False, cancel_futures=True)
            if self.cancel.is_set() or self._fechando:       # cancelar/fechar: encerra as análises em andamento
                for p in procs:
                    p.terminate()
        except Exception:
            pass

    def _worker(self, trabalho, minimo, maximo, modo, encaixar, com_tom, params, nucleos):
        self.q.put(("msg", "Preparando a análise em paralelo (na primeira vez pode demorar)..."))
        try:
            import librosa  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            self.q.put(("fatal", "Faltam bibliotecas.\nInstale com:\n\npip install -r requirements.txt"))
            return
        ex, tipo = self._criar_executor(max(1, nucleos))
        self._pool = ex
        try:
            futs = {}
            for iid, path in trabalho:            # BPM e tom de cada faixa entram juntos e rodam ao mesmo tempo
                self.q.put(("status", iid, "Na fila..."))
                fb = ex.submit(tarefa_bpm, str(path), minimo, maximo, modo, encaixar)
                ft = ex.submit(tarefa_tom, str(path), modo, params) if com_tom else None
                futs[iid] = (path, fb, ft)
            pendentes, total, feitos = set(futs), len(futs), 0
            alvo = "BPM e tom" if com_tom else "BPM"
            self.q.put(("msg", f"Analisando {alvo} simultaneamente ({tipo}, {nucleos} núcleo(s))..."))
            while pendentes and not self.cancel.is_set():
                aguardando = [f for i in pendentes for f in futs[i][1:] if f is not None and not f.done()]
                if aguardando:
                    wait(aguardando, timeout=0.3, return_when=FIRST_COMPLETED)
                for iid in list(pendentes):
                    path, fb, ft = futs[iid]
                    if fb.done() and (ft is None or ft.done()):
                        pendentes.discard(iid)
                        feitos += 1
                        self._entregar(iid, path, fb, ft)
                        self.q.put(("progress", feitos, total))
            if self.cancel.is_set():
                for _, fb, ft in futs.values():
                    fb.cancel()
                    if ft is not None:
                        ft.cancel()
        finally:
            self._encerrar_pool()
            self.q.put(("done", self.cancel.is_set()))

    def _entregar(self, iid, path, fb, ft):
        try:
            bpm, conf, aviso = fb.result()
        except Exception as e:
            registrar_erro(f"{path}\nBPM: {e!r}")
            self.q.put(("error", iid, str(e)))
            return
        tom = None
        if ft is not None:
            try:
                t = ft.result()
                tom = {"pc": t["pc"], "menor": t["menor"], "conf": t["conf"]}
            except Exception as e:                # o BPM continua valendo mesmo se o tom falhar
                registrar_erro(f"{path}\nTom: {e!r}")
        self.q.put(("result", iid, {"bpm": bpm, "conf": conf, "aviso": aviso, "tom": tom}))

    def cancelar(self):
        self.cancel.set()
        self.var_status.set("Cancelando após a música atual...")

    def _processar_fila(self):
        try:
            while True:
                msg = self.q.get_nowait()
                tipo = msg[0]
                if tipo == "msg":
                    self.var_status.set(msg[1])
                elif tipo == "status" and msg[1] in self.items:
                    self._linha(msg[1], status=msg[2])
                elif tipo == "result" and msg[1] in self.items:
                    r = msg[2]
                    t = r["tom"]
                    status = "Verifique a oitava" if r["aviso"] else "Pronto"
                    self._marcar_pronto(msg[1], bpm=r["bpm"], conf_bpm=r["conf"],
                                        tom=({"pc": t["pc"], "menor": t["menor"]} if t else None),
                                        conf_tom=(t["conf"] if t else None), status=status)
                elif tipo == "error" and msg[1] in self.items:
                    self.items[msg[1]]["state"] = "error"
                    self._linha(msg[1], status="Erro", tag="error")
                elif tipo == "progress":
                    self.progresso["value"] = msg[1]
                    self.var_status.set(f"Analisando {msg[1]} de {msg[2]}...")
                elif tipo == "fatal":
                    self._travar(False)
                    messagebox.showerror("Bibliotecas ausentes", msg[1])
                    self.var_status.set("Erro: bibliotecas ausentes.")
                elif tipo == "done":
                    self._travar(False)
                    self._ordenar_automatico()
                    prontos = sum(1 for it in self.items.values() if it["state"] == "ready")
                    txt = "Análise cancelada." if msg[1] else "Análise concluída."
                    self.var_status.set(
                        f"{txt} {prontos} música(s) pronta(s). Confira as de confiança \"Baixa\" "
                        "(em laranja) antes de renomear."
                    )
                elif tipo == "copia_fim":
                    _, ok, erros, ja_la, destino, ultimo = msg
                    partes = [f"{ok} música(s) copiada(s) para: {destino}"]
                    if ja_la:
                        partes.append(f"{ja_la} já estava(m) nessa pasta")
                    self.var_status.set(". ".join(partes) + ".")
                    if erros:
                        messagebox.showwarning("Copiar para a pasta de sets",
                                               f"{erros} arquivo(s) não puderam ser copiados.\n\n{ultimo}")
                # --- aba de espectro e tom ---
                elif tipo == "esp_status":
                    self.var_status.set(msg[1])
                elif tipo == "esp_ok":
                    self._esp_finalizar()
                    self._mostrar_resultado_esp(msg[1], msg[2])
                elif tipo == "esp_err":
                    self._esp_finalizar()
                    self.var_status.set("Não foi possível analisar o espectro e o tom.")
                    messagebox.showerror("Espectro e Tom", msg[1])
                # --- player ---
                elif tipo == "play_status":
                    self.var_status.set(msg[1])
                elif tipo == "play_pronto":
                    self._player_pronto(msg[1], msg[2])
                elif tipo == "play_erro":
                    self._carregando_player = False
                    self.btn_play.configure(state="normal")
                    self.var_status.set("Não foi possível carregar a faixa no player.")
                    messagebox.showerror("Player", msg[1])
                elif tipo == "audio_diag":
                    self._mostrar_diagnostico(msg[1])
                elif tipo == "audio_teste":
                    self._resultado_teste_audio(msg[1], msg[2])
                elif tipo == "audio_pip":
                    if msg[1]:
                        self.player.backend.recarregar()
                        self.var_status.set("Componente de áudio instalado.")
                        messagebox.showinfo("Áudio", "Componente de áudio instalado.\nClique em play novamente.")
                        threading.Thread(target=self._worker_diagnostico, daemon=True).start()
                    else:
                        self.var_status.set("Não foi possível instalar o componente de áudio.")
                        messagebox.showerror("Áudio", "Não foi possível instalar o componente de áudio.\n\n" + msg[2])
                elif tipo == "play_analise":
                    self._resultado_analise_tocando(msg[1], msg[2], msg[3], msg[4])
        except queue.Empty:
            pass
        self.after(100, self._processar_fila)

    # ------------------------------- renomear ------------------------------ #
    def renomear(self):
        sel = [i for i in self.tree.selection() if self.items[i]["state"] == "ready"]
        alvo = sel or [i for i, it in self.items.items() if it["state"] == "ready"]
        if not alvo:
            messagebox.showinfo("Renomear", "Nenhuma música analisada e pronta para renomear.\n"
                                            "Clique em \"Analisar BPM\" primeiro.")
            return
        if not messagebox.askyesno("Confirmar", f"Renomear {len(alvo)} arquivo(s) agora?\n\n"
                                                "Você poderá desfazer depois, se precisar."):
            return

        lote, erros = [], 0
        for iid in alvo:
            it = self.items[iid]
            antigo = it["path"]
            novo = antigo.with_name(self._nome_novo(iid))
            try:
                if novo.exists():
                    raise FileExistsError("já existe um arquivo com esse nome")
                antigo.rename(novo)
            except Exception:
                erros += 1
                self._linha(iid, status="Falhou", tag="error")
                continue
            self.paths_set.discard(antigo)
            self.paths_set.add(novo)
            it["path"], it["state"] = novo, "done"
            if self.esp_caminho == antigo:      # mantém a aba de espectro apontando para o arquivo certo
                self.esp_caminho = novo
                self.var_esp_arq.set(f"Arquivo: {novo.name}")
            self._linha(iid, arquivo=novo.name, novo="", status="Renomeado ✔", tag="done")
            lote.append((iid, novo, antigo))

        if lote:
            self.undo_stack.append(lote)
            self.btn_desfazer.configure(state="normal")
        msg = f"{len(lote)} arquivo(s) renomeado(s)."
        if erros:
            msg += f" {erros} falharam (nome já existe ou arquivo em uso)."
        self.var_status.set(msg)
        if erros:
            messagebox.showwarning("Atenção", msg)

    def desfazer(self):
        if not self.undo_stack:
            return
        lote = self.undo_stack.pop()
        revertidos = 0
        for iid, novo, antigo in reversed(lote):
            try:
                if not (novo.exists() and not antigo.exists()):
                    continue
                novo.rename(antigo)
            except Exception:
                continue
            revertidos += 1
            if self.esp_caminho == novo:
                self.esp_caminho = antigo
                self.var_esp_arq.set(f"Arquivo: {antigo.name}")
            if iid in self.items:
                it = self.items[iid]
                self.paths_set.discard(novo)
                self.paths_set.add(antigo)
                it["path"] = antigo
                self._marcar_pronto(iid, status="Pronto")
        self.btn_desfazer.configure(state="normal" if self.undo_stack else "disabled")
        self.var_status.set(f"Desfeito: {revertidos} arquivo(s) voltaram ao nome original.")

    def _fechar(self):
        self._fechando = True
        self.cancel.set()
        self._encerrar_pool()
        self.player.descarregar()
        self.destroy()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _preparar_ambiente()
    if "--selftest" in sys.argv:
        sys.exit(0 if autoteste() else 1)
    try:  # texto nítido em telas de alta resolução no Windows
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        if not getattr(sys, "frozen", False):
            faltando = bibliotecas_faltando()
            if faltando:
                aviso = ("Faltam bibliotecas: " + ", ".join(faltando) + "\n\nInstale com:\n    pip install -r requirements.txt"
                         "\n\nO programa vai abrir, mas as funções que dependem delas não vão funcionar.")
                print(aviso)
                raiz_aviso = tk.Tk()
                raiz_aviso.withdraw()
                messagebox.showwarning("PsyKey", aviso, parent=raiz_aviso)
                raiz_aviso.destroy()
        App().mainloop()
    except Exception:
        import traceback
        texto = traceback.format_exc()
        registrar_erro("Falha ao iniciar:\n" + texto)
        print(texto)
        try:
            messagebox.showerror("PsyKey", "Não foi possível iniciar o programa:\n\n" + texto[-900:]
                                 + f"\n\nDetalhes em: {pasta_dados() / 'erro.log'}")
        except Exception:
            pass
        if not getattr(sys, "frozen", False) and sys.stdin is not None and sys.stdin.isatty():
            input("Pressione Enter para fechar...")
        sys.exit(1)
