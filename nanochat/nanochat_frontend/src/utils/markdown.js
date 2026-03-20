import { marked } from 'marked'
import DOMPurify from 'dompurify'
import markedKatex from 'marked-katex-extension'

marked.use(markedKatex({ throwOnError: false, nonStandard: true }))

export function renderMarkdown(text) {
  if (!text) return ''
  try {
    return DOMPurify.sanitize(marked.parse(text), {
      USE_PROFILES: { html: true, mathMl: true },
    })
  } catch {
    return text
  }
}
