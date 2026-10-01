# -*- mode: python ; coding: utf-8 -*-
# Receita do PyInstaller para o BPM Renamer (Windows 10/11, 64 bits).
# Gera a pasta dist\BPMRenamer com o programa e TODAS as bibliotecas dentro,
# para o usuario final nao precisar instalar Python nem nada mais.
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# Pacotes que carregam arquivos de dados / DLLs / modulos de forma dinamica.
for pacote in ("librosa", "soundfile", "soxr", "lazy_loader", "audioread", "pooch"):
    try:
        d, b, h = collect_all(pacote)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as erro:  # pacote opcional ausente
        print(f"[aviso] nao foi possivel coletar '{pacote}': {erro}")

datas += [("icone.ico", ".")]

a = Analysis(
    ["bpm_renamer_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "pytest", "notebook", "jupyter", "sphinx", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BPMRenamer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # aplicativo de janela (sem tela preta do console)
    icon="icone.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="BPMRenamer",
)
