"""Carrega bpm_renamer_gui.py com tkinter falso (sem abrir janelas) e fornece um sounddevice falso para os testes."""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "tests"))
import fake_tk  # noqa: E402

_cache = {}


def carregar_modulo():
    if "m" not in _cache:
        os.environ["APPDATA"] = tempfile.mkdtemp()                 # config/logs de teste isolados
        _cache["estado"] = fake_tk.instalar()
        spec = importlib.util.spec_from_file_location("bpm_renamer_gui", RAIZ / "bpm_renamer_gui.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bpm_renamer_gui"] = mod
        spec.loader.exec_module(mod)
        _cache["m"] = mod
    return _cache["m"], _cache["estado"]


class PortAudioError(Exception):
    pass


class CallbackStop(Exception):
    pass


class FakeStream:
    """OutputStream falso: não toca nada; 'pump' simula o PortAudio chamando o callback."""

    def __init__(self, fake, samplerate, channels, dtype, device, callback, finished_callback):
        self.fake, self.samplerate, self.device = fake, samplerate, device
        self.callback, self.finished_callback = callback, finished_callback
        self.active, self.closed, self.latency = False, False, 0.05

    def start(self):
        if self.closed:
            raise PortAudioError("Stream is closed")
        if self.fake.falha_start:
            raise PortAudioError("Unanticipated host error")
        self.active = True

    def stop(self):
        self.active = False

    def abort(self):
        was, self.active = self.active, False
        if was and self.finished_callback:
            self.finished_callback()

    def close(self):
        self.active, self.closed = False, True

    def pump(self, frames):
        import numpy as np
        out = np.zeros((frames, 2), dtype=np.float32)
        try:
            self.callback(out, frames, None, None)
        except CallbackStop:
            self.active = False
            if self.finished_callback:
                self.finished_callback()
        return out

    def desconectar(self):
        """Simula o dispositivo sumindo: o stream para sozinho e o PortAudio chama o callback de fim."""
        self.active = False
        if self.finished_callback:
            self.finished_callback()


class FakeSD:
    """Módulo sounddevice falso, configurável."""
    __version__ = "0.5.1"
    PortAudioError = PortAudioError
    CallbackStop = CallbackStop

    def __init__(self, dispositivos=None, taxas=(44100, 48000)):
        self.dispositivos = dispositivos if dispositivos is not None else [
            {"name": "Speakers (Realtek)", "max_output_channels": 2, "hostapi": 0, "default_samplerate": 48000.0},
            {"name": "Headphones (USB Audio)", "max_output_channels": 2, "hostapi": 0, "default_samplerate": 44100.0},
            {"name": "Microphone (Realtek)", "max_output_channels": 0, "hostapi": 0, "default_samplerate": 44100.0},
            {"name": "Speakers (Realtek) WASAPI", "max_output_channels": 2, "hostapi": 1, "default_samplerate": 48000.0},
        ]
        self.taxas, self.falha_start, self.streams = set(taxas), False, []
        self.default = types.SimpleNamespace(device=(2, 0), hostapi=0)

    def query_devices(self, device=None):
        if device is None:
            return list(self.dispositivos)
        if device < 0 or device >= len(self.dispositivos):
            raise PortAudioError("Error querying device -1")
        return self.dispositivos[device]

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]

    def check_output_settings(self, device=None, channels=None, dtype=None, samplerate=None):
        if device is not None and (device < 0 or device >= len(self.dispositivos)):
            raise PortAudioError("Invalid device")
        if samplerate not in self.taxas:
            raise PortAudioError("Invalid sample rate")

    def get_portaudio_version(self):
        return (1246720, "PortAudio V19.7.0-devel (fake)")

    def OutputStream(self, samplerate=None, channels=None, dtype=None, device=None, callback=None,
                     finished_callback=None):
        if device is not None and (device < 0 or device >= len(self.dispositivos)):
            raise PortAudioError("Invalid device")
        s = FakeStream(self, samplerate, channels, dtype, device, callback, finished_callback)
        self.streams.append(s)
        return s

    def abertos(self):
        return [s for s in self.streams if not s.closed]

    def _terminate(self):
        pass

    def _initialize(self):
        pass
