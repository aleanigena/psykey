# 🎧 PsyKey

### BPM • Key • Camelot • Spectrum • Player • Loudness

**PsyKey** é uma ferramenta desktop para **análise, organização e preparação de bibliotecas musicais**, desenvolvida especialmente para DJs, produtores e artistas de música eletrônica.

O projeto nasceu como **BPM Renamer** e evoluiu para uma plataforma completa de análise musical, com foco especial em gêneros de andamento elevado como **Psytrance, Darkpsy, Forest, Hi-Tech, Psycore e Hardcore**.

> **Analyze your tracks. Organize your library. Prepare your set.**

---

## 🚀 O que é o PsyKey?

O PsyKey permite analisar uma coleção de músicas e obter automaticamente informações importantes para organização e preparação de sets:

* 🎚️ BPM
* 🎼 Tonalidade
* 🔢 Código Camelot
* 📊 Confiança da análise
* 🌈 Espectro
* 〰️ Forma de onda
* 🔊 Loudness / LUFS
* 📈 VU Meter
* ⛰️ Peak / True Peak
* 🎧 Reprodução de áudio
* 📁 Organização de arquivos
* 🏷️ Renomeamento automático
* ↩️ Desfazer operações
* 🎛️ Preparação de biblioteca para DJ

---

# ✨ Principais recursos

## 🎚️ 1. Detecção avançada de BPM

O PsyKey foi desenvolvido pensando especialmente em música eletrônica com andamento relativamente estável.

A análise utiliza a periodicidade do envelope de ataques/transientes, combinando informações da região grave e do espectro da faixa.

Isso permite trabalhar com valores muito altos de BPM e reduzir problemas de interpretação como:

```text
75 BPM  ↔ 150 BPM  ↔ 300 BPM
```

### Perfis disponíveis

| Perfil             |                 Faixa |
| ------------------ | --------------------: |
| Psytrance geral    |           135–200 BPM |
| Forest / Full-on   |           135–155 BPM |
| Darkpsy            |           145–175 BPM |
| Hi-Tech            |           165–195 BPM |
| Psycore / Hardcore |           190–350 BPM |
| Techno             |           115–145 BPM |
| Extremo            |           200–500 BPM |
| Personalizado      | Definido pelo usuário |

O programa também possui diferentes modos de análise:

* **Preciso**
* **Máximo**
* **Rápido**

---

# 🎼 2. Detecção de tonalidade

O PsyKey analisa o conteúdo harmônico da música e estima:

* Nota;
* Maior / menor;
* Tonalidade principal;
* Confiança;
* Alternativas possíveis.

O resultado pode ser utilizado diretamente no nome do arquivo.

Exemplo:

```text
148,00 - 8A Am - Minha Música.mp3
```

---

# 🔢 3. Sistema Camelot

A tonalidade detectada pode ser convertida automaticamente para o sistema Camelot.

Exemplo:

```text
Am → 8A
C  → 8B
```

Isso facilita a preparação de sets e a seleção de faixas harmonicamente compatíveis.

O PsyKey também apresenta outras possibilidades quando a análise não é suficientemente conclusiva.

> Em gêneros extremamente percussivos, como algumas produções de Darkpsy e Psycore, a tonalidade pode ser naturalmente ambígua. O PsyKey mostra alternativas para facilitar a conferência.

---

# 🌈 4. Análise de espectro

O programa possui análise espectral com representação logarítmica de frequências.

O analisador trabalha com uma faixa visual de aproximadamente:

```text
20 Hz → 35 kHz
```

limitada naturalmente pela frequência de Nyquist do arquivo.

A análise utiliza FFT e janela Blackman-Harris para obter uma visualização detalhada do conteúdo espectral.

Isso permite observar:

* Subgrave;
* Grave;
* Médios;
* Presença;
* Agudos;
* Distribuição energética;
* Alterações espectrais durante a reprodução.

---

# 〰️ 5. Forma de onda

O PsyKey apresenta uma forma de onda visual da música.

A posição atual da reprodução é indicada na waveform e pode ser utilizada para navegar pela faixa.

```text
[───────────────●────────────────────]
                ↑
             posição
```

Também é possível clicar na forma de onda para saltar diretamente para outro ponto da música.

---

# 🎧 6. Player interno

O PsyKey agora possui um player de áudio integrado.

É possível:

* ▶ Reproduzir;
* ⏸ Pausar;
* ⏹ Parar;
* ⏩ Navegar pela faixa;
* Selecionar dispositivo de saída;
* Visualizar waveform;
* Visualizar espectro em tempo real;
* Monitorar níveis de áudio.

O sistema também pode trocar o dispositivo de saída sem perder a posição atual da música.

---

# 📈 7. Medidores profissionais

Durante a reprodução, o PsyKey calcula diferentes indicadores de nível.

### Medição disponível

* Peak;
* True Peak;
* VU;
* LUFS Momentary;
* LUFS Short-Term;
* LUFS Integrated.

O sistema inclusive compensa a latência da saída de áudio para que os medidores acompanhem a posição realmente audível.

Isso transforma o player em uma pequena estação de **monitoramento e inspeção técnica da faixa**.

---

# 📊 8. Analisador em tempo real

Durante a reprodução, o espectro pode ser atualizado em tempo real.

O analisador utiliza FFT e apresenta bandas de frequência em escala logarítmica.

O resultado é uma visualização semelhante a ferramentas encontradas em softwares de produção e equipamentos de áudio.

---

# 📁 9. Organização da biblioteca

O PsyKey permite adicionar:

* Arquivos individuais;
* Pastas;
* Subpastas;
* Grandes coleções musicais.

Formatos atualmente suportados:

```text
.mp3
.flac
.wav
```

O programa identifica arquivos que já possuem BPM no início do nome para evitar processamento desnecessário.

---

# 🏷️ 10. Renomeamento inteligente

O PsyKey pode transformar:

```text
Minha Música.mp3
```

em:

```text
148,00 - Minha Música.mp3
```

ou:

```text
148,00 - 8A Am - Minha Música.mp3
```

O usuário pode escolher como a tonalidade será adicionada:

* BPM + Camelot + nota;
* BPM + Camelot;
* BPM + nota;
* somente BPM.

Os formatos de tonalidade são configuráveis no projeto.

---

# ↩️ 11. Desfazer

Renomeamentos são realizados em lotes.

O PsyKey mantém um histórico para permitir:

```text
↩ Desfazer último lote
```

Isso reduz o risco de alterações acidentais em bibliotecas grandes.

---

# 🖱️ 12. Menu de contexto

Clique com o botão direito em uma faixa para acessar rapidamente:

* 🎧 Copiar para pasta de sets;
* 📂 Abrir pasta original;
* 🎧 Reproduzir no player interno;
* ▶ Reproduzir no player padrão;
* 🎼 Ver espectro e tom;
* ✏ Corrigir tonalidade;
* ⚙ Escolher pasta de sets;
* 🗑 Remover da lista.

---

# 🗂️ 13. Organização para montagem de sets

A biblioteca pode ser organizada por diferentes critérios:

```text
Tom → BPM
BPM → Tom
Somente Tom
Somente BPM
Nome do arquivo
```

Isso permite transformar o PsyKey em uma ferramenta de preparação de biblioteca antes de um set.

---

# 🎛️ 14. Foco em música eletrônica

Embora possa analisar diferentes tipos de música, o PsyKey foi especialmente projetado pensando em:

* Psytrance;
* Darkpsy;
* Forest;
* Full-on;
* Hi-Tech;
* Psycore;
* Hardcore;
* Techno;
* música eletrônica de BPM elevado.

O objetivo não é apenas detectar BPM.

O objetivo é ajudar o DJ a responder:

> **“O que é essa faixa e onde ela se encaixa no meu set?”**

---

# 🛡️ 15. Diagnóstico de áudio

Problemas de reprodução não simplesmente encerram o programa.

O PsyKey possui diagnóstico do sistema de áudio e verifica:

* `sounddevice`;
* PortAudio;
* dispositivos de saída;
* taxa de amostragem;
* disponibilidade do backend.

Quando ocorre um problema, o programa apresenta uma janela explicando a causa e oferecendo ações como:

* **Testar novamente**
* **Abrir diagnóstico**
* **Instalar componente**
* **Fechar**

---

# 🧪 16. Autoteste

O projeto possui um sistema interno de autoteste que verifica componentes importantes da aplicação.

Entre os testes estão:

* NumPy;
* SciPy;
* SoundFile;
* Librosa;
* SoundDevice;
* detecção de BPM;
* detecção de tonalidade;
* processamento paralelo;
* medição de LUFS;
* espectro;
* waveform.

O teste utiliza uma faixa sintética conhecida e verifica se o BPM esperado é aproximadamente 148 BPM.

---

# ⚡ Arquitetura

O PsyKey utiliza processamento assíncrono e paralelo para evitar que análises pesadas travem a interface.

A aplicação utiliza:

* Python;
* Tkinter / ttk;
* NumPy;
* SciPy;
* Librosa;
* SoundFile;
* SoundDevice;
* ThreadPoolExecutor;
* ProcessPoolExecutor;
* FFT;
* processamento digital de sinais.

O projeto também possui tratamento específico para execução empacotada com PyInstaller.

---

# 💾 Configurações

As configurações do programa são armazenadas automaticamente.

No Windows, os dados ficam em:

```text
%APPDATA%\PsyKey
```

O projeto também possui migração das configurações antigas do BPM Renamer para o PsyKey.

---

# 📝 Logs e diagnóstico

O programa mantém registros técnicos para facilitar a identificação de problemas.

Os logs não precisam expor os caminhos pessoais das músicas e são utilizados principalmente para diagnóstico.

Em caso de erro inesperado, o PsyKey registra os detalhes em arquivo de log e apresenta uma mensagem ao usuário.

---

# 🖥️ Compatibilidade

O projeto foi desenvolvido principalmente visando:

### Windows

O projeto possui suporte para geração de executável através de **PyInstaller** e script de build para Windows.

Também pode ser executado diretamente pelo código-fonte em ambientes Python compatíveis.

---

# 🚀 Instalação pelo código-fonte

Clone o repositório:

```bash
git clone https://github.com/SEU-USUARIO/psykey.git
cd psykey
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

Execute:

```bash
python bpm_renamer_gui.py
```

---

# 📦 Executável Windows

O projeto pode ser empacotado como aplicativo Windows utilizando:

```text
build_windows.bat
```

O objetivo é permitir que usuários finais utilizem o PsyKey sem precisar instalar Python ou configurar manualmente o ambiente.

---

# 🗃️ Estrutura sugerida do repositório

```text
PsyKey/
│
├── bpm_renamer_gui.py
├── requirements.txt
├── build_windows.bat
├── LEIAME.txt
├── LICENSE
├── README.md
├── icone.ico
│
├── screenshots/
│   ├── bpm-analysis.png
│   ├── spectrum-key.png
│   ├── player.png
│   └── loudness-meter.png
│
└── docs/
    ├── installation.md
    └── troubleshooting.md
```

---

# 🛣️ Roadmap

### Biblioteca

* [x] Detecção de BPM
* [x] Detecção de tonalidade
* [x] Camelot
* [x] Renomeamento automático
* [x] Organização por BPM
* [x] Organização por tonalidade
* [x] Desfazer
* [x] Pasta de sets

### Análise

* [x] Espectrograma
* [x] Waveform
* [x] FFT
* [x] Analisador espectral
* [x] Peak
* [x] True Peak
* [x] VU
* [x] LUFS

### Reprodução

* [x] Player interno
* [x] Pause / Stop
* [x] Seek
* [x] Seleção de dispositivo
* [x] Diagnóstico de áudio
* [x] Reprodução pelo player padrão

### Próximas versões

* [ ] Drag & Drop
* [ ] Exportação CSV
* [ ] Exportação JSON
* [ ] Escrita de BPM nos metadados
* [ ] Escrita de Key nos metadados
* [ ] Escrita de Camelot nos metadados
* [ ] Leitura de ID3/FLAC tags
* [ ] Detecção de gênero
* [ ] Energy Level
* [ ] Compatibilidade com mais formatos
* [ ] Análise em lote de BPM + Key
* [ ] Sistema avançado de playlists
* [ ] Exportação para software de DJ
* [ ] Suporte a Rekordbox
* [ ] Suporte a Serato
* [ ] Suporte a Traktor
* [ ] Tema escuro aprimorado
* [ ] Internacionalização
* [ ] Testes automatizados completos
* [ ] Releases automáticos no GitHub

---

# 🎯 Filosofia do projeto

O PsyKey não pretende substituir um DJ.

Ele pretende **tirar do DJ o trabalho repetitivo**.

Em vez de passar horas:

```text
abrindo música
      ↓
ouvindo
      ↓
descobrindo BPM
      ↓
descobrindo tom
      ↓
renomeando
      ↓
organizando
      ↓
testando volume
```

o PsyKey concentra essas tarefas em uma única ferramenta:

```text
                    ┌──────────────┐
                    │    MÚSICA    │
                    └──────┬───────┘
                           ↓
             ┌─────────────────────────┐
             │         PSYKEY          │
             └─────────────────────────┘
                ↓       ↓       ↓
              BPM      KEY    SPECTRUM
                ↓       ↓       ↓
             CAMELOT   LUFS    WAVEFORM
                └───────┬───────┘
                        ↓
                ┌───────────────┐
                │   SUA BIBLIOTECA │
                └───────┬───────┘
                        ↓
                    🎧 SET
```

---

# 📸 Screenshots

Adicione capturas de tela em:

```text
/screenshots/
```

Recomenda-se mostrar:

1. Tela principal com biblioteca;
2. Análise BPM;
3. Análise de espectro + Camelot;
4. Player;
5. Medidores LUFS/VU;
6. Menu de contexto;
7. Diagnóstico de áudio.

---

# 🤝 Contribuição

Contribuições são bem-vindas.

Sugestões, correções, melhorias de desempenho e novos recursos podem ser enviados através de Issues e Pull Requests.

---

# 📜 Licença

Escolha a licença que será utilizada pelo projeto.

Uma opção simples para software open-source é:

```text
MIT License
```

---

# 🎧 PsyKey

**BPM. Key. Camelot. Spectrum. Loudness.**

**Organize your music. Understand your tracks. Build better sets.**
