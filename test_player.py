import unittest

import numpy as np

from helpers import FakeSD, carregar_modulo

m, estado_tk = carregar_modulo()


def faixa(sr=44100, seg=4.0, canais=2, freq=440.0, amp=0.5):
    t = np.arange(int(sr * seg)) / sr
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return (np.stack([x, x * 0.5], axis=1) if canais == 2 else x[:, None]), sr


def novo_player(sd=None, dados=None, dispositivo=None):
    sd = sd or FakeSD()
    backend = m.SoundDeviceBackend(importar=lambda: sd)
    rep = m.Reprodutor(backend, dispositivo)
    dados, sr = dados or faixa()
    m.ler_audio_estereo = lambda caminho: (dados, sr)          # decodificação simulada
    rep.carregar("faixa_de_teste.wav")
    return rep, sd


class TestPlayer(unittest.TestCase):
    def test_tocar_entrega_as_amostras(self):
        rep, sd = novo_player()
        rep.volume = 1.0
        rep.tocar()
        self.assertTrue(rep.tocando)
        self.assertEqual(len(sd.abertos()), 1)
        saida = sd.abertos()[0].pump(1000)
        np.testing.assert_allclose(saida, rep.dados[:1000], atol=1e-6)
        saida2 = sd.abertos()[0].pump(1000)
        np.testing.assert_allclose(saida2, rep.dados[1000:2000], atol=1e-6)
        self.assertEqual(rep.pos, 2000)
        rep.descarregar()
        self.assertEqual(len(sd.abertos()), 0)

    def test_pause_play_stop_seek(self):
        rep, sd = novo_player()
        rep.tocar(); sd.abertos()[0].pump(4410)
        rep.pausar()
        self.assertFalse(rep.tocando)
        self.assertEqual(len(sd.abertos()), 0)                       # pausar fecha o stream
        self.assertAlmostEqual(rep.pos, 4410 - int(0.05 * 44100), delta=5)   # volta o que estava no buffer da placa
        pos_pausa = rep.pos
        rep.tocar()                                                  # continua de onde parou
        self.assertEqual(len(sd.abertos()), 1)
        self.assertGreaterEqual(rep.pos, pos_pausa)
        rep.parar()
        self.assertEqual((rep.pos, len(sd.abertos())), (0, 0))
        rep.buscar(2.0)
        self.assertEqual(rep.pos, 88200)
        rep.tocar(); out = sd.abertos()[0].pump(500)
        np.testing.assert_allclose(out[:, 0], rep.dados[88200:88700, 0] * rep.volume, atol=1e-6)
        rep.buscar(999)                                              # além do fim: limita
        self.assertLess(rep.pos, len(rep.dados))
        rep.descarregar()

    def test_fim_da_faixa(self):
        rep, sd = novo_player(dados=faixa(seg=1.2))
        rep.tocar()
        s = sd.abertos()[0]
        while s.active:
            s.pump(4096)
        self.assertTrue(rep.fim_chegou)
        self.assertFalse(rep.tocando)
        self.assertFalse(rep.consumir_interrupcao())                 # fim normal não é "dispositivo perdido"
        rep.pos = 0; rep.fim_chegou = False
        rep.tocar()                                                  # depois do fim, tocar de novo funciona
        self.assertTrue(rep.tocando)
        self.assertEqual(len(sd.abertos()), 1)
        rep.descarregar()

    def test_mono_vira_estereo(self):
        rep, sd = novo_player(dados=faixa(canais=1))
        rep.volume = 1.0; rep.tocar()
        out = sd.abertos()[0].pump(800)
        np.testing.assert_allclose(out[:, 0], rep.dados[:800, 0], atol=1e-6)
        np.testing.assert_allclose(out[:, 1], out[:, 0], atol=1e-6)
        rep.descarregar()

    def test_taxa_nao_suportada_reamostra_sem_mudar_a_duracao(self):
        dados = faixa(sr=96000, seg=3.0)
        rep, sd = novo_player(dados=dados)                           # a "placa" só aceita 44100/48000
        rep.tocar()
        self.assertEqual(sd.abertos()[0].samplerate, 48000)
        self.assertEqual(rep.sr_buf, 48000)
        self.assertAlmostEqual(len(rep._buf) / 48000, 3.0, places=2)
        self.assertAlmostEqual(rep.duracao, 3.0, places=3)
        sd.abertos()[0].pump(24000)                                  # 0,5 s do buffer
        self.assertAlmostEqual(rep.posicao_ouvida() / rep.sr, 0.5 - 0.05, delta=0.01)   # posição na taxa original
        rep.descarregar()

    def test_trocas_repetidas_nao_vazam_streams(self):
        rep, sd = novo_player()
        for i in range(6):
            dados, sr = faixa(seg=2.0, freq=300 + 50 * i)
            m.ler_audio_estereo = lambda caminho, d=dados, s=sr: (d, s)
            rep.carregar(f"faixa{i}.wav")
            rep.tocar(); sd.abertos()[0].pump(1024)
            if i % 2:
                rep.pausar(); rep.tocar()
            self.assertEqual(len(sd.abertos()), 1)
        rep.descarregar()
        self.assertEqual(len(sd.abertos()), 0)

    def test_troca_de_dispositivo_durante_a_reproducao(self):
        rep, sd = novo_player()
        rep.tocar(); sd.abertos()[0].pump(44100)
        rep.trocar_dispositivo("Headphones (USB Audio)")
        self.assertTrue(rep.tocando)
        self.assertEqual(len(sd.abertos()), 1)
        self.assertEqual(sd.abertos()[0].device, 1)
        self.assertGreater(rep.pos, 30000)                           # retomou perto de onde estava
        rep.descarregar()

    def test_dispositivo_salvo_inexistente_volta_ao_padrao(self):
        rep, sd = novo_player(dispositivo="Fone Bluetooth que sumiu")
        rep.tocar()
        self.assertEqual(rep.dispositivo, m.SISTEMA_PADRAO)
        self.assertIn("não está mais disponível", rep.aviso)
        self.assertIsNone(sd.abertos()[0].device)
        rep.descarregar()

    def test_dispositivo_desconectado(self):
        rep, sd = novo_player(dispositivo="Headphones (USB Audio)")
        rep.tocar(); sd.abertos()[0].pump(4410)
        sd.abertos()[0].desconectar()                                # o fone USB foi arrancado
        self.assertFalse(rep.tocando)
        self.assertTrue(rep.consumir_interrupcao())
        self.assertFalse(rep.consumir_interrupcao())                 # avisa uma única vez
        sd.dispositivos.pop(1)                                       # o dispositivo deixa de existir
        rep.trocar_dispositivo(m.SISTEMA_PADRAO)                     # o que a interface faz: volta ao padrão e retoma
        self.assertTrue(rep.tocando)
        self.assertIsNone(sd.abertos()[-1].device)
        rep.descarregar()

    def test_pausar_e_tocar_rapido_ignora_callback_atrasado(self):
        rep, sd = novo_player()
        rep.tocar(); antigo = sd.abertos()[0]
        antigo_fim = antigo.finished_callback
        rep.pausar(); rep.tocar()
        antigo_fim()                                                 # "fim" atrasado do stream antigo chega agora
        self.assertTrue(rep.tocando)                                 # e não pode marcar o novo stream como parado
        self.assertFalse(rep.consumir_interrupcao())
        rep.descarregar()

    def test_falha_ao_iniciar_vira_erro_amigavel(self):
        rep, sd = novo_player()
        sd.falha_start = True
        with self.assertRaises(m.AudioIndisponivel) as cm:
            rep.tocar()
        self.assertEqual(cm.exception.codigo, "dispositivo")
        self.assertFalse(rep.tocando)
        self.assertEqual(len(sd.abertos()), 0)

    def test_sem_sounddevice_nao_derruba_o_player(self):
        def falha():
            raise ModuleNotFoundError("No module named 'sounddevice'")
        rep = m.Reprodutor(m.SoundDeviceBackend(importar=falha))
        dados, sr = faixa()
        m.ler_audio_estereo = lambda caminho: (dados, sr)
        rep.carregar("x.wav")                                        # carregar/medir continua funcionando
        self.assertIsNotNone(rep.medir())
        with self.assertRaises(m.AudioIndisponivel) as cm:
            rep.tocar()
        self.assertEqual(cm.exception.codigo, "sem_sounddevice")
        rep.descarregar()

    def test_medidores_acompanham_a_posicao(self):
        rep, sd = novo_player(dados=faixa(seg=3.0, amp=0.5))
        rep.tocar(); sd.abertos()[0].pump(44100)
        med = rep.medir()
        self.assertAlmostEqual(med["pico"][0], 20 * np.log10(0.5), delta=0.2)         # canal L: -6 dBFS
        self.assertAlmostEqual(med["pico"][1], 20 * np.log10(0.25), delta=0.2)        # canal R: -12 dBFS
        self.assertLess(med["lufs_m"], 0)
        self.assertIsNotNone(rep.espectro_bandas())
        rep.descarregar()


if __name__ == "__main__":
    unittest.main()
