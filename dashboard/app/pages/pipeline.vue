<script setup lang="ts">
import type { StatusResponse } from '~/server/utils/types'
import type { PipelineRun } from '~/server/utils/gcs'

useHead({
  title: 'Pipeline — Contact Refiner',
  meta: [
    { name: 'description', content: 'Pipeline phases, live stats, run history, rules, AI prompts, and scoring.' },
  ],
})

const { data: status } = useFetch<StatusResponse>('/api/status')
const { data: runs, status: runsStatus } = useFetch<PipelineRun[]>('/api/pipeline-runs')

const { relativeLabel } = useNextRun(computed(() => status.value?.status))

const latestRun = computed(() => runs.value?.length ? runs.value[0] : null)

const expandedPhase = ref<number | null>(null)
const expandedRule = ref<string | null>(null)

function toggle(phase: number) {
  expandedPhase.value = expandedPhase.value === phase ? null : phase
}
function toggleRule(rule: string) {
  expandedRule.value = expandedRule.value === rule ? null : rule
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const min = Math.floor(seconds / 60)
  const sec = seconds % 60
  return sec > 0 ? `${min}m ${sec}s` : `${min}m`
}

function phaseLiveSummary(phaseKey: string): string | null {
  const detail = latestRun.value?.phases?.[phaseKey]
  if (!detail) return null
  const parts: string[] = [formatElapsed(detail.elapsed_s)]
  if (detail.changes_applied) parts.push(`${detail.changes_applied} applied`)
  if (detail.promoted) parts.push(`${detail.promoted} promoted`)
  if (detail.ai_cost_usd) parts.push(`$${detail.ai_cost_usd.toFixed(3)}`)
  return parts.join(' · ')
}

const phases = [
  {
    num: 0,
    key: 'phase0',
    title: 'Review Feedback',
    icon: 'i-lucide-message-square-check',
    color: 'text-blue-400',
    bg: 'bg-blue-500/10 border-blue-500/20',
    summary: 'Applies your approved/rejected decisions from the Review page, then feeds them into the learning system.',
    details: [
      'Reads exported review decisions from GCS',
      'Applies approved changes via Google People API',
      'Rejected changes are recorded to prevent re-suggestion',
      'Edited changes are applied with your modifications',
      'All decisions feed into Bayesian confidence adjustment',
      'Contacts that no longer exist (404) are gracefully skipped',
    ],
  },
  {
    num: 1,
    key: 'phase1',
    title: 'Analyze + Auto-Fix HIGH',
    icon: 'i-lucide-scan-search',
    color: 'text-green-400',
    bg: 'bg-green-500/10 border-green-500/20',
    summary: 'Runs all rule-based normalizers, then auto-applies changes with confidence >= 90%.',
    details: [
      'Creates a backup of all contacts',
      'Runs 8 normalizer modules: names, phones, emails, addresses, orgs, URLs, enrichment, deletion flagging',
      'Each normalizer returns changes with confidence scores',
      'Confidence is adjusted by memory (learned from your past decisions)',
      'Changes at >= 90% confidence are auto-applied (no AI, no review needed)',
      'Changes at 60-89% go to the Review queue',
      'Safety limit: max 200 auto-changes per run',
    ],
  },
  {
    num: 2,
    key: 'phase2',
    title: 'AI Review (MEDIUM)',
    icon: 'i-lucide-brain',
    color: 'text-purple-400',
    bg: 'bg-purple-500/10 border-purple-500/20',
    summary: 'Claude Haiku reviews medium-confidence changes, may promote or reject them.',
    details: [
      'Model: Claude Haiku ($0.80/M input, $4/M output) — 10x cheaper than Sonnet',
      'Cost cap: $3/day hard limit',
      'Sends batches of up to 10 contacts to AI',
      'AI can raise confidence (promote to auto-fix) or lower it',
      'AI can suggest additional changes not caught by rules',
      'Checkpointed: saves progress every batch, can resume after timeout',
      'AI learnings are merged into memory for future runs',
    ],
  },
  {
    num: 3,
    key: 'phase3',
    title: 'Activity Tagging',
    icon: 'i-lucide-calendar-clock',
    color: 'text-amber-400',
    bg: 'bg-amber-500/10 border-amber-500/20',
    summary: 'Scans Gmail + Calendar to tag contacts with interaction year labels and identify LTNS contacts.',
    details: [
      'Scans Gmail for email threads with each contact',
      'Scans Google Calendar for shared meetings',
      'Assigns year labels: Y2024, Y2025, Y2026, etc.',
      'Identifies "Long Time No See" (LTNS) contacts: people with past interaction but long gap',
      'Top 50 LTNS contacts are flagged for reconnection',
    ],
  },
  {
    num: 4,
    key: 'phase4',
    title: 'FollowUp Scoring',
    icon: 'i-lucide-user-round-check',
    color: 'text-cyan-400',
    bg: 'bg-cyan-500/10 border-cyan-500/20',
    summary: 'Combines interaction history + LinkedIn signals + contact completeness into a reconnection score.',
    details: [
      'Score = interaction_score + linkedin_score + completeness_score',
      'Interaction: interaction_count x months_gap (more interactions + longer gap = higher priority)',
      'LinkedIn weights: job_change=30, active=10, profile=3, no_activity=0',
      'Completeness: 2 points per signal (email, phone, org, LinkedIn URL — max 8)',
      'job_change bypasses minimum filters (instant priority)',
      'Top 50 contacts ranked and written to dashboard',
    ],
  },
]

const normalizers = [
  {
    id: 'diacritics',
    title: 'Diacritics Correction',
    icon: 'i-lucide-case-sensitive',
    desc: 'Adds missing Slovak/Czech diacritics to names.',
    details: 'Uses a dictionary of 2,000+ Slovak/Czech names. Dictionary match = 100% confidence. Case variant = 95%. Suffix pattern match = 65%. Example: Stefan -> Stefan, Lubica -> Lubica.',
  },
  {
    id: 'phones',
    title: 'Phone Normalization',
    icon: 'i-lucide-phone',
    desc: 'Formats phones to international standard, detects duplicates and types.',
    details: 'Uses phonenumbers library. Default region: SK. Enforces +421 XXX XXX XXX format. Classifies mobile vs landline. Detects duplicate numbers (same digits, different format).',
  },
  {
    id: 'emails',
    title: 'Email Normalization',
    icon: 'i-lucide-mail',
    desc: 'Lowercases, validates, detects duplicates and invalid addresses.',
    details: 'Uses email_validator library. Lowercases all addresses. Flags invalid domains. Detects duplicate emails. Distinguishes free (gmail, azet.sk) vs corporate domains.',
  },
  {
    id: 'names',
    title: 'Name Parsing',
    icon: 'i-lucide-user',
    desc: 'Splits full names, extracts titles, removes companies from name fields.',
    details: 'Parses X.500 DN format (CN=Name/O=Company). Extracts 26 title types (Ing., Mgr., Dr., Prof., etc.). Splits givenName/familyName. Detects company names accidentally stored in name fields.',
  },
  {
    id: 'orgs',
    title: 'Organization Normalization',
    icon: 'i-lucide-building-2',
    desc: 'Title-cases org names, deduplicates, infers from email domains.',
    details: 'Slovak-aware title casing. Detects legal suffixes (s.r.o., a.s., spol., GmbH, Ltd). Maps email domains to company names. Removes duplicate org entries.',
  },
  {
    id: 'tobedeleted',
    title: 'Deletion Candidate Flagging',
    icon: 'i-lucide-trash-2',
    desc: 'Flags low-value contacts with minimal information for review.',
    details: 'Scores contacts on 5 signals: full name (1pt), email (0.5-1pt), phone (1pt), address (1pt), birthday (0.5pt), org+title (0.5pt), notes (0.5pt). Contacts with 0-1 signals are flagged. Confidence: 0.55 (0.5 signals), 0.45 (0 signals). Always goes to Review — never auto-deleted.',
  },
  {
    id: 'enricher',
    title: 'Enrichment from Notes',
    icon: 'i-lucide-sparkles',
    desc: 'Extracts structured data (phones, emails, dates) from unstructured notes.',
    details: 'Regex-based extraction from biographies/notes. Finds phone numbers (confidence 0.70), emails (0.70), birthdays/anniversaries (0.55-0.60), company registration numbers IČO/DIČ (0.92).',
  },
]

const aiPrompt = `You are a Google Contacts cleanup assistant.
You analyze contacts and suggest corrections.
You specialize in Slovak and Czech names, diacritics,
phone numbers, and duplicate detection.

RULES:
- ALWAYS respond in JSON format
- Slovak/Czech names always with diacritics
- When uncertain, prefer keeping the original
- Confidence: 0.95+ certain, 0.70-0.90 probable,
  below 0.60 speculative`

const memoryExplainer = [
  { label: 'Learning source', value: 'Your approve/reject/edit decisions in Review' },
  { label: 'Method', value: 'Bayesian smoothing — blends base confidence with your approval rate' },
  { label: 'Formula', value: 'adjusted = (base × 10 + approval_rate × decisions) / (10 + decisions)' },
  { label: 'Min decisions', value: '5 before adjustment kicks in' },
  { label: 'Confidence range', value: 'Clamped to 0.30 — 0.98 (never fully certain or fully rejected)' },
  { label: 'Categories', value: '26 rule types tracked independently (diacritics, phones, tobedeleted, etc.)' },
]
</script>

<template>
  <div class="space-y-8">
    <!-- Header with live status -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-bold text-neutral-100">
          Pipeline
        </h1>
        <p class="text-sm text-neutral-400 mt-1">
          Phases, live stats, run history, and reference documentation.
        </p>
      </div>
      <div class="flex items-center gap-3">
        <div class="text-right text-xs">
          <div class="text-neutral-500">Next run</div>
          <div class="text-neutral-300 font-medium">{{ relativeLabel }}</div>
        </div>
        <StatusBadge :status="status?.status ?? 'idle'" />
      </div>
    </div>

    <!-- Pipeline Phases (with live stats) -->
    <section>
      <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
        <UIcon name="i-lucide-workflow" class="size-5 text-primary-400" />
        Pipeline Phases
      </h2>
      <p class="text-sm text-neutral-500 mb-4">
        Runs daily at 09:00 Europe/Bratislava. Each phase runs in sequence.
      </p>

      <div class="space-y-3">
        <div
          v-for="phase in phases"
          :key="phase.num"
          class="border rounded-xl overflow-hidden transition-colors"
          :class="expandedPhase === phase.num ? phase.bg : 'border-neutral-800 bg-neutral-900/50'"
        >
          <button
            class="w-full flex items-center gap-3 px-4 py-3 text-left"
            @click="toggle(phase.num)"
          >
            <span class="flex items-center justify-center size-7 rounded-lg text-xs font-bold bg-neutral-800 text-neutral-300">
              {{ phase.num }}
            </span>
            <UIcon :name="phase.icon" class="size-5" :class="phase.color" />
            <span class="font-medium text-neutral-200 flex-1">{{ phase.title }}</span>
            <!-- Live stats badge -->
            <span
              v-if="phaseLiveSummary(phase.key)"
              class="text-[10px] text-neutral-500 font-mono tabular-nums mr-2 hidden sm:inline"
            >
              {{ phaseLiveSummary(phase.key) }}
            </span>
            <UIcon
              :name="expandedPhase === phase.num ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
              class="size-4 text-neutral-500"
            />
          </button>

          <div v-if="expandedPhase === phase.num" class="px-4 pb-4">
            <!-- Live phase detail (if available) -->
            <div
              v-if="latestRun?.phases?.[phase.key]"
              class="mb-3 p-3 rounded-lg bg-neutral-800/50 border border-neutral-700/30"
            >
              <p class="text-[10px] uppercase tracking-wider text-neutral-500 mb-2">Last run stats</p>
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                <div>
                  <span class="text-neutral-500">Duration:</span>
                  <span class="text-neutral-300 ml-1 font-mono">{{ formatElapsed(latestRun.phases[phase.key].elapsed_s) }}</span>
                </div>
                <div v-if="latestRun.phases[phase.key].changes_applied !== undefined">
                  <span class="text-neutral-500">Applied:</span>
                  <span class="text-green-400 ml-1 font-mono">{{ latestRun.phases[phase.key].changes_applied }}</span>
                </div>
                <div v-if="latestRun.phases[phase.key].promoted">
                  <span class="text-neutral-500">Promoted:</span>
                  <span class="text-primary-400 ml-1 font-mono">{{ latestRun.phases[phase.key].promoted }}</span>
                </div>
                <div v-if="latestRun.phases[phase.key].demoted">
                  <span class="text-neutral-500">Demoted:</span>
                  <span class="text-amber-400 ml-1 font-mono">{{ latestRun.phases[phase.key].demoted }}</span>
                </div>
                <div v-if="latestRun.phases[phase.key].ai_cost_usd">
                  <span class="text-neutral-500">Cost:</span>
                  <span class="text-amber-400 ml-1 font-mono">${{ latestRun.phases[phase.key].ai_cost_usd!.toFixed(3) }}</span>
                </div>
              </div>
            </div>

            <p class="text-sm text-neutral-300 mb-3">
              {{ phase.summary }}
            </p>
            <ul class="space-y-1.5">
              <li
                v-for="(detail, i) in phase.details"
                :key="i"
                class="text-sm text-neutral-400 flex items-start gap-2"
              >
                <span class="text-neutral-600 mt-0.5">-</span>
                {{ detail }}
              </li>
            </ul>
          </div>
        </div>
      </div>
    </section>

    <!-- Run History -->
    <section id="runs">
      <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
        <UIcon name="i-lucide-play-circle" class="size-5 text-primary-400" />
        Run History
      </h2>
      <RunHistoryTable :runs="runs ?? []" :loading="runsStatus === 'pending'" />
    </section>

    <!-- Reference Documentation -->
    <div class="border-t border-neutral-800 pt-8">
      <p class="text-xs uppercase tracking-wider text-neutral-600 mb-6">Reference Documentation</p>

      <!-- Normalizer Rules -->
      <section class="mb-8">
        <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
          <UIcon name="i-lucide-list-checks" class="size-5 text-green-400" />
          Normalizer Rules (Phase 1)
        </h2>
        <p class="text-sm text-neutral-500 mb-4">
          Rule-based modules that detect and fix contact data issues. No AI involved.
        </p>

        <div class="grid gap-3 md:grid-cols-2">
          <div
            v-for="rule in normalizers"
            :key="rule.id"
            class="border border-neutral-800 rounded-xl bg-neutral-900/50 overflow-hidden"
          >
            <button
              class="w-full flex items-center gap-3 px-4 py-3 text-left"
              @click="toggleRule(rule.id)"
            >
              <UIcon :name="rule.icon" class="size-4 text-green-400" />
              <div class="flex-1 min-w-0">
                <p class="text-sm font-medium text-neutral-200">{{ rule.title }}</p>
                <p class="text-xs text-neutral-500 truncate">{{ rule.desc }}</p>
              </div>
              <UIcon
                :name="expandedRule === rule.id ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
                class="size-4 text-neutral-600"
              />
            </button>
            <div v-if="expandedRule === rule.id" class="px-4 pb-4">
              <p class="text-sm text-neutral-400 leading-relaxed">
                {{ rule.details }}
              </p>
            </div>
          </div>
        </div>
      </section>

      <!-- AI Prompt -->
      <section class="mb-8">
        <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
          <UIcon name="i-lucide-brain" class="size-5 text-purple-400" />
          AI System Prompt (Phase 2)
        </h2>
        <p class="text-sm text-neutral-500 mb-4">
          This is the system instruction sent to Claude Haiku for reviewing medium-confidence changes.
        </p>
        <div class="bg-neutral-900 border border-neutral-800 rounded-xl p-4 font-mono text-sm text-neutral-300 whitespace-pre-wrap leading-relaxed">{{ aiPrompt }}</div>
        <p class="text-xs text-neutral-600 mt-2">
          Model: claude-haiku-4-5-20251001 | Cost cap: $3/day | Also loads instructions.md (editable rules) and memory.json (learned patterns)
        </p>
      </section>

      <!-- Learning System -->
      <section class="mb-8">
        <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
          <UIcon name="i-lucide-graduation-cap" class="size-5 text-amber-400" />
          Learning System (Memory)
        </h2>
        <p class="text-sm text-neutral-500 mb-4">
          The system learns from your Review decisions to improve future confidence scores.
        </p>

        <div class="bg-neutral-900 border border-neutral-800 rounded-xl divide-y divide-neutral-800">
          <div
            v-for="item in memoryExplainer"
            :key="item.label"
            class="flex items-start gap-4 px-4 py-3"
          >
            <span class="text-sm text-neutral-500 w-32 shrink-0">{{ item.label }}</span>
            <span class="text-sm text-neutral-300 font-mono">{{ item.value }}</span>
          </div>
        </div>
      </section>

      <!-- FollowUp Scoring -->
      <section class="mb-8">
        <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
          <UIcon name="i-lucide-calculator" class="size-5 text-cyan-400" />
          FollowUp Scoring Formula (Phase 4)
        </h2>
        <p class="text-sm text-neutral-500 mb-4">
          How contacts are ranked for reconnection. Three additive components.
        </p>

        <div class="grid gap-3 md:grid-cols-3">
          <div class="bg-neutral-900 border border-neutral-800 rounded-xl p-4">
            <p class="text-sm font-semibold text-neutral-200 mb-2">Interaction Score</p>
            <p class="text-xs text-neutral-400 mb-2">How much you've interacted and how long ago.</p>
            <div class="font-mono text-sm text-amber-400">
              interaction_count x months_gap
            </div>
            <p class="text-xs text-neutral-500 mt-2">
              count: 0 (none), 1 (email OR meeting), 2 (both)
            </p>
          </div>

          <div class="bg-neutral-900 border border-neutral-800 rounded-xl p-4">
            <p class="text-sm font-semibold text-neutral-200 mb-2">LinkedIn Score</p>
            <p class="text-xs text-neutral-400 mb-2">Signal from their LinkedIn profile.</p>
            <div class="space-y-1 text-sm font-mono">
              <div class="flex justify-between">
                <span class="text-green-400">job_change</span><span class="text-neutral-400">30</span>
              </div>
              <div class="flex justify-between">
                <span class="text-yellow-400">active</span><span class="text-neutral-400">10</span>
              </div>
              <div class="flex justify-between">
                <span class="text-neutral-400">profile</span><span class="text-neutral-400">3</span>
              </div>
              <div class="flex justify-between">
                <span class="text-neutral-600">no_activity</span><span class="text-neutral-400">0</span>
              </div>
            </div>
          </div>

          <div class="bg-neutral-900 border border-neutral-800 rounded-xl p-4">
            <p class="text-sm font-semibold text-neutral-200 mb-2">Completeness Score</p>
            <p class="text-xs text-neutral-400 mb-2">How much info you have on the contact.</p>
            <div class="font-mono text-sm text-cyan-400">
              signals x 2.0
            </div>
            <p class="text-xs text-neutral-500 mt-2">
              Signals (0-4): email, phone, org, LinkedIn URL. Max: 8 points.
            </p>
          </div>
        </div>
      </section>

      <!-- Confidence + Safety -->
      <section class="mb-8">
        <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
          <UIcon name="i-lucide-gauge" class="size-5 text-rose-400" />
          Confidence Thresholds
        </h2>

        <div class="flex gap-3">
          <div class="flex-1 bg-green-500/10 border border-green-500/20 rounded-xl p-4 text-center">
            <p class="text-2xl font-bold text-green-400">90%+</p>
            <p class="text-sm text-green-400/70 mt-1">HIGH</p>
            <p class="text-xs text-neutral-500 mt-1">Auto-applied</p>
          </div>
          <div class="flex-1 bg-amber-500/10 border border-amber-500/20 rounded-xl p-4 text-center">
            <p class="text-2xl font-bold text-amber-400">60-89%</p>
            <p class="text-sm text-amber-400/70 mt-1">MEDIUM</p>
            <p class="text-xs text-neutral-500 mt-1">Goes to Review</p>
          </div>
          <div class="flex-1 bg-neutral-500/10 border border-neutral-500/20 rounded-xl p-4 text-center">
            <p class="text-2xl font-bold text-neutral-400">&lt;60%</p>
            <p class="text-sm text-neutral-400/70 mt-1">LOW</p>
            <p class="text-xs text-neutral-500 mt-1">Dropped silently</p>
          </div>
        </div>
      </section>

      <section class="pb-8">
        <h2 class="text-lg font-semibold text-neutral-200 mb-3 flex items-center gap-2">
          <UIcon name="i-lucide-shield-alert" class="size-5 text-red-400" />
          Safety Controls
        </h2>
        <div class="bg-neutral-900 border border-neutral-800 rounded-xl p-4 space-y-2 text-sm text-neutral-400">
          <p><span class="text-red-400 font-medium">Emergency Stop</span> — Config page pause button halts the pipeline immediately.</p>
          <p><span class="text-amber-400 font-medium">Cost Cap</span> — AI spending capped at $3/day. Pipeline continues without AI if limit reached.</p>
          <p><span class="text-green-400 font-medium">Change Limit</span> — Max 200 auto-applied changes per run.</p>
          <p><span class="text-blue-400 font-medium">Backup</span> — Full contact backup created before every Phase 1 run.</p>
          <p><span class="text-purple-400 font-medium">Nothing is permanent</span> — Deletion candidates are only flagged for review, never auto-deleted.</p>
        </div>
      </section>
    </div>
  </div>
</template>
