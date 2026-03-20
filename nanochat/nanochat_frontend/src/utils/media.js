export function isImage(path) {
  const name = fileName(path)
  return /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(name)
}

export function mediaUrl(path) {
  if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:'))
    return path
  if (path.startsWith('/')) return path
  return '/' + path
}

export function fileName(path) {
  try {
    const u = new URL(path, location.origin)
    const n = u.searchParams.get('name')
    if (n) return n
    return u.pathname.split('/').pop() || path
  } catch {
    return path.split('/').pop() || path
  }
}
