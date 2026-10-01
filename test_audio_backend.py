import unittest

from helpers import FakeSD, carregar_modulo

m, _ = carregar_modulo()


def novo_backend(sd=None):
    sd = sd or FakeSD()
    return m.SoundDeviceBackend(importar=lambda: sd), sd


class TestErros(unittest.TestCase):
    def test_traducao(self):
        casos = [
            (ModuleNotFoundError("No module named 'sounddevice'"), "sem_sounddevice"),
            (OSError("PortAudio library not found"), "sem_portaudio"),
            (RuntimeError("Error querying device -1"), "sem_saida"),
            (RuntimeError("Invalid sample rate"), "taxa"),
            (RuntimeError("Unanticipated host error"), "dispositivo"),
            (RuntimeError("qualquer coisa"), "desconhecido"),
        ]
        for exc, codigo in casos:
            e = m.traduzir_erro(exc)
            self.assertEqual(e.codigo, codigo)
            self.assertNotIn("No module named", e.resumo)          # o usuário nunca vê o texto técnico cru
            self.assertTrue(e.detalhe)                             # mas o detalhe fica para o log

    def test_sem_sounddevice_nao_derruba(self):
        def falha():
            raise ModuleNotFoundError("No module named 'sounddevice'")
        b = m.SoundDeviceBackend(importar=falha)
        ok, det = b.disponivel()
        self.assertFalse(ok)
        d = b.diagnostico()
        self.assertEqual(d["sounddevice"], "AUSENTE")
        self.assertEqual(d["estado"], "indisponível")
        self.assertEqual(d["codigo"], "sem_sounddevice")
        with self.assertRaises(m.AudioIndisponivel):
            b.listar_saidas()

    def test_recarregar_apos_instalar(self):
        estado = {"instalado": False}

        def importar():
            if not estado["instalado"]:
                raise ModuleNotFoundError("No module named 'sounddevice'")
            return FakeSD()
        b = m.SoundDeviceBackend(importar=importar)
        self.assertFalse(b.disponivel()[0])
        estado["instalado"] = True
        self.assertFalse(b.disponivel()[0])        # a falha fica em cache até o usuário pedir para testar de novo
        b.recarregar()
        self.assertTrue(b.disponivel()[0])

    def test_comando_de_instalacao(self):
        cmd = m.comando_instalar_sounddevice()
        self.assertEqual(cmd[1:4], ["-m", "pip", "install"])
        self.assertTrue(cmd[-1].startswith("sounddevice"))


class TestDispositivos(unittest.TestCase):
    def test_lista_somente_saidas_da_api_padrao(self):
        b, _ = novo_backend()
        nomes = [d.nome for d in b.listar_saidas()]
        self.assertEqual(nomes, ["Speakers (Realtek)", "Headphones (USB Audio)"])    # sem microfone, sem duplicata WASAPI
        self.assertTrue(b.listar_saidas()[0].api == "MME")

    def test_resolver_dispositivo(self):
        b, _ = novo_backend()
        self.assertEqual(b.resolver_dispositivo(None), (None, None))
        self.assertEqual(b.resolver_dispositivo(m.SISTEMA_PADRAO), (None, None))
        self.assertEqual(b.resolver_dispositivo("Headphones (USB Audio)"), (1, None))
        idx, aviso = b.resolver_dispositivo("Fone Bluetooth que sumiu")
        self.assertIsNone(idx)
        self.assertIn("não está mais disponível", aviso)

    def test_taxa_do_arquivo_ou_alternativa(self):
        b, sd = novo_backend()
        self.assertEqual(b.escolher_taxa(None, 44100), (44100, False))
        self.assertEqual(b.escolher_taxa(None, 48000), (48000, False))
        self.assertEqual(b.escolher_taxa(None, 96000), (48000, True))      # placa não aceita 96k: cai para 48k
        sd.taxas = {44100}
        self.assertEqual(b.escolher_taxa(None, 88200), (44100, True))
        sd.taxas = set()
        with self.assertRaises(m.AudioIndisponivel):
            b.escolher_taxa(None, 44100)

    def test_diagnostico_ok(self):
        b, _ = novo_backend()
        d = b.diagnostico(None, 44100)
        self.assertEqual((d["player"], d["estado"]), ("OK", "disponível"))
        self.assertEqual(d["dispositivo"], "Speakers (Realtek)")
        self.assertEqual(d["samplerate"], "44100 Hz")
        texto = m.formatar_diagnostico(d)
        for trecho in ("Player interno: OK", "sounddevice: OK (0.5.1)", "PortAudio: OK", "Canais: 2", "Backend: MME"):
            self.assertIn(trecho, texto)
        self.assertEqual(m.resumo_estado(d), "✓ Áudio disponível")

    def test_diagnostico_sem_saida(self):
        b, _ = novo_backend(FakeSD(dispositivos=[]))
        d = b.diagnostico()
        self.assertEqual((d["dispositivo"], d["estado"], d["codigo"]), ("NENHUM", "indisponível", "sem_saida"))
        self.assertIn("Verifique", m.resumo_estado(d))

    def test_diagnostico_dispositivo_salvo_sumiu(self):
        b, _ = novo_backend()
        d = b.diagnostico("Fone que sumiu", 44100)
        self.assertEqual(d["estado"], "disponível")
        self.assertIn("não está mais disponível", d["aviso"])


class TestStreamETom(unittest.TestCase):
    def test_tom_de_teste(self):
        b, sd = novo_backend()
        # o stream falso não toca sozinho: um thread bombeia o callback como o PortAudio faria
        import threading, time

        def bombear():
            t0 = time.time()
            while time.time() - t0 < 3:
                if sd.streams and sd.streams[-1].active:
                    sd.streams[-1].pump(2048)
                    if not sd.streams[-1].active:
                        return
                time.sleep(0.005)
        t = threading.Thread(target=bombear); t.start()
        r = b.testar_saida(None)
        t.join()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["taxa"], 48000)
        self.assertEqual(len(sd.abertos()), 0)                      # nada fica aberto

    def test_teste_com_falha_no_start(self):
        b, sd = novo_backend()
        sd.falha_start = True
        r = b.testar_saida(None)
        self.assertFalse(r["ok"])
        self.assertTrue(r["detalhe"])
        self.assertEqual(len(sd.abertos()), 0)

    def test_fechar_seguro_repetido(self):
        b, sd = novo_backend()
        s = b.criar_saida(None, 44100, lambda *a: None)
        b.fechar_seguro(s)
        b.fechar_seguro(s)
        b.fechar_seguro(None)
        self.assertEqual(len(sd.abertos()), 0)

    def test_abrir_dispositivo_invalido(self):
        b, _ = novo_backend()
        with self.assertRaises(m.AudioIndisponivel) as cm:
            b.criar_saida(99, 44100, lambda *a: None)
        self.assertEqual(cm.exception.codigo, "dispositivo")


if __name__ == "__main__":
    unittest.main()
