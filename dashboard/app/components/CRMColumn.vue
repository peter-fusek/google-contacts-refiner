<script setup lang="ts">
import type { CRMContact, CRMStage } from '~/server/utils/types'

defineProps<{
  stage: CRMStage
  label: string
  contacts: CRMContact[]
  color: string
}>()

const emit = defineEmits<{
  drop: [resourceName: string, stage: CRMStage]
  select: [contact: CRMContact]
  'reach-out': [resourceName: string]
}>()

const dragOver = ref(false)
// Counter-based drag tracking — relatedTarget is unreliable on drag events in Chrome
// Increment on dragenter, decrement on dragleave, highlight when > 0
let dragCounter = 0

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  dragCounter++
  dragOver.value = true
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
}

function onDragLeave() {
  dragCounter--
  if (dragCounter <= 0) {
    dragCounter = 0
    dragOver.value = false
  }
}

function onDrop(e: DragEvent, stage: CRMStage) {
  e.preventDefault()
  dragCounter = 0
  dragOver.value = false
  const resourceName = e.dataTransfer?.getData('text/plain')
  if (resourceName) emit('drop', resourceName, stage)
}
</script>

<template>
  <div
    class="flex flex-col min-w-[80vw] sm:min-w-[260px] max-w-[300px] shrink-0 rounded-xl border transition-colors snap-center"
    :class="dragOver ? 'border-primary-500/50 bg-primary-500/5' : 'border-neutral-800 bg-neutral-900/30'"
    @dragenter="onDragEnter"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop($event, stage)"
  >
    <!-- Header -->
    <div class="flex items-center justify-between px-3 py-2.5 border-b border-neutral-800/50">
      <div class="flex items-center gap-2">
        <span class="size-2 rounded-full" :class="color" />
        <span class="text-xs font-semibold text-neutral-300 uppercase tracking-wider">{{ label }}</span>
      </div>
      <span class="text-[10px] text-neutral-600 font-mono tabular-nums">{{ contacts.length }}</span>
    </div>

    <!-- Cards -->
    <div class="flex-1 p-2 space-y-2 overflow-y-auto max-h-[calc(100vh-260px)]">
      <CRMCard
        v-for="c in contacts"
        :key="c.resourceName"
        :contact="c"
        @select="emit('select', $event)"
        @reach-out="emit('reach-out', $event)"
      />
      <p v-if="!contacts.length" class="text-center text-[10px] text-neutral-700 py-4">
        Drop contacts here
      </p>
    </div>
  </div>
</template>
