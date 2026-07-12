import { marked } from 'marked'
import DOMPurify from 'dompurify'
import markedKatex from 'marked-katex-extension'

marked.use(markedKatex({ throwOnError: false, nonStandard: true }))

function stripWrappingQuote(text: string): string {
  const trimmed = text.trim()
  const quote = trimmed[0]
  if ((quote !== '"' && quote !== "'") || trimmed[trimmed.length - 1] !== quote)
    return text

  const inner = trimmed.slice(1, -1)
  const unescaped = unescapeStringifiedMath(inner)
  return isDisplayMath(unescaped) || isInlineMath(unescaped) ? unescaped : text
}

function unescapeStringifiedMath(text: string): string {
  return text
    .replace(/\\n/g, '\n')
    .replace(/\\\\(?=(?:[A-Za-z]|\[|\]|\(|\)))/g, '\\')
}

function isDisplayMath(text: string): boolean {
  return /^\\\[[\s\S]*\\\]$/.test(text.trim()) || /^\$\$[\s\S]*\$\$$/.test(text.trim())
}

function isInlineMath(text: string): boolean {
  return /^\\\([\s\S]*\\\)$/.test(text.trim()) || /^\$[\s\S]*\$$/.test(text.trim())
}

function normalizeMathDelimiters(text: string): string {
  return stripWrappingQuote(text)
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, math: string) => {
      return `$$\n${math.trim()}\n$$`
    })
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, math: string) => {
      return `$${math.trim()}$`
    })
}

export function renderMarkdown(text: string): string {
  if (!text) return ''
  try {
    return DOMPurify.sanitize(marked.parse(normalizeMathDelimiters(text)) as string, {
      USE_PROFILES: { html: true, mathMl: true },
    })
  } catch {
    return text
  }
}
