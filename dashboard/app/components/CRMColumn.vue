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
const columnRef = ref<HTMLElement>()

function onDragOver(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  dragOver.value = true
}

function onDragLeave(e: DragEvent) {
  // Only clear when truly leaving the column — dragleave fires on every child element
  if (columnRef.value && !columnRef.value.contains(e.relatedTarget as Node)) {
    dragOver.value = false
  }
}

function onDrop(e: DragEvent, stage: CRMStage) {
  e.preventDefault()
  dragOver.value = false
  const resourceName = e.dataTransfer?.getData('text/plain')
  if (resourceName) emit('drop', resourceName, stage)
}
</script>

<template>
  <div
    ref="columnRef"
    class="flex flex-col min-w-[80vw] sm:min-w-[260px] max-w-[300px] shrink-0 rounded-xl border transition-colors snap-center"
    :class="dragOver ? 'border-primary-500/50 bg-primary-500/5' : 'border-neutral-800 bg-neutral-900/30'"
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
