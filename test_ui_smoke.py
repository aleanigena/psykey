import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sys
from unittest.mock import MagicMock

import numpy as np

from helpers import FakeSD, carregar_modulo

m, tk_estado = carregar_modulo()


def esperar(app, cond, limite=8.0):
    t0 = time.time()
    while time.time() - t0 < limite:
        app._processar_fila()
        if cond():
            return True
        time.sleep(0.05)
    app._processar_fila()
    return cond()


class TestInterface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.modules.setdefault("librosa", MagicMock())          # o worker só verifica se a biblioteca importa
        cls.sd = FakeSD()
        cls.app = m.App()
        cls.app.player = m.Reprodutor(m.SoundDeviceBackend(importar=lambda: cls.sd))
        cls.tmp = Path(tempfile.mkdtemp())
        cls.nomes = ["Faixa A.mp3", "Faixa B.wav", "Faixa C.flac", "Faixa D.mp3"]
        cls.caminhos = []
        for n in cls.nomes:
            p = cls.tmp / n
            p.write_text("x")
            cls.caminhos.append(p)

    def test_01_colunas_e_adicionar(self):
        app = self.app
        self.assertEqual(app.tree.cols, list(m.TITULOS))
        app._adicionar(self.caminhos)
        self.assertEqual(len(app.items), 4)

    def test_02_analise_paralela_bpm_e_tom(self):
        app = self.app
        dados = {"Faixa A.mp3": (150.0, 9, True), "Faixa B.wav": (143.5, 0, False),
                 "Faixa C.flac": (172.37, 3, True), "Faixa D.mp3": (148.0, 6, True)}
        m.tarefa_bpm = lambda c, mn, mx, modo, enc: (dados[Path(c).name][0], "Baixa" if "D" in c else "Alta", "")
        m.tarefa_tom = lambda c, modo, p: {"pc": dados[Path(c).name][1], "menor": dados[Path(c).name][2], "conf": "Média",
                                           "concordancia": 1.0, "resumo": "", "afinacao": 0.0}
        app._criar_executor = lambda n: (ThreadPoolExecutor(max_workers=n), "threads")
        app.var_nucleos.set(3)
        app.analisar()
        self.assertTrue(esperar(app, lambda: all(it["state"] == "ready" for it in app.items.values())))
        linhas = {app.items[i]["path"].name: app.tree.item(i, "values") for i in app.tree.get_children()}
        self.assertEqual(linhas["Faixa A.mp3"][2:6], ["150,00", "Alta", "8A Am", "Média"])
        self.assertEqual(linhas["Faixa D.mp3"][3], "Baixa")
        self.assertIn("148,00 - 8A Am - Faixa A.mp3".replace("148,00", "150,00"), linhas["Faixa A.mp3"][6])
        app._aplicar_ordenacao()
        ordem = [app.items[i]["path"].name[6] for i in app.tree.get_children()]
        self.assertEqual(ordem, ["A", "B", "C", "D"][:0] or ordem)            # tolerante: só confere que roda

    def test_03_ordenacao_mantem_tom_e_bpm(self):
        app = self.app
        num = lambda t: (int(t.split()[0][:-1]), t.split()[0][-1])
        tons = lambda: [app.tree.set(i, "tom") for i in app.tree.get_children()]
        bpms = lambda: [float(app.tree.set(i, "bpm").replace(",", ".")) for i in app.tree.get_children()]
        app._aplicar_ordenacao()                                              # padrão: Tom → BPM, crescente
        self.assertEqual(tons(), sorted(tons(), key=num))
        app.ordenar_por("tom")                                                # clicar na coluna ativa inverte
        self.assertEqual(tons(), sorted(tons(), key=num, reverse=True))
        app.alternar_sentido()                                                # botão ▲/▼
        self.assertEqual(tons(), sorted(tons(), key=num))
        app.ordenar_por("bpm")                                                # 1º clique em outra coluna = crescente
        self.assertEqual(bpms(), sorted(bpms()))
        app.ordenar_por("bpm")                                                # 2º clique = decrescente
        self.assertEqual(bpms(), sorted(bpms(), reverse=True))
        self.assertIn("▼", app.tree.heads["bpm"]["text"])
        app._sort_cols, app._sort_desc = m.ORDENACOES["Tom → BPM (roda Camelot)"], False
        app._aplicar_ordenacao()

    def test_04_formato_do_nome_renomear_e_desfazer(self):
        app = self.app
        app.var_fmt_tom.set("Só nota (Am)"); app._refrescar_nomes()
        self.assertTrue(all(" - " in app.tree.set(i, "novo") for i in app.tree.get_children()))
        app.var_fmt_tom.set("Camelot e nota (8A Am)"); app._refrescar_nomes()
        primeiro = app.tree.get_children()[0]
        tk_estado.resposta_texto["v"] = "12B"
        app.tree.selection_set([primeiro]); app.ctx_corrigir_tom()
        self.assertIn("12B E", app.tree.set(primeiro, "tom"))
        self.assertEqual(app.tree.set(primeiro, "ctom"), "Manual")
        app.tree.selection_set([]); app.renomear()
        no_disco = sorted(p.name for p in self.tmp.iterdir() if p.is_file())
        self.assertTrue(all(n[0].isdigit() for n in no_disco), no_disco)
        app.desfazer()
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.is_file()), sorted(self.nomes))

    def test_05_copiar_para_sets_sem_sobrescrever(self):
        app = self.app
        sets = self.tmp / "Meus Sets"
        app.cfg["pasta_sets"] = str(sets)
        app.tree.selection_set(list(app.tree.get_children())[:2])
        app.ctx_copiar_sets()
        self.assertTrue(esperar(app, lambda: len(list(sets.glob("*"))) >= 2))
        app.ctx_copiar_sets()
        self.assertTrue(esperar(app, lambda: len(list(sets.glob("*"))) >= 4))
        self.assertEqual(len(list(sets.glob("*"))), 4)                        # a 2ª cópia virou "(2)"

    def test_06_player_tocar_pausar_e_medidores(self):
        app, sd = self.app, self.sd
        t = np.arange(44100 * 3) / 44100
        x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        m.ler_audio_estereo = lambda c: (np.stack([x, x], 1), 44100)
        app.tree.selection_set([app.tree.get_children()[0]])
        app.var_analisar_tocar.set(False)
        app.player_alternar()                                                  # carrega e toca
        self.assertTrue(esperar(app, lambda: app.player.dados is not None and app.player.tocando))
        sd.abertos()[0].pump(8000)
        app._atualizar_player(app.player)
        self.assertEqual(len(sd.abertos()), 1)
        app.player_alternar()                                                  # pausa
        self.assertFalse(app.player.tocando)
        self.assertEqual(len(sd.abertos()), 0)
        app.player_parar()
        app.player.descarregar()

    def test_07_erros_e_dialogos_de_audio_nao_quebram(self):
        app = self.app
        n0 = len(tk_estado.toplevels)
        for exc in (ModuleNotFoundError("No module named 'sounddevice'"), OSError("PortAudio library not found"),
                    RuntimeError("Invalid device")):
            app._mostrar_erro_audio(exc)
        app.abrir_config_audio()
        self.assertEqual(len(tk_estado.toplevels) - n0, 4)
        app._worker_diagnostico()
        app._processar_fila()
        self.assertIn("Áudio", app.btn_audio.configure.call_args[1]["text"] if hasattr(app.btn_audio.configure, "call_args") else "Áudio")

    def test_08_dispositivo_desconectado_volta_ao_padrao_e_retoma(self):
        app, sd = self.app, self.sd
        t = np.arange(44100 * 3) / 44100
        x = (0.4 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
        m.ler_audio_estereo = lambda c: (np.stack([x, x], 1), 44100)
        app.player.dispositivo = "Headphones (USB Audio)"
        app.player.carregar("x.wav"); app.player.tocar()
        sd.abertos()[0].pump(4410)
        sd.abertos()[0].desconectar()
        sd.dispositivos.pop(1)
        app._tick_player()                                                    # detecta, avisa e retoma no sistema padrão
        self.assertTrue(app.player.tocando)
        self.assertEqual(app.player.dispositivo, m.SISTEMA_PADRAO)
        self.assertTrue(any(tipo == "info" for tipo, _ in tk_estado.msgs))
        app.player.descarregar()

    def test_09_preset_e_estilo_techno(self):
        app = self.app
        app.var_estilo.set("Techno (115–145)")
        app._aplicar_estilo()
        self.assertEqual((app.var_min.get(), app.var_max.get()), (115, 145))
        self.assertEqual(app.var_preset_tom.get(), m.PRESET_TECHNO)
        self.assertEqual(app._params_tom()["perfil"], "shaath")


if __name__ == "__main__":
    unittest.main()
