/** Starts the native preview immediately after a track is chosen. */
document.addEventListener('click', (event) => {
  const target = event.target as Element | null
  if (!target?.closest('.asset-list > button')) return
  window.setTimeout(() => {
    const audio = document.querySelector<HTMLAudioElement>('.asset-preview audio')
    if (audio) void audio.play()
  }, 0)
})
