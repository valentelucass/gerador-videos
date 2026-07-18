/** Mirrors the chosen transition pool in the visual timeline. */
document.addEventListener('click', (event) => {
  const button = (event.target as Element | null)?.closest<HTMLButtonElement>('.apply-choice.wide')
  if (!button || !button.textContent?.includes('overlays')) return
  window.setTimeout(() => {
    const track = document.querySelector('.video-track')
    if (!track) return
    track.querySelectorAll('.overlay-marker').forEach(node => node.remove())
    const selected = [...document.querySelectorAll('.visual-card.chosen span')]
    const clips = [...track.querySelectorAll('.timeline-clip')]
    if (!selected.length) return
    Array.from({ length: Math.max(0, clips.length - 1) }, (_, index) => selected[index % selected.length]).forEach((label, index) => {
      const marker = document.createElement('span')
      marker.className = 'overlay-marker'
      marker.textContent = `✦ ${label.textContent || 'Overlay'}`
      clips[index].after(marker)
    })
  }, 0)
})
