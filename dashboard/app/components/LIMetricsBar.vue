<script setup lang="ts">
import type { LICRMResponse } from '~/server/utils/types'

defineProps<{
  stats: LICRMResponse['stats']
}>()
</script>

<template>
  <div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
    <StatsCard label="Contacts" :value="stats.totalContacts" icon="i-lucide-users" />
    <StatsCard label="Connected" :value="stats.connected" icon="i-lucide-user-check" color="green" />
    <StatsCard label="Pending" :value="stats.pending" icon="i-lucide-clock" color="amber" />
    <StatsCard label="Creator Mode" :value="stats.creatorMode" icon="i-lucide-shield-alert" color="red" />
    <StatsCard label="DMs Sent" :value="stats.dmsSent" icon="i-lucide-send" color="cyan" />
    <StatsCard label="Responded" :value="stats.responded" icon="i-lucide-message-circle" color="green" />
    <StatsCard label="Followers" :value="stats.followers.toLocaleString()" icon="i-lucide-trending-up" />
    <StatsCard label="Accept Rate" :value="stats.acceptanceRate" icon="i-lucide-percent" color="green" />
  </div>
</template>
