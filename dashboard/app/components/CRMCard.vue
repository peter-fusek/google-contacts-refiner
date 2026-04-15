<script setup lang="ts">
import type { CRMContact } from '~/server/utils/types'

const props = defineProps<{
  contact: CRMContact
}>()

const emit = defineEmits<{
  select: [contact: CRMContact]
  'reach-out': [resourceName: string]
}>()

function signalColor(type: string | undefined): string {
  if (type === 'job_change') return 'text-green-400 bg-green-500/15'
  if (type === 'active') return 'text-yellow-400 bg-yellow-500/15'
  return 'text-neutral-500 bg-neutral-800'
}

function dragStart(e: DragEvent) {
  if (!e.dataTransfer) return
  e.dataTransfer.setData('text/plain', props.contact.resourceName)
  e.dataTransfer.effectAllowed = 'move'
}
</script>

<template>
  <div
    draggable="true"
    class="bg-neutral-900 border border-neutral-800 rounded-lg p-3 cursor-grab active:cursor-grabbing hover:border-neutral-700 transition-colors group"
    @dragstart="dragStart"
    @click="emit('select', contact)"
  >
    <!-- Name + score + quick action -->
    <div class="flex items-start justify-between gap-2 mb-1.5">
      <div class="min-w-0 flex-1">
        <p class="text-sm font-medium text-neutral-200 truncate">{{ contact.name }}</p>
        <p v-if="contact.contact.org" class="text-[11px] text-neutral-500 truncate">{{ contact.contact.org }}</p>
      </div>
      <div class="flex items-center gap-1 shrink-0">
        <button
          v-if="contact.stage === 'inbox'"
          class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded text-neutral-600 hover:text-amber-400 hover:bg-amber-500/10"
          title="Mark as Reached Out"
          @click.stop="emit('reach-out', contact.resourceName)"
        >
          <UIcon name="i-lucide-send" class="size-3.5" />
        </button>
        <span class="text-xs font-mono font-bold tabular-nums" :class="contact.score_total >= 100 ? 'text-cyan-400' : contact.linkedin?.signal_type === 'job_change' ? 'text-green-400' : 'text-neutral-500'">
          {{ contact.score_total }}
        </span>
      </div>
    </div>

    <!-- LinkedIn signal badge + signal text -->
    <div v-if="contact.linkedin" class="mb-1.5">
      <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium" :class="signalColor(contact.linkedin.signal_type)">
        <UIcon name="i-lucide-linkedin" class="size-3" />
        {{ contact.linkedin.signal_type?.replace('_', ' ') }}
      </span>
      <p
        v-if="contact.linkedin.signal_text"
        class="text-[10px] text-neutral-500 truncate mt-0.5"
        :title="contact.linkedin.signal_text"
      >
        {{ contact.linkedin.signal_text }}
      </p>
    </div>

    <!-- Meta line -->
    <div class="flex items-center gap-3 text-[10px] text-neutral-600">
      <span v-if="contact.interaction.months_gap" class="tabular-nums">{{ contact.interaction.months_gap }}mo gap</span>
      <span v-if="contact.notes" class="flex items-center gap-0.5">
        <UIcon name="i-lucide-sticky-note" class="size-2.5" />
        note
      </span>
      <span v-if="contact.tags.length" class="flex items-center gap-0.5">
        <UIcon name="i-lucide-tag" class="size-2.5" />
        {{ contact.tags.length }}
      </span>
    </div>

    <!-- FollowUp prompt preview -->
    <p
      v-if="contact.followup_prompt"
      class="mt-1.5 text-[10px] text-neutral-600 italic line-clamp-2 overflow-hidden"
      :title="contact.followup_prompt"
    >
      {{ contact.followup_prompt }}
    </p>
  </div>
</template>
