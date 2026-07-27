import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type BackgroundAnimation = "none" | "movimento_sutil" | "movimento_lateral" | "pulsacao";
type MediaTab = "assets" | "curadoria";

type MediaType = "imagem" | "video_generico";
type Scene = {
  id: string; image_id: number; tipo_midia: MediaType; asset_key?: string; image: string;
  visual?: { subject?: string; action?: string; setting?: string; framing?: string; details?: string };
  annotation?: { lines: string[]; at: string; emoji?: string | null };
};
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

type Catalog = { images: string[]; videos: string[]; backgrounds: string[]; default_background?: string | null; music: string[]; sounds: string[] };
type PexelsCandidate = { id: number; preview_url: string; thumbnail?: string; width: number; height: number; duration?: number; creator?: string; pexels_url?: string };
type PexelsItem = { scene_id: string; scene_image: string; query: string; asset_key?: string; text: string; visual_reference?: string; candidates: PexelsCandidate[]; is_annotation?: boolean; search_error?: string };
type LocalProject = {
  id: string;
  name: string;
  updated_at: string;
  source: string;
  uploaded_images: string[];
  image_bindings: ImageBindings;
  background: string;
  music: string;
  animation: BackgroundAnimation;
  pexels_items: PexelsItem[];
  pexels_queries: Record<string, string>;
  translations: Record<string, string>;
  visual_translations: Record<string, string>;
  selected_pexels: Record<string, PexelsCandidate>;
  pexels_expected_count: number;
};
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
const LOCAL_PROJECTS_STORAGE_KEY = "synthreel:horizontal-projects:v1";
const LOCAL_ACTIVE_PROJECT_STORAGE_KEY = "synthreel:horizontal-active-project:v1";
const PLACEHOLDER_IMAGE_PATTERN = /^cena_\d+(?:_[a-z])?\.(?:png|jpe?g|webp)$/i;
const THUMBNAIL_PAGE_SIZE = 24;
const PEXELS_SCENES_PAGE_SIZE = 4;
const RENDER_COMPLETE_SOUND_URL = "/assets/sounds/Mountain%20Audio%20-%20New%20Idea%20Notification.mp3";
const RENDER_ERROR_SOUND_URL = "/assets/sounds/Wrong%20Answer.mp3";
const PHOTO_VISUAL_PRESET = "Raw smartphone documentary photography, harsh direct flash, natural imperfections, slightly grainy texture, muted brown, gray and dark tones, worn everyday environments, candid unposed people, realistic ordinary faces, tired, neutral or concerned expressions, non-commercial appearance, clear main subject, simple composition, sharp enough to understand the scene, horizontal 16:9.";
const PHOTO_NEGATIVE_PROMPT = "Avoid glossy advertising, studio photography, cinematic lighting, luxury environments, perfect models, plastic skin, excessive retouching, overly clean surfaces, symmetrical posing, dramatic movie color grading, neon colors, oversaturation, artificial smiles, CGI appearance, 3D render, fantasy elements, abstract metaphors, excessive objects, visual clutter, deformed hands, distorted faces and unreadable text.";
const GRAPHIC_VISUAL_PRESET = "Simple editorial data visualization, clean neutral background, clear lines or bars, strong contrast, few elements, accurate proportions, visually understandable, horizontal 16:9.";
const GRAPHIC_NEGATIVE_PROMPT = "Avoid 3D charts, floating objects, metaphorical graphics, decorative illustrations, futuristic dashboards, excessive colors, perspective distortion, tiny labels, visual clutter and complex interfaces.";
const GRAPHIC_VISUAL_TERMS = new Set([
  "grafico", "graficos", "grafica", "graficas", "graph", "graphs", "chart", "charts",
  "barras", "barra", "bars", "bar", "linha", "linhas", "line", "lines", "lineas",
  "comparacao", "comparacoes", "comparison", "comparisons", "evolucao", "evolucoes", "trend",
  "trends", "porcentagem", "porcentagens", "porcentaje", "porcentajes", "percentage", "percent",
  "inflacao", "inflacoes", "inflacion", "inflaciones", "inflation", "inflations", "margem",
  "margens", "margen", "margenes", "margin", "margins", "preco", "precos", "precio", "precios",
  "price", "prices",
]);
let renderAudioContext: AudioContext | null = null;
let renderErrorBuffer: Promise<AudioBuffer> | null = null;

function downloadTextFile(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function fileSlug(value: string): string {
  const normalized = value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
  return normalized || "roteiro";
}

function newProjectId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `project-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function newLocalProject(name = "Projeto sem título"): LocalProject {
  return {
    id: newProjectId(), name, updated_at: new Date().toISOString(), source: "", uploaded_images: [], image_bindings: {},
    background: "", music: "", animation: "movimento_sutil", pexels_items: [], pexels_queries: {},
    translations: {}, visual_translations: {}, selected_pexels: {}, pexels_expected_count: 0,
  };
}

function projectTitle(value: string | undefined): string {
  const title = value?.trim().replace(/\s+/g, " ");
  return title ? title.slice(0, 60) : "Projeto sem título";
}

function mediaLabel(filename: string): string {
  const base = filename.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (/interview/i.test(base)) return "Entrevista documental";
  if (/wireframe.*grid.*black/i.test(base)) return "Grade wireframe em fundo preto";
  return base ? base.replace(/\b\w/g, letter => letter.toUpperCase()) : filename;
}

function flowVisualPreset(scene: Scene): { kind: string; preset: string; negative: string } {
  const visual = scene.visual ?? {};
  const terms = new Set(
    Object.values(visual)
      .join(" ")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .match(/[a-z0-9]+/g) ?? [],
  );
  if ([...terms].some(term => GRAPHIC_VISUAL_TERMS.has(term))) {
    return { kind: "GRÁFICO", preset: GRAPHIC_VISUAL_PRESET, negative: GRAPHIC_NEGATIVE_PROMPT };
  }
  return { kind: "FOTOGRAFIA DOCUMENTAL", preset: PHOTO_VISUAL_PRESET, negative: PHOTO_NEGATIVE_PROMPT };
}

function googleFlowText(script: Script, batchSize: number): { text: string; imageCount: number; batchCount: number } {
  const items = script.blocks.flatMap(block => block.scenes
    .filter(scene => scene.tipo_midia === "imagem")
    .map(scene => ({ blockText: block.text.trim(), scene })));
  const batchCount = Math.ceil(items.length / batchSize);
  const batches = Array.from({ length: batchCount }, (_, batchIndex) => {
    const start = batchIndex * batchSize;
    const batch = items.slice(start, start + batchSize);
    const firstId = batch[0]?.scene.image_id ?? 0;
    const lastId = batch.at(-1)?.scene.image_id ?? 0;
    const body = Array.from({ length: Math.ceil(batch.length / 5) }, (_, groupIndex) => {
      const group = batch.slice(groupIndex * 5, groupIndex * 5 + 5);
      const startNumber = groupIndex * 5 + 1;
      const endNumber = startNumber + group.length - 1;
      const prompts = group.map(({ blockText, scene }, sceneIndex) => {
        const visual = scene.visual ?? {};
        const preset = flowVisualPreset(scene);
        const number = String(startNumber + sceneIndex).padStart(2, "0");
        return [
          `IMAGEM ${number} DE ${String(batch.length).padStart(2, "0")} · REFERÊNCIA FLOW ID ${scene.image_id}`,
          `Arquivo esperado: ${scene.image}`,
          `Narração de contexto: ${blockText}`,
          "Cena:",
          `- Sujeito: ${visual.subject ?? ""}`,
          `- Ação: ${visual.action ?? ""}`,
          `- Ambiente: ${visual.setting ?? ""}`,
          `- Enquadramento: ${visual.framing ?? ""}`,
          `- Detalhes: ${visual.details ?? ""}`,
          `- Preset automático (${preset.kind}): ${preset.preset}`,
          `- Bloco negativo: ${preset.negative}`,
        ].join("\n");
      }).join("\n\n────────────────────────────────────────\n\n");
      return [
        "────────────────────────────────────────────────────────────────────────────────",
        `SUBLOTE ${String(groupIndex + 1).padStart(2, "0")} · GERE EXATAMENTE AS IMAGENS ${String(startNumber).padStart(2, "0")} A ${String(endNumber).padStart(2, "0")} · ${group.length} ITENS`,
        "Depois destas 5 imagens, pare. Não avance para o próximo sublote sem novo comando.",
        "────────────────────────────────────────────────────────────────────────────────",
        "",
        prompts,
      ].join("\n");
    }).join("\n\n\n");
    const number = String(batchIndex + 1).padStart(2, "0");
    const header = [
      "================================================================================",
      `INÍCIO — LOTE GOOGLE FLOW ${number} · ${batch.length} IMAGENS · FLOW IDs ${firstId}–${lastId}`,
      "================================================================================",
      "",
      "INSTRUÇÕES DE GERAÇÃO — APLICAR A TODAS AS IMAGENS DESTE LOTE",
      `- Este lote contém EXATAMENTE ${batch.length} imagens solicitadas. Não gere uma imagem para cada número entre os FLOW IDs ${firstId} e ${lastId}.`,
      "- FLOW ID é somente uma etiqueta de referência; a numeração que vale é IMAGEM 01 DE N até IMAGEM N DE N.",
      "- Gere somente 5 imagens por vez, seguindo cada SUBLOTE. Ao terminar um sublote, pare e espere um novo comando antes de iniciar o próximo.",
      "- Siga o brief de cada cena com precisão; não misture cenas, IDs ou personagens.",
      "- Os cinco campos do brief descrevem apenas conteúdo. Aplique somente o preset automático e o bloco negativo impressos em cada imagem; não acrescente estética, metáforas, objetos flutuantes ou cenários conceituais.",
      "- Mantenha no máximo dois ou três elementos principais e uma composição simples, fácil de entender.",
      "- Cenas de vídeo foram removidas intencionalmente: gere somente os itens deste lote.",
      "",
      body,
      "",
      "================================================================================",
      `FIM DO LOTE GOOGLE FLOW ${number} · ${batch.length} IMAGENS CONCLUÍDAS`,
      "================================================================================",
    ].join("\n");
    return header;
  });
  return { text: batches.join("\n\n\n\n\n\n\n\n"), imageCount: items.length, batchCount };
}

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

function sceneAssets(script: Script | null): Scene[] {
  return script?.blocks.flatMap(block => block.scenes) ?? [];
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

function unresolvedAssets(
  script: Script,
  bindings: ImageBindings,
  uploadedImages: string[],
  catalogImages: string[],
): string[] {
  return script.blocks.flatMap((block, blockIndex) => block.scenes.flatMap((scene, sceneIndex) => {
    const sourceImage = boundImageFor(bindings, blockIndex, sceneIndex, scene.image) ?? scene.image;
    return assetIsReady(scene, sourceImage, uploadedImages, catalogImages, []) ? [] : [scene.image];
  }));
}

function linkedSceneCount(
  script: Script | null,
  bindings: ImageBindings,
  uploadedImages: string[],
  catalogImages: string[],
  catalogVideos: string[],
): number {
  if (!script) return 0;
  return script.blocks.reduce((total, block, blockIndex) => total + block.scenes.filter((scene, sceneIndex) => {
    const sourceImage = boundImageFor(bindings, blockIndex, sceneIndex, scene.image) ?? scene.image;
    return assetIsReady(scene, sourceImage, uploadedImages, catalogImages, catalogVideos);
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

function assetIsReady(scene: Scene, asset: string, uploadedImages: string[], catalogImages: string[], catalogVideos: string[]): boolean {
  return scene.tipo_midia === "video_generico"
    ? catalogVideos.includes(asset)
    : imageIsReady(asset, uploadedImages, catalogImages);
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
  const [projects, setProjects] = useState<LocalProject[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("Projeto sem título");
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [source, setSource] = useState("");
  const [script, setScript] = useState<Script | null>(null);
  const [catalog, setCatalog] = useState<Catalog>({ images: [], videos: [], backgrounds: [], music: [], sounds: [] });
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
  const [pexelsItems, setPexelsItems] = useState<PexelsItem[]>([]);
  const [pexelsQueries, setPexelsQueries] = useState<Record<string, string>>({});
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [visualTranslations, setVisualTranslations] = useState<Record<string, string>>({});
  const [translationLoading, setTranslationLoading] = useState<Record<string, boolean>>({});
  const [visualTranslationLoading, setVisualTranslationLoading] = useState<Record<string, boolean>>({});
  const [selectedPexels, setSelectedPexels] = useState<Record<string, PexelsCandidate>>({});
  const [expandedPexelsScene, setExpandedPexelsScene] = useState<string | null>(null);
  const [pexelsPreviewErrors, setPexelsPreviewErrors] = useState<Record<string, boolean>>({});
  const [pexelsPage, setPexelsPage] = useState(0);
  const [pexelsExpectedCount, setPexelsExpectedCount] = useState(0);
  const [pexelsBusy, setPexelsBusy] = useState(false);
  const [mediaTab, setMediaTab] = useState<MediaTab>("assets");
  const [musicPreviewPlaying, setMusicPreviewPlaying] = useState(false);
  const [musicPreviewVolume, setMusicPreviewVolume] = useState(0.14);
  const [promptCopied, setPromptCopied] = useState(false);
  const [flowExportReady, setFlowExportReady] = useState(false);
  const [flowBatchSize, setFlowBatchSize] = useState<25 | 50>(25);
  const jsonInput = useRef<HTMLInputElement>(null);
  const imagesInput = useRef<HTMLInputElement>(null);
  const backgroundInput = useRef<HTMLInputElement>(null);
  const musicInput = useRef<HTMLInputElement>(null);
  const notifiedCompletedJob = useRef<string | null>(null);
  const musicPreview = useRef<HTMLAudioElement | null>(null);
  const promptCopyTimer = useRef<number | null>(null);
  const projectsHydrated = useRef(false);

  const stopMusicPreview = () => {
    const preview = musicPreview.current;
    if (!preview) return;
    preview.pause();
    preview.currentTime = 0;
    setMusicPreviewPlaying(false);
  };

  const startMusicPreview = () => {
    if (!music) return;
    const url = `/assets/music/${encodeURIComponent(music)}`;
    let preview = musicPreview.current;
    if (!preview || preview.src !== new URL(url, window.location.href).href) {
      stopMusicPreview();
      preview = new Audio(url);
      preview.volume = musicPreviewVolume;
      preview.onended = () => setMusicPreviewPlaying(false);
      musicPreview.current = preview;
    }
    preview.currentTime = 0;
    preview.volume = musicPreviewVolume;
    void preview.play()
      .then(() => setMusicPreviewPlaying(true))
      .catch(() => setMusicPreviewPlaying(false));
  };

  const toggleMusicPreview = () => {
    if (musicPreviewPlaying) stopMusicPreview();
    else startMusicPreview();
  };

  useEffect(() => () => {
    stopMusicPreview();
    musicPreview.current = null;
  }, []);

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

  // Elimina somente o marcador da versão antiga. Vínculos atuais pertencem ao
  // projeto local e precisam sobreviver ao reinício do painel.
  useEffect(() => {
    try {
      if (window.sessionStorage.getItem(IMAGE_BINDING_STRATEGY_VERSION) !== "active") {
        window.sessionStorage.setItem(IMAGE_BINDING_STRATEGY_VERSION, "active");
      }
    } catch { /* storage indisponível */ }
  }, []);

  const restoreProject = (project: LocalProject) => {
    setProjectId(project.id);
    setProjectName(project.name);
    setSource(project.source);
    setUploadedImages(project.uploaded_images ?? []);
    setImageBindings(project.image_bindings ?? {});
    setBackground(project.background ?? "");
    setMusic(project.music ?? "");
    setAnimation(project.animation ?? "movimento_sutil");
    setPexelsItems(project.pexels_items ?? []);
    setPexelsQueries(project.pexels_queries ?? {});
    setTranslations(project.translations ?? {});
    setVisualTranslations(project.visual_translations ?? {});
    setSelectedPexels(project.selected_pexels ?? {});
    setPexelsExpectedCount(project.pexels_expected_count ?? 0);
    setExpandedPexelsScene(null);
    setPexelsPage(0);
    setTimingWarnings([]);
    setFlowExportReady(Boolean(project.source));
    setJobId("");
    setOutputUrl("");
    setRenderError("");
    if (project.source.trim()) {
      try {
        const restoredScript = JSON.parse(project.source) as Script;
        setScript(restoredScript);
        setStatus(`Projeto “${project.name}” restaurado com ${sceneCount(restoredScript)} cenas.`);
      } catch {
        setScript(null);
        setStatus(`Projeto “${project.name}” restaurado, mas o JSON salvo precisa ser corrigido.`);
      }
    } else {
      setScript(null);
      setStatus(`Projeto “${project.name}” pronto para receber o roteiro.`);
    }
  };

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(LOCAL_PROJECTS_STORAGE_KEY) ?? "[]") as LocalProject[];
      const valid = Array.isArray(stored) ? stored.filter(item => item && typeof item.id === "string") : [];
      const activeId = window.localStorage.getItem(LOCAL_ACTIVE_PROJECT_STORAGE_KEY);
      const initial = valid.find(item => item.id === activeId) ?? valid[0] ?? newLocalProject();
      const nextProjects = valid.some(item => item.id === initial.id) ? valid : [initial];
      setProjects(nextProjects);
      restoreProject(initial);
    } catch {
      const initial = newLocalProject();
      setProjects([initial]);
      restoreProject(initial);
    } finally {
      projectsHydrated.current = true;
    }
  }, []);

  useEffect(() => {
    if (!projectsHydrated.current || !projectId) return;
    const snapshot: LocalProject = {
      id: projectId, name: projectName.trim() || "Projeto sem título", updated_at: new Date().toISOString(), source, uploaded_images: uploadedImages,
      image_bindings: imageBindings, background, music, animation, pexels_items: pexelsItems,
      pexels_queries: pexelsQueries, translations, visual_translations: visualTranslations,
      selected_pexels: selectedPexels, pexels_expected_count: pexelsExpectedCount,
    };
    setProjects(current => {
      const next = [snapshot, ...current.filter(item => item.id !== snapshot.id)];
      try {
        window.localStorage.setItem(LOCAL_PROJECTS_STORAGE_KEY, JSON.stringify(next));
        window.localStorage.setItem(LOCAL_ACTIVE_PROJECT_STORAGE_KEY, snapshot.id);
      } catch { /* quota/storage indisponível: o trabalho na tela continua intacto */ }
      return next;
    });
  }, [projectId, projectName, source, uploadedImages, imageBindings, background, music, animation, pexelsItems, pexelsQueries, translations, visualTranslations, selectedPexels, pexelsExpectedCount]);

  const createProject = () => {
    const next = newLocalProject(`Projeto ${projects.length + 1}`);
    setProjects(current => [next, ...current]);
    restoreProject(next);
    setProjectDialogOpen(false);
  };

  const chooseProject = (id: string) => {
    const next = projects.find(item => item.id === id);
    if (next) restoreProject(next);
    setProjectDialogOpen(false);
  };

  const deleteProject = (id: string) => {
    const target = projects.find(project => project.id === id);
    if (!target || !window.confirm(`Excluir o projeto “${target.name}”? Os arquivos enviados não serão apagados.`)) return;
    const remaining = projects.filter(project => project.id !== id);
    const fallback = remaining[0] ?? newLocalProject();
    const nextProjects = remaining.length ? remaining : [fallback];
    setProjects(nextProjects);
    try { window.localStorage.setItem(LOCAL_PROJECTS_STORAGE_KEY, JSON.stringify(nextProjects)); } catch { /* storage indisponível */ }
    restoreProject(fallback);
  };

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
      if (next.title?.trim()) setProjectName(projectTitle(next.title));
      if (announce) setStatus(`${sceneCount(next)} cenas carregadas. Voz e narrativa vêm do JSON.`);
      return next;
    } catch {
      if (announce) setStatus("O JSON não é válido. Corrija-o antes de renderizar.");
      return null;
    }
  };

  const copyScriptPrompt = async () => {
    try {
      const prompt = await api<string>("/api/script-prompt");
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(prompt);
      } else {
        const temporary = document.createElement("textarea");
        temporary.value = prompt;
        temporary.style.position = "fixed";
        temporary.style.opacity = "0";
        document.body.appendChild(temporary);
        temporary.select();
        document.execCommand("copy");
        temporary.remove();
      }
      if (promptCopyTimer.current !== null) window.clearTimeout(promptCopyTimer.current);
      setPromptCopied(true);
      promptCopyTimer.current = window.setTimeout(() => {
        setPromptCopied(false);
        promptCopyTimer.current = null;
      }, 1800);
      setStatus("Prompt canônico copiado. Cole-o no ChatGPT e preencha o tema do vídeo.");
    } catch (error) {
      setStatus(`Não foi possível copiar o prompt: ${readableError(error)}`);
    }
  };

  useEffect(() => () => {
    if (promptCopyTimer.current !== null) window.clearTimeout(promptCopyTimer.current);
  }, []);

  const readJsonFile = async (file: File) => {
    const text = await file.text();
    setImageBindings({});
    setActiveImagePickerKey(null);
    setTimingWarnings([]);
    setFlowExportReady(false);
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

  const uploadMedia = async (endpoint: "/api/images" | "/api/backgrounds" | "/api/music", files: FileList | File[]): Promise<string[]> => {
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

  const uploadMusic = async (files: FileList | File[]) => {
    try {
      setStatus("Importando trilha sonora…");
      const saved = await uploadMedia("/api/music", files);
      if (saved.length) {
        setMusic(saved.at(-1) ?? "");
        setStatus("Trilha importada e selecionada para este projeto.");
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

  const onMusicInput = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) void uploadMusic(event.target.files);
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

  const pexelsQueryPayload = (activeScript: Script): Record<string, string> => {
    const result: Record<string, string> = {};
    activeScript.blocks.forEach(block => block.scenes.forEach(scene => {
      const query = pexelsQueries[scene.id]?.trim();
      if (scene.tipo_midia === "video_generico" && query) result[scene.id] = query;
    }));
    return result;
  };

  const searchPexels = async (sceneId?: string) => {
    const activeScript = parseScript(source, false);
    if (!activeScript) {
      setStatus("Cole ou importe um roteiro JSON antes de buscar B-roll.");
      return;
    }
    try {
      setPexelsBusy(true);
      setStatus("Buscando alternativas horizontais no Pexels…");
      const result = await api<{ items: PexelsItem[]; expected_scene_ids: string[] }>("/api/pexels/candidates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script: activeScript, queries: pexelsQueryPayload(activeScript), ...(sceneId ? { scene_id: sceneId } : {}) }),
      });
      const existingIndex = sceneId ? pexelsItems.findIndex(item => item.scene_id === sceneId) : -1;
      setPexelsItems(current => {
        if (!sceneId) return result.items;
        const index = current.findIndex(item => item.scene_id === sceneId);
        if (index < 0) return [...current, ...result.items];
        const next = [...current];
        next.splice(index, 1, ...result.items);
        return next;
      });
      setPexelsPage(sceneId && existingIndex >= 0 ? Math.floor(existingIndex / PEXELS_SCENES_PAGE_SIZE) : 0);
      if (!sceneId) setPexelsExpectedCount(result.expected_scene_ids.length);
      setTranslations(current => sceneId
        ? Object.fromEntries(Object.entries(current).filter(([key]) => key !== sceneId))
        : {});
      setVisualTranslations(current => sceneId
        ? Object.fromEntries(Object.entries(current).filter(([key]) => key !== sceneId))
        : {});
      if (sceneId) {
        setSelectedPexels(current => {
          const next = { ...current };
          delete next[sceneId];
          return next;
        });
      }
      void translatePexelsItems(activeScript, result.items);
      setMediaTab("curadoria");
      const missing = result.expected_scene_ids.filter(id => !result.items.some(item => item.scene_id === id));
      const failedSearches = result.items.filter(item => item.search_error).length;
      setStatus(
        missing.length
          ? `Falha de conferência: faltam ${missing.join(", ")} na curadoria.`
          : result.items.length
            ? `${result.items.length}/${result.expected_scene_ids.length} cenas video_generico carregadas.${failedSearches ? ` ${failedSearches} cena(s) sem opções; ajuste a descrição e busque novamente.` : " Escolha um B-roll por cena."}`
            : "O roteiro não possui cenas de vídeo genérico.",
      );
    } catch (error) {
      setStatus(readableError(error));
    } finally {
      setPexelsBusy(false);
    }
  };

  const downloadPexelsVideo = async (item: PexelsItem, candidate: PexelsCandidate) => {
    const activeScript = parseScript(source, false);
    if (!activeScript) return;
    try {
      setPexelsBusy(true);
      setStatus(`Baixando B-roll aprovado para ${item.scene_id}…`);
      const result = await api<{ filename: string }>("/api/pexels/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script: activeScript,
          queries: { ...pexelsQueryPayload(activeScript), [item.scene_id]: pexelsQueries[item.scene_id]?.trim() || item.query },
          scene_id: item.scene_id,
          video_id: candidate.id,
        }),
      });
      await refreshCatalog();
      setSelectedPexels(current => ({ ...current, [item.scene_id]: candidate }));
      setExpandedPexelsScene(null);
      setStatus(`B-roll ${result.filename} aprovado e salvo. Ele já será usado pela cena ${item.scene_id}.`);
    } catch (error) {
      setStatus(readableError(error));
    } finally {
      setPexelsBusy(false);
    }
  };

  const translatePexelsItems = async (activeScript: Script, items: PexelsItem[]) => {
    if (activeScript.language.toLowerCase().startsWith("pt")) {
      setTranslations(current => ({ ...current, ...Object.fromEntries(items.map(item => [item.scene_id, item.text])) }));
      setVisualTranslations(current => ({ ...current, ...Object.fromEntries(items.filter(item => item.visual_reference).map(item => [item.scene_id, item.visual_reference ?? ""])) }));
      return;
    }
    const pending = items.flatMap(item => [
      { sceneId: item.scene_id, kind: "text" as const, value: item.text },
      ...(item.visual_reference ? [{ sceneId: item.scene_id, kind: "visual" as const, value: item.visual_reference }] : []),
    ]);
    setTranslationLoading(current => ({ ...current, ...Object.fromEntries(items.map(item => [item.scene_id, true])) }));
    setVisualTranslationLoading(current => ({ ...current, ...Object.fromEntries(items.filter(item => item.visual_reference).map(item => [item.scene_id, true])) }));
    // Mantém poucas requisições simultâneas ao serviço de tradução, sem exigir
    // que o operador clique cena por cena ou sature o serviço externo.
    const worker = async () => {
      while (pending.length) {
        const task = pending.shift();
        if (!task) return;
        try {
          const result = await api<{ portuguese: string }>("/api/translate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: task.value, source_language: activeScript.language }),
          });
          if (task.kind === "text") setTranslations(current => ({ ...current, [task.sceneId]: result.portuguese }));
          else setVisualTranslations(current => ({ ...current, [task.sceneId]: result.portuguese }));
        } catch {
          if (task.kind === "text") setTranslations(current => ({ ...current, [task.sceneId]: "Tradução indisponível; use o original acima." }));
          else setVisualTranslations(current => ({ ...current, [task.sceneId]: "Tradução indisponível; use a referência original acima." }));
        } finally {
          if (task.kind === "text") setTranslationLoading(current => ({ ...current, [task.sceneId]: false }));
          else setVisualTranslationLoading(current => ({ ...current, [task.sceneId]: false }));
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, pending.length) }, worker));
  };

  const openVideosFolder = async () => {
    try {
      await api<{ folder: string }>("/api/pexels/open-folder", { method: "POST" });
      setStatus("A pasta local dos B-rolls foi aberta no Explorador de Arquivos.");
    } catch (error) {
      setStatus(readableError(error));
    }
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
      setFlowExportReady(report.valid);
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
      setFlowExportReady(false);
      setStatus(readableError(error));
    }
  };

  const downloadGoogleFlowTxt = () => {
    const activeScript = parseScript(source, false);
    if (!activeScript || !flowExportReady) {
      setStatus("Valide o JSON antes de preparar o arquivo para o Google Flow.");
      return;
    }
    const result = googleFlowText(activeScript, flowBatchSize);
    if (!result.imageCount) {
      setStatus("Este roteiro não possui cenas com tipo_midia: imagem para enviar ao Google Flow.");
      return;
    }
    const filename = `google-flow_${fileSlug(activeScript.title)}_${result.imageCount}-imagens_lotes-${flowBatchSize}.txt`;
    downloadTextFile(filename, result.text);
    setStatus(`TXT do Google Flow baixado: ${result.imageCount} imagens em ${result.batchCount} lote(s). Vídeos, transições e sons foram ignorados.`);
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
  const requiredAssets = sceneAssets(script);
  const linkedImages = linkedSceneCount(script, imageBindings, uploadedImages, catalog.images, catalog.videos);
  const thumbnailPageCount = Math.max(1, Math.ceil(uploadedImages.length / THUMBNAIL_PAGE_SIZE));
  const currentThumbnailPage = Math.min(thumbnailPage, thumbnailPageCount - 1);
  const thumbnailStart = currentThumbnailPage * THUMBNAIL_PAGE_SIZE;
  const visibleUploadedImages = uploadedImages.slice(thumbnailStart, thumbnailStart + THUMBNAIL_PAGE_SIZE);
  const imageProgress = requiredAssets.length ? Math.round((linkedImages / requiredAssets.length) * 100) : 0;
  const scriptProgress = script ? 100 : 0;
  const backgroundProgress = background ? 100 : 0;
  const pexelsPageCount = Math.max(1, Math.ceil(pexelsItems.length / PEXELS_SCENES_PAGE_SIZE));
  const currentPexelsPage = Math.min(pexelsPage, pexelsPageCount - 1);
  const pexelsStart = currentPexelsPage * PEXELS_SCENES_PAGE_SIZE;
  const visiblePexelsItems = pexelsItems.slice(pexelsStart, pexelsStart + PEXELS_SCENES_PAGE_SIZE);

  return (
    <main className="app-shell">
      <header className="appbar">
        <div className="brand"><span className="brand-mark">SR</span><span>SynthReel</span><small>horizontal</small></div>
        <div className="appbar-actions">
          <button className="project-trigger" onClick={() => setProjectDialogOpen(true)} title="Abrir projetos salvos"><span>Projetos</span><b>{projectName}</b><i>⌄</i></button>
          <span className="scene-indicator">{script ? `${sceneCount(script)} cenas` : "sem roteiro"}</span>
          <button className="button quiet" onClick={validate}>Validar</button>
          <button className="button quiet" disabled={!script || pexelsBusy} onClick={() => void searchPexels()}>{pexelsBusy ? "Buscando…" : "Buscar B-roll"}</button>
          <button className="button primary" disabled={Boolean(jobId)} onClick={render}>{jobId ? "Renderizando…" : "Gerar vídeo"}</button>
        </div>
      </header>

      <section className="workbench" aria-label="Área de produção">
        <article className="panel json-panel">
          <div className="panel-header">
            <div><span className="panel-index">01</span><h1>Roteiro JSON</h1></div>
            <div className="json-header-actions"><button className={`button quiet compact prompt-copy-button${promptCopied ? " copied" : ""}`} onClick={() => void copyScriptPrompt()}>{promptCopied ? "✓ Copiado" : "Copiar prompt"}</button><button className="button quiet compact" onClick={() => jsonInput.current?.click()}>Importar JSON</button></div>
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
              setFlowExportReady(false);
            }}
          />
          <section className={`script-feedback${timingWarnings.length ? " has-warnings" : ""}`} aria-label="Validação e avisos do roteiro">
            {timingWarnings.length > 0 ? <>
              <b>Prévia acústica · {timingWarnings.length} cena(s) precisam de corte</b>
              {timingWarnings.map(scene => (
                <div className="timing-warning" key={scene.id}>
                  <span><b>{scene.id}</b> — {scene.duration.toFixed(2)} s</span>
                  <small>Corte: “{scene.suggested_split?.first_text ?? "divida próximo à metade"}” / “{scene.suggested_split?.second_text ?? "crie a segunda cena"}”</small>
                </div>
              ))}
            </> : <span>Validação, duração acústica e sugestões de corte aparecem aqui.</span>}
          </section>
          {script && <section className="flow-export" aria-label="Exportação para Google Flow">
            <div><b>Google Flow</b><small>Exporta somente cenas de imagem; B-roll, transições, sons e anotações ficam de fora.</small></div>
            <label>Lotes
              <select value={flowBatchSize} onChange={event => setFlowBatchSize(Number(event.target.value) as 25 | 50)}>
                <option value={25}>25 imagens</option>
                <option value={50}>50 imagens</option>
              </select>
            </label>
            <button className="button quiet compact" disabled={!flowExportReady} title={flowExportReady ? "Baixar roteiro compacto para o Google Flow" : "Valide o JSON para liberar a exportação"} onClick={downloadGoogleFlowTxt}>Baixar TXT Flow</button>
          </section>}
          <div className="music-select" onMouseEnter={startMusicPreview} onMouseLeave={stopMusicPreview}>
            <span><b>Trilha do vídeo</b><small>{catalog.music.length ? "Escolha a música usada nesta renderização" : "Nenhuma música disponível"}</small></span>
            <button type="button" className={`music-preview-toggle${musicPreviewPlaying ? " playing" : ""}`} aria-label={musicPreviewPlaying ? "Pausar prévia da trilha" : "Ouvir prévia da trilha"} title={musicPreviewPlaying ? "Pausar prévia" : "Ouvir prévia"} disabled={!music} onClick={toggleMusicPreview}>{musicPreviewPlaying ? "❚❚" : "▶"}</button>
            <label className="music-preview-volume" title="Volume da prévia">
              <span>🔈</span>
              <input type="range" min="0" max="0.35" step="0.01" value={musicPreviewVolume} aria-label="Volume da prévia da trilha" onChange={event => {
                const volume = Number(event.target.value);
                setMusicPreviewVolume(volume);
                if (musicPreview.current) musicPreview.current.volume = volume;
              }} />
            </label>
            <button type="button" className="music-import" onClick={() => musicInput.current?.click()} title="Importar uma trilha MP3, WAV ou M4A">＋</button>
            <input ref={musicInput} type="file" hidden accept="audio/mpeg,audio/wav,audio/x-wav,audio/mp4,.mp3,.wav,.m4a" onChange={onMusicInput} />
            <select
              aria-label="Trilha do vídeo"
              value={music}
              disabled={!catalog.music.length || Boolean(jobId)}
              onChange={event => { stopMusicPreview(); setMusic(event.target.value); }}
            >
              {!catalog.music.length && <option value="">Sem músicas disponíveis</option>}
              {catalog.music.map(item => <option key={item} value={item}>{mediaLabel(item)}</option>)}
            </select>
          </div>
        </article>

        <article className="panel scenes-panel">
          <div className="panel-header">
            <div><span className="panel-index">02</span><h1>Mídias das cenas</h1></div>
            <div className="scene-panel-actions">
              <span className="panel-count">{uploadedImages.length} enviada(s) nesta tela</span>
              {uploadedImages.length > 0 && (
                <button className="button quiet compact" onClick={clearUploadedImages}>Limpar lista</button>
              )}
            </div>
          </div>
          <div className="media-tabs" aria-label="Áreas de mídia">
            <button className={mediaTab === "assets" ? "active" : ""} onClick={() => setMediaTab("assets")}>Imagens e vínculos</button>
            <button className={mediaTab === "curadoria" ? "active" : ""} onClick={() => setMediaTab("curadoria")}>Curadoria B-roll{pexelsItems.length ? ` · ${pexelsItems.length}` : ""}</button>
          </div>
          {!script && <div className="media-await"><b>Carregue o roteiro para preparar as mídias.</b><span>As cenas, os vínculos e a curadoria aparecem aqui depois da leitura do JSON.</span></div>}
          {script && mediaTab === "assets" && <>
          <div className="asset-drop" onDragOver={event => event.preventDefault()} onDrop={dropImages} onClick={() => imagesInput.current?.click()} role="button" tabIndex={0}>
            <b>Solte as imagens IA aqui</b><span>ou clique para importar</span>
          </div>
          <input ref={imagesInput} type="file" hidden multiple accept="image/png,image/jpeg,image/webp" onChange={onImagesInput} />
          <div className="flow-progress image-progress" aria-label="Progresso das imagens">
            <div><span>Assets prontos</span><b>{script ? `${linkedImages}/${requiredAssets.length}` : "aguardando roteiro"}</b></div>
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
                <span>Revisão de mídia por cena</span>
                <button className="button quiet compact" onClick={() => void openVideosFolder()}>Abrir pasta dos vídeos</button>
              </div>
              {!uploadedImages.length && <p className="scene-bindings-empty">Envie imagens para liberar as opções de vínculo. Nenhuma imagem antiga é exibida aqui.</p>}
              {uploadedImages.length > 0 && <p className="scene-bindings-note">O ID é a referência editorial da cena. O sistema compara o brief com os nomes descritivos que o Google Flow gerar; a ordem do envio não é usada.</p>}
              <div className="scene-binding-list">
                {script.blocks.flatMap((block, blockIndex) => block.scenes.map((scene, sceneIndex) => {
                  if (scene.tipo_midia === "video_generico") {
                    const downloaded = catalog.videos.includes(scene.image);
                    return <div className={`scene-binding ${downloaded ? "ready" : "pending"}`} key={`${block.id}-${scene.id}`}>
                      <span className="scene-binding-label"><b>Cena {blockIndex + 1} · vídeo</b><code>{scene.asset_key ?? scene.image}</code></span>
                      <span className="scene-binding-source">{downloaded ? <>B-roll salvo: <code>{scene.image}</code></> : <>Disponível na aba Curadoria: <code>{scene.image}</code></>}</span>
                    </div>;
                  }
                  const pickerKey = sceneBindingKey(blockIndex, sceneIndex);
                  const boundImage = boundImageFor(imageBindings, blockIndex, sceneIndex, scene.image);
                  const sourceImage = boundImage ?? scene.image;
                  const isReady = imageIsReady(sourceImage, uploadedImages, catalog.images);
                  const pickerImages = activeImagePickerKey === pickerKey ? uploadedImages.filter(image => image !== scene.image) : boundImage ? [boundImage] : [];
                  return (
                    <div className={`scene-binding${isReady ? " ready" : " pending"}`} key={`${block.id}-${scene.id}-${blockIndex}-${sceneIndex}`}>
                      <span className="scene-binding-label"><b>Cena {blockIndex + 1} · ID {scene.image_id}</b><code>{scene.asset_key ?? scene.image}</code></span>
                      <select aria-label={`Escolher imagem para a cena ${blockIndex + 1}`} value={boundImage ?? ""} disabled={!uploadedImages.length || Boolean(jobId)} onPointerDown={() => setActiveImagePickerKey(pickerKey)} onFocus={() => setActiveImagePickerKey(pickerKey)} onChange={event => bindUploadedImageToScene(blockIndex, sceneIndex, event.target.value)}>
                        <option value="">Usar {scene.image} (nome do JSON)</option>{pickerImages.map(image => <option key={image} value={image}>{image}</option>)}
                      </select>
                      <span className="scene-binding-source">{boundImage ? <>Arquivo enviado: <code>{boundImage}</code></> : <>Referência editorial: <code>ID {scene.image_id}</code></>}</span>
                      {boundImage && <button type="button" className="button quiet compact scene-binding-rename" disabled={Boolean(jobId)} onClick={event => { event.preventDefault(); renameJsonImageFromBinding(blockIndex, sceneIndex); }}>Trocar nome no JSON</button>}
                    </div>
                  );
                }))}
              </div>
            </div>
          )}
          </>}
          {script && mediaTab === "curadoria" && (
            <section className="pexels-review standalone" aria-label="Curadoria de B-roll do Pexels">
              <div className="curation-heading"><div><span className="panel-index">B-ROLL</span><b>Escolhas editoriais</b><small>O gancho e as inserções visíveis passam pela sua aprovação.</small></div><button className="button quiet compact" onClick={() => void openVideosFolder()}>Abrir pasta dos vídeos</button></div>
              {pexelsItems.length > 0 ? <>
                  <p className="scene-bindings-note">Prévia automática, tradução e aprovação humana de B-roll horizontal. As cenas são paginadas para não carregar todas as prévias de uma vez. Vídeos fornecidos por <a href="https://www.pexels.com" target="_blank" rel="noreferrer">Pexels</a>.</p>
                  <div className="pexels-results-summary"><b>Conferência: {pexelsItems.length}/{pexelsExpectedCount || pexelsItems.length} cenas video_generico</b><span>Exibindo cenas {pexelsStart + 1}–{Math.min(pexelsStart + PEXELS_SCENES_PAGE_SIZE, pexelsItems.length)} · página {currentPexelsPage + 1} de {pexelsPageCount}</span></div>
                  {visiblePexelsItems.map(item => {
                    const scene = script?.blocks.flatMap(block => block.scenes).find(candidate => candidate.id === item.scene_id);
                    const saved = Boolean(scene && catalog.videos.includes(scene.image));
                    const chosen = selectedPexels[item.scene_id];
                    const collapsed = Boolean(chosen && expandedPexelsScene !== item.scene_id);
                    return (
                      <article className={`pexels-item${chosen ? " selected" : ""}`} key={item.scene_id}>
                        <div className="pexels-copy"><b>{item.scene_id} · {saved ? "aprovado" : "aguardando aprovação"}</b><span>Original: {item.text}</span>
                          <span>Português: {translations[item.scene_id] ?? (translationLoading[item.scene_id] ? "traduzindo…" : "traduzindo…")}</span>
                          {item.visual_reference && <span className="pexels-reference">REFERÊNCIA VISUAL (PT-BR): {visualTranslations[item.scene_id] ?? (visualTranslationLoading[item.scene_id] ? "traduzindo…" : "traduzindo…")}</span>}
                        </div>
                        {chosen && <button className="pexels-selected-summary" type="button" onClick={() => setExpandedPexelsScene(current => current === item.scene_id ? null : item.scene_id)}>
                          <span>✓ Escolhido · {chosen.width}×{chosen.height} · {chosen.duration ?? "?"}s</span><b>{expandedPexelsScene === item.scene_id ? "Minimizar" : "Trocar vídeo"}</b>
                        </button>}
                        {!collapsed && <>
                        {item.is_annotation && <p className="annotation-broll">FUNÇÃO: este vídeo fica fullscreen atrás da anotação, com blur leve. Ele não é uma cena independente; escolha a atmosfera que você quer que permaneça visível por trás do texto.</p>}
                        <div className="pexels-search"><input value={pexelsQueries[item.scene_id] ?? item.query} aria-label={`Busca Pexels para ${item.scene_id}`} onChange={event => setPexelsQueries(current => ({ ...current, [item.scene_id]: event.target.value }))} /><button className="button quiet compact" disabled={pexelsBusy} onClick={() => void searchPexels(item.scene_id)}>Buscar 4 novas opções</button></div>
                        <div className="pexels-candidates">
                          {item.candidates.map(candidate => (
                            <figure key={candidate.id}>
                              {pexelsPreviewErrors[`${item.scene_id}:${candidate.id}`] ? <div className="pexels-preview-error"><span>Prévia indisponível neste navegador.</span>{candidate.pexels_url && <a href={candidate.pexels_url} target="_blank" rel="noreferrer">Abrir no Pexels</a>}</div> : <video src={candidate.preview_url} poster={candidate.thumbnail} controls autoPlay muted loop playsInline preload="metadata" onError={() => setPexelsPreviewErrors(current => ({ ...current, [`${item.scene_id}:${candidate.id}`]: true }))} />}
                              <figcaption>{candidate.width}×{candidate.height} · {candidate.duration ?? "?"}s{candidate.creator ? ` · ${candidate.creator}` : ""}</figcaption>
                              <button className="button compact" disabled={pexelsBusy} onClick={() => void downloadPexelsVideo(item, candidate)}>{item.is_annotation ? "Usar como fundo" : "Usar este vídeo"}</button>
                            </figure>
                          ))}
                          {!item.candidates.length && <p className="asset-grid-empty">{item.search_error ?? "Nenhum B-roll horizontal foi encontrado."} Altere a descrição em inglês e busque de novo.</p>}
                        </div>
                        </>}
                      </article>
                    );
                  })}
                  {pexelsItems.length > PEXELS_SCENES_PAGE_SIZE && <div className="asset-grid-pagination pexels-pagination" aria-label="Navegação da curadoria Pexels">
                    <span>Cenas {pexelsStart + 1}–{Math.min(pexelsStart + PEXELS_SCENES_PAGE_SIZE, pexelsItems.length)} de {pexelsItems.length}</span>
                    <div><button className="button quiet compact" disabled={currentPexelsPage === 0} onClick={() => setPexelsPage(page => Math.max(0, page - 1))}>Anteriores</button><button className="button quiet compact" disabled={currentPexelsPage >= pexelsPageCount - 1} onClick={() => setPexelsPage(page => Math.min(pexelsPageCount - 1, page + 1))}>Próximas</button></div>
                  </div>}
              </> : <div className="curation-empty"><b>Nenhuma busca realizada</b><span>Depois de validar o roteiro, use “Buscar B-roll” no cabeçalho. A curadoria aparece aqui, sem disputar espaço com as imagens.</span></div>}
            </section>
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
              {catalog.backgrounds.map(item => <option key={item} value={item}>{mediaLabel(item)}</option>)}
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

      {projectDialogOpen && <div className="project-dialog-backdrop" role="presentation" onMouseDown={() => setProjectDialogOpen(false)}>
        <section className="project-dialog" role="dialog" aria-modal="true" aria-label="Projetos" onMouseDown={event => event.stopPropagation()}>
          <header><div><span>Projetos</span><h2>Onde você parou</h2></div><button className="dialog-close" onClick={() => setProjectDialogOpen(false)} aria-label="Fechar projetos">×</button></header>
          <p>O projeto atual é salvo automaticamente neste navegador.</p>
          <div className="project-list">
            {projects.map(project => <article className={project.id === projectId ? "active" : ""} key={project.id}>
              <button className="project-open" onClick={() => chooseProject(project.id)}><b>{project.name}</b><small>{project.source ? `${project.source.length.toLocaleString("pt-BR")} caracteres salvos` : "Ainda sem roteiro"}</small></button>
              <button className="project-delete" onClick={() => deleteProject(project.id)} title={`Excluir ${project.name}`} aria-label={`Excluir ${project.name}`}>⌫</button>
            </article>)}
          </div>
          <button className="button primary project-new" onClick={createProject}>＋ Criar novo projeto</button>
        </section>
      </div>}

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
