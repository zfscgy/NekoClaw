export function toolCallName(content) {
  if (!content || typeof content !== 'string') return 'tool'
  const idx = content.indexOf('(')
  return idx >= 0 ? content.slice(0, idx) : content
}

export function toolCallRest(content) {
  if (!content || typeof content !== 'string') return ''
  const idx = content.indexOf('(')
  return idx >= 0 ? content.slice(idx) : ''
}

export function visibleItems(items) {
  return items.filter(i => {
    if (i.type !== 'tool_call') return true
    const name = (i.content || '').match(/^(\w+)/)?.[1]
    return name !== 'send_message_with_attachments'
  })
}

export function actionGroupLabel(items, visible) {
  const tools = visible.filter(i => i.type === 'tool_call')
  const hasResponse = visible.some(i => i.type === 'reasoning_response')
  if (tools.length) {
    const names = tools.map(i => toolCallName(i.content))
    return names.length === 1 ? `Used ${names[0]}` : `Used ${names.join(', ')}`
  }
  return hasResponse ? 'Reasoned' : 'Thinking'
}
