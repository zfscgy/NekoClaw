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
