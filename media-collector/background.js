/* Coletor local de vídeo direto. Não descriptografa, converte, reconstrói
 * streams segmentados, remove marcas d'água ou contorna bloqueios. */
const mediaByTab = new Map();

const VIDEO_EXTENSION = /\.(?:mp4|webm|mov|m4v|avi|mkv)(?:$|[?#])/i;
const STREAM_MANIFEST = /\.(?:m3u8|mpd)(?:$|[?#])/i;
const VIDEO_RESOLUTION = /(?:^|[^\d])(4320|2160|1440|1080|720|540|480|360)p?(?:[^\d]|$)/i;

function headerValue(headers, name) {
  return (headers || []).find(header => header.name.toLowerCase() === name)?.value || "";
}

function mediaKind(url, responseHeaders = []) {
  const contentType = headerValue(responseHeaders, "content-type").toLowerCase();
  if (contentType.startsWith("video/") || VIDEO_EXTENSION.test(url)) return "video";
  if (STREAM_MANIFEST.test(url) || /application\/(?:vnd\.apple\.mpegurl|dash\+xml)/i.test(contentType)) return "stream";
  return null;
}

function resolutionFromUrl(url) {
  let path = "";
  try {
    const parsed = new URL(url);
    path = decodeURIComponent(`${parsed.pathname} ${parsed.search}`);
    const width = Number(parsed.searchParams.get("width") || parsed.searchParams.get("w") || 0);
    const height = Number(parsed.searchParams.get("height") || parsed.searchParams.get("h") || 0);
    if (width > 0 && height > 0) return { width, height };
  } catch (_) {
    return { width: 0, height: 0 };
  }

  const dimension = path.match(/(?:^|[?&_.\-])(\d{3,5})[xX](\d{3,5})(?:$|[?&_.\-])/i);
  if (dimension) return { width: Number(dimension[1]), height: Number(dimension[2]) };
  if (/(?:^|[^a-z0-9])8k(?:[^a-z0-9]|$)/i.test(path)) return { width: 7680, height: 4320 };
  if (/(?:^|[^a-z0-9])4k(?:[^a-z0-9]|$)/i.test(path)) return { width: 3840, height: 2160 };
  if (/(?:^|[^a-z0-9])2k(?:[^a-z0-9]|$)/i.test(path)) return { width: 2560, height: 1440 };
  const heightMatch = path.match(VIDEO_RESOLUTION);
  return { width: 0, height: heightMatch ? Number(heightMatch[1]) : 0 };
}

function filenameFromUrl(url) {
  try {
    const name = decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
    if (/^[\w .()\-]{1,140}\.[a-z0-9]{2,5}$/i.test(name)) return name;
  } catch (_) {
    // URL inválida não deve interromper a observação da aba.
  }
  return `video-${new Date().toISOString().replace(/[:.]/g, "-")}.mp4`;
}

function qualityScore(item) {
  const pixels = Number(item.width || 0) * Number(item.height || 0);
  const height = Number(item.height || 0);
  const sizeHint = Math.min(Number(item.contentLength || 0) / 1024 / 1024, 999);
  return pixels + (height * 1000) + sizeHint;
}

function resolutionLabel(item) {
  if (item.width && item.height) return `${item.width}×${item.height}`;
  if (item.height) return `${item.height}p estimado`;
  return "resolução não informada";
}

function variantKey(item) {
  try {
    const parsed = new URL(item.url);
    // Agrupa somente variantes com o mesmo caminho, removendo sufixos claros
    // de qualidade. Caminhos diferentes continuam como vídeos distintos.
    const normalizedPath = decodeURIComponent(parsed.pathname)
      .replace(/([_.\-])(?:4320|2160|1440|1080|720|540|480|360)p(?=\.[a-z0-9]+$)/i, "")
      .replace(/([_.\-])(?:8k|4k|2k)(?=\.[a-z0-9]+$)/i, "")
      .toLowerCase();
    return `${parsed.origin}${normalizedPath}`;
  } catch (_) {
    return item.url;
  }
}

function sortItems(items) {
  return items.sort((left, right) => qualityScore(right) - qualityScore(left) || right.detectedAt - left.detectedAt);
}

function upsert(tabId, url, options = {}) {
  if (tabId < 0 || !/^https?:/i.test(url)) return null;
  const kind = options.kind || mediaKind(url, options.responseHeaders) || (options.fromVideoElement ? "video" : null);
  if (!kind) return null;

  const list = mediaByTab.get(tabId) || [];
  const inferred = resolutionFromUrl(url);
  const candidate = {
    id: crypto.randomUUID(),
    url,
    kind,
    filename: filenameFromUrl(url),
    width: Number(options.width || inferred.width || 0),
    height: Number(options.height || inferred.height || 0),
    contentLength: Number(options.contentLength || headerValue(options.responseHeaders, "content-length") || 0),
    detectedAt: Date.now(),
    sources: [options.source || "network"]
  };

  const existing = list.find(item => item.url === url);
  if (existing) {
    existing.width = Math.max(existing.width || 0, candidate.width || 0);
    existing.height = Math.max(existing.height || 0, candidate.height || 0);
    existing.contentLength = Math.max(existing.contentLength || 0, candidate.contentLength || 0);
    if (!existing.sources.includes(candidate.sources[0])) existing.sources.push(candidate.sources[0]);
    mediaByTab.set(tabId, sortItems(list));
    return existing;
  }

  list.push(candidate);
  mediaByTab.set(tabId, sortItems(list).slice(0, 300));
  return candidate;
}

function collect(details) {
  upsert(details.tabId, details.url, { responseHeaders: details.responseHeaders, source: "network" });
}

browser.webRequest.onHeadersReceived.addListener(
  collect,
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

browser.tabs.onRemoved.addListener(tabId => mediaByTab.delete(tabId));

async function activeTab() {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function scanLoadedPage(tab) {
  const [found = []] = await browser.tabs.executeScript(tab.id, {
    code: `(() => {
      const urls = new Map();
      const isHttp = value => /^https?:/i.test(value || "");
      const add = (url, width = 0, height = 0, fromVideoElement = false) => {
        if (!isHttp(url)) return;
        const current = urls.get(url) || { url, width: 0, height: 0, fromVideoElement: false };
        current.width = Math.max(current.width, Number(width) || 0);
        current.height = Math.max(current.height, Number(height) || 0);
        current.fromVideoElement ||= fromVideoElement;
        urls.set(url, current);
      };
      document.querySelectorAll("video").forEach(video => {
        add(video.currentSrc, video.videoWidth, video.videoHeight, true);
        add(video.src, video.videoWidth, video.videoHeight, true);
        video.querySelectorAll("source[src]").forEach(source => add(source.src, video.videoWidth, video.videoHeight, true));
      });
      document.querySelectorAll("[data-video-src], [data-src]").forEach(node => {
        add(node.getAttribute("data-video-src") || node.getAttribute("data-src"));
      });
      performance.getEntriesByType("resource").forEach(entry => {
        if (/\\.(?:mp4|webm|mov|m4v|avi|mkv|m3u8|mpd)(?:$|[?#])/i.test(entry.name)) add(entry.name);
      });
      return [...urls.values()];
    })()`
  });

  for (const item of found) {
    upsert(tab.id, item.url, {
      width: item.width,
      height: item.height,
      fromVideoElement: item.fromVideoElement,
      source: "page-scan"
    });
  }
  return found.length;
}

function bestDirectVideos(tabId) {
  const selected = new Map();
  for (const item of mediaByTab.get(tabId) || []) {
    if (item.kind !== "video") continue;
    const key = variantKey(item);
    const current = selected.get(key);
    if (!current || qualityScore(item) > qualityScore(current)) selected.set(key, item);
  }
  return [...selected.values()];
}

async function download(item) {
  const downloadId = await browser.downloads.download({
    url: item.url,
    filename: `media-collector/${item.filename}`,
    saveAs: false,
    conflictAction: "uniquify"
  });
  return downloadId;
}

browser.runtime.onMessage.addListener(async message => {
  const tab = await activeTab();
  if (!tab?.id) return { ok: false, error: "Nenhuma aba ativa." };

  if (message.type === "scan") {
    try {
      const scanned = await scanLoadedPage(tab);
      return { ok: true, scanned };
    } catch (error) {
      return { ok: false, error: `Não foi possível varrer esta página: ${error.message}` };
    }
  }
  if (message.type === "list") {
    const items = mediaByTab.get(tab.id) || [];
    return {
      ok: true,
      items,
      directCount: items.filter(item => item.kind === "video").length,
      streamCount: items.filter(item => item.kind === "stream").length,
      bestCount: bestDirectVideos(tab.id).length
    };
  }
  if (message.type === "clear") {
    mediaByTab.delete(tab.id);
    return { ok: true };
  }
  if (message.type === "download") {
    const item = (mediaByTab.get(tab.id) || []).find(candidate => candidate.id === message.id);
    if (!item) return { ok: false, error: "Vídeo não encontrado na fila desta aba." };
    if (item.kind !== "video") return { ok: false, error: "Stream adaptativo detectado; não há arquivo único para baixar neste coletor." };
    return { ok: true, downloadId: await download(item) };
  }
  if (message.type === "download-best") {
    const selected = bestDirectVideos(tab.id);
    let queued = 0;
    const failures = [];
    for (const item of selected) {
      try {
        await download(item);
        queued += 1;
      } catch (error) {
        failures.push(`${item.filename}: ${error.message}`);
      }
    }
    return { ok: failures.length === 0, queued, total: selected.length, failures };
  }
  return { ok: false, error: "Comando desconhecido." };
});
