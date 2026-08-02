const items = document.querySelector("#items");
const status = document.querySelector("#status");
const clear = document.querySelector("#clear");
const scan = document.querySelector("#scan");
const downloadAll = document.querySelector("#download-all");

function escaped(value) {
  const element = document.createElement("span");
  element.textContent = value;
  return element.innerHTML;
}

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

function quality(item) {
  if (item.width && item.height) return `${item.width}×${item.height}`;
  if (item.height) return `${item.height}p estimado pela URL`;
  return "resolução não informada";
}

function sizeHint(item) {
  if (!item.contentLength) return "";
  return ` · ${(item.contentLength / 1024 / 1024).toFixed(1)} MB`;
}

async function load(scanPage = false) {
  if (scanPage) {
    const scanResult = await browser.runtime.sendMessage({ type: "scan" });
    if (!scanResult.ok) setStatus(scanResult.error, true);
  }
  const response = await browser.runtime.sendMessage({ type: "list" });
  if (!response.ok) return setStatus(response.error, true);

  const videos = response.items.filter(item => item.kind === "video");
  if (!videos.length) {
    items.innerHTML = "";
    const streamNote = response.streamCount ? ` ${response.streamCount} stream(s) adaptativo(s) detectado(s), sem arquivo direto.` : "";
    return setStatus(`Nenhum vídeo direto detectado ainda.${streamNote} Reproduza ou role o conteúdo autorizado e use “Varrer página”.`);
  }

  const streamNote = response.streamCount ? ` · ${response.streamCount} stream(s) adaptativo(s) não baixável(is)` : "";
  setStatus(`${response.directCount} vídeo(s) direto(s) detectado(s) · ${response.bestCount} melhor(es) variante(s) para baixar${streamNote}.`);
  items.innerHTML = videos.map(item => `
    <article>
      <div>
        <b>${escaped(quality(item))}${escaped(sizeHint(item))}</b>
        <span title="${escaped(item.filename)}">${escaped(item.filename)}</span>
      </div>
      <button data-id="${item.id}">Baixar</button>
    </article>
  `).join("");

  items.querySelectorAll("button[data-id]").forEach(button => button.addEventListener("click", async () => {
    button.disabled = true;
    const response = await browser.runtime.sendMessage({ type: "download", id: button.dataset.id });
    setStatus(response.ok ? "Download enviado à fila do Firefox." : response.error, !response.ok);
    if (!response.ok) button.disabled = false;
  }));
}

clear.addEventListener("click", async () => {
  await browser.runtime.sendMessage({ type: "clear" });
  await load(false);
});

scan.addEventListener("click", async () => {
  scan.disabled = true;
  setStatus("Varrendo vídeos já carregados na página…");
  await load(true);
  scan.disabled = false;
});

downloadAll.addEventListener("click", async () => {
  downloadAll.disabled = true;
  setStatus("Enviando as melhores variantes diretas à fila do Firefox…");
  const result = await browser.runtime.sendMessage({ type: "download-best" });
  downloadAll.disabled = false;
  if (!result.ok) return setStatus(`${result.queued}/${result.total} baixado(s). ${result.failures.join(" | ")}`, true);
  setStatus(`${result.queued}/${result.total} melhor(es) variante(s) enviada(s) à fila do Firefox.`);
});

load(true).catch(error => setStatus(String(error), true));
