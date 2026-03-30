<script setup lang="ts">
import type { PhaseDetail, PipelineRun } from '~/server/utils/gcs'

defineProps<{
  runs: PipelineRun[]
  loading?: boolean
}>()

const expandedRows = ref<Set<number>>(new Set())
function toggleRow(i: number) {
  const s = new Set(expandedRows.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expandedRows.value = s
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const min = Math.floor(seconds / 60)
  const sec = seconds % 60
  return sec > 0 ? `${min}m ${sec}s` : `${min}m`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { month: 'short', day: 'numeric' })
    + ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

const phaseLabels: Record<string, string> = {
  phase0: 'Review Feedback',
  phase1: 'Analyze + Auto-fix',
  phase2: 'AI Review',
  phase3: 'Activity Tagging',
  phase4: 'FollowUp Scoring',
  phase5: 'CRM Sync',
}

function phaseStatParts(detail: PhaseDetail): Array<{ text: string; link?: string }> {
  const parts: Array<{ text: string; link?: string }> = []
  if (detail.changes_applied !== undefined) {
    const link = detail.session_id ? `/changelog?sessionId=${detail.session_id}` : undefined
    parts.push({ text: `${detail.changes_applied} applied`, link })
  }
  if (detail.changes_failed) parts.push({ text: `${detail.changes_failed} failed` })
  if (detail.changes_skipped) parts.push({ text: `${detail.changes_skipped} skipped` })
  if (detail.promoted) parts.push({ text: `${detail.promoted} promoted` })
  if (detail.demoted) parts.push({ text: `${detail.demoted} demoted` })
  if (detail.ai_cost_usd) parts.push({ text: `$${detail.ai_cost_usd.toFixed(3)}` })
  if (detail.fix_changes_applied) {
    const link = detail.session_id ? `/changelog?sessionId=${detail.session_id}` : undefined
    parts.push({ text: `${detail.fix_changes_applied} fixes applied`, link })
  }
  if ((detail as Record<string, unknown>).notes_synced) parts.push({ text: `${(detail as Record<string, unknown>).notes_synced} notes synced` })
  if ((detail as Record<string, unknown>).tags_memberships) parts.push({ text: `${(detail as Record<string, unknown>).tags_memberships} tag memberships` })
  return parts
}
</script>

<template>
  <div v-if="loading" class="text-center py-8">
    <UIcon name="i-lucide-loader" class="size-6 text-neutral-500 mx-auto mb-2 animate-spin" />
    <p class="text-neutral-500 text-sm">Loading runs...</p>
  </div>

  <div v-else-if="runs.length" class="rounded-xl border border-neutral-800 bg-neutral-900/50 overflow-hidden">
    <table class="w-full text-xs">
      <thead>
        <tr class="border-b border-neutral-800 text-neutral-500 uppercase tracking-wider">
          <th class="text-left px-4 py-3 w-6"></th>
          <th class="text-left px-4 py-3">Date</th>
          <th class="text-left px-4 py-3">Duration</th>
          <th class="text-left px-4 py-3">Phases</th>
          <th class="text-right px-4 py-3">Queue</th>
          <th class="text-left px-4 py-3">Status</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="(run, i) in runs" :key="i">
          <tr
            class="border-b border-neutral-800/50 transition-colors"
            :class="[run.phases ? 'cursor-pointer' : '', 'hover:bg-neutral-800/30']"
            @click="run.phases && toggleRow(i)"
          >
            <td class="px-4 py-2.5 text-neutral-600">
              <UIcon
                v-if="run.phases"
                :name="expandedRows.has(i) ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
                class="size-3.5"
              />
            </td>
            <td class="px-4 py-2.5 text-neutral-300 tabular-nums font-mono">
              {{ formatDate(run.date) }}
            </td>
            <td class="px-4 py-2.5 text-neutral-400 tabular-nums">
              {{ formatDuration(run.duration_seconds) }}
            </td>
            <td class="px-4 py-2.5">
              <div class="flex gap-1">
                <span
                  v-for="phase in run.phases_completed"
                  :key="phase"
                  class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary-500/15 text-primary-400"
                >
                  {{ phase }}
                </span>
              </div>
            </td>
            <td class="px-4 py-2.5 text-right text-neutral-400 tabular-nums">
              {{ run.queue_size }}
            </td>
            <td class="px-4 py-2.5">
              <span
                v-if="run.errors.length === 0"
                class="inline-flex items-center gap-1 text-green-400"
              >
                <UIcon name="i-lucide-check-circle" class="size-3" />
                OK
              </span>
              <span
                v-else
                class="inline-flex items-center gap-1 text-red-400 cursor-help"
                :title="run.errors.join('\n')"
              >
                <UIcon name="i-lucide-alert-circle" class="size-3" />
                {{ run.errors.length }} error{{ run.errors.length > 1 ? 's' : '' }}
              </span>
            </td>
          </tr>

          <!-- Expanded phase details -->
          <tr v-if="expandedRows.has(i) && run.phases">
            <td colspan="6" class="px-4 pb-3 pt-0 bg-neutral-900/30">
              <div class="ml-6 border-l border-neutral-700/50 pl-4 py-2 space-y-2">
                <div
                  v-for="phase in run.phases_completed"
                  :key="phase"
                  class="flex items-baseline gap-4 text-[11px]"
                >
                  <span class="text-neutral-400 w-32 shrink-0">
                    {{ phaseLabels[phase] || phase }}
                  </span>
                  <span class="text-neutral-500 tabular-nums w-16 shrink-0">
                    {{ run.phases[phase] ? formatDuration(run.phases[phase].elapsed_s) : '—' }}
                  </span>
                  <span class="text-neutral-600">
                    <template v-if="run.phases[phase]">
                      <template v-for="(part, pi) in phaseStatParts(run.phases[phase])" :key="pi">
                        <span v-if="pi > 0"> · </span>
                        <NuxtLink v-if="part.link" :to="part.link" class="text-primary-400 hover:text-primary-300 underline underline-offset-2" @click.stop>{{ part.text }}</NuxtLink>
                        <span v-else>{{ part.text }}</span>
                      </template>
                    </template>
                    <template v-else>No details</template>
                  </span>
                </div>
                <div v-if="run.changes_applied !== undefined" class="flex gap-4 pt-1 border-t border-neutral-800/50 text-[11px]">
                  <span class="text-neutral-500">Total: {{ run.changes_applied }} applied</span>
                  <span v-if="run.changes_failed" class="text-red-400/60">{{ run.changes_failed }} failed</span>
                </div>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>

  <p v-else class="text-sm text-neutral-600 text-center py-8">
    No pipeline runs recorded yet.
  </p>
</template>
