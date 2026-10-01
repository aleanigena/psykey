import os
import unittest
from pathlib import Path

import numpy as np

from helpers import carregar_modulo

m, _ = carregar_modulo()


class TestCamelotETom(unittest.TestCase):
    def test_camelot_e_siglas(self):
        esperado = {(9, True): ("8A", "Am"), (0, False): ("8B", "C"), (6, True): ("11A", "F#m"), (9, False): ("11B", "A"),
                    (3, True): ("2A", "Ebm"), (8, True): ("1A", "G#m"), (11, False): ("1B", "B")}
        for (pc, menor), (cod, sigla) in esperado.items():
            self.assertEqual(m.codigo_camelot(pc, menor), cod)
            self.assertEqual(m.nome_tom(pc, menor)[1], sigla)
            self.assertEqual(m.SIGLA_POR_CODIGO[cod], sigla)
        self.assertEqual(len(m.SIGLA_POR_CODIGO), 24)

    def test_compativeis_e_relativas(self):
        self.assertEqual(m.compativeis("8A"), {"7A", "9A", "8B"})
        self.assertEqual(m.compativeis("12B"), {"11B", "1B", "12A"})
        self.assertTrue(m._relativas((6, True), (9, False)))          # F#m x A
        self.assertFalse(m._relativas((6, True), (0, False)))

    def test_confianca_por_perfis(self):
        f = (6, True)
        c = m.confianca_combinada
        self.assertEqual(c({"shaath": f, "gomez": f, "temperley": f}, "shaath", 1.0), "Alta")
        self.assertEqual(c({"shaath": f, "gomez": f, "temperley": (9, False)}, "shaath", 1.0), "Média")
        self.assertEqual(c({"shaath": f, "gomez": (0, False), "temperley": (2, True)}, "shaath", 1.0), "Baixa")
        self.assertEqual(c({"shaath": f, "gomez": f, "temperley": f}, "shaath", 0.3), "Média")

    def test_motor_de_tom_com_sinal_sintetico(self):
        rng = np.random.default_rng(7)
        sr = 4410
        for pc, menor in ((0, False), (9, True), (6, True), (2, True), (4, True)):   # C, Am, F#m, Dm, Em
            perfil = np.roll(np.array(m.KK_MENOR if menor else m.KK_MAIOR, float), pc)
            prob = perfil / perfil.sum()
            y = np.zeros(int(150 * sr), dtype=np.float32)
            passo = int(0.4 * sr); t = np.arange(passo) / sr
            for k in range(0, len(y) - passo, passo):
                midi = 48 + int(rng.choice(12, p=prob)) + 12 * int(rng.integers(0, 3))
                f = 440 * 2 ** ((midi - 69) / 12)
                y[k:k + passo] += (0.3 * np.sin(2 * np.pi * f * t) * np.hanning(passo) ** 0.5).astype(np.float32)
            r = m.analisar_tom_sinal(y, sr, m.obter_preset(m.PRESET_PADRAO))
            self.assertEqual((r["pc"], r["menor"]), (pc, menor), m.codigo_camelot(pc, menor))

    def test_presets_techno_e_estilos(self):
        p = m.obter_preset(m.PRESET_TECHNO)
        self.assertEqual((p["perfil"], p["fft"]), ("shaath", 16384))
        self.assertEqual(m.ESTILOS["Techno (115–145)"], (115, 145))
        self.assertEqual(m.ESTILO_PARA_PRESET_TOM["Techno (115–145)"], m.PRESET_TECHNO)
        for nome in m.NOMES_PRESETS_TOM:
            self.assertTrue(m.resumo_preset(m.obter_preset(nome)))


class TestLoudnessEEspectro(unittest.TestCase):
    def test_lufs_de_referencia(self):
        for sr in (44100, 48000, 96000):
            t = np.arange(10 * sr) / sr
            s = (10 ** (-23 / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
            ml = m.MedidorLoudness(np.stack([s, s], axis=1), sr)
            self.assertAlmostEqual(ml.integrado_total, -23.0, delta=0.1, msg=f"sr={sr}")

    def test_gate_ignora_silencio(self):
        sr = 48000
        t = np.arange(10 * sr) / sr
        s = (10 ** (-20 / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        x = np.concatenate([s, np.zeros(20 * sr, np.float32)])
        self.assertAlmostEqual(m.MedidorLoudness(np.stack([x, x], 1), sr).integrado_total, -20.0, delta=0.1)

    def test_pico_true_peak_e_vu(self):
        sr = 48000
        t = np.arange(sr) / sr
        x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        r = m.medir_instantaneo(np.stack([x, x * 0.5], axis=1), sr, sr)
        self.assertAlmostEqual(r["pico"][0], -6.02, delta=0.05)
        self.assertAlmostEqual(r["pico"][1], -12.04, delta=0.05)
        self.assertAlmostEqual(r["vu"][0], 20 * np.log10(0.5 / np.sqrt(2)) + 18, delta=0.1)
        n = np.arange(4800)
        y = np.sin(2 * np.pi * 11025 * n / sr + np.pi / 8).astype(np.float32)
        r = m.medir_instantaneo(np.stack([y, y], 1), len(y), sr)
        self.assertGreater(r["tp"], r["pico"][0])                     # true peak enxerga o pico entre amostras
        self.assertAlmostEqual(r["tp"], 0.0, delta=0.1)

    def test_espectro_ate_35khz_e_nyquist(self):
        sr = 96000
        an = m.AnalisadorEspectro(sr)
        n = np.arange(an.n_fft)
        db = an.bandas_db(0.5 * np.sin(2 * np.pi * 1000 * n / sr) + 0.25 * np.sin(2 * np.pi * 30000 * n / sr))
        c = an.mapa["centros"]
        self.assertAlmostEqual(db.max(), -6.0, delta=1.0)
        self.assertAlmostEqual(c[int(np.argmax(db))], 1000, delta=60)
        b30 = int(np.argmin(np.abs(c - 30000)))
        self.assertAlmostEqual(db[b30 - 1:b30 + 2].max(), -12.0, delta=1.5)
        an44 = m.AnalisadorEspectro(44100)
        db44 = an44.bandas_db(np.sin(2 * np.pi * 1000 * np.arange(an44.n_fft) / 44100))
        self.assertTrue((db44[~an44.mapa["validas"]] == -200.0).all())   # acima de Nyquist: sem informação


class TestListaENomes(unittest.TestCase):
    def test_nomes(self):
        tom = {"pc": 9, "menor": True}
        self.assertEqual(m.montar_nome(148.0, tom, "ambos", "Faixa.mp3"), "148,00 - 8A Am - Faixa.mp3")
        self.assertEqual(m.montar_nome(148.0, tom, "camelot", "Faixa.mp3"), "148,00 - 8A - Faixa.mp3")
        self.assertEqual(m.montar_nome(148.0, tom, "nota", "Faixa.mp3"), "148,00 - Am - Faixa.mp3")
        self.assertEqual(m.montar_nome(172.37, None, "ambos", "X.wav"), "172,37 - X.wav")
        self.assertTrue(m.PADRAO_JA_RENOMEADO.match("148,00 - 8A Am - Faixa.mp3"))

    def test_ordenacao_tom_e_bpm(self):
        itens = {"a": (150.0, (9, True)), "b": (143.5, (0, False)), "c": (170.0, (9, True)), "d": (None, None),
                 "e": (140.0, (9, True)), "g": (128.0, (0, False))}

        def chave(i, col):
            b, t = itens[i]
            if col == "bpm":
                return b
            return None if t is None else (int(m.codigo_camelot(*t)[:-1]), m.codigo_camelot(*t)[-1])
        asc = m.ordenar_itens(itens, chave, ("tom", "bpm"), False)
        self.assertEqual(asc, ["e", "a", "c", "g", "b", "d"])          # 8A (140,150,170) -> 8B (128,143.5) -> sem valor
        desc = m.ordenar_itens(itens, chave, ("tom", "bpm"), True)
        self.assertEqual(desc, ["b", "g", "c", "a", "e", "d"])
        self.assertEqual(m.ordenar_itens(itens, chave, ("bpm", "tom"), False), ["g", "e", "b", "a", "c", "d"])


class TestConfigELog(unittest.TestCase):
    def test_config_migra_do_nome_antigo(self):
        import tempfile
        base = Path(tempfile.mkdtemp())
        (base / "BPMRenamer").mkdir()
        (base / "BPMRenamer" / "config.json").write_text('{"pasta_sets": "D:/Sets"}', encoding="utf-8")
        antigo, os.environ["APPDATA"] = os.environ.get("APPDATA"), str(base)
        try:
            self.assertEqual(m.carregar_config(), {"pasta_sets": "D:/Sets"})
            self.assertTrue((base / "PsyKey" / "config.json").exists())
            m.registrar_info("teste")
            self.assertTrue((base / "PsyKey" / "psykey.log").exists())
        finally:
            os.environ["APPDATA"] = antigo

    def test_caminho_livre_nao_sobrescreve(self):
        import tempfile
        pasta = Path(tempfile.mkdtemp())
        (pasta / "a.mp3").write_text("x")
        self.assertEqual(m.caminho_livre(pasta, "a.mp3").name, "a (2).mp3")
        self.assertEqual(m.caminho_livre(pasta, "b.mp3").name, "b.mp3")


if __name__ == "__main__":
    unittest.main()
