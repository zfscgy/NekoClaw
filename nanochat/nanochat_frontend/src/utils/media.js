function decodeName(value) {
  if (!value) return ''
  let decoded = value
  for (let i = 0; i < 2; i++) {
    try {
      const next = decodeURIComponent(decoded)
      if (next === decoded) break
      decoded = next
    } catch {
      break
    }
  }
  return decoded
}

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
    if (n) return decodeName(n)
    return decodeName(u.pathname.split('/').pop() || path)
  } catch {
    return decodeName(path.split('/').pop() || path)
  }
}
