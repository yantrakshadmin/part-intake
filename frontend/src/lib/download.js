/** Save figures as PNG files: SVG figures are rasterized at 2× via canvas;
 *  WebGL canvases need preserveDrawingBuffer:true on their renderer. */

function trigger(href, filename) {
  const a = document.createElement('a')
  a.href = href
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(href), 5000)
}

export function downloadSvgPng(svg, filename, scale = 2) {
  const w = svg.width.baseVal.value || svg.viewBox.baseVal.width
  const h = svg.height.baseVal.value || svg.viewBox.baseVal.height
  const xml = new XMLSerializer().serializeToString(svg)
  const url = URL.createObjectURL(
    new Blob([xml], { type: 'image/svg+xml;charset=utf-8' }))
  const img = new Image()
  img.onload = () => {
    const c = document.createElement('canvas')
    c.width = Math.round(w * scale)
    c.height = Math.round(h * scale)
    const ctx = c.getContext('2d')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, c.width, c.height)
    ctx.drawImage(img, 0, 0, c.width, c.height)
    URL.revokeObjectURL(url)
    c.toBlob((b) => trigger(URL.createObjectURL(b), filename), 'image/png')
  }
  img.src = url
}

export function downloadCanvasPng(canvas, filename) {
  canvas.toBlob((b) => trigger(URL.createObjectURL(b), filename), 'image/png')
}

/** Find the figure inside `el` (svg or canvas) and download it. */
export function downloadFigure(el, filename) {
  if (!el) return
  const svg = el.querySelector('svg.insert-iso, svg.truck-iso')
  if (svg) return downloadSvgPng(svg, filename)
  const canvas = el.querySelector('canvas')
  if (canvas) return downloadCanvasPng(canvas, filename)
}
