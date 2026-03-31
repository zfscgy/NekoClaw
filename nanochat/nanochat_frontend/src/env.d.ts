/// <reference types="vite/client" />

declare module 'marked-katex-extension' {
  import type { MarkedExtension } from 'marked'

  interface KatexOptions {
    throwOnError?: boolean
    nonStandard?: boolean
  }

  export default function markedKatex(options?: KatexOptions): MarkedExtension
}
