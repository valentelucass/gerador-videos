const previews = new Map<number, string>()

document.addEventListener('change', event => {
  const input = event.target as HTMLInputElement
  if (!input.matches('.media-actions input[type="file"]') || !input.files?.length) return
  const files = [...input.files]
  const cards = [...document.querySelectorAll<HTMLElement>('.asset-card')]
  files.forEach((file, fileIndex) => {
    const match = file.name.match(/cena[_ -]?(\d+)/i)
    const index = match ? Number(match[1]) - 1 : fileIndex
    if (!cards[index]) return
    const url = URL.createObjectURL(file)
    previews.set(index, url)
    const thumb = cards[index].querySelector<HTMLElement>('.asset-thumb')
    if (thumb) thumb.innerHTML = `<img src="${url}" alt="Mídia importada" />`
  })
})

const showScenePreview = (index: number) => {
  const url = previews.get(index)
  const canvas = document.querySelector<HTMLElement>('.preview-canvas')
  if (url && canvas) canvas.innerHTML = `<img src="${url}" alt="Prévia da mídia da cena" />`
}

document.addEventListener('click', event => {
  const card = (event.target as Element | null)?.closest<HTMLElement>('.asset-card')
  if (!card) return
  showScenePreview([...document.querySelectorAll('.asset-card')].indexOf(card))
})

document.addEventListener('click', event => {
  const clip = (event.target as Element | null)?.closest<HTMLElement>('.timeline-clip')
  if (!clip) return
  showScenePreview([...document.querySelectorAll('.timeline-clip')].indexOf(clip))
})
