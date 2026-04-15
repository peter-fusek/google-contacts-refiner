<script setup lang="ts">
import type { LIContact } from '~/server/utils/types'

defineProps<{
  tier: string
  label: string
  contacts: LIContact[]
  color: string
}>()

const emit = defineEmits<{
  select: [contact: LIContact]
}>()
</script>

<template>
  <div class="flex flex-col min-w-[260px] max-w-[320px]">
    <!-- Header -->
    <div class="flex items-center justify-between px-3 py-2 mb-2 rounded-lg" :class="color">
      <span class="text-xs font-semibold uppercase tracking-wider">{{ label }}</span>
      <span class="text-xs font-mono tabular-nums opacity-70">{{ contacts.length }}</span>
    </div>

    <!-- Cards -->
    <div class="space-y-2 overflow-y-auto max-h-[calc(100vh-300px)] pr-1">
      <LIContactCard
        v-for="contact in contacts"
        :key="contact.id"
        :contact="contact"
        @select="emit('select', $event)"
      />
      <div v-if="!contacts.length" class="text-xs text-neutral-600 text-center py-6">
        No contacts
      </div>
    </div>
  </div>
</template>
