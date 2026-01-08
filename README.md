# API de Narração de Vídeo (TTS & Legendas)

Esta é uma API assíncrona construída com **FastAPI** que automatiza a narração de vídeos. Ela recebe um vídeo e um roteiro, gera o áudio usando **Edge TTS** (gratuito), mixa o áudio baixando o volume original (ducking) e adiciona legendas no estilo "viral".

Ideal para automações com **n8n**, Make ou scripts personalizados.

## Funcionalidades

*   🎙️ **TTS Gratuito:** Usa a biblioteca `edge-tts` (Voz padrão: Antonio - pt-BR).
*   📉 **Audio Ducking:** O volume do vídeo original diminui automaticamente quando a narração começa.
*   ⏱️ **Ajuste Automático:** Se o texto for longo para o tempo definido, o áudio é levemente acelerado para caber.
*   📝 **Legendas Virais:** Opção de queimar legendas no vídeo (Fonte grande, borda preta).
*   📂 **Entrada Flexível:** Aceita upload de arquivo ou URL direta do vídeo.

---

## 🚀 Instalação e Execução Local

### Pré-requisitos
*   Python 3.8+
*   FFmpeg instalado no sistema (ou a lib baixará um binário automaticamente)

### 1. Instalar Dependências

```bash
# Clone o repositório
git clone <seu-repo>
cd <seu-repo>

# Crie um ambiente virtual
python -m venv venv

# Ative o ambiente virtual
source venv/bin/activate  # Linux/Mac
# Windows (PowerShell):
#   .\venv\Scripts\Activate.ps1
# Windows (cmd.exe):
#   .\venv\Scripts\activate.bat

# Instale os pacotes
pip install -r requirements.txt
```

### 2. Rodar o Servidor

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
O servidor iniciará em `http://127.0.0.1:8000`.

Observação importante: em projetos que escrevem arquivos dentro do repo (como `temp/` e `output/`), usar `--reload` pode reiniciar o servidor a cada arquivo criado e cancelar o processamento.
Se quiser hot-reload, rode assim (excluindo `temp/` e `output/`):

```bash
python -m uvicorn main:app --reload --reload-exclude temp --reload-exclude output --host 127.0.0.1 --port 8000
```

Se você receber `WinError 10013` (permissão/porta proibida), tente outra porta (por ex. 8001):

```bash
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

E rode o teste apontando para a porta escolhida:

```bash
set API_URL=http://127.0.0.1:8001
python test_api.py
```

---

## 📖 Documentação da API

### 1. Iniciar Processamento (`POST /narrate`)

Envia o vídeo e o script para a fila de processamento.

**Parâmetros (Form Data):**
*   `file`: (Opcional) Arquivo de vídeo (.mp4, .mov, etc).
*   `video_url`: (Opcional) URL direta para baixar o vídeo (se não enviar arquivo).
*   `script`: (Obrigatório) JSON em string com a lista de falas.
*   `add_subtitles`: (Opcional) `true` ou `false` (padrão `false`).
*   `voice`: (Opcional) Nome da voz do Edge TTS (padrão `pt-BR-AntonioNeural`).

**Exemplo de Script JSON:**
```json
[
  {
    "start": "00:00",
    "end": "00:05",
    "text": "Este é o início do vídeo, olha que legal."
  },
  {
    "start": "00:06",
    "end": "00:10",
    "text": "Aqui a narração continua sincronizada."
  }
]
```

**Retorno:**
```json
{
  "job_id": "uuid-do-trabalho",
  "status": "queued"
}
```

### 2. Verificar Status (`GET /status/{job_id}`)

Verifica se o vídeo está pronto.

**Retorno:**
```json
{
  "status": "processing" // ou "completed", "failed"
}
```

### 3. Baixar Vídeo (`GET /download/{job_id}`)

Baixa o arquivo final processado.

---

## 💻 Exemplos de Uso

### Exemplo com cURL

**Upload de Arquivo Local:**
```bash
curl -X POST "http://127.0.0.1:8000/narrate" \
  -F "file=@meu_video.mp4" \
  -F "add_subtitles=true" \
  -F 'script=[{"start":"00:00","end":"00:05","text":"Teste de narração."}]'
```

**Usando URL de Vídeo:**
```bash
curl -X POST "http://127.0.0.1:8000/narrate" \
  -F "video_url=https://exemplo.com/video.mp4" \
  -F "add_subtitles=true" \
  -F 'script=[{"start":"00:00","end":"00:05","text":"Teste de narração."}]'
```

### Exemplo com Python (requests)

```python
import requests
import json

url = "http://127.0.0.1:8000/narrate"
script = [
    {"start": "00:00", "end": "00:05", "text": "Olá mundo, este é um teste."}
]

# Enviando arquivo
files = {'file': open('video.mp4', 'rb')}
data = {
    'script': json.dumps(script),
    'add_subtitles': True
}

response = requests.post(url, files=files, data=data)
job_id = response.json()['job_id']
print(f"Job ID: {job_id}")
```

---

## ☁️ Deploy no Render

Esta API está pronta para ser hospedada no **Render.com**.

1.  Crie uma conta no Render.
2.  Clique em **"New +"** -> **"Web Service"**.
3.  Conecte seu repositório do GitHub/GitLab.
4.  Configure:
    *   **Runtime:** Python 3
    *   **Build Command:** `pip install -r requirements.txt`
    *   **Start Command:** `uvicorn main:app --host 0.0.0.0 --port $PORT` (Render/Linux)
  *   **Env Vars (recomendado):**
    * `JOB_RETENTION_SECONDS` (padrão: 900) — tempo máximo para manter arquivos/metadata.
    * `CLEANUP_INTERVAL_SECONDS` (padrão: 60) — intervalo do coletor de lixo.
    * `DELETE_OUTPUT_AFTER_DOWNLOAD` (padrão: 1) — apaga o `output/<job_id>.mp4` após o primeiro download.
5.  Clique em **Create Web Service**.

Observação sobre armazenamento:
- `temp/` é removido automaticamente ao final de cada job.
- `output/` por padrão é apagado após download (e também é limpo pelo job de cleanup após `JOB_RETENTION_SECONDS`).

**Observação sobre o FFmpeg no Render:**
A biblioteca `imageio-ffmpeg` incluída no `requirements.txt` geralmente baixa um binário estático do FFmpeg automaticamente, o que deve funcionar na maioria dos ambientes Linux do Render sem configuração extra.

Se houver problemas, você pode precisar adicionar o FFmpeg ao ambiente do Render, mas para este projeto a dependência Python deve resolver.
