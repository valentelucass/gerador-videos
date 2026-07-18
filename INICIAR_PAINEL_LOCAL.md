# SynthReel: painel local

O repositório é organizado em `frontend/` (React + TypeScript) e `backend/`
(Python, pipeline, clonador de voz e dados locais). O SynthReel usa FastAPI apenas
como ponte local para os motores Python de preparação e renderização.

Em dois terminais, na raiz do projeto:

```powershell
python -m uvicorn backend.src.api.app:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
cd frontend
npm run dev
```

Abra `http://127.0.0.1:5173`.

O painel não expõe a API na rede: ambos os serviços ficam presos a `127.0.0.1`.
O fluxo horizontal continua HITL: o preparo cria prompts/slots, a curadoria
humana coloca os assets reais e só então o renderizador pode concluir o MP4
em `backend/workspace/output/horizontal/`.
