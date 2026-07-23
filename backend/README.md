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

O fluxo de produção é composto diretamente pelo backend: ele renderiza cenas em fragmentos curtos, concatena-os na entrega final, aplica trilha/SFX e publica o MP4. Nenhum arquivo de `scripts/` é chamado pela API. A esteira aceita narrativas de até 20 minutos, processa um render pesado por vez e exige, por padrão, 8 GiB livres no workspace (ajustável com `SYNTHREEL_HORIZONTAL_MIN_FREE_DISK_GIB`).
