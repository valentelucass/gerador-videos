import { ChangeEvent, DragEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Script = {
  title: string;
  language: string;
  narrator_gender: string;
  background_animation?: "none" | "movimento_sutil" | "movimento_lateral" | "pulsacao";
  blocks: { id: string; text: string; scenes: { id: string; image: string }[] }[];
};
type Catalog = { images: string[]; backgrounds: string[]; music: string[]; sounds: string[] };
type Voice = { id: string; preview_url: string | null };
type VoiceLanguage = { locale: string; groups: { Masculinas: Voice[]; Femininas: Voice[] } };
type VoiceCatalog = { languages: VoiceLanguage[]; total: number; generated: number };

const example: Script = {
  title: "Novo vídeo", language: "pt-BR", narrator_gender: "male", background_animation: "movimento_sutil",
  blocks: [{ id: "bloco_01", text: "Cole aqui a narração oficial do seu roteiro.", scenes: [{ id: "cena_01", image: "sua-imagem.jpg" }] }],
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
  return body as T;
}

function App() {
  const [source, setSource] = useState(JSON.stringify(example, null, 2));
  const [script, setScript] = useState<Script>(example);
  const [catalog, setCatalog] = useState<Catalog>({ images: [], backgrounds: [], music: [], sounds: [] });
  const [background, setBackground] = useState("");
  const [voices, setVoices] = useState<VoiceCatalog>({ languages: [], total: 0, generated: 0 });
  const [selectedVoice, setSelectedVoice] = useState("");
  const [status, setStatus] = useState("Importe o JSON e envie as imagens das cenas.");
  const [jobId, setJobId] = useState("");

  const refreshCatalog = async () => {
    try {
      const next = await api<Catalog>("/api/catalog");
      setCatalog(next);
      setBackground(current => current || next.backgrounds.find(name => name.includes("Wireframe_grid")) || next.backgrounds[0] || "");
    } catch { setStatus("Inicie o backend em http://localhost:8000."); }
  };
  useEffect(() => { void refreshCatalog(); }, []);
  const refreshVoices = async () => {
    try { setVoices(await api<VoiceCatalog>("/api/voices")); }
    catch { setStatus("Não foi possível carregar o catálogo de vozes."); }
  };
  useEffect(() => { void refreshVoices(); }, []);

  useEffect(() => {
    if (!jobId) return;
    const timer = window.setInterval(async () => {
      try {
        const job = await api<{ status: string; output?: string; error?: string }>(`/api/jobs/${jobId}`);
        if (job.status === "complete") { setStatus(`Vídeo pronto: ${job.output}`); setJobId(""); }
        if (job.status === "failed") { setStatus(`A renderização falhou: ${job.error}`); setJobId(""); }
      } catch { setStatus("Não foi possível acompanhar a renderização."); setJobId(""); }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [jobId]);

  const applyJson = (value = source) => {
    try {
      const next = JSON.parse(value) as Script;
      setScript(next);
      setStatus(`${next.blocks.reduce((sum, block) => sum + block.scenes.length, 0)} cenas carregadas. A duração será definida pela voz real.`);
    } catch { setStatus("O JSON não é válido. Corrija-o antes de renderizar."); }
  };
  const importJson = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text(); setSource(text); applyJson(text);
  };
  const uploadFiles = async (files: FileList | File[]) => {
    if (!files.length) return;
    const form = new FormData(); Array.from(files).forEach(file => form.append("files", file));
    try {
      const result = await api<{ saved: string[] }>("/api/images", { method: "POST", body: form });
      setStatus(`${result.saved.length} imagem(ns) salva(s) em assets/images.`);
      await refreshCatalog();
    } catch (error) { setStatus(String(error)); }
  };
  const uploadImages = (event: ChangeEvent<HTMLInputElement>) => { if (event.target.files) void uploadFiles(event.target.files); };
  const dropImages = (event: DragEvent<HTMLLabelElement>) => { event.preventDefault(); void uploadFiles(event.dataTransfer.files); };
  const validate = async () => {
    try {
      const report = await api<{ valid: boolean; errors: string[]; missing_images: string[] }>("/api/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(script) });
      setStatus(report.valid ? (report.missing_images.length ? `Faltam estas imagens: ${report.missing_images.join(", ")}` : "Roteiro válido. Todas as imagens estão disponíveis.") : report.errors.join("\n"));
    } catch (error) { setStatus(String(error)); }
  };
  const render = async () => {
    if (!background) { setStatus("Escolha uma imagem de fundo antes de renderizar."); return; }
    try {
      const result = await api<{ job_id: string }>("/api/render", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ script, background_image: background, voice: selectedVoice || null }) });
      setJobId(result.job_id); setStatus("Gerando narração neural e renderizando o vídeo…");
    } catch (error) { setStatus(String(error)); }
  };
  const generatePreviews = async () => {
    try {
      await api("/api/voice-previews", { method: "POST" });
      setStatus("Gerando todas as amostras de voz… a lista será atualizada automaticamente.");
      const timer = window.setInterval(() => { void refreshVoices(); }, 2500);
      window.setTimeout(() => window.clearInterval(timer), 180000);
    } catch (error) { setStatus(String(error)); }
  };
  const chooseVoice = (voice: string, locale: string, gender: "Masculinas" | "Femininas") => {
    const next = { ...script, language: locale, narrator_gender: gender === "Masculinas" ? "male" : "female" };
    setSelectedVoice(voice); setScript(next); setSource(JSON.stringify(next, null, 2));
    setStatus(`${voice} selecionada para a renderização.`);
  };

  return <main>
    <header><div><p className="eyebrow">SYNTHREEL / HORIZONTAL</p><h1>Gerador de vídeo</h1><p>JSON, imagens e um fundo. Só o necessário.</p></div><div className="voice">Voz neural · duração real</div></header>
    <section className="workspace">
      <article className="panel json-panel"><div className="panel-title"><h2>1. Roteiro JSON</h2><label className="text-button">Importar arquivo<input type="file" accept="application/json" hidden onChange={importJson} /></label></div><textarea value={source} spellCheck={false} onChange={event => setSource(event.target.value)} /><button className="secondary full" onClick={() => applyJson()}>Aplicar JSON</button></article>
      <article className="panel upload-panel"><div className="panel-title"><h2>2. Imagens das cenas</h2><span>{catalog.images.length} no acervo</span></div><label className="dropzone" onDragOver={event => event.preventDefault()} onDrop={dropImages}>Arraste as imagens aqui <small>ou clique para importar</small><input type="file" hidden multiple accept="image/png,image/jpeg,image/webp" onChange={uploadImages} /></label><div className="thumbs">{catalog.images.slice(-8).map(image => <img key={image} src={`/assets/images/${encodeURIComponent(image)}`} title={image} alt="Imagem disponível" />)}</div></article>
      <article className="panel background-panel"><div className="panel-title"><h2>3. Fundo animado</h2><span>movimento contínuo</span></div>{background && <img className="background-preview" src={`/assets/backgrounds/${encodeURIComponent(background)}`} alt="Fundo escolhido" />}<select value={background} onChange={event => setBackground(event.target.value)}>{catalog.backgrounds.length === 0 && <option>Sem fundos disponíveis</option>}{catalog.backgrounds.map(item => <option key={item} value={item}>{item}</option>)}</select><label className="animation">Animação <select value={script.background_animation ?? "movimento_sutil"} onChange={event => { const next = { ...script, background_animation: event.target.value as Script["background_animation"] }; setScript(next); setSource(JSON.stringify(next, null, 2)); }}><option value="movimento_sutil">Movimento suave</option><option value="movimento_lateral">Movimento lateral</option><option value="pulsacao">Pulsação</option><option value="none">Sem movimento</option></select></label></article>
    </section>
    <section className="voice-library"><div className="voice-library-title"><div><p className="eyebrow">VOZES NEURAIS</p><h2>Escolha ouvindo</h2><span>{voices.generated}/{voices.total} amostras prontas</span></div><button className="secondary" onClick={generatePreviews}>Gerar todas as amostras</button></div>{voices.languages.map(language => <article className="voice-language" key={language.locale}><h3>{language.locale}</h3><div className="voice-groups">{(["Masculinas", "Femininas"] as const).map(gender => <section className="voice-group" key={gender}><h4>{gender}</h4>{language.groups[gender].map(voice => <div className={`voice-card ${selectedVoice === voice.id ? "selected" : ""}`} key={voice.id}><b>{voice.id.replace(`${language.locale}-`, "").replace("Neural", "")}</b><button onClick={() => chooseVoice(voice.id, language.locale, gender)}>Usar voz</button>{voice.preview_url ? <audio controls preload="none" src={voice.preview_url} /> : <small>Amostra ainda não gerada</small>}</div>)}</section>)}</div></article>)}</section>
    <section className="actions"><p className="status">{status}</p><div><button className="secondary" onClick={validate}>Validar</button><button className="primary" disabled={Boolean(jobId)} onClick={render}>{jobId ? "Renderizando…" : "Gerar vídeo"}</button></div></section>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
