# SynthReel horizontal

Backend do gerador de vídeos horizontais baseado em JSON, imagens estáticas, TTS neural e FFmpeg.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000
```

As imagens de cena são importadas para `assets/images/`; os fundos selecionáveis ficam em `fundos/`. O endpoint de renderização gera a narração por `TTSNeuralEngine` e salva cada lote isolado em `workspace/lotes_horizontais/`.
