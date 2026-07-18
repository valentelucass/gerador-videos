/** Local timeline navigation: horizontal scrolling and zoom without rendering. */
let scale = 1
const applyScale = () => document.querySelector<HTMLElement>('.tracks')?.style.setProperty('--timeline-width', `${Math.round(1200 * scale)}px`)

document.addEventListener('DOMContentLoaded', () => {
  const toolbar = document.querySelector('.timeline-toolbar')
  if (!toolbar || toolbar.querySelector('.zoom-controls')) return
  const controls = document.createElement('div')
  controls.className = 'zoom-controls'
  controls.innerHTML = '<button type="button" data-zoom="out">−</button><span>100%</span><button type="button" data-zoom="in">+</button>'
  toolbar.append(controls)
  controls.addEventListener('click', event => {
    const value = (event.target as HTMLElement).dataset.zoom
    if (!value) return
    scale = Math.max(.6, Math.min(2.4, scale + (value === 'in' ? .2 : -.2)))
    controls.querySelector('span')!.textContent = `${Math.round(scale * 100)}%`
    applyScale()
  })
  applyScale()
})

document.addEventListener('click', event => {
  const target = event.target as Element | null
  if (!target?.closest('.asset-card')) return
  const canvas = document.querySelector<HTMLElement>('.preview-canvas')
  const label = target.closest('.asset-card')?.querySelector('.asset-thumb span')?.textContent
  if (canvas && label) canvas.dataset.template = label
})
