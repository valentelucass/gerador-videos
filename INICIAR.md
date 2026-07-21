# Iniciar o painel local

Abra dois terminais na raiz deste projeto.

No primeiro:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000
```

No segundo:

```powershell
cd frontend
npm install
npm run dev
```

Abra o endereço mostrado pelo Vite, normalmente `http://localhost:5173`.

Fluxo: importe ou cole o JSON, arraste as imagens para `assets/images`, escolha o fundo animado e clique em **Gerar vídeo**. A voz é sintetizada pelo Edge TTS em `-10%`, e a duração final segue a duração real da narração — inclusive para vídeos de um minuto ou mais.

Os vídeos e seus manifestos ficam em `workspace/lotes_horizontais/<id-do-lote>/`.
