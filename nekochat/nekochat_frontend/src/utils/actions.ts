export interface ActionItem {
  type: string
  content?: string
  toolCallId?: string
  toolName?: string
  toolResult?: string
  toolArguments?: Record<string, unknown>
}

export function toolCallName(content: string): string {
  if (!content || typeof content !== 'string') return 'tool'
  const idx = content.indexOf('(')
  return idx >= 0 ? content.slice(0, idx) : content
}

export function toolCallRest(content: string): string {
  if (!content || typeof content !== 'string') return ''
  const idx = content.indexOf('(')
  return idx >= 0 ? content.slice(idx) : ''
}

export function visibleItems(items: ActionItem[]): ActionItem[] {
  return items.filter(i => {
    if (i.type !== 'tool_call') return true
    const name = (i.content || '').match(/^(\w+)/)?.[1]
    return name !== 'send_message_with_attachments'
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
