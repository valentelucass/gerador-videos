import { useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent, MouseEvent } from 'react'
import { Captions, Clapperboard, FileText, FolderOpen, ImagePlus, Music2, Play, Save, Scissors, Sparkles, Upload, Video, X } from 'lucide-react'
import './App.css'
import './overrides.css'

type Scene = { texto?: string; template_id?: number; fonte_midia?: string; prompt_ou_busca?: string; textos_tela?: string[]; midias?: unknown[]; sub_cenas?: Scene[] }
type Script = { tema?: string; idioma?: string; cenas?: Scene[] }
type Asset = { name: string; path: string; url: string; kind: string }
type Assets = { tracks: Asset[]; backgrounds: Asset[]; transitions: Asset[] }
type ProcessLog = { id: string; kind: string; status: string; command: string[]; output: string; created_at: string; project_id?: string }
type ProjectSummary = { id: string; title: string; has_script: boolean; media_count: number; updated_at: string }
type PreviewTask = { scene: number; attempt: number; message: string } | null
type Tab = 'media' | 'audio' | 'text' | 'transitions' | 'background'

const api = 'http://127.0.0.1:8000/api'
const mediaHost = 'http://127.0.0.1:8000'
const blank: Script = { tema: 'Novo projeto', idioma: 'pt-BR', cenas: [] }
const timecode = (seconds: number) => `00:${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}:00`
const sceneDuration = (scene: Scene) => Math.max(3, Math.ceil((scene.texto?.trim().split(/\s+/).filter(Boolean).length ?? 0) / 2.5))

export default function App() {
  const [script, setScript] = useState<Script>(blank)
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(() => localStorage.getItem('synthreel.currentProject'))
  const [tab, setTab] = useState<Tab>('media')
  const [selected, setSelected] = useState(0)
  const [notice, setNotice] = useState('Importe um roteiro JSON para começar')
  const [assets, setAssets] = useState<Assets>({ tracks: [], backgrounds: [], transitions: [] })
  const [projectMedia, setProjectMedia] = useState<Asset[]>([])
  const [music, setMusic] = useState<Asset | null>(null)
  const [musicPreview, setMusicPreview] = useState<Asset | null>(null)
  const [musicDuration, setMusicDuration] = useState<number | null>(null)
  const [background, setBackground] = useState<Asset | null>(null)
  const [backgroundPreview, setBackgroundPreview] = useState<Asset | null>(null)
  const [backgroundByScene, setBackgroundByScene] = useState<Record<number, string>>({})
  const [textColor, setTextColor] = useState('#000000')
  const [template4TextColor, setTemplate4TextColor] = useState('#ffffff')
  const [template6TextColor, setTemplate6TextColor] = useState('#ffffff')
  const [template4ColorDraft, setTemplate4ColorDraft] = useState('#ffffff')
  const [template6ColorDraft, setTemplate6ColorDraft] = useState('#ffffff')
  const [otherColorDraft, setOtherColorDraft] = useState('#000000')
  const [textBorderEnabled, setTextBorderEnabled] = useState(true)
  const [textBorderColor, setTextBorderColor] = useState('#ffffff')
  const [chosenOverlays, setChosenOverlays] = useState<string[]>([])
  const [overlaysByCut, setOverlaysByCut] = useState<Record<number, string>>({})
  const [selectedCut, setSelectedCut] = useState<number | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [scenePreviews, setScenePreviews] = useState<Record<number, string>>({})
  const [previewProgress, setPreviewProgress] = useState<{ done: number; total: number } | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [scenePreviewErrors, setScenePreviewErrors] = useState<Record<number, string>>({})
  const [previewTask, setPreviewTask] = useState<PreviewTask>(null)
  const [showingFullPreview, setShowingFullPreview] = useState(false)
  const [fullPreviewLoading, setFullPreviewLoading] = useState(false)
  const [dragged, setDragged] = useState<number | null>(null)
  const [logsOpen, setLogsOpen] = useState(false)
  const [processLogs, setProcessLogs] = useState<ProcessLog[]>([])
  const [projectsOpen, setProjectsOpen] = useState(false)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null)
  const [renameTitle, setRenameTitle] = useState('')
  const [narrationUrl, setNarrationUrl] = useState<string | null>(null)
  const [narrationLoading, setNarrationLoading] = useState(false)
  const [narrationTime, setNarrationTime] = useState(0)
  const [narrationDuration, setNarrationDuration] = useState(0)
  const narrationRef = useRef<HTMLAudioElement>(null)
  const pendingNarrationRatio = useRef<number | null>(null)
  const previewBatchRef = useRef<string | null>(null)

  const scenes = script.cenas ?? []
  const active = scenes[selected]
  const duration = useMemo(() => scenes.reduce((total, scene) => total + sceneDuration(scene), 0), [scenes])
  const payload = (content = script) => ({ title: content.tema || 'projeto', niche: 'historia', content, music_track: music?.path ?? null, background_default: background?.path ?? null, background_by_scene: Object.fromEntries(Object.entries(backgroundByScene).map(([index, path]) => [String(Number(index) + 1), path]).filter(([, path]) => Boolean(path))), text_color: textColor, text_border_enabled: textBorderEnabled, text_border_color: textBorderColor, text_styles: { template_4: { color: template4TextColor, border_enabled: textBorderEnabled, border_color: textBorderColor }, template_6: { color: template6TextColor, border_enabled: textBorderEnabled, border_color: textBorderColor }, others: { color: textColor, border_enabled: textBorderEnabled, border_color: textBorderColor } }, transition_pool: [...new Set(Object.values(overlaysByCut).filter(Boolean))], transition_assignments: Object.fromEntries(Object.entries(overlaysByCut).filter(([, path]) => Boolean(path))) })

  useEffect(() => {
    fetch(`${api}/assets`).then(response => response.json()).then((available: Assets) => {
      setAssets(available)
      const lastProject = localStorage.getItem('synthreel.currentProject')
      if (lastProject) void openProject(lastProject, available)
    }).catch(() => setNotice('API local indisponível'))
  }, [])
  useEffect(() => { if (!showingFullPreview) { setPreviewUrl(scenePreviews[selected] ?? null); setPreviewError(scenePreviewErrors[selected] ?? null) } }, [selected, scenePreviews, scenePreviewErrors, showingFullPreview])
  useEffect(() => {
    if (!currentProjectId || !scenes.length || !projectMedia.length) return
    const batch = `${currentProjectId}:${scenes.length}:${projectMedia.length}:${background?.path ?? ''}`
    if (previewBatchRef.current === batch) return
    previewBatchRef.current = batch
    void preloadPreviews(currentProjectId)
  }, [currentProjectId, scenes.length, projectMedia.length, background?.path])
  const refreshLogs = async () => {
    try { setProcessLogs(await fetch(`${api}/logs`).then(response => response.json()) as ProcessLog[]) }
    catch { setNotice('Não foi possível carregar os logs locais.') }
  }
  useEffect(() => { if (!logsOpen) return; void refreshLogs(); const timer = window.setInterval(() => void refreshLogs(), 2500); return () => window.clearInterval(timer) }, [logsOpen])
  useEffect(() => {
    if (!currentProjectId || !scenes.length) return
    localStorage.setItem('synthreel.currentProject', currentProjectId)
    const timer = window.setTimeout(() => { void saveProject().catch(() => setNotice('Não foi possível salvar automaticamente o projeto.')) }, 700)
    return () => window.clearTimeout(timer)
  }, [script, music, background, backgroundByScene, textColor, template4TextColor, template6TextColor, textBorderEnabled, textBorderColor, overlaysByCut, currentProjectId])
  useEffect(() => {
    if (!narrationUrl || pendingNarrationRatio.current === null) return
    const audio = narrationRef.current
    if (!audio) return
    const seekAndPlay = async () => {
      const ratio = pendingNarrationRatio.current ?? 0
      pendingNarrationRatio.current = null
      audio.currentTime = ratio * (audio.duration || narrationDuration || duration)
      setNarrationTime(audio.currentTime)
      try { await audio.play() } catch { setNotice('Use o controle de play da narração para iniciar o áudio.') }
    }
    audio.addEventListener('loadedmetadata', seekAndPlay, { once: true })
    if (audio.readyState >= 1) void seekAndPlay()
    return () => audio.removeEventListener('loadedmetadata', seekAndPlay)
  }, [narrationUrl, narrationDuration, duration])

  const saveProject = async (content = script, preserveSelections = true) => {
    const body = preserveSelections ? payload(content) : { ...payload(content), music_track: null, background_default: null, background_by_scene: {}, transition_pool: [], transition_assignments: {} }
    const response = await fetch(`${api}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail?.[0] || 'O roteiro precisa estar válido.')
    const saved = await response.json() as { id: string }
    setCurrentProjectId(saved.id); localStorage.setItem('synthreel.currentProject', saved.id)
    setProjects(current => {
      const existing = current.find(project => project.id === saved.id)
      const summary: ProjectSummary = {
        id: saved.id,
        title: content.tema || 'projeto',
        has_script: true,
        media_count: existing?.media_count ?? 0,
        updated_at: new Date().toISOString(),
      }
      return [summary, ...current.filter(project => project.id !== saved.id)]
    })
    return saved.id
  }
  const refreshProjects = async () => {
    try { setProjects(await fetch(`${api}/projects`).then(response => response.json()) as ProjectSummary[]) }
    catch { setNotice('Não foi possível carregar a biblioteca de projetos.') }
  }
  const assetFrom = (path: string | null | undefined, available: Assets) => [...available.tracks, ...available.backgrounds, ...available.transitions].find(asset => asset.path === path) ?? null
  const openProject = async (id: string, available = assets) => {
    try {
      const response = await fetch(`${api}/projects/${id}`)
      const project = await response.json()
      if (!response.ok) throw new Error(project.detail || 'Não foi possível abrir o projeto.')
      const media = await fetch(`${api}/projects/${id}/media`).then(item => item.json()) as Asset[]
      setScript(project.content as Script); setProjectMedia(media); setSelected(0)
      setMusic(assetFrom(project.music_track, available)); setBackground(assetFrom(project.background_default, available)); setBackgroundByScene(Object.fromEntries(Object.entries(project.background_by_scene ?? {}).map(([index, path]) => [Number(index) - 1, String(path)])))
      const textStyles = project.text_styles ?? {}; const asColor = (value: unknown, fallback: string) => String(value || fallback).replace(/^0x/i, '#'); const other = asColor(textStyles.others?.color, '#000000'); const template4 = asColor(textStyles.template_4?.color, '#ffffff'); const template6 = asColor(textStyles.template_6?.color, '#ffffff'); setChosenOverlays(project.transition_pool ?? []); setOverlaysByCut(project.transition_assignments ?? {}); setTextColor(other); setTemplate4TextColor(template4); setTemplate6TextColor(template6); setOtherColorDraft(other); setTemplate4ColorDraft(template4); setTemplate6ColorDraft(template6); setTextBorderEnabled(textStyles.others?.border_enabled !== false); setTextBorderColor(asColor(textStyles.others?.border_color, '#ffffff'))
      setScenePreviews({}); setScenePreviewErrors({}); previewBatchRef.current = null; setNarrationUrl(null); setCurrentProjectId(id); localStorage.setItem('synthreel.currentProject', id)
      setRenameTitle(String(project.content.tema || id)); setNotice(`Projeto aberto: ${project.content.tema || id}.`); setProjectsOpen(false)
    } catch (error) {
      localStorage.removeItem('synthreel.currentProject'); setCurrentProjectId(null)
      setNotice(error instanceof Error ? error.message : 'Não foi possível abrir o projeto.')
    }
  }
  const newProject = () => {
    localStorage.removeItem('synthreel.currentProject'); setCurrentProjectId(null); setScript(blank); setProjectMedia([]); setMusic(null); setBackground(null); setBackgroundByScene({}); setTextColor('#000000'); setTemplate4TextColor('#ffffff'); setTemplate6TextColor('#ffffff'); setTextBorderEnabled(true); setTextBorderColor('#ffffff'); setChosenOverlays([]); setOverlaysByCut({}); setSelected(0); setScenePreviews({}); setScenePreviewErrors({}); previewBatchRef.current = null; setNarrationUrl(null); setProjectsOpen(false); setNotice('Novo projeto criado. Importe um roteiro JSON para começar.')
  }
  const deleteProject = async (project: ProjectSummary) => {
    if (!window.confirm(`Excluir o projeto “${project.title}” e todas as mídias dele? Esta ação não pode ser desfeita.`)) return
    try {
      setDeletingProjectId(project.id)
      const response = await fetch(`${api}/projects/${project.id}`, { method: 'DELETE' })
      if (!response.ok) throw new Error((await response.json()).detail || 'Não foi possível excluir o projeto.')
      setProjects(current => current.filter(item => item.id !== project.id))
      if (currentProjectId === project.id) {
        localStorage.removeItem('synthreel.currentProject'); setCurrentProjectId(null); setScript(blank); setProjectMedia([]); setMusic(null); setBackground(null); setBackgroundByScene({}); setTextColor('#000000'); setTemplate4TextColor('#ffffff'); setTemplate6TextColor('#ffffff'); setTextBorderEnabled(true); setTextBorderColor('#ffffff'); setChosenOverlays([]); setOverlaysByCut({}); setSelected(0); setScenePreviews({}); setScenePreviewErrors({}); previewBatchRef.current = null; setNarrationUrl(null)
      }
      await refreshProjects(); setNotice(`Projeto excluído: ${project.title}.`)
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Não foi possível excluir o projeto.') }
    finally { setDeletingProjectId(null) }
  }
  const renameCurrentProject = async () => {
    if (!currentProjectId || !renameTitle.trim()) return
    try {
      const response = await fetch(`${api}/projects/${currentProjectId}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: renameTitle.trim() }) })
      const result = await response.json().catch(() => ({})) as { detail?: string; id?: string; title?: string }
      if (!response.ok || !result.id) throw new Error(result.detail || 'Não foi possível renomear o projeto.')
      setCurrentProjectId(result.id); localStorage.setItem('synthreel.currentProject', result.id)
      setScript(current => ({ ...current, tema: result.title || renameTitle.trim() }))
      setProjects(current => current.map(project => project.id === currentProjectId ? { ...project, id: result.id as string, title: result.title || renameTitle.trim(), updated_at: new Date().toISOString() } : project))
      setNotice(`Projeto renomeado para ${result.title || renameTitle.trim()}.`)
      await refreshProjects()
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Não foi possível renomear o projeto.') }
  }
  const load = async (file?: File) => {
    if (!file) return
    try { const content = JSON.parse(await file.text()) as Script; setScript(content); setSelected(0); setProjectMedia([]); setMusic(null); setBackground(null); setBackgroundByScene({}); setTextColor('#000000'); setTemplate4TextColor('#ffffff'); setTemplate6TextColor('#ffffff'); setTextBorderEnabled(true); setTextBorderColor('#ffffff'); setChosenOverlays([]); setOverlaysByCut({}); setScenePreviews({}); setScenePreviewErrors({}); previewBatchRef.current = null; await saveProject(content, false); setNotice('Roteiro carregado e salvo. Agora envie suas mídias.') }
    catch { setNotice('JSON inválido') }
  }
  const upload = async (files?: FileList | null) => {
    if (!files?.length) return
    try {
      const id = await saveProject(); const body = new FormData(); [...files].forEach(file => body.append('files', file))
      const response = await fetch(`${api}/projects/${id}/media`, { method: 'POST', body })
      if (!response.ok) throw new Error('Falha ao importar mídias.')
      const uploaded = await response.json() as { uploaded: string[] }
      const media = await fetch(`${api}/projects/${id}/media`).then(response => response.json()) as { name: string; path: string; url?: string; kind?: string }[]
      setProjectMedia(media.map(item => ({ ...item, url: item.url || `/media/projects/${id}/${encodeURIComponent(item.name)}`, kind: item.kind || 'image' })))
      setProjects(current => current.map(project => project.id === id ? { ...project, media_count: media.length, updated_at: new Date().toISOString() } : project))
      setScenePreviews({}); setScenePreviewErrors({}); previewBatchRef.current = null
      setNotice(`${uploaded.uploaded.length} mídia(s) importada(s). Preparando as composições das cenas em segundo plano.`)
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Falha ao importar mídias.') }
  }
  const ensurePreview = async (index: number, knownProjectId?: string, backgroundPath?: string | null, force = false) => {
    if (!scenes[index] || (!force && scenePreviews[index])) return true
    const id = knownProjectId || await saveProject()
    setPreviewError(null); setScenePreviewErrors(current => { const next = { ...current }; delete next[index]; return next })
    let lastError = 'A prévia não pôde ser carregada.'
    for (let attempt = 1; attempt <= 3; attempt++) {
      setPreviewTask({ scene: index, attempt, message: attempt === 1 ? 'Compondo mídia e template…' : `Reconectando e tentando novamente (${attempt}/3)…` })
      try {
        const response = await fetch(`${api}/projects/${id}/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scene_index: index, background_default: backgroundPath }) })
        const result = await response.json().catch(() => ({})) as { detail?: string; url?: string }
        if (!response.ok) {
          lastError = result.detail || `Prévia da Cena ${index + 1} indisponível.`
          if (response.status < 500) break
          throw new Error(lastError)
        }
        const url = `${mediaHost}${result.url}?v=${Date.now()}`
        setScenePreviews(current => ({ ...current, [index]: url })); setPreviewTask(null)
        if (index === selected) setPreviewUrl(url)
        return true
      } catch (error) {
        lastError = error instanceof Error && error.message ? error.message : 'Conexão com a API local falhou.'
        if (attempt < 3) await new Promise(resolve => window.setTimeout(resolve, attempt * 900))
      }
    }
    const message = `Prévia da Cena ${index + 1} indisponível após 3 tentativas: ${lastError}`
    setScenePreviewErrors(current => ({ ...current, [index]: message })); setPreviewTask(null)
    if (index === selected) setPreviewError(message)
    return false
  }
  const preloadPreviews = async (knownProjectId?: string, backgroundPath?: string | null, force = false, sceneBackgrounds: Record<number, string> = {}) => {
    if (!scenes.length) return
    setPreviewProgress({ done: 0, total: scenes.length }); setPreviewError(null)
    const id = knownProjectId || await saveProject(); let failed = 0
    for (let index = 0; index < scenes.length; index++) {
      if (!(await ensurePreview(index, id, sceneBackgrounds[index] ?? backgroundPath, force))) failed++
      setPreviewProgress({ done: index + 1, total: scenes.length })
    }
    setPreviewProgress(null)
    setNotice(failed ? `${scenes.length - failed} prévia(s) pronta(s), ${failed} com erro. A cena pode ser tentada novamente.` : 'Composições das cenas prontas. Clique em qualquer cena para visualizá-la.')
  }
  const createFullPreview = async () => {
    if (!scenes.length || !projectMedia.length) return
    setFullPreviewLoading(true); setPreviewError(null); setNotice('Montando a prévia completa com cenas, narração, trilha e overlays…')
    try {
      const id = await saveProject()
      const response = await fetch(`${api}/projects/${id}/preview/full`, { method: 'POST' })
      const result = await response.json().catch(() => ({})) as { detail?: string; url?: string }
      if (!response.ok || !result.url) throw new Error(result.detail || 'Não foi possível montar a prévia completa.')
      setPreviewUrl(`${mediaHost}${result.url}?v=${Date.now()}`); setShowingFullPreview(true); setNotice('Prévia completa pronta. A reprodução abaixo inclui áudio e trilha.')
    } catch (error) { setPreviewError(error instanceof Error ? error.message : 'Falha ao criar a prévia completa.') }
    finally { setFullPreviewLoading(false) }
  }
  const loadNarration = async () => {
    if (narrationUrl) return narrationUrl
    setNarrationLoading(true)
    try {
      const id = await saveProject()
      const response = await fetch(`${api}/projects/${id}/narration`, { method: 'POST' })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || 'Narração temporária indisponível.')
      const url = `${mediaHost}${result.url}?v=${Date.now()}`
      setNarrationUrl(url); setNotice('Narração temporária pronta. Clique em qualquer ponto da faixa Voz para ouvir dali.')
      return url
    } catch (error) { setNotice(error instanceof Error ? error.message : 'Falha ao gerar narração temporária.'); return null }
    finally { setNarrationLoading(false) }
  }
  const playNarrationAt = async (ratio: number) => {
    pendingNarrationRatio.current = Math.max(0, Math.min(1, ratio))
    const url = await loadNarration()
    if (!url) return
    const audio = narrationRef.current
    if (!audio) return
    const point = pendingNarrationRatio.current * (audio.duration || narrationDuration || duration)
    pendingNarrationRatio.current = null
    audio.currentTime = point; setNarrationTime(point)
    try { await audio.play() } catch { setNotice('Use o controle de play da narração para iniciar o áudio.') }
  }
  const scrubVoice = (event: MouseEvent<HTMLDivElement>) => {
    const element = event.currentTarget
    const ratio = (event.clientX - element.getBoundingClientRect().left) / element.getBoundingClientRect().width
    void playNarrationAt(ratio)
  }
  const prepare = async () => {
    const response = await fetch(`${api}/jobs/prepare`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()) })
    setNotice(response.ok ? 'Preparo iniciado' : 'Revise o projeto antes de preparar')
  }
  const selectScene = (index: number) => {
    setShowingFullPreview(false)
    setSelected(index)
    if (!scenePreviews[index] && projectMedia.length) void ensurePreview(index)
  }
  const updateScene = (index: number, patch: Partial<Scene>) => setScript(current => ({ ...current, cenas: current.cenas?.map((scene, i) => i === index ? { ...scene, ...patch } : scene) }))
  const moveScene = (from: number, to: number) => {
    if (from === to) return
    setScript(current => {
      const next = [...(current.cenas ?? [])]
      const [scene] = next.splice(from, 1)
      next.splice(to, 0, scene)
      return { ...current, cenas: next }
    })
    setScenePreviews({}); setScenePreviewErrors({}); selectScene(to)
    setNotice('Cena reordenada. Texto, voz e tratamento visual foram movidos juntos.')
  }
  const startDrag = (event: DragEvent<HTMLButtonElement>, index: number) => { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', String(index)); setDragged(index) }
  const onDrop = (event: DragEvent<HTMLButtonElement>, target: number) => { event.preventDefault(); const source = Number(event.dataTransfer.getData('text/plain')); if (Number.isInteger(source) && source >= 0) moveScene(source, target); else if (dragged !== null) moveScene(dragged, target); setDragged(null) }
  const applyOverlays = () => {
    const next: Record<number, string> = {}
    for (let cut = 0; cut < Math.max(0, scenes.length - 1); cut++) next[cut] = chosenOverlays[cut % chosenOverlays.length] || ''
    setOverlaysByCut(next); setSelectedCut(null); setNotice(`${chosenOverlays.length} overlay(s) distribuído(s) entre as cenas.`)
  }
  const chooseOverlay = (asset: Asset) => {
    if (selectedCut !== null) { setOverlaysByCut(current => ({ ...current, [selectedCut]: asset.path })); setNotice(`Overlay da transição ${selectedCut + 1} alterado.`); return }
    setChosenOverlays(current => current.includes(asset.path) ? current.filter(path => path !== asset.path) : [...current, asset.path])
  }
  const applyBackgroundToScene = (asset: Asset) => {
    setBackgroundPreview(asset); setBackgroundByScene(current => ({ ...current, [selected]: asset.path }))
    setScenePreviews(current => { const next = { ...current }; delete next[selected]; return next })
    setScenePreviewErrors(current => { const next = { ...current }; delete next[selected]; return next })
    setPreviewUrl(null); setNotice(`Fundo aplicado somente à Cena ${selected + 1}: ${asset.name}.`)
    if (projectMedia.length && scenes.length) window.setTimeout(() => void ensurePreview(selected, undefined, asset.path, true), 0)
  }
  const applyBackgroundToAll = (asset: Asset) => {
    setBackground(asset); setBackgroundPreview(asset); setBackgroundByScene({}); setScenePreviews({}); setScenePreviewErrors({}); setPreviewUrl(null); previewBatchRef.current = `${currentProjectId}:${scenes.length}:${projectMedia.length}:${asset.path}`
    setNotice(`Fundo aplicado a todas as cenas compatíveis: ${asset.name}. Recriando as composições.`)
    if (projectMedia.length && scenes.length) window.setTimeout(() => void preloadPreviews(undefined, asset.path, true, {}), 0)
  }
  const refreshTextPreviews = (message: string) => {
    setScenePreviews({}); setScenePreviewErrors({}); setPreviewUrl(null); previewBatchRef.current = null
    setNotice(message)
    if (projectMedia.length && scenes.length) window.setTimeout(() => void preloadPreviews(undefined, undefined, true), 0)
  }
  const applyTextColor = (color: string) => { setTextColor(color); setOtherColorDraft(color); refreshTextPreviews(`Cor dos demais templates alterada para ${color}. Recriando as composições.`) }
  const applyTemplateTextColor = (template: 4 | 6, color: string) => { (template === 4 ? setTemplate4TextColor : setTemplate6TextColor)(color); refreshTextPreviews(`Cor do Template ${template} alterada para ${color}. Recriando as composições.`) }
  const applyColorToAllTemplates = (color: string) => { setTextColor(color); setTemplate4TextColor(color); setTemplate6TextColor(color); setOtherColorDraft(color); setTemplate4ColorDraft(color); setTemplate6ColorDraft(color); refreshTextPreviews(`Cor aplicada a todos os templates: ${color}. Recriando as composições.`) }
  const applyTextBorderEnabled = (enabled: boolean) => { setTextBorderEnabled(enabled); refreshTextPreviews(enabled ? 'Contorno do texto ativado. Recriando as composições.' : 'Contorno do texto removido. Recriando as composições.') }
  const applyTextBorderColor = (color: string) => { setTextBorderColor(color); refreshTextPreviews(`Cor do contorno alterada para ${color}. Recriando as composições.`) }
  const musicWarning = music && musicDuration && musicDuration < duration ? `A trilha tem ${Math.round(musicDuration)}s para um vídeo estimado em ${Math.round(duration)}s. O render vai repetir a faixa para cobrir toda a narração.` : null

  return <main className="studio">
    <header className="topbar"><div className="brand"><span><Scissors size={19} /></span><b>Estúdio de Corte</b><small>LOCAL</small></div><div className="project-name">{script.tema}</div><div className="top-actions"><button onClick={() => { setRenameTitle(script.tema || ''); void refreshProjects(); setProjectsOpen(true) }}><FolderOpen size={16} />Projetos</button><button onClick={newProject}>Novo</button><button onClick={() => setLogsOpen(true)}><FileText size={16} />Logs</button><button className="export" onClick={prepare}><Upload size={16} />Preparar vídeo</button></div></header>
    <nav className="toolstrip">{([['media', Video, 'Mídia'], ['audio', Music2, 'Áudio'], ['text', Captions, 'Texto'], ['transitions', Sparkles, 'Overlays'], ['background', Clapperboard, 'Fundos']] as const).map(([id, Icon, label]) => <button key={id} className={`tool ${tab === id ? 'active' : ''}`} onClick={() => setTab(id)}><Icon />{label}</button>)}</nav>
    <section className="editing-area">
      <aside className="media-panel"><div className="panel-head"><b>{tab === 'media' ? 'Mídia do projeto' : tab === 'audio' ? 'Escolher trilha' : tab === 'transitions' ? selectedCut === null ? 'Escolher overlays' : `Trocar overlay da transição ${selectedCut + 1}` : tab === 'background' ? 'Escolher fundo' : 'Texto em tela'}</b></div>
        {tab === 'media' && <><div className="media-actions"><label><FolderOpen size={15} />Roteiro<input type="file" accept="application/json" onChange={event => load(event.target.files?.[0])} /></label><label><ImagePlus size={15} />Importar mídias<input type="file" multiple accept="image/*,video/*" onChange={event => upload(event.target.files)} /></label></div><div className="asset-grid">{scenes.length ? scenes.map((scene, index) => {
          const composedPreview = scenePreviews[index]
          const previewFailed = scenePreviewErrors[index]
          return <button key={index} className={`asset-card ${index === selected ? 'selected' : ''}`} onClick={() => selectScene(index)}><div className={`asset-thumb ${composedPreview ? 'is-composed' : 'is-pending'}`}>{composedPreview ? <video muted autoPlay loop playsInline preload="metadata" src={composedPreview} /> : <div className="asset-thumb-status"><Clapperboard size={18} /><small>{previewFailed ? 'Prévia indisponível' : 'Compondo template…'}</small></div>}<span>Template {scene.template_id}</span></div><p>Cena {index + 1}</p><small>{composedPreview ? 'Composição final do template' : previewFailed ? 'Clique para tentar novamente' : 'Preparando layout final'}</small></button>
        }) : <div className="empty-library">Primeiro passo: importe o JSON do roteiro.</div>}</div>{projectMedia.length > 0 && <small className="imported-count">{projectMedia.length} mídia(s) disponível(is). Os cards exibem a composição final de cada template.</small>}</>}
        {tab === 'audio' && <div className="asset-list">{assets.tracks.map(asset => <button className={musicPreview?.path === asset.path ? 'chosen' : ''} onClick={() => setMusicPreview(asset)} key={asset.path}><Music2 size={15} />{asset.name}</button>)}{musicPreview && <div className="asset-preview"><audio controls autoPlay src={`${mediaHost}${musicPreview.url}`} onLoadedMetadata={event => setMusicDuration(event.currentTarget.duration)} /><button className="apply-choice" onClick={() => { setMusic(musicPreview); setNotice(`Trilha aplicada: ${musicPreview.name}`) }}>Usar esta trilha</button></div>}</div>}
        {tab === 'transitions' && <div className="visual-library">{assets.transitions.map(asset => <button className={`visual-card ${(selectedCut !== null ? overlaysByCut[selectedCut] === asset.path : chosenOverlays.includes(asset.path)) ? 'chosen' : ''}`} onClick={() => chooseOverlay(asset)} key={asset.path}><video muted autoPlay loop playsInline src={`${mediaHost}${asset.url}`} /><span>{asset.name}</span></button>)}{selectedCut === null ? <button className="apply-choice wide" disabled={!chosenOverlays.length} onClick={applyOverlays}>Aplicar às transições</button> : <button className="apply-choice wide" onClick={() => setSelectedCut(null)}>Concluir troca desta transição</button>}</div>}
        {tab === 'background' && <div className="visual-library">{assets.backgrounds.map(asset => <button className={`visual-card ${backgroundPreview?.path === asset.path ? 'chosen' : ''}`} onClick={() => setBackgroundPreview(asset)} key={asset.path}><img src={`${mediaHost}${asset.url}`} alt={asset.name} /><span>{asset.name}</span></button>)}{backgroundPreview && <><button className="apply-choice wide" onClick={() => applyBackgroundToScene(backgroundPreview)}>Usar somente na Cena {selected + 1}</button><button className="apply-choice wide" onClick={() => applyBackgroundToAll(backgroundPreview)}>Usar em todas as cenas</button></>}</div>}
        {tab === 'text' && <div className="tool-info text-controls"><Captions size={25} /><b>Texto em tela</b><p>Escolha a cor e confirme onde ela deve ser aplicada.</p><section><strong>Template 4 · texto puro</strong><label>Cor do texto<input type="color" value={template4ColorDraft} onChange={event => setTemplate4ColorDraft(event.target.value)} /></label><div className="text-apply"><button onClick={() => applyTemplateTextColor(4, template4ColorDraft)}>Aplicar só no Template 4</button><button onClick={() => applyColorToAllTemplates(template4ColorDraft)}>Aplicar a todos</button></div></section><section><strong>Template 6 · texto sobre foto</strong><label>Cor do texto<input type="color" value={template6ColorDraft} onChange={event => setTemplate6ColorDraft(event.target.value)} /></label><div className="text-apply"><button onClick={() => applyTemplateTextColor(6, template6ColorDraft)}>Aplicar só no Template 6</button><button onClick={() => applyColorToAllTemplates(template6ColorDraft)}>Aplicar a todos</button></div></section><section><strong>Demais templates com texto</strong><label>Cor do texto<input type="color" value={otherColorDraft} onChange={event => setOtherColorDraft(event.target.value)} /></label><div className="text-swatches"><button className={otherColorDraft.toLowerCase() === '#000000' ? 'chosen' : ''} onClick={() => setOtherColorDraft('#000000')}>Preto</button><button className={otherColorDraft.toLowerCase() === '#ffffff' ? 'chosen' : ''} onClick={() => setOtherColorDraft('#ffffff')}>Branco</button><button className={otherColorDraft.toLowerCase() === '#ffcc00' ? 'chosen' : ''} onClick={() => setOtherColorDraft('#ffcc00')}>Amarelo</button></div><div className="text-apply"><button onClick={() => applyTextColor(otherColorDraft)}>Aplicar aos demais</button><button onClick={() => applyColorToAllTemplates(otherColorDraft)}>Aplicar a todos</button></div></section><label className="border-toggle"><span>Usar contorno nos textos</span><input type="checkbox" checked={textBorderEnabled} onChange={event => applyTextBorderEnabled(event.target.checked)} /></label>{textBorderEnabled && <><label>Cor do contorno<input type="color" value={textBorderColor} onChange={event => applyTextBorderColor(event.target.value)} /></label><div className="text-swatches"><button className={textBorderColor.toLowerCase() === '#000000' ? 'chosen' : ''} onClick={() => applyTextBorderColor('#000000')}>Preto</button><button className={textBorderColor.toLowerCase() === '#ffffff' ? 'chosen' : ''} onClick={() => applyTextBorderColor('#ffffff')}>Branco</button><button className={textBorderColor.toLowerCase() === '#ffcc00' ? 'chosen' : ''} onClick={() => applyTextBorderColor('#ffcc00')}>Amarelo</button></div></>}<small>O texto continua vindo exclusivamente do roteiro.</small></div>}
      </aside>
      <section className="preview-panel"><div className="preview-head"><span>Reprodutor · {showingFullPreview ? 'Prévia completa' : active ? `Cena ${selected + 1}` : '—'}</span><span>{timecode(narrationTime)} / {timecode(narrationDuration || duration)}</span></div><div className="preview"><div className="preview-canvas">{previewUrl ? <video key={previewUrl} controls autoPlay loop={!showingFullPreview} src={previewUrl} /> : <><Clapperboard size={42} /><strong>{fullPreviewLoading ? 'Montando a prévia completa do vídeo' : active ? `Preparando a composição da Cena ${selected + 1}` : 'A prévia aparecerá depois de importar o roteiro.'}</strong><small>{fullPreviewLoading ? 'Encadeando cenas, narração, trilha e overlays selecionados…' : previewTask?.scene === selected ? previewTask.message : previewProgress ? `${previewProgress.done} de ${previewProgress.total} cenas preparadas` : active ? 'Selecione uma cena para carregar a prévia do template.' : 'Sem cena selecionada'}</small>{(previewTask?.scene === selected || fullPreviewLoading) && <div className="preview-loader"><i /><i /><i /></div>}</>}{previewError && <><small className="preview-error">{previewError}</small><button className="preview-retry" onClick={() => void ensurePreview(selected)}>Tentar novamente</button></>}</div></div><div className="player-controls">{previewProgress ? <span className="preview-queue">Preparando composições: {previewProgress.done}/{previewProgress.total}</span> : previewTask ? <span className="preview-queue">Cena {previewTask.scene + 1} · tentativa {previewTask.attempt}/3</span> : showingFullPreview ? <span className="preview-queue">Prévia completa do editor</span> : <span className="preview-queue">Prévia pronta ao selecionar a cena</span>}<button className="narration-button" disabled={!scenes.length || !projectMedia.length || fullPreviewLoading} onClick={() => void createFullPreview()}>{fullPreviewLoading ? 'Montando vídeo…' : 'Ver prévia completa'}</button><button className="narration-button" disabled={!active || !projectMedia.length || previewTask !== null} onClick={() => { setShowingFullPreview(false); void ensurePreview(selected, undefined, undefined, true) }}>Atualizar prévia</button>{narrationUrl ? <audio className="narration-player" ref={narrationRef} controls src={narrationUrl} onLoadedMetadata={event => setNarrationDuration(event.currentTarget.duration)} onTimeUpdate={event => setNarrationTime(event.currentTarget.currentTime)} /> : <button className="narration-button" disabled={!scenes.length || narrationLoading} onClick={() => void playNarrationAt(0)}>{narrationLoading ? 'Gerando voz…' : 'Gerar voz para edição'}</button>}<span>Composição temporária · não renderiza o vídeo final</span></div></section>
      <aside className="details-panel"><div className="panel-head"><b>Detalhes</b></div>{active ? <div className="inspector"><label>Template<select value={active.template_id ?? 1} onChange={event => updateScene(selected, { template_id: +event.target.value })}>{Array.from({ length: 12 }, (_, index) => index + 1).map(value => <option key={value}>{value}</option>)}</select></label><button className="save-button" onClick={() => { setPreviewUrl(null); setNotice('Alterações aplicadas. Gere novamente a prévia da cena.') }}><Save size={15} />Aplicar alterações</button><p className="contract-note">Arraste uma cena sobre outra para reordenar a timeline inteira. A cena leva junto texto, voz e tratamento visual.</p></div> : <div className="tool-info">Importe um roteiro para editar cenas.</div>}</aside>
    </section>
    <section className="timeline-zone"><div className="timeline-toolbar"><button className="timeline-play" disabled={!scenes.length} onClick={() => void playNarrationAt(narrationDuration ? narrationTime / narrationDuration : 0)}><Play size={16} /></button><span>{notice}</span><div className="timeline-hint">Arraste para reordenar cenas · clique no ✦ para trocar um overlay · clique na Voz para ouvir daquele ponto</div></div>{scenes.length ? <div className="timeline-body"><div className="track-labels"><div><Video size={14} />Vídeo</div><div><Captions size={14} />Texto</div><div><Music2 size={14} />Voz</div><div><Music2 size={14} />Música</div></div><div className="tracks"><div className="track video-track">{scenes.map((scene, index) => <div className="clip-group" key={index}><button draggable className={`timeline-clip ${index === selected ? 'selected' : ''}`} onDragStart={event => startDrag(event, index)} onDragEnd={() => setDragged(null)} onDragOver={event => event.preventDefault()} onDrop={event => onDrop(event, index)} onClick={() => selectScene(index)} style={{ flex: sceneDuration(scene) }}><small>CENA {index + 1}</small><b>Template {scene.template_id}</b></button>{index < scenes.length - 1 && <button className={`overlay-marker ${overlaysByCut[index] ? '' : 'empty-overlay'}`} onClick={() => { setSelectedCut(index); setTab('transitions') }}>✦ {overlaysByCut[index] ? assets.transitions.find(asset => asset.path === overlaysByCut[index])?.name || 'Overlay' : 'Escolher'}</button>}</div>)}</div><div className="track text-track">{scenes.map((scene, index) => <button key={index} onClick={() => selectScene(index)} style={{ flex: sceneDuration(scene) }}>{scene.texto?.slice(0, 26) || 'Sem texto'}</button>)}</div><div className="track voice-track"><div className="voice-wave" onClick={scrubVoice}><i className="voice-cursor" style={{ left: `${(narrationTime / (narrationDuration || duration || 1)) * 100}%` }} />Narração do roteiro · {narrationLoading ? 'gerando áudio…' : narrationUrl ? 'clique para ouvir deste ponto' : 'clique para gerar e ouvir'}</div></div><div className="track music-track"><div>{music ? music.name : 'Escolha uma trilha na aba Áudio.'}{musicWarning ? ` · ${musicWarning}` : ''}</div></div></div></div> : <div className="timeline-empty"><Video size={25} /><b>Timeline vazia</b><span>Ela só é criada ao importar cenas reais do roteiro.</span></div>}</section>
    {logsOpen && <div className="logs-backdrop" role="dialog" aria-modal="true" aria-label="Logs de processamento"><section className="logs-panel"><header><div><b>Logs de processamento</b><small>Histórico persistente dos projetos · atualização automática</small></div><div><button onClick={() => void refreshLogs()}>Atualizar</button><button aria-label="Fechar logs" onClick={() => setLogsOpen(false)}><X size={17} /></button></div></header><div className="logs-list">{processLogs.length ? processLogs.map(log => <article key={log.id}><div><b>{log.kind}</b>{log.project_id && <em>Projeto: {log.project_id}</em>}<span className={log.status}>{log.status}</span><time>{new Date(log.created_at).toLocaleString()}</time></div><code>{log.command.join(' ')}</code>{log.output && <pre>{log.output}</pre>}</article>) : <p>Nenhum log de projeto armazenado ainda.</p>}</div></section></div>}
    {projectsOpen && <div className="logs-backdrop" role="dialog" aria-modal="true" aria-label="Biblioteca de projetos"><section className="logs-panel projects-panel"><header><div><b>Projetos</b><small>Abra, renomeie ou remova um projeto completo.</small></div><div><button onClick={newProject}>Novo projeto</button><button aria-label="Fechar projetos" onClick={() => setProjectsOpen(false)}><X size={17} /></button></div></header>{currentProjectId && <div className="project-rename"><input value={renameTitle} onChange={event => setRenameTitle(event.target.value)} aria-label="Novo nome do projeto" /><button onClick={() => void renameCurrentProject()}>Renomear atual</button></div>}<div className="projects-list">{projects.length ? projects.map(project => <article key={project.id} className={project.id === currentProjectId ? 'current' : ''}><button className="project-open" disabled={deletingProjectId === project.id} onClick={() => void openProject(project.id)}><b>{project.title}</b><small>{project.media_count} mídia(s) · {new Date(project.updated_at).toLocaleString()}</small></button><button className="project-delete" disabled={deletingProjectId === project.id} onClick={() => void deleteProject(project)} aria-label={`Excluir ${project.title}`}>{deletingProjectId === project.id ? 'Excluindo…' : 'Excluir'}</button></article>) : <p>Nenhum projeto salvo ainda.</p>}</div></section></div>}
  </main>
}
