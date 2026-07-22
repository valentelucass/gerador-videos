# Iniciar o SynthReel local

Em um único terminal:

```powershell
cd frontend
npm install
npm start
```

O comando inicia o backend em `http://localhost:8000` e o painel Vite juntos.
Ao pressionar `Ctrl+C` ou encerrar esse terminal, os dois processos são
encerrados.

Não execute um segundo `npm start` enquanto o primeiro estiver aberto. O
iniciador agora avisa qual porta está ocupada, em vez de abrir o painel em outra
porta.

Abra o endereço mostrado pelo Vite, normalmente `http://localhost:5173`.

Fluxo: importe ou cole o JSON, arraste as imagens para `assets/images`, escolha o fundo animado e clique em **Gerar vídeo**. A voz é sintetizada pelo Edge TTS em `-10%`, e a duração final segue a duração real da narração — inclusive para vídeos de um minuto ou mais.

Os vídeos e seus manifestos ficam em `workspace/lotes_horizontais/<id-do-lote>/`.
