import { ChangeEvent, DragEvent, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type BackgroundAnimation = "none" | "movimento_sutil" | "movimento_lateral" | "pulsacao";
type TextStyle = "impact" | "serif_vintage" | "minimalista" | "constelacao_dourada" | "impact_sem_borda" | "branco_limpo" | "neon_violeta" | "coral_contorno" | "ouro_sem_contorno" | "prata_azul" | "verde_lima" | "azul_eletrico" | "vermelho_alerta" | "rosa_chiclete" | "laranja_energia" | "cinza_aco" | "azul_marinho" | "roxo_real" | "verde_menta" | "amarelo_retro";
type MediaTab = "assets" | "review" | "curadoria";
type PromptMode = "with_broll" | "without_broll" | "psychology_without_broll" | "cats_without_broll";

type MediaType = "imagem" | "video_generico";
type Scene = {
  id: string; image_id: number; tipo_midia: MediaType; asset_key?: string; image: string;
  visual?: { subject?: string; action?: string; setting?: string; framing?: string; details?: string };
  transition?: { in?: string; out?: string };
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
  uploaded_media_newest_first?: boolean;
  image_bindings: ImageBindings;
  background: string;
  music: string;
  animation: BackgroundAnimation;
  text_style: TextStyle;
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
  music_name?: string | null;
  error?: string;
  progress?: number;
  stage?: string;
  render_elapsed_seconds?: number;
  estimated_remaining_seconds?: number;
  error_code?: string;
  error_detail?: string;
  log_url?: string;
  events_url?: string;
};

function formatRemainingTime(seconds: number): string {
  const rounded = Math.max(0, Math.ceil(seconds / 5) * 5);
  const minutes = Math.floor(rounded / 60);
  const remainingSeconds = rounded % 60;
  if (minutes >= 60) return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  if (minutes) return remainingSeconds ? `${minutes} min ${remainingSeconds} s` : `${minutes} min`;
  return `${remainingSeconds} s`;
}
type AnimationAutomationStatus = {
  running: boolean;
  pid: number | null;
  started_at: number | null;
  last_return_code: number | null;
  completed_images: number;
  total_images: number;
  current_state: string | null;
  current_image: string | null;
  last_event: string | null;
  message: string;
  resume_available: boolean;
  resume_url: string | null;
  project_id?: string | null;
};
type TimingScene = {
  id: string;
  duration: number;
  suggested_split?: { first_text: string; second_text: string };
};
type TimingReport = { narration_duration: number; scenes: TimingScene[] };
type SceneCompositionPreview = {
  scene: Scene; blockIndex: number; sceneIndex: number; sourceImage: string | null; isVideo: boolean; fullscreen: boolean;
};

const animationOptions: { value: BackgroundAnimation; label: string }[] = [
  { value: "movimento_sutil", label: "Movimento suave" },
  { value: "movimento_lateral", label: "Movimento lateral" },
  { value: "pulsacao", label: "Pulsação" },
  { value: "none", label: "Sem movimento" },
];
const textStyleOptions: { value: TextStyle; label: string; description: string; flowInstruction: string }[] = [
  {
    value: "constelacao_dourada",
    label: "Constelação dourada",
    description: "Serif dourada · contorno suave",
    flowInstruction: "Use letras em espanhol, em MAIÚSCULAS, desenhadas por linhas finas de luz dourada que conectam estrelas como uma constelação. Integre-as ao espaço negativo da cena e use-as somente nos momentos de revelação, dilema ou alívio.",
  },
  {
    value: "impact",
    label: "Impact forte",
    description: "Amarela · borda preta forte",
    flowInstruction: "Use letras fortes, em MAIÚSCULAS, de alto contraste, no estilo Impact. Reserve-as apenas para textos estratégicos e mantenha a leitura imediata.",
  },
  {
    value: "serif_vintage",
    label: "Serif vintage",
    description: "Creme · contorno marrom",
    flowInstruction: "Use uma tipografia serifada vintage, elegante e legível. Quando houver texto estratégico, mantenha-o curto, em espanhol e integrado organicamente à composição.",
  },
  {
    value: "minimalista",
    label: "Sans minimalista",
    description: "Ciano claro · sem borda",
    flowInstruction: "Use uma tipografia sans-serif limpa, minimalista e muito legível. Quando houver texto estratégico, mantenha-o curto, em espanhol e com bastante espaço negativo.",
  },
  {
    value: "impact_sem_borda",
    label: "Impact sem borda",
    description: "Rosa vibrante · sem contorno",
    flowInstruction: "Use letras Impact em MAIÚSCULAS, rosa vibrante e sem borda. Mantenha contraste suficiente com o fundo e use texto em espanhol somente nos momentos realmente importantes.",
  },
  {
    value: "branco_limpo",
    label: "Branco limpo",
    description: "Branca · sem borda",
    flowInstruction: "Use texto branco limpo, sem borda e com composição editorial minimalista. Mantenha-o curto, em espanhol, e sempre muito legível sobre espaço negativo escuro.",
  },
  {
    value: "neon_violeta",
    label: "Neon violeta",
    description: "Violeta · brilho e borda escura",
    flowInstruction: "Use texto violeta luminoso, com brilho sutil e contorno escuro. Use espanhol e evite excesso de neon ou aparência futurista fora do próprio texto.",
  },
  {
    value: "coral_contorno",
    label: "Coral contornado",
    description: "Coral · borda preta marcante",
    flowInstruction: "Use letras em coral quente, com contorno preto marcante, em espanhol e em MAIÚSCULAS. Reserve-as para uma afirmação emocional ou chamada decisiva.",
  },
  { value: "ouro_sem_contorno", label: "Ouro limpo", description: "Dourada · sem borda", flowInstruction: "Use texto dourado limpo, elegante, sem borda e em espanhol apenas nos momentos de destaque." },
  { value: "prata_azul", label: "Prata azul", description: "Prateada · contorno azul", flowInstruction: "Use letras prateadas com contorno azul profundo, limpas e legíveis, em espanhol para uma revelação racional." },
  { value: "verde_lima", label: "Verde lima", description: "Lima · borda escura", flowInstruction: "Use letras verde-lima com contorno escuro, em espanhol e com alto contraste, para destacar uma descoberta." },
  { value: "azul_eletrico", label: "Azul elétrico", description: "Azul · borda marinho", flowInstruction: "Use letras azul-elétrico com contorno azul-marinho e brilho sutil, em espanhol somente em pontos-chave." },
  { value: "vermelho_alerta", label: "Vermelho alerta", description: "Vermelha · borda vinho", flowInstruction: "Use texto vermelho intenso, com contorno vinho, em espanhol e somente quando houver alerta ou consequência importante." },
  { value: "rosa_chiclete", label: "Rosa chiclete", description: "Rosa · contorno vinho", flowInstruction: "Use letras rosa-chiclete com contorno vinho, em espanhol, com aparência editorial divertida e legível." },
  { value: "laranja_energia", label: "Laranja energia", description: "Laranja · borda marrom", flowInstruction: "Use letras laranja vibrante com contorno marrom escuro, em espanhol, para uma virada energética." },
  { value: "cinza_aco", label: "Cinza aço", description: "Cinza · borda grafite", flowInstruction: "Use tipografia cinza-aço, monoespaçada e com contorno grafite, em espanhol para fatos, dados ou contraste racional." },
  { value: "azul_marinho", label: "Azul marinho", description: "Azul claro · borda profunda", flowInstruction: "Use letras azul-claro com contorno azul-marinho, serifadas e elegantes, em espanhol para reflexão séria." },
  { value: "roxo_real", label: "Roxo real", description: "Lilás · borda púrpura", flowInstruction: "Use letras lilás com contorno púrpura profundo, em espanhol e com tom sofisticado para uma conclusão marcante." },
  { value: "verde_menta", label: "Verde menta", description: "Menta · contorno verde", flowInstruction: "Use texto verde-menta com contorno verde-escuro, em espanhol, para alívio, solução ou mensagem positiva." },
  { value: "amarelo_retro", label: "Amarelo retrô", description: "Amarelo · borda oliva", flowInstruction: "Use letras amarelo-retro com contorno oliva, em espanhol, com aparência editorial clássica e legível." },
];
const LEGACY_SESSION_IMAGES_KEY = "synthreel:session-images";
const IMAGE_BINDING_STRATEGY_VERSION = "synthreel:semantic-image-bindings-v1";
const LOCAL_PROJECTS_STORAGE_KEY = "synthreel:horizontal-projects:v1";
const LOCAL_ACTIVE_PROJECT_STORAGE_KEY = "synthreel:horizontal-active-project:v1";
const PLACEHOLDER_IMAGE_PATTERN = /^cena_\d+(?:_[a-z])?\.(?:png|jpe?g|webp)$/i;
const VIDEO_MEDIA_PATTERN = /\.(?:mp4|mov|webm)$/i;
const THUMBNAIL_PAGE_SIZE = 12;
const SCENE_REVIEW_PAGE_SIZE = 6;
const PEXELS_SCENES_PAGE_SIZE = 4;
const TRANSLATION_CONCURRENCY = 2;
const RENDER_COMPLETE_SOUND_URL = "/assets/sounds/Mountain%20Audio%20-%20New%20Idea%20Notification.mp3";

function needsTranslation(original: string, translated: string | undefined, language: string): boolean {
  if (!translated) return true;
  if (language.toLowerCase().startsWith("pt")) return false;
  // Versões antigas do painel salvavam o original no campo de tradução após
  // um 503. Espaços diferentes não devem impedir que esse valor seja refeito.
  const normalize = (value: string) => value.trim().replace(/\s+/g, " ").toLocaleLowerCase();
  return normalize(original) === normalize(translated);
}

const formatVideoDuration = (seconds: number) => {
  const totalSeconds = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(totalSeconds / 60);
  const remainder = totalSeconds % 60;
  return minutes ? `${minutes} min ${remainder.toString().padStart(2, "0")} s` : `${remainder} s`;
};
const RENDER_ERROR_SOUND_URL = "/assets/sounds/Wrong%20Answer.mp3";
const PHOTO_VISUAL_PRESET = "Raw smartphone documentary photography, harsh direct flash, natural imperfections, slightly grainy texture, muted brown, gray and dark tones, worn everyday environments, candid unposed people, realistic ordinary faces, tired, neutral or concerned expressions, non-commercial appearance, clear main subject, simple composition, sharp enough to understand the scene, horizontal 16:9.";
const PHOTO_NEGATIVE_PROMPT = "Avoid glossy advertising, studio photography, cinematic lighting, luxury environments, perfect models, plastic skin, excessive retouching, overly clean surfaces, symmetrical posing, dramatic movie color grading, neon colors, oversaturation, artificial smiles, CGI appearance, 3D render, fantasy elements, abstract metaphors, excessive objects, visual clutter, deformed hands, distorted faces and unreadable text.";
const GRAPHIC_VISUAL_PRESET = "Simple editorial data visualization, clean neutral background, clear lines or bars, strong contrast, few elements, accurate proportions, visually understandable, horizontal 16:9.";
const GRAPHIC_NEGATIVE_PROMPT = "Avoid 3D charts, floating objects, metaphorical graphics, decorative illustrations, futuristic dashboards, excessive colors, perspective distortion, tiny labels, visual clutter and complex interfaces.";
const PSYCHOLOGY_LITHOGRAPH_VISUAL_PRESET = "Vintage cosmic lithograph illustration, therapeutic fairy-tale mood, distressed texture embedded across the full canvas (never a physical paper sheet or printed card), soft organic hand-drawn linework, deep silent dark void background, hopeful protagonist and symbolic tools drawn in delicate golden lines and constellations, open flowing composition, artwork bleeding cleanly to every edge as one continuous full-bleed image, horizontal 16:9.";
const PSYCHOLOGY_LITHOGRAPH_NEGATIVE_PROMPT = "Avoid frames, borders, decorative margins, dividers, enclosed panels, paper sheet edges, printed-card layout, inner rectangular image area, mat board, parchment margin, beige or white outline, lotus ornaments, modern glossy digital illustration, neon glow, 3D render, visual clutter, tiny unreadable text and watermarks.";
const CAT_EDITORIAL_ILLUSTRATION_PRESET = "Expressive editorial cat-behavior illustration, warm hand-drawn 2D look, clean cream or white background, high contrast, simple readable silhouettes, anatomically correct domestic cat posture and paws, consistent recurring cat and caretaker characters, subtle facial expressions, few objects, uncluttered composition, horizontal 16:9.";
const CAT_EDITORIAL_ILLUSTRATION_NEGATIVE_PROMPT = "Avoid photorealism, glossy advertising, 3D render, anime proportions, childish baby style, dark or busy backgrounds, excessive objects, extra limbs, deformed paws, distorted cat anatomy, human-like cat hands, scary expressions, text, captions, logos, watermarks and complex interfaces.";
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
    id: newProjectId(), name, updated_at: new Date().toISOString(), source: "", uploaded_images: [], uploaded_media_newest_first: true, image_bindings: {},
    background: "", music: "", animation: "movimento_sutil", text_style: "impact", pexels_items: [], pexels_queries: {},
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
  if (["litografia", "cosmica", "vintage"].every(term => terms.has(term))) {
    return { kind: "LITOGRAFIA CÓSMICA VINTAGE", preset: PSYCHOLOGY_LITHOGRAPH_VISUAL_PRESET, negative: PSYCHOLOGY_LITHOGRAPH_NEGATIVE_PROMPT };
  }
  if (["ilustracao", "felina", "editorial"].every(term => terms.has(term))) {
    return { kind: "ILUSTRAÇÃO FELINA EDITORIAL", preset: CAT_EDITORIAL_ILLUSTRATION_PRESET, negative: CAT_EDITORIAL_ILLUSTRATION_NEGATIVE_PROMPT };
  }
  if ([...terms].some(term => GRAPHIC_VISUAL_TERMS.has(term))) {
    return { kind: "GRÁFICO", preset: GRAPHIC_VISUAL_PRESET, negative: GRAPHIC_NEGATIVE_PROMPT };
  }
  return { kind: "FOTOGRAFIA DOCUMENTAL", preset: PHOTO_VISUAL_PRESET, negative: PHOTO_NEGATIVE_PROMPT };
}

function googleFlowText(script: Script, batchSize: number, textStyle: TextStyle): { text: string; imageCount: number; batchCount: number } {
  const typography = textStyleOptions.find(option => option.value === textStyle) ?? textStyleOptions[0];
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
      `- Tipografia selecionada para este projeto (${typography.label}): ${typography.flowInstruction} Esta escolha substitui qualquer sugestão genérica de fonte no brief, mas não autoriza texto em toda cena.`,
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

function isUploadedVideo(filename: string): boolean {
  return VIDEO_MEDIA_PATTERN.test(filename);
}

function uploadedMediaUrl(filename: string): string {
  const base = isUploadedVideo(filename) ? "/assets/videos/" : "/assets/images/";
  return `${base}${encodeURIComponent(filename)}`;
}

function sceneMediaUrl(filename: string, uploadedImages: string[]): string {
  return uploadedImages.includes(filename)
    ? uploadedMediaUrl(filename)
    : `${isUploadedVideo(filename) ? "/assets/videos/" : "/assets/images/"}${encodeURIComponent(filename)}`;
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
  const [sceneReviewPage, setSceneReviewPage] = useState(0);
  const [activeImagePickerKey, setActiveImagePickerKey] = useState<string | null>(null);
  const [activeScenePreview, setActiveScenePreview] = useState<SceneCompositionPreview | null>(null);
  const [background, setBackground] = useState("");
  const [music, setMusic] = useState("");
  const [animation, setAnimation] = useState<BackgroundAnimation>("movimento_sutil");
  const [textStyle, setTextStyle] = useState<TextStyle>("impact");
  const [status, setStatus] = useState("Aguardando roteiro JSON.");
  const [jobId, setJobId] = useState("");
  const [renderProgress, setRenderProgress] = useState(0);
  const [renderStage, setRenderStage] = useState("");
  const [renderElapsedSeconds, setRenderElapsedSeconds] = useState<number | null>(null);
  const [renderEtaSeconds, setRenderEtaSeconds] = useState<number | null>(null);
  const [outputUrl, setOutputUrl] = useState("");
  const [renderError, setRenderError] = useState("");
  const [timingWarnings, setTimingWarnings] = useState<TimingScene[]>([]);
  const [narrationDuration, setNarrationDuration] = useState<number | null>(null);
  const [validationFeedback, setValidationFeedback] = useState("");
  const [renderLogUrl, setRenderLogUrl] = useState("");
  const [pexelsItems, setPexelsItems] = useState<PexelsItem[]>([]);
  const [pexelsQueries, setPexelsQueries] = useState<Record<string, string>>({});
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [visualTranslations, setVisualTranslations] = useState<Record<string, string>>({});
  const [translationLoading, setTranslationLoading] = useState<Record<string, boolean>>({});
  const [visualTranslationLoading, setVisualTranslationLoading] = useState<Record<string, boolean>>({});
  const [translationFailures, setTranslationFailures] = useState<Record<string, boolean>>({});
  const [visualTranslationFailures, setVisualTranslationFailures] = useState<Record<string, boolean>>({});
  const [selectedPromptMode, setSelectedPromptMode] = useState<PromptMode>("with_broll");
  const [selectedPexels, setSelectedPexels] = useState<Record<string, PexelsCandidate>>({});
  const [expandedPexelsScene, setExpandedPexelsScene] = useState<string | null>(null);
  const [pexelsPreviewErrors, setPexelsPreviewErrors] = useState<Record<string, boolean>>({});
  const [pexelsPage, setPexelsPage] = useState(0);
  const [pexelsExpectedCount, setPexelsExpectedCount] = useState(0);
  const [pexelsBusy, setPexelsBusy] = useState(false);
  const [automation, setAutomation] = useState<AnimationAutomationStatus>({
    running: false, pid: null, started_at: null, last_return_code: null,
    completed_images: 0, total_images: 0, current_state: null, current_image: null, last_event: null,
    resume_available: false, resume_url: null, project_id: null, message: "Consultando automação…",
  });
  const [automationBusy, setAutomationBusy] = useState(false);
  const animationImageNames = uploadedImages.filter(filename => /\.(?:jpg|jpeg|png|webp)$/i.test(filename));
  const [mediaTab, setMediaTab] = useState<MediaTab>("assets");
  const [musicPreviewPlaying, setMusicPreviewPlaying] = useState(false);
  const [musicPreviewVolume, setMusicPreviewVolume] = useState(0.14);
  const [promptCopied, setPromptCopied] = useState<PromptMode | null>(null);
  const [flowExportReady, setFlowExportReady] = useState(false);
  const [flowBatchSize, setFlowBatchSize] = useState<25 | 50>(25);
  const jsonInput = useRef<HTMLInputElement>(null);
  const imagesInput = useRef<HTMLInputElement>(null);
  const scenePreviewInput = useRef<HTMLInputElement>(null);
  const backgroundInput = useRef<HTMLInputElement>(null);
  const musicInput = useRef<HTMLInputElement>(null);
  const notifiedCompletedJob = useRef<string | null>(null);
  const musicPreview = useRef<HTMLAudioElement | null>(null);
  const promptCopyTimer = useRef<number | null>(null);
  const projectsHydrated = useRef(false);
  // A leitura inicial do catálogo pode terminar depois de um upload. Sem um
  // identificador monotônico, essa resposta antiga reverte a música recém
  // importada para a trilha padrão.
  const catalogRefreshSequence = useRef(0);
  const translationRequests = useRef(new Map<string, Promise<string>>());
  const automaticBindingRequestKey = useRef("");

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
    const sequence = ++catalogRefreshSequence.current;
    try {
      const next = await api<Catalog>("/api/catalog");
      if (sequence !== catalogRefreshSequence.current) return;
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
      if (sequence !== catalogRefreshSequence.current) return;
      setStatus("Inicie o backend em http://localhost:8000.");
    }
  };

  const refreshAutomation = async () => {
    try {
      setAutomation(await api<AnimationAutomationStatus>("/api/automation"));
    } catch {
      setAutomation(current => ({ ...current, message: "Automação indisponível; inicie o backend." }));
    }
  };

  const automationCommand = async (command: "start" | "resume" | "stop" | "open-log") => {
    if ((command === "start" || command === "resume") && !animationImageNames.length) {
      setStatus("Envie ao menos uma imagem em Mídias das cenas antes de animar.");
      return;
    }
    if (command === "resume") {
      const destination = automation.resume_url ? `\n\n${automation.resume_url}` : "";
      const shouldResume = window.confirm(
        `Retomar explicitamente o projeto Vibes salvo? Isso não cria um projeto novo.${destination}`,
      );
      if (!shouldResume) {
        setStatus("Retomada cancelada. Nenhum projeto ou upload foi alterado.");
        return;
      }
    }
    setAutomationBusy(true);
    try {
      const init: RequestInit = command === "start" || command === "resume"
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filenames: animationImageNames, project_id: projectId, resume_existing: command === "resume" }) }
        : { method: "POST" };
      const endpoint = command === "resume" ? "start" : command;
      const result = await api<AnimationAutomationStatus | { path: string }>(`/api/automation/${endpoint}`, init);
      if ("running" in result) setAutomation(result);
      else await refreshAutomation();
      setStatus(command === "start" ? "Automação iniciada em um projeto Vibes novo." : command === "resume" ? "Automação retomada no projeto Vibes escolhido." : command === "stop" ? "Automação interrompida." : "Pasta/arquivo da automação aberto.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Não foi possível executar o comando da automação.");
      await refreshAutomation();
    } finally {
      setAutomationBusy(false);
    }
  };

  useEffect(() => { void refreshCatalog(); }, []);
  useEffect(() => {
    void refreshAutomation();
    if (!automation.running) return;
    const timer = window.setInterval(() => { void refreshAutomation(); }, 3_000);
    return () => window.clearInterval(timer);
  }, [automation.running]);

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
    // Projetos antigos salvavam em ordem cronológica; os atuais apresentam o
    // último envio primeiro, inclusive depois de reabrir o projeto.
    const savedMedia = project.uploaded_images ?? [];
    setUploadedImages(project.uploaded_media_newest_first ? savedMedia : [...savedMedia].reverse());
    setImageBindings(project.image_bindings ?? {});
    setBackground(project.background ?? "");
    setMusic(project.music ?? "");
    setAnimation(project.animation ?? "movimento_sutil");
    setTextStyle(project.text_style ?? "impact");
    setPexelsItems(project.pexels_items ?? []);
    setPexelsQueries(project.pexels_queries ?? {});
    setTranslations(project.translations ?? {});
    setVisualTranslations(project.visual_translations ?? {});
    setSelectedPexels(project.selected_pexels ?? {});
    setPexelsExpectedCount(project.pexels_expected_count ?? 0);
    setExpandedPexelsScene(null);
    setPexelsPage(0);
    setTimingWarnings([]);
    setNarrationDuration(null);
    setValidationFeedback("");
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
      id: projectId, name: projectName.trim() || "Projeto sem título", updated_at: new Date().toISOString(), source, uploaded_images: uploadedImages, uploaded_media_newest_first: true,
      image_bindings: imageBindings, background, music, animation, text_style: textStyle, pexels_items: pexelsItems,
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
  }, [projectId, projectName, source, uploadedImages, imageBindings, background, music, animation, textStyle, pexelsItems, pexelsQueries, translations, visualTranslations, selectedPexels, pexelsExpectedCount]);

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
        setRenderElapsedSeconds(
          typeof job.render_elapsed_seconds === "number"
            ? Math.max(0, job.render_elapsed_seconds)
            : null,
        );
        setRenderEtaSeconds(
          typeof job.estimated_remaining_seconds === "number"
            ? Math.max(0, job.estimated_remaining_seconds)
            : null,
        );
        if (job.status === "complete") {
          if (notifiedCompletedJob.current !== jobId) {
            playRenderCompleteSound();
            notifiedCompletedJob.current = jobId;
          }
          setRenderProgress(100);
          setRenderStage(job.stage ?? "Vídeo final pronto");
          setRenderEtaSeconds(null);
          setOutputUrl(job.output_url ?? "");
          setStatus(`Vídeo final pronto. Trilha usada: ${job.music_name ?? "nenhuma"}.`);
          setRenderError("");
          setRenderLogUrl("");
          setJobId("");
        }
        if (job.status === "failed") {
          playRenderErrorSound();
          setRenderStage(job.stage ?? "Falha na renderização");
          setRenderEtaSeconds(null);
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

  const copyScriptPrompt = async (mode: PromptMode) => {
    try {
      const prompt = await api<string>(`/api/script-prompt?mode=${mode}`);
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
      setPromptCopied(mode);
      promptCopyTimer.current = window.setTimeout(() => {
        setPromptCopied(null);
        promptCopyTimer.current = null;
      }, 1800);
      setStatus(
        mode === "without_broll"
          ? "Prompt sem B-roll copiado. Ele gera somente imagens para fullscreen e cartões."
          : mode === "psychology_without_broll"
            ? "Prompt de psicologia sem B-roll copiado. Ele gera somente imagens para fullscreen e cartões."
            : mode === "cats_without_broll"
              ? "Prompt do canal de gatos copiado. Ele gera ilustrações felinas consistentes, sem B-roll."
            : "Prompt com B-roll copiado. Cole-o no ChatGPT e preencha o tema do vídeo.",
      );
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
    setNarrationDuration(null);
    setValidationFeedback("");
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
      setStatus("Importando mídias de cena…");
      const saved = await uploadMedia("/api/images", files);
      if (saved.length) {
        setUploadedImages(current => [...new Set([...saved, ...current])]);
        const videoCount = saved.filter(isUploadedVideo).length;
        setStatus(videoCount
          ? `${saved.length} mídia(s) importada(s), incluindo ${videoCount} vídeo(s). Os vídeos entram mudos: somente imagem, narração, trilha e efeitos serão usados.`
          : `${saved.length} imagem(ns) importada(s). O sistema usará a descrição do arquivo e o brief da cena; a ordem do envio não importa.`);
      }
    } catch (error) {
      setStatus(readableError(error));
    }
  };

  const uploadMediaForScenePreview = async (files: FileList | File[]) => {
    try {
      setStatus("Importando mídia para a prévia da cena…");
      const saved = await uploadMedia("/api/images", files);
      if (!saved.length) return;
      setUploadedImages(current => [...new Set([...saved, ...current])]);
      const selected = saved[0];
      setActiveScenePreview(current => current ? {
        ...current, sourceImage: selected, isVideo: isUploadedVideo(selected),
      } : current);
      setStatus(`${saved.length} mídia(s) importada(s). A primeira já está na prévia desta cena.`);
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
      // O hover do seletor pode estar reproduzindo a trilha anterior quando o
      // operador clica no +. Paramos essa prévia para ela não parecer a
      // música que acabou de ser escolhida.
      stopMusicPreview();
      setStatus("Importando trilha sonora…");
      const saved = await uploadMedia("/api/music", files);
      if (saved.length) {
        const selectedMusic = saved.at(-1) ?? "";
        setMusic(selectedMusic);
        setStatus(`Trilha importada e selecionada para este projeto: ${selectedMusic}`);
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

  const deleteUploadedImages = async () => {
    if (!uploadedImages.length) return;
    const count = uploadedImages.length;
    if (!window.confirm(`Apagar permanentemente as ${count} mídias enviadas nesta tela? Imagens e vídeos serão removidos dos respectivos acervos; o roteiro será preservado.`)) return;
    try {
      setStatus(`Apagando ${count} mídias enviadas…`);
      const result = await api<{ deleted: string[]; missing: string[] }>("/api/images", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(uploadedImages),
      });
      setUploadedImages([]);
      setImageBindings({});
      setThumbnailPage(0);
      setActiveImagePickerKey(null);
      await refreshCatalog();
      setStatus(
        result.missing.length
          ? `${result.deleted.length} mídia(s) apagada(s); ${result.missing.length} já não estava(m) no acervo.`
          : `${result.deleted.length} mídia(s) apagada(s). Você já pode enviar outro conjunto.`,
      );
    } catch (error) {
      setStatus(`Não foi possível apagar as imagens: ${readableError(error)}`);
    }
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
      setTranslationFailures(current => sceneId
        ? Object.fromEntries(Object.entries(current).filter(([key]) => key !== sceneId))
        : {});
      setVisualTranslationFailures(current => sceneId
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
    type TranslationTask = { sceneId: string; kind: "text" | "visual"; value: string };
    const pending = items.flatMap<TranslationTask>(item => [
      { sceneId: item.scene_id, kind: "text", value: item.text },
      ...(item.visual_reference ? [{ sceneId: item.scene_id, kind: "visual" as const, value: item.visual_reference }] : []),
    ]);
    setTranslationLoading(current => ({ ...current, ...Object.fromEntries(items.map(item => [item.scene_id, true])) }));
    setVisualTranslationLoading(current => ({ ...current, ...Object.fromEntries(items.filter(item => item.visual_reference).map(item => [item.scene_id, true])) }));
    setTranslationFailures(current => ({ ...current, ...Object.fromEntries(items.map(item => [item.scene_id, false])) }));
    setVisualTranslationFailures(current => ({ ...current, ...Object.fromEntries(items.filter(item => item.visual_reference).map(item => [item.scene_id, false])) }));

    // Muitas cenas compartilham o mesmo texto do bloco. Uma única requisição
    // atende todas elas; isso evita estourar o limite do tradutor externo e
    // impede que uma falha transitória deixe a referência sem tradução.
    const requestTranslation = (value: string): Promise<string> => {
      const key = `${activeScript.language.toLowerCase()}\u0000${value}`;
      const running = translationRequests.current.get(key);
      if (running) return running;
      const request = api<{ portuguese: string }>("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: value, source_language: activeScript.language }),
      }).then(result => result.portuguese);
      translationRequests.current.set(key, request);
      void request.then(
        () => translationRequests.current.delete(key),
        () => translationRequests.current.delete(key),
      );
      return request;
    };

    // Mantém poucas requisições simultâneas ao serviço de tradução, sem exigir
    // que o operador clique cena por cena ou sature o serviço externo.
    const worker = async () => {
      while (pending.length) {
        const task = pending.shift();
        if (!task) return;
        try {
          const portuguese = await requestTranslation(task.value);
          if (task.kind === "text") setTranslations(current => ({ ...current, [task.sceneId]: portuguese }));
          else setVisualTranslations(current => ({ ...current, [task.sceneId]: portuguese }));
        } catch {
          if (task.kind === "text") {
            setTranslations(current => ({ ...current, [task.sceneId]: task.value }));
            setTranslationFailures(current => ({ ...current, [task.sceneId]: true }));
          } else {
            setVisualTranslations(current => ({ ...current, [task.sceneId]: task.value }));
            setVisualTranslationFailures(current => ({ ...current, [task.sceneId]: true }));
          }
        } finally {
          if (task.kind === "text") setTranslationLoading(current => ({ ...current, [task.sceneId]: false }));
          else setVisualTranslationLoading(current => ({ ...current, [task.sceneId]: false }));
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(TRANSLATION_CONCURRENCY, pending.length) }, worker));
  };

  const retryPexelsTranslation = (item: PexelsItem) => {
    if (!script) return;
    void translatePexelsItems(script, [item]);
  };

  // A curadoria é persistida no navegador. Se a página fechar no meio das
  // requisições, as entradas que voltarem sem texto em PT-BR são retomadas
  // automaticamente ao abrir o projeto, em vez de ficarem presas em
  // "traduzindo…" para sempre.
  useEffect(() => {
    if (!script || script.language.toLowerCase().startsWith("pt")) return;
    const missing = pexelsItems.filter(item => (
      (needsTranslation(item.text, translations[item.scene_id], script.language)
        && !translationLoading[item.scene_id] && !translationFailures[item.scene_id])
      || Boolean(item.visual_reference
        && needsTranslation(item.visual_reference, visualTranslations[item.scene_id], script.language)
        && !visualTranslationLoading[item.scene_id] && !visualTranslationFailures[item.scene_id])
    ));
    if (missing.length) void translatePexelsItems(script, missing);
  }, [script, pexelsItems, translations, visualTranslations, translationLoading, visualTranslationLoading]);

  const openVideosFolder = async () => {
    try {
      await api<{ folder: string }>("/api/pexels/open-folder", { method: "POST" });
      setStatus("A pasta local dos B-rolls foi aberta no Explorador de Arquivos.");
    } catch (error) {
      setStatus(readableError(error));
    }
  };

  const openFinalVideosFolder = async () => {
    try {
      await api<{ folder: string }>("/api/outputs/open-folder", { method: "POST" });
      setStatus("A pasta dos vídeos finalizados foi aberta no Explorador de Arquivos.");
    } catch (error) {
      setStatus(readableError(error));
    }
  };

  const validate = async (measureTiming = true) => {
    const activeScript = parseScript(source, false);
    if (!activeScript) {
      setValidationFeedback("Cole ou importe um roteiro JSON antes de validar.");
      setStatus("Cole ou importe um roteiro JSON antes de validar.");
      return;
    }
    try {
      setValidationFeedback("");
      setStatus(measureTiming ? "Medindo a narração com a voz definida no JSON…" : "Associando mídias às cenas…");
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
          measure_timing: measureTiming,
        }),
      });
      setImageBindings(bindingsFromResolvedSources(activeScript, report.resolved_image_sources));
      setFlowExportReady(report.valid);
      const warnings = report.timing?.scenes.filter(scene => scene.duration > 9) ?? [];
      if (measureTiming) {
        setTimingWarnings(warnings);
        setNarrationDuration(report.timing?.narration_duration ?? null);
      }
      const validationNotes = [
        ...(report.timing ? [`Duração estimada do vídeo: ${formatVideoDuration(report.timing.narration_duration)}.`] : []),
        ...(warnings.length ? [`${warnings.length} cena(s) ultrapassam 9 s; veja as sugestões de corte abaixo.`] : []),
        ...(report.missing_images.length ? [`Faltam: ${report.missing_images.join(", ")}`] : []),
      ];
      const feedback = !report.valid
        ? report.errors.join(" ")
        : report.timing_error
          ? report.timing_error
          : validationNotes.join(" ");
      setValidationFeedback(feedback);
      setStatus(
        report.valid
          ? (report.timing_error
            ? report.timing_error
            : validationNotes.join(" ") || (measureTiming
              ? "Roteiro válido. Assets e duração acústica aprovados."
              : "Mídias associadas. Revise as prévias de composição antes do render."))
          : report.errors.join("\n"),
      );
    } catch (error) {
      setFlowExportReady(false);
      const message = readableError(error);
      setValidationFeedback(message);
      setStatus(message);
    }
  };

  useEffect(() => {
    if (mediaTab !== "review" || !script || !uploadedImages.length || jobId) return;
    const requestKey = JSON.stringify({
      script: source,
      uploadedImages: [...uploadedImages].sort(),
      bindings: bindingPayload(script, imageBindings),
    });
    if (automaticBindingRequestKey.current === requestKey) return;
    automaticBindingRequestKey.current = requestKey;

    const associateBeforePreview = async () => {
      try {
        const report = await api<{
          resolved_image_sources: Record<string, string>; missing_images: string[];
        }>("/api/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script,
            manual_image_bindings: bindingPayload(script, imageBindings),
            uploaded_images: uploadedImages,
            measure_timing: false,
          }),
        });
        const resolved = bindingsFromResolvedSources(script, report.resolved_image_sources);
        setImageBindings(resolved);
        const associated = Object.keys(resolved).length;
        setStatus(
          associated
            ? `${associated} mídia(s) associada(s) automaticamente para revisão antes do render.`
            : "Não foi possível associar as mídias automaticamente. Use “Escolher e pré-visualizar” para decidir a imagem da cena.",
        );
      } catch (error) {
        automaticBindingRequestKey.current = "";
        setStatus(`Não foi possível preparar as prévias: ${readableError(error)}`);
      }
    };
    void associateBeforePreview();
  }, [mediaTab, script, source, uploadedImages, imageBindings, jobId]);

  const downloadGoogleFlowTxt = () => {
    const activeScript = parseScript(source, false);
    if (!activeScript || !flowExportReady) {
      setStatus("Valide o JSON antes de preparar o arquivo para o Google Flow.");
      return;
    }
    const result = googleFlowText(activeScript, flowBatchSize, textStyle);
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
    // A trilha é uma escolha do projeto. Só o áudio do vídeo enviado como
    // mídia de cena é descartado no renderer; a faixa selecionada aqui deve
    // sempre seguir no pedido de renderização.
    const selectedMusic = music
      || catalog.music.find(name => /fundo_documentario/i.test(name))
      || catalog.music[0]
      || "";
    if (!selectedMusic) {
      setRenderError("Escolha ou envie uma música de fundo antes de gerar o vídeo.");
      setStatus("Nenhuma música de fundo está disponível para este projeto.");
      return;
    }
    if (selectedMusic !== music) setMusic(selectedMusic);
    setScript(renderScript);
    setSource(JSON.stringify(renderScript, null, 2));
    try {
      setOutputUrl("");
      setRenderError("");
      setRenderLogUrl("");
      setRenderProgress(2);
      setRenderStage("Enviando trabalho para renderização");
      setRenderElapsedSeconds(null);
      setRenderEtaSeconds(null);
      const result = await api<{ job_id: string }>("/api/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script: renderScript,
          manual_image_bindings: bindingPayload(activeScript, imageBindings),
          uploaded_images: uploadedImages,
          ...(background ? { background_image: background } : {}),
          music_name: selectedMusic,
          text_style: textStyle,
        }),
      });
      notifiedCompletedJob.current = null;
      setJobId(result.job_id);
      setRenderProgress(5);
      setRenderStage("Preparando narração, imagens e trilha");
      setStatus(`Renderizando o vídeo completo com a trilha: ${selectedMusic}. O andamento aparece na barra inferior.`);
    } catch (error) {
      setRenderProgress(0);
      setRenderStage("");
      setRenderElapsedSeconds(null);
      setRenderEtaSeconds(null);
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
  const reviewScenes = script?.blocks.flatMap((block, blockIndex) => block.scenes.map((scene, sceneIndex) => ({ block, blockIndex, scene, sceneIndex }))) ?? [];
  const sceneReviewPageCount = Math.max(1, Math.ceil(reviewScenes.length / SCENE_REVIEW_PAGE_SIZE));
  const currentSceneReviewPage = Math.min(sceneReviewPage, sceneReviewPageCount - 1);
  const reviewStart = currentSceneReviewPage * SCENE_REVIEW_PAGE_SIZE;
  const visibleReviewScenes = reviewScenes.slice(reviewStart, reviewStart + SCENE_REVIEW_PAGE_SIZE);
  const scriptProgress = script ? 100 : 0;
  const backgroundProgress = background ? 100 : 0;
  const pexelsPageCount = Math.max(1, Math.ceil(pexelsItems.length / PEXELS_SCENES_PAGE_SIZE));
  const currentPexelsPage = Math.min(pexelsPage, pexelsPageCount - 1);
  const pexelsStart = currentPexelsPage * PEXELS_SCENES_PAGE_SIZE;
  const visiblePexelsItems = pexelsItems.slice(pexelsStart, pexelsStart + PEXELS_SCENES_PAGE_SIZE);
  const hasBrollScenes = Boolean(script?.blocks.some(block => block.scenes.some(scene => scene.tipo_midia === "video_generico")));

  // A revisão não depende da busca de B-roll. Ao abrir essa página, trazemos
  // para PT-BR somente os textos dos blocos visíveis, para o operador entender
  // o contexto antes de trocar uma imagem por outra ou por um vídeo mudo.
  useEffect(() => {
    if (!script || mediaTab !== "review") return;
    const pending = visibleReviewScenes.filter(({ scene, block }) => (
      needsTranslation(block.text, translations[scene.id], script.language)
      && !translationLoading[scene.id] && !translationFailures[scene.id]
    ));
    if (!pending.length) return;
    if (script.language.toLowerCase().startsWith("pt")) {
      setTranslations(current => ({ ...current, ...Object.fromEntries(pending.map(({ scene, block }) => [scene.id, block.text])) }));
      return;
    }
    let cancelled = false;
    for (const { scene, block } of pending) {
      setTranslationLoading(current => ({ ...current, [scene.id]: true }));
      const key = `${script.language.toLowerCase()}\u0000${block.text}`;
      const request = translationRequests.current.get(key) ?? api<{ portuguese: string }>("/api/translate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: block.text, source_language: script.language }),
      }).then(result => result.portuguese);
      translationRequests.current.set(key, request);
      setTranslationFailures(current => ({ ...current, [scene.id]: false }));
      void request.then(
        portuguese => { if (!cancelled) setTranslations(current => ({ ...current, [scene.id]: portuguese })); },
        () => {
          if (!cancelled) {
            setTranslations(current => ({ ...current, [scene.id]: block.text }));
            setTranslationFailures(current => ({ ...current, [scene.id]: true }));
          }
        },
      ).finally(() => {
        translationRequests.current.delete(key);
        if (!cancelled) setTranslationLoading(current => ({ ...current, [scene.id]: false }));
      });
    }
    return () => { cancelled = true; };
  }, [script, mediaTab, currentSceneReviewPage, translations, translationLoading]);

  useEffect(() => {
    if (!hasBrollScenes && mediaTab === "curadoria") setMediaTab("assets");
  }, [hasBrollScenes, mediaTab]);

  return (
    <main className="app-shell">
      <header className="appbar">
        <div className="brand"><span className="brand-mark">SR</span><span>SynthReel</span><small>horizontal</small></div>
        <div className="appbar-actions">
          <button className="project-trigger" onClick={() => setProjectDialogOpen(true)} title="Abrir projetos salvos"><span>Projetos</span><b>{projectName}</b><i>⌄</i></button>
          <span className="scene-indicator">{script ? `${sceneCount(script)} cenas` : "sem roteiro"}</span>
          <div className="automation-actions" aria-label="Controles da automação de animação">
            <span className={`automation-indicator${automation.running ? " running" : ""}`} title={automation.message}><i />IA {automation.completed_images}/{animationImageNames.length}</span>
            <button className="button quiet compact" disabled={automationBusy} onClick={() => void automationCommand("open-log")}>Log IA</button>
            {!automation.running && automation.resume_available && <button className="button quiet compact" disabled={automationBusy} onClick={() => void automationCommand("resume")}>Retomar Vibes</button>}
            <button className={`button compact ${automation.running ? "danger" : "primary"}`} disabled={automationBusy} onClick={() => void automationCommand(automation.running ? "stop" : "start")}>{automationBusy ? "Aguarde…" : automation.running ? "Parar IA" : "Animar IA"}</button>
          </div>
          <button className="button quiet" onClick={() => void validate()}>Validar</button>
          {hasBrollScenes && <button className="button quiet" disabled={pexelsBusy} onClick={() => void searchPexels()}>{pexelsBusy ? "Buscando…" : "Buscar B-roll"}</button>}
          <button className="button primary" disabled={Boolean(jobId)} onClick={render}>{jobId ? "Renderizando…" : "Gerar vídeo"}</button>
        </div>
      </header>

      <section className="workbench" aria-label="Área de produção">
        <article className="panel json-panel">
          <div className="panel-header">
            <div><span className="panel-index">01</span><h1>Roteiro JSON</h1></div>
            <div className="json-header-actions">
              <select
                className="prompt-picker"
                aria-label="Escolha o prompt do roteiro"
                value={selectedPromptMode}
                onChange={event => setSelectedPromptMode(event.target.value as PromptMode)}
              >
                <option value="with_broll">Prompt com B-roll</option>
                <option value="without_broll">Prompt sem B-roll</option>
                <option value="psychology_without_broll">Prompt psicologia</option>
                <option value="cats_without_broll">Prompt gatos</option>
              </select>
              <button className={`button quiet compact prompt-copy-button${promptCopied === selectedPromptMode ? " copied" : ""}`} onClick={() => void copyScriptPrompt(selectedPromptMode)}>
                {promptCopied === selectedPromptMode ? "✓ Copiado" : "Copiar prompt"}
              </button>
              <button className="button quiet compact" onClick={() => jsonInput.current?.click()}>Importar JSON</button>
            </div>
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
              setTimingWarnings([]);
              setNarrationDuration(null);
              setValidationFeedback("");
              setFlowExportReady(false);
            }}
          />
          <section className={`script-feedback${timingWarnings.length ? " has-warnings" : ""}`} aria-label="Validação e avisos do roteiro">
            {validationFeedback && <span>{validationFeedback}</span>}
            {narrationDuration !== null && <div className="video-duration-estimate"><b>Duração estimada do vídeo</b><span>{formatVideoDuration(narrationDuration)}</span><small>Medida pela narração com a voz selecionada.</small></div>}
            {timingWarnings.length > 0 ? <>
              <b>Prévia acústica · {timingWarnings.length} cena(s) precisam de corte</b>
              {timingWarnings.map(scene => (
                <div className="timing-warning" key={scene.id}>
                  <span><b>{scene.id}</b> — {scene.duration.toFixed(2)} s</span>
                  <small>Corte: “{scene.suggested_split?.first_text ?? "divida próximo à metade"}” / “{scene.suggested_split?.second_text ?? "crie a segunda cena"}”</small>
                </div>
              ))}
            </> : narrationDuration === null && !validationFeedback && <span>Validação, duração acústica e sugestões de corte aparecem aqui.</span>}
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
                <><button className="button quiet compact" onClick={clearUploadedImages}>Limpar lista</button><button className="button danger compact" onClick={() => void deleteUploadedImages()}>Apagar mídias</button></>
              )}
            </div>
          </div>
          <div className="media-tabs" aria-label="Áreas de mídia">
            <button className={mediaTab === "assets" ? "active" : ""} onClick={() => setMediaTab("assets")}>Biblioteca de mídias</button>
            <button className={mediaTab === "review" ? "active" : ""} onClick={() => { setSceneReviewPage(0); setMediaTab("review"); }}>Revisão por cena{script ? ` · ${sceneCount(script)}` : ""}</button>
            {hasBrollScenes && <button className={mediaTab === "curadoria" ? "active" : ""} onClick={() => setMediaTab("curadoria")}>Curadoria B-roll{pexelsItems.length ? ` · ${pexelsItems.length}` : ""}</button>}
          </div>
          {!script && <div className="media-await"><b>Carregue o roteiro para preparar as mídias.</b><span>As cenas, os vínculos e a curadoria aparecem aqui depois da leitura do JSON.</span></div>}
          {script && mediaTab === "assets" && <>
          <div className="asset-drop" onDragOver={event => event.preventDefault()} onDrop={dropImages} onClick={() => imagesInput.current?.click()} role="button" tabIndex={0}>
            <b>Solte imagens ou vídeos aqui</b><span>MP4, MOV e WebM entram sem áudio</span>
          </div>
          <input ref={imagesInput} type="file" hidden multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" onChange={onImagesInput} />
          <div className="flow-progress image-progress" aria-label="Progresso das imagens">
            <div><span>Assets prontos</span><b>{script ? `${linkedImages}/${requiredAssets.length}` : "aguardando roteiro"}</b></div>
            <i><em style={{ width: `${imageProgress}%` }} /></i>
          </div>
          <div className="asset-grid" aria-label="Mídias enviadas nesta tela">
            {visibleUploadedImages.map(image => (
              <figure key={image}>
                {isUploadedVideo(image) ? (
                  <video src={uploadedMediaUrl(image)} title={image} muted playsInline preload="metadata" />
                ) : (
                  <img src={uploadedMediaUrl(image)} title={image} alt={`Imagem enviada: ${image}`} loading="lazy" decoding="async" />
                )}
                <figcaption>{isUploadedVideo(image) ? "🎬 Vídeo mudo · " : ""}{image}</figcaption>
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
          </>}
          {script && mediaTab === "review" && <section className="scene-review" aria-label="Revisão de mídia por cena">
            <div className="scene-review-heading">
              <div><b>Revisão de mídia por cena</b><small>Abra a prévia para escolher a mídia já posicionada, antes do render.</small></div>
              <div className="scene-review-heading-actions">
                <button className="button quiet compact" onClick={() => void openVideosFolder()}>Abrir pasta dos vídeos</button>
              </div>
            </div>
            {!uploadedImages.length && <p className="scene-bindings-empty">Envie imagens ou vídeos pela Biblioteca para liberar as substituições visuais.</p>}
            <div className="scene-review-list">
              {visibleReviewScenes.map(({ block, blockIndex, scene, sceneIndex }) => {
                const pickerKey = sceneBindingKey(blockIndex, sceneIndex);
                const boundImage = boundImageFor(imageBindings, blockIndex, sceneIndex, scene.image);
                const sourceImage = boundImage ?? scene.image;
                const isVideo = isUploadedVideo(sourceImage) || scene.tipo_midia === "video_generico";
                const isReady = assetIsReady(scene, sourceImage, uploadedImages, catalog.images, catalog.videos);
                const pickerOpen = activeImagePickerKey === pickerKey;
                return <article className={`scene-review-card${isReady ? " ready" : " pending"}`} key={`${block.id}-${scene.id}-${blockIndex}-${sceneIndex}`}>
                  <div className="scene-review-media">
                    {isReady ? isVideo
                      ? <video src={sceneMediaUrl(sourceImage, uploadedImages)} muted controls playsInline preload="metadata" />
                      : <img src={sceneMediaUrl(sourceImage, uploadedImages)} alt={`Mídia escolhida para ${scene.id}`} loading="lazy" />
                      : <div className="scene-review-missing">{scene.tipo_midia === "imagem" && uploadedImages.length
                        ? "Mídia desta cena ainda não associada"
                        : "Mídia ainda não encontrada"}</div>}
                    <small>{isVideo ? "🎬 Vídeo mudo" : "🖼 Imagem"}</small>
                  </div>
                  <div className="scene-review-copy">
                    <span className="scene-review-kicker">Bloco {blockIndex + 1} · cena {sceneIndex + 1} · ID {scene.image_id}</span>
                    <b>{scene.id}</b>
                    <p><strong>Texto do bloco:</strong> {block.text}</p>
                    <p className="scene-review-translation"><strong>{translationFailures[scene.id] || needsTranslation(block.text, translations[scene.id], script?.language ?? "pt") ? "Original:" : "PT-BR:"}</strong> {translations[scene.id] ?? (translationLoading[scene.id] ? "traduzindo…" : "abrindo tradução…")}</p>
                    <small>Brief visual: {scene.visual?.subject ?? scene.asset_key ?? "sem descrição"}{scene.visual?.action ? ` · ${scene.visual.action}` : ""}</small>
                  </div>
                  <div className="scene-review-actions">
                    <button type="button" className="button quiet compact" onClick={() => setActiveScenePreview({
                      scene, blockIndex, sceneIndex, sourceImage: isReady ? sourceImage : null, isVideo,
                      fullscreen: scene.tipo_midia === "video_generico" || scene.transition?.in === "zoom_in",
                    })}>{isReady ? "Prévia de composição" : "Escolher e pré-visualizar"}</button>
                    {scene.tipo_midia === "imagem" && <>
                      <button type="button" className="button quiet compact" disabled={!uploadedImages.length || Boolean(jobId)} onClick={() => setActiveImagePickerKey(pickerOpen ? null : pickerKey)}>{pickerOpen ? "Fechar opções" : "Trocar mídia"}</button>
                      {boundImage && <button type="button" className="button quiet compact" disabled={Boolean(jobId)} onClick={() => bindUploadedImageToScene(blockIndex, sceneIndex, "")}>Usar mídia do JSON</button>}
                    </>}
                  </div>
                  {scene.tipo_midia === "video_generico" && <span className="scene-review-broll">{catalog.videos.includes(scene.image) ? "B-roll salvo. Você pode revisar ou trocar na Curadoria B-roll." : "B-roll pendente na Curadoria B-roll."}</span>}
                  {pickerOpen && <div className="scene-review-picker" aria-label={`Escolher mídia visual para ${scene.id}`}>
                    {uploadedImages.map(image => <button type="button" key={image} className={sourceImage === image ? "selected" : ""} disabled={Boolean(jobId)} title={image} onClick={() => { bindUploadedImageToScene(blockIndex, sceneIndex, image); setActiveImagePickerKey(null); }}>
                      {isUploadedVideo(image) ? <video src={uploadedMediaUrl(image)} muted playsInline preload="metadata" /> : <img src={uploadedMediaUrl(image)} alt={image} loading="lazy" />}
                      <span>{isUploadedVideo(image) ? "Vídeo mudo" : "Imagem"}</span>
                    </button>)}
                  </div>}
                </article>;
              })}
            </div>
            {reviewScenes.length > SCENE_REVIEW_PAGE_SIZE && <div className="asset-grid-pagination scene-review-pagination" aria-label="Navegação da revisão por cena">
              <span>Cenas {reviewStart + 1}–{Math.min(reviewStart + SCENE_REVIEW_PAGE_SIZE, reviewScenes.length)} de {reviewScenes.length}</span>
              <div><button type="button" className="button quiet compact" disabled={currentSceneReviewPage === 0} onClick={() => setSceneReviewPage(page => Math.max(0, page - 1))}>Anteriores</button><button type="button" className="button quiet compact" disabled={currentSceneReviewPage >= sceneReviewPageCount - 1} onClick={() => setSceneReviewPage(page => Math.min(sceneReviewPageCount - 1, page + 1))}>Próximas</button></div>
            </div>}
          </section>}
          {script && mediaTab === "curadoria" && (
            <section className="pexels-review standalone" aria-label="Curadoria de B-roll do Pexels">
              <div className="curation-heading"><div><span className="panel-index">B-ROLL</span><b>Escolhas editoriais</b><small>O gancho e as inserções visíveis passam pela sua aprovação.</small></div><button className="button quiet compact" onClick={() => void openVideosFolder()}>Abrir pasta dos vídeos</button></div>
              {pexelsItems.length > 0 ? <>
                  <p className="scene-bindings-note">Prévia manual, tradução e aprovação humana de B-roll horizontal. As cenas são paginadas para não carregar todas as prévias de uma vez. Vídeos fornecidos por <a href="https://www.pexels.com" target="_blank" rel="noreferrer">Pexels</a>.</p>
                  <div className="pexels-results-summary"><b>Conferência: {pexelsItems.length}/{pexelsExpectedCount || pexelsItems.length} cenas video_generico</b><span>Exibindo cenas {pexelsStart + 1}–{Math.min(pexelsStart + PEXELS_SCENES_PAGE_SIZE, pexelsItems.length)} · página {currentPexelsPage + 1} de {pexelsPageCount}</span></div>
                  {visiblePexelsItems.map(item => {
                    const scene = script?.blocks.flatMap(block => block.scenes).find(candidate => candidate.id === item.scene_id);
                    const saved = Boolean(scene && catalog.videos.includes(scene.image));
                    const chosen = selectedPexels[item.scene_id];
                    const collapsed = Boolean(chosen && expandedPexelsScene !== item.scene_id);
                    const textNeedsTranslation = needsTranslation(item.text, translations[item.scene_id], script?.language ?? "pt");
                    const visualNeedsTranslation = Boolean(item.visual_reference && needsTranslation(item.visual_reference, visualTranslations[item.scene_id], script?.language ?? "pt"));
                    return (
                      <article className={`pexels-item${chosen ? " selected" : ""}`} key={item.scene_id}>
                        <div className="pexels-copy"><b>{item.scene_id} · {saved ? "aprovado" : "aguardando aprovação"}</b><span>Original: {item.text}</span>
                          <span>{translationFailures[item.scene_id] || textNeedsTranslation ? "Original:" : "Português:"} {translations[item.scene_id] ?? "traduzindo…"}{translationLoading[item.scene_id] ? " (traduzindo…)" : ""}</span>
                          {item.visual_reference && <span className="pexels-reference">{visualTranslationFailures[item.scene_id] || visualNeedsTranslation ? "REFERÊNCIA VISUAL (original):" : "REFERÊNCIA VISUAL (PT-BR):"} {visualTranslations[item.scene_id] ?? "traduzindo…"}{visualTranslationLoading[item.scene_id] ? " (traduzindo…)" : ""}</span>}
                          {(translationFailures[item.scene_id] || visualTranslationFailures[item.scene_id]) && <button className="text-button" type="button" onClick={() => retryPexelsTranslation(item)}>A tradução falhou temporariamente. Tentar de novo</button>}
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
                              {pexelsPreviewErrors[`${item.scene_id}:${candidate.id}`] ? <div className="pexels-preview-error"><span>Prévia indisponível neste navegador.</span>{candidate.pexels_url && <a href={candidate.pexels_url} target="_blank" rel="noreferrer">Abrir no Pexels</a>}</div> : <video src={candidate.preview_url} poster={candidate.thumbnail} controls muted loop playsInline preload="none" onError={() => setPexelsPreviewErrors(current => ({ ...current, [`${item.scene_id}:${candidate.id}`]: true }))} />}
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

        <aside className="settings-column" aria-label="Configurações visuais do vídeo">
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

        <article className="panel typography-panel">
          <div className="panel-header">
            <div><span className="panel-index">04</span><h1>Estilo das chamadas</h1></div>
            <span className="type-status">selecionada</span>
          </div>
          <p className="panel-hint">Clique na prévia para escolher fonte, cor, borda e sombra das chamadas sobrepostas.</p>
          <div className="type-style-picker" role="radiogroup" aria-label="Escolher estilo das chamadas">
            {textStyleOptions.map(option => (
              <button
                type="button"
                className={`type-style-option${textStyle === option.value ? " selected" : ""}`}
                key={option.value}
                role="radio"
                aria-checked={textStyle === option.value}
                onClick={() => setTextStyle(option.value)}
              >
                <span className={`type-style-sample ${option.value}`}>SE INSCREVA</span>
                <span className="type-style-label"><b>{option.label}</b><small>{textStyle === option.value ? "Em uso neste projeto" : option.description}</small></span>
              </button>
            ))}
          </div>
          <small className="typography-note">A escolha também orienta os textos estratégicos em espanhol enviados ao Google Flow.</small>
        </article>
        </aside>
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
      {activeScenePreview && <div className="scene-preview-backdrop" role="presentation" onMouseDown={() => setActiveScenePreview(null)}>
        <section className="scene-preview-dialog" role="dialog" aria-modal="true" aria-label={`Prévia de composição de ${activeScenePreview.scene.id}`} onMouseDown={event => event.stopPropagation()}>
          <header><div><span>Prévia antes do render</span><h2>{activeScenePreview.scene.id}</h2></div><button className="dialog-close" onClick={() => setActiveScenePreview(null)} aria-label="Fechar prévia">×</button></header>
          <div className={`scene-composition-preview${activeScenePreview.fullscreen ? " fullscreen" : " card"}`}>
            {!activeScenePreview.fullscreen && (backgroundUrl ? <img className={`scene-composition-background ${animation}`} src={backgroundUrl} alt="Fundo da composição" /> : <div className="scene-composition-no-background">Escolha um fundo para ver a composição completa.</div>)}
            <div className="scene-composition-media">
              {activeScenePreview.sourceImage ? (activeScenePreview.isVideo
                ? <video src={sceneMediaUrl(activeScenePreview.sourceImage, uploadedImages)} muted controls autoPlay loop playsInline />
                : <img src={sceneMediaUrl(activeScenePreview.sourceImage, uploadedImages)} alt={`Mídia escolhida para ${activeScenePreview.scene.id}`} />)
                : <div className="scene-composition-empty">Escolha uma mídia abaixo para vê-la nesta cena.</div>}
            </div>
            {activeScenePreview.scene.annotation && <div className="scene-composition-annotation">{activeScenePreview.scene.annotation.lines.map(line => <span key={line}>{line}</span>)}</div>}
          </div>
          {activeScenePreview.scene.tipo_midia === "imagem" && <div className="scene-preview-picker" aria-label="Mídias disponíveis para esta cena">
            {uploadedImages.map(image => <button type="button" key={image} className={activeScenePreview.sourceImage === image ? "selected" : ""} onClick={() => setActiveScenePreview(current => current ? {
              ...current, sourceImage: image, isVideo: isUploadedVideo(image),
            } : current)}>
              {isUploadedVideo(image) ? <video src={uploadedMediaUrl(image)} muted playsInline preload="metadata" /> : <img src={uploadedMediaUrl(image)} alt={image} loading="lazy" />}
              <span>{mediaLabel(image)}</span>
            </button>)}
            {!uploadedImages.length && <p>Nenhuma mídia foi enviada nesta tela.</p>}
          </div>}
          {activeScenePreview.scene.tipo_midia === "imagem" && <div className="scene-preview-actions">
            <button className="button quiet compact" disabled={Boolean(jobId)} onClick={() => scenePreviewInput.current?.click()}>Importar de outra pasta</button>
            <button className="button primary compact" disabled={!activeScenePreview.sourceImage || Boolean(jobId)} onClick={() => {
              if (!activeScenePreview.sourceImage) return;
              bindUploadedImageToScene(activeScenePreview.blockIndex, activeScenePreview.sceneIndex, activeScenePreview.sourceImage);
              setActiveScenePreview(null);
            }}>Usar esta mídia nesta cena</button>
          </div>}
          <p>Mostra o vínculo, recorte e posição previstos. Legendas, transições, som e a desaceleração automática de vídeo são aplicados somente no render final.</p>
        </section>
      </div>}
      <input ref={scenePreviewInput} type="file" hidden accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" onChange={event => {
        if (event.target.files) void uploadMediaForScenePreview(event.target.files);
        event.target.value = "";
      }} />

      <footer className={`statusbar${jobId || renderProgress ? " has-render-progress" : ""}`}>
        <div className="status-detail">
          <span className={jobId ? "status working" : "status"}>{status}</span>
          {renderError && <span className="render-error-detail" title={renderError}>{renderError}</span>}
          {(jobId || renderProgress > 0) && (
            <div className={`render-progress${jobId ? " active" : ""}`} aria-label="Andamento da renderização">
              <div>
                <span>{renderStage || "Aguardando renderização"}</span>
                <b>{renderElapsedSeconds !== null ? `${formatVideoDuration(renderElapsedSeconds)} decorridos` : "calculando…"}{renderEtaSeconds !== null ? ` · ~${formatRemainingTime(renderEtaSeconds)} restantes` : ""} · {Math.round(renderProgress)}%</b>
              </div>
              <i><em style={{ width: `${renderProgress}%` }} /></i>
            </div>
          )}
        </div>
        {outputUrl ? (
          <button className="button quiet compact" onClick={() => void openFinalVideosFolder()}>Abrir pasta dos vídeos</button>
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
