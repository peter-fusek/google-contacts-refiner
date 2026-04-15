<script setup lang="ts">
import type { LIContact } from '~/server/utils/types'

defineProps<{
  contact: LIContact
}>()

const emit = defineEmits<{
  select: [contact: LIContact]
}>()

function statusColor(status: string): string {
  switch (status) {
    case 'CONNECTED': return 'text-green-400 bg-green-500/15'
    case 'DM_SENT': return 'text-cyan-400 bg-cyan-500/15'
    case 'RESPONDED': return 'text-primary-400 bg-primary-500/15'
    case 'PENDING': return 'text-amber-400 bg-amber-500/15'
    case 'REQUEST_SENT': return 'text-blue-400 bg-blue-500/15'
    case 'CREATOR_MODE': return 'text-red-400 bg-red-500/15'
    case 'DM_SKIPPED': return 'text-neutral-500 bg-neutral-800'
    default: return 'text-neutral-500 bg-neutral-800'
  }
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}
</script>

<template>
  <div
    class="bg-neutral-900 border border-neutral-800 rounded-lg p-3 hover:border-neutral-700 transition-colors cursor-pointer group"
    @click="emit('select', contact)"
  >
    <!-- Name + tier -->
    <div class="flex items-start justify-between gap-2 mb-1.5">
      <div class="min-w-0 flex-1">
        <p class="text-sm font-medium text-neutral-200 truncate">{{ contact.name }}</p>
        <p v-if="contact.role" class="text-[11px] text-neutral-500 truncate">{{ contact.role }}</p>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        <a
          v-if="contact.linkedinUrl"
          :href="contact.linkedinUrl"
          target="_blank"
          class="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded text-neutral-600 hover:text-blue-400"
          title="Open LinkedIn profile"
          @click.stop
        >
          <UIcon name="i-lucide-external-link" class="size-3.5" />
        </a>
        <span class="text-[10px] font-mono font-bold tabular-nums text-neutral-500">
          {{ contact.tier }}
        </span>
      </div>
    </div>

    <!-- Status badge -->
    <div class="mb-1.5">
      <span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium" :class="statusColor(contact.status)">
        {{ statusLabel(contact.status) }}
      </span>
    </div>

    <!-- Meta line -->
    <div class="flex items-center gap-3 text-[10px] text-neutral-600">
      <span v-if="contact.source" class="truncate">{{ contact.source }}</span>
      <span v-if="contact.dmSentDate" class="tabular-nums shrink-0">DM {{ contact.dmSentDate }}</span>
      <span v-if="contact.notes" class="flex items-center gap-0.5 shrink-0">
        <UIcon name="i-lucide-sticky-note" class="size-2.5" />
        note
      </span>
    </div>

    <!-- Skip reason -->
    <p
      v-if="contact.skipReason"
      class="mt-1.5 text-[10px] text-red-500/60 italic truncate"
    >
      Skipped: {{ contact.skipReason }}
    </p>
  </div>
</template>
