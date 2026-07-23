import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type BackgroundAnimation = "none" | "movimento_sutil" | "movimento_lateral" | "pulsacao";

type Scene = { id: string; image_id: number; asset_key?: string; image: string };
type ImageBinding = { expectedImage: string; sourceImage: string };
type ImageBindings = Record<string, ImageBinding>;
type Script = {
  title: string;
  language: string;
  voice?: string;
  narrator_gender: "male" | "female";
  background?: string;
  background_animation?: BackgroundAnimation;
  blocks: { id: string; text: string; scenes: Scene[] }[];
};

type Catalog = { images: string[]; backgrounds: string[]; default_background?: string | null; music: string[]; sounds: string[] };
type RenderJob = {
  status: string;
  output?: string;
  output_url?: string;
  error?: string;
  progress?: number;
  stage?: string;
  error_code?: string;
  error_detail?: string;
  log_url?: string;
  events_url?: string;
};
type TimingScene = {
  id: string;
  duration: number;
  suggested_split?: { first_text: string; second_text: string };
};
type TimingReport = { scenes: TimingScene[] };

const animationOptions: { value: BackgroundAnimation; label: string }[] = [
  { value: "movimento_sutil", label: "Movimento suave" },
  { value: "movimento_lateral", label: "Movimento lateral" },
  { value: "pulsacao", label: "Pulsação" },
  { value: "none", label: "Sem movimento" },
];
const LEGACY_SESSION_IMAGES_KEY = "synthreel:session-images";
const IMAGE_BINDING_STRATEGY_VERSION = "synthreel:semantic-image-bindings-v1";
const PLACEHOLDER_IMAGE_PATTERN = /^cena_\d+(?:_[a-z])?\.(?:png|jpe?g|webp)$/i;
const THUMBNAIL_PAGE_SIZE = 24;
const RENDER_COMPLETE_SOUND_URL = "/assets/sounds/Mountain%20Audio%20-%20New%20Idea%20Notification.mp3";
const RENDER_ERROR_SOUND_URL = "/assets/sounds/Wrong%20Answer.mp3";
let renderAudioContext: AudioContext | null = null;
let renderErrorBuffer: Promise<AudioBuffer> | null = null;

function playRenderCompleteSound(): void {
  const sound = new Audio(RENDER_COMPLETE_SOUND_URL);
  sound.play().catch(() => {
    // Alguns navegadores podem bloquear som se a aba perdeu a permissão de
    // reprodução automática. A conclusão do trabalho não pode falhar por isso.
  });
}

function getRenderAudioContext(): AudioContext {
  if (!renderAudioContext) renderAudioContext = new AudioContext();
  return renderAudioContext;
}

function loadRenderErrorSound(): Promise<AudioBuffer> {
  if (!renderErrorBuffer) {
    const context = getRenderAudioContext();
    renderErrorBuffer = fetch(RENDER_ERROR_SOUND_URL)
      .then(response => {
        if (!response.ok) throw new Error("Não foi possível carregar o som de erro.");
        return response.arrayBuffer();
      })
      .then(data => context.decodeAudioData(data));
  }
  return renderErrorBuffer;
}

function unlockRenderErrorSound(): void {
  // O clique em "Gerar vídeo" é a única janela de gesto do usuário. Deixamos
  // o AudioContext ativo aqui para ele poder tocar ao receber o erro pelo
  // polling, mesmo vários minutos depois.
  const context = getRenderAudioContext();
  void context.resume().catch(() => undefined);
  void loadRenderErrorSound().catch(() => undefined);
}

function playRenderErrorSound(): void {
  // O efeito "Wrong Answer" diferencia de imediato uma falha do toque
  // positivo usado ao concluir a renderização.
  void (async () => {
    try {
      const context = getRenderAudioContext();
      await context.resume();
      const source = context.createBufferSource();
      const gain = context.createGain();
      source.buffer = await loadRenderErrorSound();
      gain.gain.value = 0.72;
      source.connect(gain).connect(context.destination);
      source.start();
    } catch {
      // A notificação visual continua sendo a fonte de verdade se o navegador
      // não disponibilizar áudio nesta sessão.
    }
  })();
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const raw = await response.text();
  let body: unknown = null;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = raw; }
  if (!response.ok) {
    const detail = typeof body === "object" && body !== null && "detail" in body
      ? (body as { detail: unknown }).detail
      : body;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body as T;
}

function sceneCount(script: Script | null): number {
  return script?.blocks.reduce((total, block) => total + block.scenes.length, 0) ?? 0;
}

function sceneImages(script: Script | null): string[] {
  return script?.blocks.flatMap(block => block.scenes.map(scene => scene.image)) ?? [];
}

function sceneBindingKey(blockIndex: number, sceneIndex: number): string {
  return `${blockIndex}:${sceneIndex}`;
}

function boundImageFor(
  bindings: ImageBindings,
  blockIndex: number,
  sceneIndex: number,
  expectedImage: string,
): string | undefined {
  const binding = bindings[sceneBindingKey(blockIndex, sceneIndex)];
  // O nome esperado faz parte do vínculo. Assim, uma edição manual do JSON
  // nunca reaproveita por acidente uma foto escolhida para o roteiro anterior.
  return binding?.expectedImage === expectedImage ? binding.sourceImage : undefined;
}

function bindingPayload(script: Script, bindings: ImageBindings): Record<string, string> {
  const result: Record<string, string> = {};
  script.blocks.forEach((block, blockIndex) => block.scenes.forEach((scene, sceneIndex) => {
    const sourceImage = boundImageFor(bindings, blockIndex, sceneIndex, scene.image);
    if (sourceImage && sourceImage !== scene.image) result[scene.image] = sourceImage;
  }));
  return result;
}

function bindingsFromResolvedSources(script: Script, resolved: Record<string, string>): ImageBindings {
  const result: ImageBindings = {};
  script.blocks.forEach((block, blockIndex) => block.scenes.forEach((scene, sceneIndex) => {
    const sourceImage = resolved[scene.image];
    if (sourceImage && sourceImage !== scene.image) {
      result[sceneBindingKey(blockIndex, sceneIndex)] = { expectedImage: scene.image, sourceImage };
    }
  }));
  return result;
}

function unresolvedImages(
  script: Script,
  bindings: ImageBindings,
  uploadedImages: string[],
  catalogImages: string[],
): string[] {
  return script.blocks.flatMap((block, blockIndex) => block.scenes.flatMap((scene, sceneIndex) => {
    const sourceImage = boundImageFor(bindings, blockIndex, sceneIndex, scene.image) ?? scene.image;
    return imageIsReady(sourceImage, uploadedImages, catalogImages) ? [] : [scene.image];
  }));
}

function linkedSceneCount(
  script: Script | null,
  bindings: ImageBindings,
  uploadedImages: string[],
  catalogImages: string[],
): number {
  if (!script) return 0;
  return script.blocks.reduce((total, block, blockIndex) => total + block.scenes.filter((scene, sceneIndex) => {
    const sourceImage = boundImageFor(bindings, blockIndex, sceneIndex, scene.image) ?? scene.image;
    return imageIsReady(sourceImage, uploadedImages, catalogImages);
  }).length, 0);
}

function isPlaceholderImage(image: string): boolean {
  return PLACEHOLDER_IMAGE_PATTERN.test(image);
}

function imageIsReady(image: string, uploadedImages: string[], catalogImages: string[]): boolean {
  // Nomes como cena_01.png são placeholders: somente um arquivo enviado nesta
  // tela pode confirmá-los. Isso evita reutilizar um asset antigo por engano.
  return uploadedImages.includes(image) || (!isPlaceholderImage(image) && catalogImages.includes(image));
}

function readableError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  try {
    const detail = JSON.parse(message) as { message?: string; errors?: string[]; missing_images?: string[]; hint?: string };
    const parts = [detail.message, ...(detail.errors ?? [])].filter((part): part is string => Boolean(part));
    if (detail.missing_images?.length) {
      parts.push(`Cena(s) sem imagem vinculada: ${detail.missing_images.join(", ")}.`);
    }
    if (detail.hint) parts.push(detail.hint);
    if (parts.length) {
      return parts.join(" ");
    }
    return message;
  } catch {
    return message;
  }
}

function App() {
  const [source, setSource] = useState("");
  const [script, setScript] = useState<Script | null>(null);
  const [catalog, setCatalog] = useState<Catalog>({ images: [], backgrounds: [], music: [], sounds: [] });
  // O painel nunca apresenta o acervo técnico já existente no projeto.
  // Somente arquivos enviados pelo operador nesta sessão aparecem aqui.
  const [uploadedImages, setUploadedImages] = useState<string[]>([]);
  // O vínculo aponta para o arquivo enviado, mas não altera scene.image no
  // JSON. O backend recebe esse mapa explicitamente ao validar/renderizar.
  const [imageBindings, setImageBindings] = useState<ImageBindings>({});
  // A grade é paginada e somente o seletor em uso monta a lista completa.
  // Assim, roteiros grandes não criam centenas de imagens e milhares de
  // <option>s logo no primeiro render.
  const [thumbnailPage, setThumbnailPage] = useState(0);
  const [activeImagePickerKey, setActiveImagePickerKey] = useState<string | null>(null);
  const [background, setBackground] = useState("");
  const [music, setMusic] = useState("");
  const [animation, setAnimation] = useState<BackgroundAnimation>("movimento_sutil");
  const [status, setStatus] = useState("Aguardando roteiro JSON.");
  const [jobId, setJobId] = useState("");
  const [renderProgress, setRenderProgress] = useState(0);
  const [renderStage, setRenderStage] = useState("");
  const [outputUrl, setOutputUrl] = useState("");
  const [renderError, setRenderError] = useState("");
  const [timingWarnings, setTimingWarnings] = useState<TimingScene[]>([]);
  const [renderLogUrl, setRenderLogUrl] = useState("");
  const jsonInput = useRef<HTMLInputElement>(null);
  const imagesInput = useRef<HTMLInputElement>(null);
  const backgroundInput = useRef<HTMLInputElement>(null);
  const notifiedCompletedJob = useRef<string | null>(null);

  const refreshCatalog = async () => {
    try {
      const next = await api<Catalog>("/api/catalog");
      setCatalog(next);
      setBackground(current => (
        current && next.backgrounds.includes(current)
          ? current
          : next.default_background ?? next.backgrounds[0] ?? ""
      ));
      setMusic(current => (
        current && next.music.includes(current)
          ? current
          : next.music.find(item => item.toLowerCase() === "fundo_documentario.mp3") ?? next.music[0] ?? ""
      ));
      setStatus(current => current.startsWith("Inicie o backend") ? "Catálogo conectado." : current);
    } catch {
      setStatus("Inicie o backend em http://localhost:8000.");
    }
  };

  useEffect(() => { void refreshCatalog(); }, []);

  // Apaga somente a chave antiga que causava miniaturas fantasma. A lista de
  // arquivos enviados deixa de sobreviver a recarregamentos da página.
  useEffect(() => {
    try { window.sessionStorage.removeItem(LEGACY_SESSION_IMAGES_KEY); } catch { /* storage indisponível */ }
  }, []);

  // Elimina o estado criado pela versão que vinculava arquivos pela posição.
  // Sem isso, o Fast Refresh poderia reenviar o mapa antigo como se ele fosse
  // uma escolha manual do operador.
  useEffect(() => {
    try {
      if (window.sessionStorage.getItem(IMAGE_BINDING_STRATEGY_VERSION) !== "active") {
        window.sessionStorage.setItem(IMAGE_BINDING_STRATEGY_VERSION, "active");
        setImageBindings({});
      }
    } catch {
      setImageBindings({});
    }
  }, []);

  // Se o painel abriu antes da API, reconecta sozinho assim que o backend
  // voltar. Não obriga recarregar a aplicação para exibir os arquivos.
  useEffect(() => {
    if (catalog.backgrounds.length) return;
    const timer = window.setInterval(() => { void refreshCatalog(); }, 3000);
    return () => window.clearInterval(timer);
  }, [catalog.backgrounds.length]);

  useEffect(() => {
    if (!jobId) return;
    const timer = window.setInterval(async () => {
      try {
        const job = await api<RenderJob>(`/api/jobs/${jobId}`);
        if (typeof job.progress === "number") {
          setRenderProgress(Math.min(100, Math.max(0, job.progress)));
        }
        if (job.stage) setRenderStage(job.stage);
        if (job.status === "complete") {
          if (notifiedCompletedJob.current !== jobId) {
            playRenderCompleteSound();
            notifiedCompletedJob.current = jobId;
          }
          setRenderProgress(100);
          setRenderStage(job.stage ?? "Vídeo final pronto");
          setOutputUrl(job.output_url ?? "");
          setStatus("Vídeo final pronto.");
          setRenderError("");
          setRenderLogUrl("");
          setJobId("");
        }
        if (job.status === "failed") {
          playRenderErrorSound();
          setRenderStage(job.stage ?? "Falha na renderização");
          const message = job.error ?? "Falha sem mensagem retornada pelo servidor.";
          setRenderError(job.error_detail && job.error_detail !== message ? job.error_detail : "");
          setRenderLogUrl(job.log_url ?? "");
          setStatus(`A renderização falhou: ${message}`);
          setJobId("");
        }
      } catch {
        setRenderStage("Não foi possível acompanhar a renderização");
        setStatus("Não foi possível acompanhar a renderização.");
        setJobId("");
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId]);

  const parseScript = (value: string, announce = true): Script | null => {
    if (!value.trim()) {
      if (announce) setStatus("Cole ou importe um roteiro JSON antes de continuar.");
      return null;
    }
    try {
      const next = JSON.parse(value) as Script;
      setScript(next);
      setAnimation(next.background_animation ?? "movimento_sutil");
      if (announce) setStatus(`${sceneCount(next)} cenas carregadas. Voz e narrativa vêm do JSON.`);
      return next;
    } catch {
      if (announce) setStatus("O JSON não é válido. Corrija-o antes de renderizar.");
      return null;
    }
  };

  const applyJson = () => { void parseScript(source); };

  const readJsonFile = async (file: File) => {
    const text = await file.text();
    setImageBindings({});
    setActiveImagePickerKey(null);
    setTimingWarnings([]);
    setSource(text);
    void parseScript(text);
  };

  const importJson = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void readJsonFile(file);
    event.target.value = "";
  };

  const dropJson = (event: DragEvent<HTMLTextAreaElement>) => {
    event.preventDefault();
    const file = Array.from(event.dataTransfer.files).find(item => item.name.toLowerCase().endsWith(".json"));
    if (file) void readJsonFile(file);
  };

  const uploadMedia = async (endpoint: "/api/images" | "/api/backgrounds", files: FileList | File[]): Promise<string[]> => {
    if (!files.length) return [];
    const form = new FormData();
    Array.from(files).forEach(file => form.append("files", file, file.name));
    const result = await api<{ saved: string[] }>(endpoint, { method: "POST", body: form });
    await refreshCatalog();
    return result.saved;
  };

  const uploadImages = async (files: FileList | File[]) => {
    try {
      setStatus("Importando imagens de cena…");
      const saved = await uploadMedia("/api/images", files);
      if (saved.length) {
        const nextUploadedImages = [...new Set([...uploadedImages, ...saved])];
        setUploadedImages(nextUploadedImages);
        setStatus(`${saved.length} imagem(ns) importada(s). O sistema usará a descrição do arquivo e o brief da cena; a ordem do envio não importa.`);
      }
    } catch (error) {
      setStatus(readableError(error));
    }
  };

  const uploadBackground = async (files: FileList | File[]) => {
    try {
      setStatus("Importando fundo…");
      const saved = await uploadMedia("/api/backgrounds", files);
      if (saved.length) {
        setBackground(saved.at(-1) ?? "");
        setStatus("Fundo importado. A prévia abaixo mostra o movimento selecionado.");
      }
    } catch (error) {
      setStatus(readableError(error));
    }
  };

  const onImagesInput = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) void uploadImages(event.target.files);
    event.target.value = "";
  };

  const onBackgroundInput = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) void uploadBackground(event.target.files);
    event.target.value = "";
  };

  const dropImages = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    void uploadImages(event.dataTransfer.files);
  };

  const dropBackground = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    void uploadBackground(event.dataTransfer.files);
  };

  const setBackgroundAnimation = (nextAnimation: BackgroundAnimation) => {
    setAnimation(nextAnimation);
    if (!script) return;
    const next = { ...script, background_animation: nextAnimation };
    setScript(next);
    setSource(JSON.stringify(next, null, 2));
  };

  const bindUploadedImageToScene = (blockIndex: number, sceneIndex: number, image: string) => {
    if (!script) return;
    const scene = script.blocks[blockIndex]?.scenes[sceneIndex];
    if (!scene) return;
    const key = sceneBindingKey(blockIndex, sceneIndex);
    if (!image || image === scene.image) {
      setImageBindings(current => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      setStatus(`Cena ${blockIndex + 1} voltará a usar o arquivo ${scene.image} definido no JSON.`);
      return;
    }
    if (!uploadedImages.includes(image)) return;
    setImageBindings(current => ({
      ...current,
      [key]: { expectedImage: scene.image, sourceImage: image },
    }));
    setStatus(`Cena ${blockIndex + 1} usará ${image}; o nome ${scene.image} no JSON foi preservado.`);
  };

  const renameJsonImageFromBinding = (blockIndex: number, sceneIndex: number) => {
    if (!script) return;
    const currentScene = script.blocks[blockIndex]?.scenes[sceneIndex];
    if (!currentScene) return;
    const key = sceneBindingKey(blockIndex, sceneIndex);
    const image = boundImageFor(imageBindings, blockIndex, sceneIndex, currentScene.image);
    if (!image) return;
    const next: Script = {
      ...script,
      blocks: script.blocks.map((block, currentBlockIndex) => (
        currentBlockIndex !== blockIndex ? block : {
          ...block,
          scenes: block.scenes.map((scene, currentSceneIndex) => (
            currentSceneIndex !== sceneIndex ? scene : { ...scene, image }
          )),
        }
      )),
    };
    setScript(next);
    setSource(JSON.stringify(next, null, 2));
    setImageBindings(current => {
      const updated = { ...current };
      delete updated[key];
      return updated;
    });
    setStatus(`O JSON da cena ${blockIndex + 1} agora usa ${image}.`);
  };

  const clearUploadedImages = () => {
    setUploadedImages([]);
    setImageBindings({});
    setThumbnailPage(0);
    setActiveImagePickerKey(null);
    setStatus("Lista local e vínculos limpos. Nenhum arquivo foi apagado e o roteiro foi preservado.");
  };

  const validate = async () => {
    const activeScript = parseScript(source, false);
    if (!activeScript) {
      setStatus("Cole ou importe um roteiro JSON antes de validar.");
      return;
    }
    try {
      setStatus("Medindo a narração com a voz definida no JSON…");
      const report = await api<{
        valid: boolean; errors: string[]; missing_images: string[]; resolved_image_sources: Record<string, string>;
        timing?: TimingReport; timing_error?: string;
      }>("/api/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script: activeScript,
          manual_image_bindings: bindingPayload(activeScript, imageBindings),
          uploaded_images: uploadedImages,
          measure_timing: true,
        }),
      });
      setImageBindings(bindingsFromResolvedSources(activeScript, report.resolved_image_sources));
      const warnings = report.timing?.scenes.filter(scene => scene.duration > 9) ?? [];
      setTimingWarnings(warnings);
      const validationNotes = [
        ...(warnings.length ? [`${warnings.length} cena(s) ultrapassam 9 s; veja as sugestões de corte abaixo.`] : []),
        ...(report.missing_images.length ? [`Faltam: ${report.missing_images.join(", ")}`] : []),
      ];
      setStatus(
        report.valid
          ? (report.timing_error
            ? report.timing_error
            : validationNotes.join(" ") || "Roteiro válido. Assets e duração acústica aprovados.")
          : report.errors.join("\n"),
      );
    } catch (error) {
      setStatus(readableError(error));
    }
  };

  const render = async () => {
    unlockRenderErrorSound();
    const activeScript = parseScript(source, false);
    if (!activeScript) {
      setStatus("Cole ou importe um roteiro JSON antes de renderizar.");
      return;
    }
    const renderScript = { ...activeScript, background_animation: animation };
    setScript(renderScript);
    setSource(JSON.stringify(renderScript, null, 2));
    try {
      setOutputUrl("");
      setRenderError("");
      setRenderLogUrl("");
      setRenderProgress(2);
      setRenderStage("Enviando trabalho para renderização");
      const result = await api<{ job_id: string }>("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script: renderScript,
          manual_image_bindings: bindingPayload(activeScript, imageBindings),
          uploaded_images: uploadedImages,
          ...(background ? { background_image: background } : {}),
          ...(music ? { music_name: music } : {}),
        }),
      });
      notifiedCompletedJob.current = null;
      setJobId(result.job_id);
      setRenderProgress(5);
      setRenderStage("Preparando narração, imagens e trilha");
      setStatus("Renderizando o vídeo completo. O andamento aparece na barra inferior.");
    } catch (error) {
      setRenderProgress(0);
      setRenderStage("");
      const message = readableError(error);
      playRenderErrorSound();
      setRenderError(message);
      setStatus(message);
    }
  };

  const backgroundUrl = background ? `/assets/backgrounds/${encodeURIComponent(background)}` : "";
  const requiredImages = sceneImages(script);
  const linkedImages = linkedSceneCount(script, imageBindings, uploadedImages, catalog.images);
  const thumbnailPageCount = Math.max(1, Math.ceil(uploadedImages.length / THUMBNAIL_PAGE_SIZE));
  const currentThumbnailPage = Math.min(thumbnailPage, thumbnailPageCount - 1);
  const thumbnailStart = currentThumbnailPage * THUMBNAIL_PAGE_SIZE;
  const visibleUploadedImages = uploadedImages.slice(thumbnailStart, thumbnailStart + THUMBNAIL_PAGE_SIZE);
  const imageProgress = requiredImages.length ? Math.round((linkedImages / requiredImages.length) * 100) : 0;
  const scriptProgress = script ? 100 : 0;
  const backgroundProgress = background ? 100 : 0;

  return (
    <main className="app-shell">
      <header className="appbar">
        <div className="brand"><span className="brand-mark">SR</span><span>SynthReel</span><small>horizontal</small></div>
        <div className="appbar-actions">
          <span className="scene-indicator">{script ? `${sceneCount(script)} cenas` : "sem roteiro"}</span>
          <button className="button quiet" onClick={validate}>Validar</button>
          <button className="button primary" disabled={Boolean(jobId)} onClick={render}>{jobId ? "Renderizando…" : "Gerar vídeo"}</button>
        </div>
      </header>

      <section className="workbench" aria-label="Área de produção">
        <article className="panel json-panel">
          <div className="panel-header">
            <div><span className="panel-index">01</span><h1>Roteiro JSON</h1></div>
            <button className="button quiet compact" onClick={() => jsonInput.current?.click()}>Importar JSON</button>
            <input ref={jsonInput} type="file" accept="application/json,.json" hidden onChange={importJson} />
          </div>
          <p className="panel-hint">Cole o JSON ou importe um arquivo.</p>
          <div className="flow-progress" aria-label="Progresso do roteiro">
            <div><span>Roteiro</span><b>{script ? `${sceneCount(script)} cenas` : "aguardando"}</b></div>
            <i><em style={{ width: `${scriptProgress}%` }} /></i>
          </div>
          <textarea
            aria-label="Cole o roteiro JSON"
            value={source}
            spellCheck={false}
            onDrop={dropJson}
            onDragOver={event => event.preventDefault()}
            onChange={event => {
              setSource(event.target.value);
              // Uma edição manual pode mudar os IDs ou o significado das
              // cenas; os vínculos anteriores não devem vazar para ela.
              setImageBindings({});
              setActiveImagePickerKey(null);
            }}
          />
          {timingWarnings.length > 0 && (
            <section className="timing-report" aria-label="Cenas que precisam de corte">
              <b>Prévia acústica: {timingWarnings.length} cena(s) acima de 9 s</b>
              {timingWarnings.map(scene => (
                <div className="timing-warning" key={scene.id}>
                  <span><b>{scene.id}</b> — {scene.duration.toFixed(2)} s</span>
                  <small>Corte sugerido: “{scene.suggested_split?.first_text ?? "divida próximo à metade"}” / “{scene.suggested_split?.second_text ?? "crie a segunda cena"}”</small>
                </div>
              ))}
            </section>
          )}
          <label className="music-select">
            <span><b>Trilha do vídeo</b><small>{catalog.music.length ? "Escolha a música usada nesta renderização" : "Nenhuma música disponível"}</small></span>
            <select
              aria-label="Trilha do vídeo"
              value={music}
              disabled={!catalog.music.length || Boolean(jobId)}
              onChange={event => setMusic(event.target.value)}
            >
              {!catalog.music.length && <option value="">Sem músicas disponíveis</option>}
              {catalog.music.map(item => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <button className="button json-apply" onClick={applyJson}>Ler roteiro</button>
        </article>

        <article className="panel scenes-panel">
          <div className="panel-header">
            <div><span className="panel-index">02</span><h1>Imagens das cenas</h1></div>
            <div className="scene-panel-actions">
              <span className="panel-count">{uploadedImages.length} enviada(s) nesta tela</span>
              {uploadedImages.length > 0 && (
                <button className="button quiet compact" onClick={clearUploadedImages}>Limpar lista</button>
              )}
            </div>
          </div>
          <div className="asset-drop" onDragOver={event => event.preventDefault()} onDrop={dropImages} onClick={() => imagesInput.current?.click()} role="button" tabIndex={0}>
            <b>Solte as imagens aqui</b><span>ou clique para importar</span>
          </div>
          <input ref={imagesInput} type="file" hidden multiple accept="image/png,image/jpeg,image/webp" onChange={onImagesInput} />
          <div className="flow-progress image-progress" aria-label="Progresso das imagens">
            <div><span>Imagens encontradas</span><b>{script ? `${linkedImages}/${requiredImages.length}` : "aguardando roteiro"}</b></div>
            <i><em style={{ width: `${imageProgress}%` }} /></i>
          </div>
          <div className="asset-grid" aria-label="Imagens enviadas nesta tela">
            {visibleUploadedImages.map(image => (
              <figure key={image}>
                <img
                  src={`/assets/images/${encodeURIComponent(image)}`}
                  title={image}
                  alt={`Imagem enviada: ${image}`}
                  loading="lazy"
                  decoding="async"
                />
                <figcaption>{image}</figcaption>
              </figure>
            ))}
            {!uploadedImages.length && <p className="asset-grid-empty">As miniaturas aparecem somente depois de um envio nesta tela.</p>}
          </div>
          {uploadedImages.length > THUMBNAIL_PAGE_SIZE && (
            <div className="asset-grid-pagination" aria-label="Navegação das miniaturas">
              <span>Mostrando {thumbnailStart + 1}–{Math.min(thumbnailStart + THUMBNAIL_PAGE_SIZE, uploadedImages.length)} de {uploadedImages.length}</span>
              <div>
                <button
                  type="button"
                  className="button quiet compact"
                  disabled={currentThumbnailPage === 0}
                  onClick={() => setThumbnailPage(page => Math.max(0, page - 1))}
                >
                  Anteriores
                </button>
                <button
                  type="button"
                  className="button quiet compact"
                  disabled={currentThumbnailPage >= thumbnailPageCount - 1}
                  onClick={() => setThumbnailPage(page => Math.min(thumbnailPageCount - 1, page + 1))}
                >
                  Próximas
                </button>
              </div>
            </div>
          )}
          {script && (
            <div className="scene-bindings" aria-label="Vínculo manual de imagens por cena">
              <div className="scene-bindings-header">
                <span>Escolha manual por cena</span>
              </div>
              {!uploadedImages.length && <p className="scene-bindings-empty">Envie imagens para liberar as opções de vínculo. Nenhuma imagem antiga é exibida aqui.</p>}
              {uploadedImages.length > 0 && <p className="scene-bindings-note">O ID é a referência editorial da cena. O sistema compara o brief com os nomes descritivos que o Google Flow gerar; a ordem do envio não é usada.</p>}
              <div className="scene-binding-list">
                {script.blocks.flatMap((block, blockIndex) => block.scenes.map((scene, sceneIndex) => {
                  const pickerKey = sceneBindingKey(blockIndex, sceneIndex);
                  const boundImage = boundImageFor(imageBindings, blockIndex, sceneIndex, scene.image);
                  const sourceImage = boundImage ?? scene.image;
                  const isReady = imageIsReady(sourceImage, uploadedImages, catalog.images);
                  // O <select> ativo é o único que recebe a lista inteira.
                  // Os demais preservam só seu valor atual, evitando N cenas ×
                  // N arquivos em nós DOM para roteiros maiores.
                  const pickerImages = activeImagePickerKey === pickerKey
                    ? uploadedImages.filter(image => image !== scene.image)
                    : boundImage ? [boundImage] : [];
                  return (
                    <div className={`scene-binding${isReady ? " ready" : " pending"}`} key={`${block.id}-${scene.id}-${blockIndex}-${sceneIndex}`}>
                      <span className="scene-binding-label"><b>Cena {blockIndex + 1} · ID {scene.image_id}</b><code>{scene.asset_key ?? scene.image}</code></span>
                      <select
                        aria-label={`Escolher imagem para a cena ${blockIndex + 1}`}
                        value={boundImage ?? ""}
                        disabled={!uploadedImages.length || Boolean(jobId)}
                        onPointerDown={() => setActiveImagePickerKey(pickerKey)}
                        onFocus={() => setActiveImagePickerKey(pickerKey)}
                        onChange={event => bindUploadedImageToScene(blockIndex, sceneIndex, event.target.value)}
                      >
                        <option value="">Usar {scene.image} (nome do JSON)</option>
                        {pickerImages.map(image => <option key={image} value={image}>{image}</option>)}
                      </select>
                      <span className="scene-binding-source">
                        {boundImage ? <>Arquivo enviado: <code>{boundImage}</code></> : <>Referência editorial: <code>ID {scene.image_id}</code></>}
                      </span>
                      {boundImage && (
                        <button
                          type="button"
                          className="button quiet compact scene-binding-rename"
                          disabled={Boolean(jobId)}
                          onClick={event => { event.preventDefault(); renameJsonImageFromBinding(blockIndex, sceneIndex); }}
                        >
                          Trocar nome no JSON
                        </button>
                      )}
                    </div>
                  );
                }))}
              </div>
            </div>
          )}
        </article>

        <article className="panel background-panel">
          <div className="panel-header">
            <div><span className="panel-index">03</span><h1>Fundo e movimento</h1></div>
            <span className="motion-live"><i /> ativo</span>
          </div>
          <div className="background-stage" onDragOver={event => event.preventDefault()} onDrop={dropBackground} onClick={() => !background && backgroundInput.current?.click()} role="button" tabIndex={0}>
            {background ? (
              <img className={`motion-image ${animation}`} src={backgroundUrl} alt="Prévia do fundo animado" />
            ) : (
              <div className="empty-background"><b>Solte um fundo aqui</b><span>ou clique para importar</span></div>
            )}
            <div className="background-overlay"><span>Prévia de movimento</span></div>
          </div>
          <input ref={backgroundInput} type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={onBackgroundInput} />
          <div className="background-actions">
            <button className="button quiet compact" onClick={() => backgroundInput.current?.click()}>Importar fundo</button>
            <select aria-label="Imagem de fundo" value={background} onChange={event => setBackground(event.target.value)}>
              <option value="">Escolha um fundo</option>
              {catalog.backgrounds.map(item => <option key={item} value={item}>{item}</option>)}
            </select>
          </div>
          <label className="motion-select">Movimento
            <select value={animation} onChange={event => setBackgroundAnimation(event.target.value as BackgroundAnimation)}>
              {animationOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <div className="flow-progress background-progress" aria-label="Progresso do fundo">
            <div><span>Fundo</span><b>{background ? "pronto" : "aguardando"}</b></div>
            <i><em style={{ width: `${backgroundProgress}%` }} /></i>
          </div>
        </article>
      </section>

      <footer className={`statusbar${jobId || renderProgress ? " has-render-progress" : ""}`}>
        <div className="status-detail">
          <span className={jobId ? "status working" : "status"}>{status}</span>
          {renderError && <span className="render-error-detail" title={renderError}>{renderError}</span>}
          {(jobId || renderProgress > 0) && (
            <div className={`render-progress${jobId ? " active" : ""}`} aria-label="Andamento da renderização">
              <div><span>{renderStage || "Aguardando renderização"}</span><b>{Math.round(renderProgress)}%</b></div>
              <i><em style={{ width: `${renderProgress}%` }} /></i>
            </div>
          )}
        </div>
        {outputUrl ? (
          <a className="output-link" href={outputUrl} target="_blank" rel="noreferrer">Abrir vídeo final</a>
        ) : renderLogUrl ? (
          <a className="output-link error-log-link" href={renderLogUrl} target="_blank" rel="noreferrer">Abrir log técnico</a>
        ) : (
          <span>Voz definida no JSON</span>
        )}
      </footer>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
