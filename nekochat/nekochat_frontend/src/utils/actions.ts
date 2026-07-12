export interface ActionItem {
  type: string
  content?: unknown
  toolCallId?: string
  toolName?: string
  toolResult?: string
  toolArguments?: Record<string, unknown>
  model?: string
}

const SUBAGENT_TOOL_NAMES = new Set(['call_subagent', 'spawn'])

export interface ToolArgEntry {
  key: string
  value: string
}

export interface ToolResultDetails {
  title: string
  argEntries: ToolArgEntry[]
  body: string
}

function _toolPayload(content: unknown): { name?: string; arguments?: unknown } | null {
  if (content != null && typeof content === 'object' && !Array.isArray(content))
    return content as { name?: string; arguments?: unknown }
  return null
}

function _formatArgValue(value: unknown): string {
  if (value == null) return String(value)
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function toolCallName(content: unknown): string {
  const payload = _toolPayload(content)
  if (payload?.name) return String(payload.name)
  if (!content || typeof content !== 'string') return 'tool'
  const idx = content.indexOf('(')
  return idx >= 0 ? content.slice(0, idx) : content
}

export function isSubagentToolName(name: string | undefined): boolean {
  return !!name && SUBAGENT_TOOL_NAMES.has(name)
}

export function isSubagentToolCall(item: ActionItem): boolean {
  if (item.type !== 'tool_call') return false
  return isSubagentToolName(item.toolName || toolCallName(item.content))
}

export function toolCallRest(content: unknown): string {
  const payload = _toolPayload(content)
  if (payload) {
    try {
      const args = typeof payload.arguments === 'string'
        ? payload.arguments
        : JSON.stringify(payload.arguments ?? {})
      return `(${args})`
    } catch {
      return `(${String(payload.arguments ?? '')})`
    }
  }
  if (!content || typeof content !== 'string') return ''
  const idx = content.indexOf('(')
  return idx >= 0 ? content.slice(idx) : ''
}

// ── [func][arg=val ...] tool call rendering ─────────────────────────────
//
// Tool calls stream in token-by-token, so the raw arguments JSON is often
// truncated mid-object/mid-string while the model is still emitting it.
// `parsePartialArgs` is a tolerant hand-rolled scanner (not a strict JSON
// parser) that walks a `{"key": value, ...}` object text and extracts every
// key/value pair it can fully resolve, plus a best-effort trailing entry —
// marked `truncated` — for whatever was cut off at the end of the buffer.

export interface PartialArgEntry {
  key: string
  value: string
  truncated?: boolean
}

function _skipWs(s: string, i: number): number {
  while (i < s.length && /\s/.test(s[i])) i++
  return i
}

/** Find the index just past the closing quote of a JSON string starting at `s[start] === '"'`, or -1 if unterminated. */
function _findStringEnd(s: string, start: number): number {
  let i = start + 1
  while (i < s.length) {
    if (s[i] === '\\') { i += 2; continue }
    if (s[i] === '"') return i + 1
    i++
  }
  return -1
}

/** Decode a complete JSON string literal (with surrounding quotes) to its text value. */
function _decodeJsonString(literal: string): string {
  try {
    return JSON.parse(literal) as string
  } catch {
    return literal.slice(1, -1)
  }
}

/** Find the index just past the matching closer for a `{`/`[` opened at `start`, or -1 if unterminated. */
function _findMatchingBracket(s: string, start: number): number {
  const open = s[start]
  const close = open === '{' ? '}' : ']'
  let depth = 0
  let i = start
  while (i < s.length) {
    const ch = s[i]
    if (ch === '"') {
      const end = _findStringEnd(s, i)
      if (end < 0) return -1
      i = end
      continue
    }
    if (ch === open) depth++
    else if (ch === close) {
      depth--
      if (depth === 0) return i + 1
    }
    i++
  }
  return -1
}

/** Format a resolved value (already a string/number/bool/null/raw-json snippet) for inline display. */
function _formatInlineValue(raw: string): string {
  const trimmed = raw.trim()
  if (trimmed.length > 160) return `${trimmed.slice(0, 160)}…`
  return trimmed
}

export function parsePartialArgs(raw: string): PartialArgEntry[] {
  const s = raw.trim()
  const entries: PartialArgEntry[] = []
  let i = 0
  // Skip a single leading '{' (the object we're scanning inside of).
  if (s[0] === '{') i = 1

  while (true) {
    i = _skipWs(s, i)
    if (i >= s.length) break
    if (s[i] === ',') { i++; continue }
    if (s[i] === '}') break

    if (s[i] !== '"') {
      // Malformed / not a key start — nothing more we can safely parse.
      break
    }
    const keyEnd = _findStringEnd(s, i)
    if (keyEnd < 0) {
      // Dangling partial key name (streamed mid-token) — surface it as a
      // truncated entry with no value yet so the UI can still show progress.
      entries.push({ key: `${s.slice(i + 1)}…`, value: '', truncated: true })
      return entries
    }
    const key = _decodeJsonString(s.slice(i, keyEnd))
    i = _skipWs(s, keyEnd)
    if (s[i] !== ':') {
      entries.push({ key, value: '', truncated: true })
      return entries
    }
    i = _skipWs(s, i + 1)
    if (i >= s.length) {
      entries.push({ key, value: '', truncated: true })
      return entries
    }

    const valueStart = i
    if (s[i] === '"') {
      const end = _findStringEnd(s, i)
      if (end < 0) {
        entries.push({ key, value: _formatInlineValue(s.slice(i + 1)), truncated: true })
        return entries
      }
      entries.push({ key, value: _formatInlineValue(_decodeJsonString(s.slice(i, end))) })
      i = end
    } else if (s[i] === '{' || s[i] === '[') {
      const end = _findMatchingBracket(s, i)
      if (end < 0) {
        entries.push({ key, value: _formatInlineValue(s.slice(valueStart)), truncated: true })
        return entries
      }
      let compact = s.slice(valueStart, end)
      try {
        compact = JSON.stringify(JSON.parse(compact))
      } catch { /* keep raw text */ }
      entries.push({ key, value: _formatInlineValue(compact) })
      i = end
    } else {
      let j = i
      while (j < s.length && s[j] !== ',' && s[j] !== '}') j++
      const literal = s.slice(i, j).trim()
      if (j >= s.length) {
        entries.push({ key, value: _formatInlineValue(literal), truncated: true })
        return entries
      }
      entries.push({ key, value: _formatInlineValue(literal) })
      i = j
    }
  }

  return entries
}

/** Render a fully-resolved argument value for inline `[k=v]` display. */
function _formatResolvedValue(value: unknown): string {
  if (value == null) return String(value)
  if (typeof value === 'string') return _formatInlineValue(value)
  try {
    return _formatInlineValue(JSON.stringify(value))
  } catch {
    return _formatInlineValue(String(value))
  }
}

/** Inline `arg=value arg2=value2` text for a tool call, tolerant of partial/streaming JSON. */
export function toolCallArgsInline(item: ActionItem): string {
  if (item.toolArguments && typeof item.toolArguments === 'object') {
    return Object.entries(item.toolArguments)
      .map(([k, v]) => `${k}=${_formatResolvedValue(v)}`)
      .join(' ')
  }
  const rest = toolCallRest(item.content ?? '').trim()
  const inner = rest.startsWith('(') && rest.endsWith(')') ? rest.slice(1, -1) : rest
  if (!inner.trim()) return ''
  return parsePartialArgs(inner)
    .map(e => e.value ? `${e.key}=${e.value}${e.truncated ? '…' : ''}` : `${e.key}${e.truncated ? '…' : ''}`)
    .join(' ')
}

/** `[toolName][arg=value ...]` display string for a tool call item. */
export function toolCallDisplay(item: ActionItem): { name: string; args: string } {
  const name = item.toolName || toolCallName(item.content ?? '')
  const args = toolCallArgsInline(item)
  return { name, args }
}

export function visibleItems(items: ActionItem[]): ActionItem[] {
  return items.filter(i => {
    if (i.type !== 'tool_call') return true
    const name = toolCallName(i.content)
    return name !== 'send_message_with_attachments' && !isSubagentToolName(i.toolName || name)
  })
}

export function actionGroupLabel(items: ActionItem[], visible: ActionItem[]): string {
  const tools = visible.filter(i => i.type === 'tool_call')
  const hasResponse = visible.some(i => i.type === 'reasoning_response')
  if (tools.length) {
    const names = tools.map(i => toolCallName(i.content ?? ''))
    return names.length === 1 ? `Used ${names[0]}` : `Used ${names.join(', ')}`
  }
  return hasResponse ? 'Reasoned' : 'Thinking'
}

export function extractToolArgEntries(item: ActionItem): ToolArgEntry[] {
  const args = item.toolArguments
  if (args && typeof args === 'object') {
    return Object.entries(args).map(([k, v]) => ({ key: k, value: _formatArgValue(v) }))
  }
  const rest = toolCallRest(item.content ?? '').trim()
  if (!rest || rest === '()') return []
  const inner = rest.startsWith('(') && rest.endsWith(')') ? rest.slice(1, -1).trim() : rest
  if (!inner) return []
  try {
    const parsed = JSON.parse(inner)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
      return Object.entries(parsed).map(([k, v]) => ({ key: k, value: _formatArgValue(v) }))
  } catch { /* ignore */ }
  return [{ key: 'raw', value: inner }]
}

export function toolResultDetails(item: ActionItem): ToolResultDetails | null {
  if (!item.toolResult) return null
  const name = item.toolName || toolCallName(item.content ?? '') || 'Tool'
  return {
    title: `${name} 执行结果`,
    argEntries: extractToolArgEntries(item),
    body: item.toolResult,
  }
}
