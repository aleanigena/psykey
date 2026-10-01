#!/usr/bin/env python3
"""
Detecta o BPM de arquivos mp3/flac/wav e renomeia colocando o BPM na frente do nome.

Exemplo:  Minha Musica.mp3  ->  128 - Minha Musica.mp3

Instalação:
    pip install librosa numpy soundfile
    (para mp3 é recomendável ter o ffmpeg instalado)

Uso:
    python bpm_renamer.py "C:/Musicas"                 # modo teste (não renomeia)
    python bpm_renamer.py "C:/Musicas" --apply         # renomeia de verdade
    python bpm_renamer.py "C:/Musicas" --apply --no-recursive
    python bpm_renamer.py "C:/Musicas" --min-bpm 70 --max-bpm 180 --apply
"""

import argparse
import re
import sys
from pathlib import Path

import librosa
import numpy as np

EXTENSOES = {".mp3", ".flac", ".wav"}

# Considera "já renomeado" nomes que começam com 2-3 dígitos seguidos de " - "
PADRAO_JA_RENOMEADO = re.compile(r"^\d{2,3}\s*-\s")


def duracao_total(caminho: Path) -> float:
    try:
        return float(librosa.get_duration(path=str(caminho)))
    except TypeError:  # versões antigas do librosa
        return float(librosa.get_duration(filename=str(caminho)))


def ajustar_oitava(bpm: float, minimo: float, maximo: float) -> float:
    """Corrige erros de metade/dobro do tempo (ex.: 70 <-> 140)."""
    while bpm < minimo:
        bpm *= 2
    while bpm > maximo:
        bpm /= 2
    return bpm


def detectar_bpm(caminho: Path, minimo: float, maximo: float, janela: float = 60.0) -> int:
    dur = duracao_total(caminho)
    # Analisa só um trecho do meio da música: mais rápido e costuma ser mais estável
    if dur > janela:
        offset = max(0.0, (dur - janela) / 2)
        y, sr = librosa.load(str(caminho), sr=22050, mono=True, offset=offset, duration=janela)
    else:
        y, sr = librosa.load(str(caminho), sr=22050, mono=True)

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo)[0])
    return int(round(ajustar_oitava(tempo, minimo, maximo)))


def listar_arquivos(pasta: Path, recursivo: bool):
    itens = pasta.rglob("*") if recursivo else pasta.glob("*")
    for p in sorted(itens):
        if p.is_file() and p.suffix.lower() in EXTENSOES:
            yield p


def main():
    ap = argparse.ArgumentParser(description="Detecta BPM e renomeia arquivos de áudio.")
    ap.add_argument("pasta", type=Path, help="Pasta com as músicas")
    ap.add_argument("--apply", action="store_true", help="Renomeia de verdade (sem isso é só teste)")
    ap.add_argument("--no-recursive", action="store_true", help="Não entra em subpastas")
    ap.add_argument("--min-bpm", type=float, default=80, help="BPM mínimo esperado (padrão 80)")
    ap.add_argument("--max-bpm", type=float, default=180, help="BPM máximo esperado (padrão 180)")
    args = ap.parse_args()

    if not args.pasta.is_dir():
        sys.exit(f"Pasta não encontrada: {args.pasta}")

    if not args.apply:
        print(">>> MODO TESTE: nada será renomeado. Use --apply para aplicar.\n")

    ok = pulados = erros = 0

    for arq in listar_arquivos(args.pasta, recursivo=not args.no_recursive):
        if PADRAO_JA_RENOMEADO.match(arq.name):
            print(f"[pulado] já tem BPM: {arq.name}")
            pulados += 1
            continue

        try:
            bpm = detectar_bpm(arq, args.min_bpm, args.max_bpm)
        except Exception as e:
            print(f"[erro]   {arq.name}: {e}")
            erros += 1
            continue

        novo = arq.with_name(f"{bpm} - {arq.name}")
        if novo.exists():
            print(f"[pulado] destino já existe: {novo.name}")
            pulados += 1
            continue

        print(f"[{bpm:>3} BPM] {arq.name}  ->  {novo.name}")
        if args.apply:
            arq.rename(novo)
        ok += 1

    print(f"\nConcluído: {ok} processados, {pulados} pulados, {erros} com erro.")


if __name__ == "__main__":
    main()
