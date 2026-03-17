<script setup lang="ts">
defineProps<{
  oldValue: string
  newValue: string
}>()
</script>

<template>
  <span class="inline-flex items-center gap-1.5 text-xs">
    <span class="bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded line-through">{{ oldValue || '(empty)' }}</span>
    <UIcon name="i-lucide-arrow-right" class="size-3 text-neutral-600 shrink-0" />
    <span v-if="newValue === '__TO_BE_DELETED__'" class="bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded font-medium">Flag for deletion</span>
    <span v-else class="bg-primary-500/15 text-primary-400 px-1.5 py-0.5 rounded">{{ newValue || '(empty)' }}</span>
  </span>
</template>
