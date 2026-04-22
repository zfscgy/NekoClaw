<template>
  <div class="config-node" :class="{ 'is-root': depth === 0 }">
    <!-- Object: render each property inline -->
    <template v-if="kind === 'object'">
      <h3 v-if="depth > 0" class="config-sub-title">{{ label }}</h3>
      <p v-if="description" class="config-hint">{{ description }}</p>

      <div class="config-section">
        <ConfigNode
          v-for="[key, child] in objectProperties"
          :key="key"
          :path="child.path"
          :schema="child.schema"
          :value="child.value"
          :full-schema="fullSchema"
          :depth="depth + 1"
          @update="bubble"
        />
      </div>
    </template>

    <!-- Dynamic map: key → value (primitive or object) -->
    <template v-else-if="kind === 'map'">
      <div class="config-map-header">
        <span class="config-label">{{ label }}</span>
        <button type="button" class="btn-secondary btn-mini" @click="addMapEntry">＋ Add</button>
      </div>
      <p v-if="description" class="config-hint">{{ description }}</p>

      <div v-if="mapEntries.length === 0" class="config-empty">No entries yet.</div>

      <div v-else class="config-map-list">
        <div
          v-for="entry in mapEntries"
          :key="entry.key"
          class="config-map-entry"
        >
          <div class="config-map-key-row">
            <input
              class="config-input config-map-key"
              :value="entry.key"
              spellcheck="false"
              @change="renameMapKey(entry.key, ($event.target as HTMLInputElement).value)"
            />
            <button
              type="button"
              class="btn-icon"
              title="Remove"
              @click="removeMapEntry(entry.key)"
            >✕</button>
          </div>

          <div v-if="mapValueIsPrimitive" class="config-map-value-row">
            <ConfigNode
              :path="`${path}.${entry.key}`"
              :schema="mapValueSchema"
              :value="entry.value"
              :full-schema="fullSchema"
              :depth="depth + 1"
              :hide-label="true"
              @update="(p, v) => updateMapValue(entry.key, v)"
            />
          </div>
          <div v-else class="config-map-value-nested">
            <ConfigNode
              :path="`${path}.${entry.key}`"
              :schema="mapValueSchema"
              :value="entry.value"
              :full-schema="fullSchema"
              :depth="depth + 1"
              @update="(p, v) => bubble(p, v)"
            />
          </div>
        </div>
      </div>
    </template>

    <!-- Array of primitives -->
    <template v-else-if="kind === 'array'">
      <label class="config-field">
        <span v-if="!hideLabel" class="config-label">{{ label }}</span>
        <p v-if="description" class="config-hint">{{ description }}</p>

        <div class="config-array-list">
          <div
            v-for="(_, i) in arrayValue"
            :key="i"
            class="config-array-row"
          >
            <input
              class="config-input"
              :type="arrayInputType"
              :value="arrayValue[i]"
              @input="updateArrayItem(i, ($event.target as HTMLInputElement).value)"
            />
            <button
              type="button"
              class="btn-icon"
              title="Remove"
              @click="removeArrayItem(i)"
            >✕</button>
          </div>
          <button type="button" class="btn-secondary btn-mini" @click="addArrayItem">
            ＋ Add
          </button>
        </div>
      </label>
    </template>

    <!-- Boolean checkbox -->
    <template v-else-if="kind === 'boolean'">
      <label class="config-field config-field-inline">
        <input
          type="checkbox"
          :checked="!!value"
          @change="emitUpdate(path, ($event.target as HTMLInputElement).checked)"
        />
        <span class="config-label config-label-inline">{{ label }}</span>
        <span v-if="description" class="config-hint-inline">{{ description }}</span>
      </label>
    </template>

    <!-- Number -->
    <template v-else-if="kind === 'number'">
      <label class="config-field">
        <span v-if="!hideLabel" class="config-label">{{ label }}</span>
        <p v-if="description" class="config-hint">{{ description }}</p>
        <input
          class="config-input"
          type="number"
          :step="isInteger ? 1 : 'any'"
          :value="value ?? ''"
          @input="onNumberInput(($event.target as HTMLInputElement).value)"
        />
      </label>
    </template>

    <!-- Enum dropdown -->
    <template v-else-if="kind === 'enum'">
      <label class="config-field">
        <span v-if="!hideLabel" class="config-label">{{ label }}</span>
        <p v-if="description" class="config-hint">{{ description }}</p>
        <select
          class="config-input"
          :value="value ?? ''"
          @change="emitUpdate(path, normalizeEnumValue(($event.target as HTMLSelectElement).value))"
        >
          <option v-if="optional" value="">(not set)</option>
          <option v-for="opt in enumOptions" :key="String(opt)" :value="String(opt)">
            {{ opt }}
          </option>
        </select>
      </label>
    </template>

    <!-- String / fallback -->
    <template v-else>
      <label class="config-field">
        <span v-if="!hideLabel" class="config-label">{{ label }}</span>
        <p v-if="description" class="config-hint">{{ description }}</p>
        <input
          class="config-input"
          :type="isSecret ? 'password' : 'text'"
          :value="stringValue"
          autocomplete="off"
          spellcheck="false"
          :placeholder="placeholderFor()"
          @input="onStringInput(($event.target as HTMLInputElement).value)"
        />
      </label>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

type Json = string | number | boolean | null | Json[] | { [k: string]: Json }
interface SchemaNode {
  type?: string | string[]
  properties?: Record<string, SchemaNode>
  additionalProperties?: boolean | SchemaNode
  items?: SchemaNode
  anyOf?: SchemaNode[]
  oneOf?: SchemaNode[]
  enum?: Json[]
  default?: Json
  title?: string
  description?: string
  format?: string
}

const props = withDefaults(defineProps<{
  path: string
  schema: SchemaNode
  value: Json
  fullSchema: SchemaNode
  depth?: number
  hideLabel?: boolean
}>(), {
  depth: 0,
  hideLabel: false,
})

const emit = defineEmits<{
  update: [path: string, value: Json]
}>()

const SECRET_HINTS = ['key', 'secret', 'token', 'password']

const label = computed(() => {
  const title = props.schema.title
  if (title) return stripConfigSuffix(title)
  return props.path.split('.').pop() || props.path
})

function stripConfigSuffix(title: string): string {
  return title.replace(/Config$/, '') || title
}

const description = computed(() => props.schema.description ?? '')

const lastSegment = computed(() => (props.path.split('.').pop() || '').toLowerCase())

const isSecret = computed(() =>
  SECRET_HINTS.some((hint) => lastSegment.value.includes(hint))
)

/**
 * Collapse ``anyOf`` / ``oneOf`` style Pydantic optionals into the first
 * non-null branch plus a note about nullability.
 */
const effectiveSchema = computed<SchemaNode>(() => flattenSchema(props.schema))

function flattenSchema(node: SchemaNode | undefined): SchemaNode {
  if (!node) return {}
  const union = node.anyOf || node.oneOf
  if (union && union.length) {
    const nonNull = union.find((s) => s.type !== 'null')
    if (nonNull) {
      return { ...nonNull, description: node.description ?? nonNull.description }
    }
  }
  return node
}

const optional = computed(() => {
  const union = props.schema.anyOf || props.schema.oneOf
  return !!union?.some((s) => s.type === 'null')
})

const kind = computed<'object' | 'map' | 'array' | 'boolean' | 'number' | 'enum' | 'string'>(() => {
  const s = effectiveSchema.value
  if (s.enum && s.enum.length) return 'enum'
  const t = Array.isArray(s.type) ? s.type.find((x) => x !== 'null') : s.type

  if (t === 'object' || s.properties || s.additionalProperties !== undefined) {
    if (s.properties && Object.keys(s.properties).length) return 'object'
    if (s.additionalProperties && s.additionalProperties !== true) return 'map'
    // Unknown object: pick based on current value.
    if (props.value && typeof props.value === 'object' && !Array.isArray(props.value)) {
      return 'object'
    }
    return 'map'
  }
  if (t === 'array') return 'array'
  if (t === 'boolean') return 'boolean'
  if (t === 'integer' || t === 'number') return 'number'
  return 'string'
})

const isInteger = computed(() => {
  const s = effectiveSchema.value
  const t = Array.isArray(s.type) ? s.type.find((x) => x !== 'null') : s.type
  return t === 'integer'
})

const enumOptions = computed<Json[]>(() => effectiveSchema.value.enum ?? [])

function normalizeEnumValue(raw: string): Json {
  if (raw === '') return null
  const match = enumOptions.value.find((o) => String(o) === raw)
  return match ?? raw
}

// ─── object ────────────────────────────────────────────────────────────
const objectProperties = computed<[string, { path: string; schema: SchemaNode; value: Json }][]>(() => {
  const s = effectiveSchema.value
  const props_ = s.properties ?? {}
  const keys = Object.keys(props_)
  const currentVal = (props.value && typeof props.value === 'object' && !Array.isArray(props.value))
    ? props.value as Record<string, Json>
    : {}
  return keys.map((k) => [k, {
    path: `${props.path}.${k}`,
    schema: props_[k],
    value: currentVal[k] ?? null,
  }])
})

// ─── map (dict[str, X]) ────────────────────────────────────────────────
const mapValueSchema = computed<SchemaNode>(() => {
  const s = effectiveSchema.value
  if (s.additionalProperties && typeof s.additionalProperties === 'object') {
    return s.additionalProperties
  }
  return { type: 'string' }
})

const mapValueKind = computed<'primitive' | 'object'>(() => {
  const s = mapValueSchema.value
  const flat = flattenSchema(s)
  if (flat.type === 'object' || flat.properties || flat.additionalProperties) return 'object'
  return 'primitive'
})

const mapValueIsPrimitive = computed(() => mapValueKind.value === 'primitive')

const mapEntries = computed<{ key: string; value: Json }[]>(() => {
  const v = props.value
  if (!v || typeof v !== 'object' || Array.isArray(v)) return []
  return Object.entries(v as Record<string, Json>).map(([key, value]) => ({ key, value }))
})

function emitMapUpdate(next: Record<string, Json>): void {
  emit('update', props.path, next)
}

function currentMap(): Record<string, Json> {
  const v = props.value
  if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
  return { ...(v as Record<string, Json>) }
}

function addMapEntry(): void {
  const next = currentMap()
  let k = 'new_key'
  let i = 1
  while (k in next) {
    k = `new_key_${i++}`
  }
  next[k] = defaultValueForSchema(mapValueSchema.value)
  emitMapUpdate(next)
}

function removeMapEntry(key: string): void {
  const next = currentMap()
  delete next[key]
  emitMapUpdate(next)
}

function renameMapKey(oldKey: string, newKey: string): void {
  const trimmed = newKey.trim()
  if (!trimmed || trimmed === oldKey) return
  const next = currentMap()
  if (trimmed in next) return
  next[trimmed] = next[oldKey]
  delete next[oldKey]
  emitMapUpdate(next)
}

function updateMapValue(key: string, value: Json): void {
  const next = currentMap()
  next[key] = value
  emitMapUpdate(next)
}

// ─── array ─────────────────────────────────────────────────────────────
const arrayValue = computed<Json[]>(() => Array.isArray(props.value) ? props.value as Json[] : [])

const arrayInputType = computed(() => {
  const itemType = effectiveSchema.value.items?.type
  if (itemType === 'integer' || itemType === 'number') return 'number'
  return 'text'
})

function emitArrayUpdate(next: Json[]): void {
  emit('update', props.path, next)
}

function updateArrayItem(i: number, raw: string): void {
  const itemType = effectiveSchema.value.items?.type
  const next = arrayValue.value.slice()
  if (itemType === 'integer') next[i] = parseInt(raw || '0', 10) || 0
  else if (itemType === 'number') next[i] = parseFloat(raw || '0') || 0
  else next[i] = raw
  emitArrayUpdate(next)
}

function addArrayItem(): void {
  const next = arrayValue.value.slice()
  next.push(defaultValueForSchema(effectiveSchema.value.items ?? { type: 'string' }))
  emitArrayUpdate(next)
}

function removeArrayItem(i: number): void {
  const next = arrayValue.value.slice()
  next.splice(i, 1)
  emitArrayUpdate(next)
}

// ─── primitives ────────────────────────────────────────────────────────
const stringValue = computed(() => {
  const v = props.value
  if (v == null) return ''
  if (typeof v === 'string') return v
  return String(v)
})

function onStringInput(raw: string): void {
  if (raw === '' && optional.value) {
    emit('update', props.path, null)
    return
  }
  emit('update', props.path, raw)
}

function onNumberInput(raw: string): void {
  if (raw === '') {
    emit('update', props.path, optional.value ? null : 0)
    return
  }
  const n = isInteger.value ? parseInt(raw, 10) : parseFloat(raw)
  emit('update', props.path, Number.isFinite(n) ? n : 0)
}

function emitUpdate(path: string, value: Json): void {
  emit('update', path, value)
}

function bubble(path: string, value: Json): void {
  emit('update', path, value)
}

function placeholderFor(): string {
  const s = effectiveSchema.value
  const d = s.default
  if (d == null) return ''
  if (typeof d === 'string') return d
  return String(d)
}

function defaultValueForSchema(s: SchemaNode): Json {
  const flat = flattenSchema(s)
  const t = Array.isArray(flat.type) ? flat.type.find((x) => x !== 'null') : flat.type
  if (flat.default !== undefined) return flat.default as Json
  if (t === 'object' || flat.properties) {
    const obj: Record<string, Json> = {}
    for (const [k, v] of Object.entries(flat.properties ?? {})) {
      obj[k] = defaultValueForSchema(v)
    }
    return obj
  }
  if (t === 'array') return []
  if (t === 'boolean') return false
  if (t === 'integer' || t === 'number') return 0
  return ''
}
</script>
