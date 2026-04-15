<script setup lang="ts">
definePageMeta({ layout: false })

const steps = [
  { num: '01', title: 'Authenticate', description: 'Connect your Google account with OAuth2. Read-only access to Contacts, Gmail, and Calendar.' },
  { num: '02', title: 'Analyze', description: 'A 6-phase daily pipeline scans all contacts with 26 rule categories, AI reviews ambiguous cases, scores reconnection candidates, and syncs CRM data back to Google Contacts.' },
  { num: '03', title: 'Review & Learn', description: 'Review changes on the dashboard. Approve, reject, or edit. The system learns from every decision — rejected changes never come back.' },
]

const faqs = [
  { q: 'Is Contact Refiner free?', a: 'Yes. Contact Refiner is open source and free to self-host. Your data stays in your own Google account and infrastructure.' },
  { q: 'Does it delete or modify contacts without my permission?', a: 'No. Every change goes through a review queue. You approve or reject each change before it applies. The system learns from your decisions so rejected changes never come back.' },
  { q: 'What kind of fixes does it make?', a: 'Contact Refiner fixes missing diacritics on Slovak and Czech names, normalizes phone numbers, standardizes titles and company names, flags low-value contacts for deletion, and uses AI to catch edge cases that rules miss.' },
  { q: 'How does the LinkedIn integration work?', a: 'Contact Refiner scans LinkedIn profiles to detect job changes and activity. It scores your contacts for reconnection opportunities and surfaces the best candidates in a personal CRM board.' },
  { q: 'Does the CRM sync change my Google Contacts?', a: 'Yes — CRM notes sync to contact biographies and tags become contact groups. Changes are additive only: nothing is ever removed from your contacts. You can search CRM data natively on any device.' },
]

useHead({
  title: 'Contact Refiner — AI-Powered Google Contacts Cleanup',
  meta: [
    { name: 'description', content: 'Fix diacritics, formatting, and duplicates in Google Contacts. 26 rule categories, 6-phase daily pipeline with AI review, LinkedIn signals, personal CRM with bi-directional sync, feedback learning, and a dashboard to approve every change.' },
    { property: 'og:title', content: 'Contact Refiner — AI-Powered Google Contacts Cleanup' },
    { property: 'og:description', content: 'Automatically fix diacritics, formatting, and duplicates in your Google Contacts. AI-powered analysis with human review.' },
    { property: 'og:type', content: 'website' },
    { property: 'og:url', content: 'https://contactrefiner.com' },
    { property: 'og:image', content: 'https://contactrefiner.com/og-image.png' },
    { property: 'og:image:width', content: '1200' },
    { property: 'og:image:height', content: '630' },
    { name: 'twitter:card', content: 'summary_large_image' },
    { name: 'twitter:title', content: 'Contact Refiner — AI-Powered Google Contacts Cleanup' },
    { name: 'twitter:description', content: 'Automatically fix diacritics, formatting, and duplicates in your Google Contacts.' },
    { name: 'twitter:image', content: 'https://contactrefiner.com/og-image.png' },
  ],
  link: [
    { rel: 'canonical', href: 'https://contactrefiner.com' },
  ],
  script: [
    {
      type: 'application/ld+json',
      innerHTML: JSON.stringify({
        '@context': 'https://schema.org',
        '@graph': [
          {
            '@type': 'WebSite',
            'name': 'Contact Refiner',
            'url': 'https://contactrefiner.com',
            'description': 'AI-powered Google Contacts cleanup tool',
            'publisher': { '@id': 'https://contactrefiner.com/#organization' },
          },
          {
            '@type': 'Organization',
            '@id': 'https://contactrefiner.com/#organization',
            'name': 'Instarea',
            'url': 'https://instarea.sk',
            'sameAs': ['https://github.com/peter-fusek'],
          },
          {
            '@type': 'SoftwareApplication',
            'name': 'Contact Refiner',
            'applicationCategory': 'BusinessApplication',
            'operatingSystem': 'Web',
            'url': 'https://contactrefiner.com',
            'description': 'Automatically fix diacritics, formatting, and duplicates in your Google Contacts. 6-phase pipeline with 26 rule categories, AI review, LinkedIn signals, personal CRM with bi-directional sync, feedback learning, and daily email digest.',
            'offers': {
              '@type': 'Offer',
              'price': '0',
              'priceCurrency': 'USD',
            },
            'author': { '@id': 'https://contactrefiner.com/#organization' },
            'downloadUrl': 'https://github.com/peter-fusek/contactrefiner',
            'screenshot': 'https://contactrefiner.com/og-image.png',
            'featureList': 'Diacritics restoration, Smart formatting, Contact cleanup, AI review, CRM sync to Google Contacts, Smart hashtag labels, Pipeline health monitoring, Learning system, Daily email digest, Interaction tracking, Privacy-first self-hosted',
          },
          {
            '@type': 'HowTo',
            'name': 'How to clean up your Google Contacts with AI',
            'description': 'Set up Contact Refiner to automatically fix diacritics, formatting, and duplicates in your Google Contacts.',
            'step': steps.map((s, i) => ({
              '@type': 'HowToStep',
              'name': s.title,
              'text': s.description,
              'position': i + 1,
            })),
          },
          {
            '@type': 'FAQPage',
            'mainEntity': faqs.map(f => ({
              '@type': 'Question',
              'name': f.q,
              'acceptedAnswer': { '@type': 'Answer', 'text': f.a },
            })),
          },
        ],
      }),
    },
  ],
})

const features = [
  {
    icon: 'i-lucide-contact',
    title: 'Google Contacts Native',
    description: 'Lives inside your Google Contacts. Changes sync directly via People API. Works on every device — iOS, Android, web, desktop.',
    color: 'text-green-400',
  },
  {
    icon: 'i-lucide-languages',
    title: 'Diacritics Restoration',
    description: 'Restores missing accents on Slovak and Czech names. Context-aware: only applies when the surname confirms SK/CZ origin.',
    color: 'text-primary-400',
  },
  {
    icon: 'i-lucide-scan-text',
    title: 'Smart Formatting',
    description: 'Normalizes phone numbers, titles, company names, and name casing across all contacts.',
    color: 'text-cyan-400',
  },
  {
    icon: 'i-lucide-trash-2',
    title: 'Contact Cleanup',
    description: 'Flags low-value one-off contacts — no full name, no email, no phone. Review and delete the clutter.',
    color: 'text-orange-400',
  },
  {
    icon: 'i-lucide-brain',
    title: 'AI Review',
    description: 'Claude AI catches edge cases that rules miss. You approve every change before it applies.',
    color: 'text-amber-400',
  },
  {
    icon: 'i-lucide-radar',
    title: 'LinkedIn Social Signals',
    description: 'Scans LinkedIn profiles to detect job changes and reconnection opportunities. Surfaces contacts worth reaching out to.',
    color: 'text-blue-400',
  },
  {
    icon: 'i-lucide-kanban',
    title: 'Personal CRM',
    description: 'Kanban board with quick-action buttons, inline AI reconnect prompts, and LinkedIn signal context on every tile. Drag contacts through stages.',
    color: 'text-purple-400',
  },
  {
    icon: 'i-lucide-user-round-search',
    title: 'FollowUp Intelligence',
    description: 'Scores contacts by interaction history, months since last contact, LinkedIn job changes, and profile completeness. Top candidates surface automatically.',
    color: 'text-teal-400',
  },
  {
    icon: 'i-lucide-refresh-cw',
    title: 'CRM Sync to Contacts',
    description: 'Notes sync back to Google Contacts biographies. Tags become contact groups. Your CRM data lives everywhere — native search, mobile, desktop.',
    color: 'text-emerald-400',
  },
  {
    icon: 'i-lucide-hash',
    title: 'Smart Hashtag Labels',
    description: 'Write #hashtags in CRM notes — they auto-convert to searchable tags and Google Contact groups. Slovak diacritics supported.',
    color: 'text-pink-400',
  },
  {
    icon: 'i-lucide-activity',
    title: 'Pipeline Health Monitor',
    description: 'Visual run history showing duration, errors, and phase completion. Spot problems before they affect your contacts.',
    color: 'text-red-400',
  },
  {
    icon: 'i-lucide-graduation-cap',
    title: 'Learning System',
    description: 'Remembers your review decisions. Rejected changes never come back. Confidence adjusts based on your feedback.',
    color: 'text-violet-400',
  },
  {
    icon: 'i-lucide-bell-ring',
    title: 'Daily Email Digest',
    description: 'Get a morning summary of what the pipeline fixed overnight. Changes applied, errors, queue status — all in one email.',
    color: 'text-rose-400',
  },
  {
    icon: 'i-lucide-shield-check',
    title: 'Privacy First',
    description: 'Runs on your own infrastructure. Security audited. Your contacts never leave your Google account.',
    color: 'text-green-400',
  },
]

</script>

<template>
  <div class="min-h-screen bg-neutral-950 text-neutral-200">
    <!-- Nav -->
    <nav class="fixed top-0 inset-x-0 z-50 border-b border-neutral-800/50 bg-neutral-950/80 backdrop-blur-lg">
      <div class="max-w-5xl mx-auto flex items-center justify-between px-6 h-14">
        <div class="flex items-center gap-2">
          <img src="/favicon.svg" alt="Contact Refiner" class="size-8" />
          <span class="text-sm font-semibold text-primary-400">Contact Refiner</span>
        </div>
        <div class="flex items-center gap-4">
          <a
            href="https://github.com/peter-fusek/contactrefiner"
            target="_blank"
            rel="noopener"
            class="text-neutral-500 hover:text-neutral-300 transition-colors"
          >
            <UIcon name="i-lucide-github" class="size-5" />
          </a>
          <NuxtLink
            to="/dashboard"
            class="text-xs text-neutral-500 hover:text-neutral-300 transition-colors"
          >
            Dashboard
          </NuxtLink>
          <NuxtLink
            to="/dashboard"
            class="text-xs px-3 py-1.5 rounded-lg border border-neutral-700 text-neutral-400 hover:text-neutral-200 hover:border-neutral-600 transition-colors"
          >
            Live Demo
          </NuxtLink>
        </div>
      </div>
    </nav>

    <!-- Hero -->
    <section class="pt-32 pb-20 px-6 hero-gradient">
      <div class="max-w-3xl mx-auto text-center space-y-6">
        <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-neutral-800 bg-neutral-900/50 text-xs text-neutral-400">
          <span class="size-1.5 rounded-full bg-primary-400 animate-pulse-glow" />
          Open source · Google Contacts native · 6-phase pipeline · AI review · CRM sync · Feedback learning
        </div>

        <h1 class="text-4xl md:text-5xl font-bold tracking-tight text-neutral-100 leading-tight">
          Your contacts are a mess.<br>
          <span class="text-primary-400">Let AI fix them.</span>
        </h1>

        <p class="text-lg text-neutral-400 max-w-xl mx-auto leading-relaxed">
          Contact Refiner automatically fixes diacritics, formatting, and duplicates
          in your Google Contacts. 26 rule categories, Claude AI review, LinkedIn signals,
          personal CRM, and a feedback learning loop. You approve every change.
        </p>

        <div class="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
          <NuxtLink
            to="/dashboard"
            class="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary-600 hover:bg-primary-500 text-white text-sm font-medium transition-colors"
            title="Browse the dashboard with masked demo data — no login required"
          >
            <UIcon name="i-lucide-play" class="size-4" />
            Try Live Demo
          </NuxtLink>
          <a
            href="https://github.com/peter-fusek/contactrefiner"
            target="_blank"
            rel="noopener"
            class="inline-flex items-center gap-2 px-6 py-3 rounded-xl border border-neutral-700 hover:border-neutral-600 text-neutral-300 text-sm font-medium transition-colors"
          >
            <UIcon name="i-lucide-github" class="size-4" />
            View on GitHub
          </a>
        </div>
      </div>

      <!-- Terminal preview -->
      <div class="max-w-2xl mx-auto mt-16">
        <div class="rounded-xl border border-neutral-800 bg-neutral-900/80 overflow-hidden">
          <div class="flex items-center gap-1.5 px-4 py-2.5 border-b border-neutral-800/50">
            <span class="size-2.5 rounded-full bg-red-500/60" />
            <span class="size-2.5 rounded-full bg-amber-500/60" />
            <span class="size-2.5 rounded-full bg-green-500/60" />
            <span class="ml-3 text-[11px] text-neutral-600">terminal</span>
          </div>
          <div class="p-5 text-sm leading-relaxed font-mono">
            <div class="text-neutral-500">$ python main.py # daily pipeline — 5 phases, fully automated</div>
            <div class="mt-2">
              <span class="text-violet-400">Phase 0:</span>
              <span class="text-neutral-400"> Process review feedback → learn approvals and rejections</span>
            </div>
            <div>
              <span class="text-primary-400">Phase 1:</span>
              <span class="text-neutral-400"> Analyze all contacts → auto-fix HIGH confidence changes</span>
            </div>
            <div>
              <span class="text-amber-400">Phase 2:</span>
              <span class="text-neutral-400"> AI review MEDIUM confidence → promote or demote</span>
            </div>
            <div>
              <span class="text-cyan-400">Phase 3:</span>
              <span class="text-neutral-400"> Tag activity from Gmail + Calendar interactions</span>
            </div>
            <div>
              <span class="text-teal-400">Phase 4:</span>
              <span class="text-neutral-400"> Score top FollowUp candidates (LinkedIn + interactions)</span>
            </div>
            <div>
              <span class="text-emerald-400">Phase 5:</span>
              <span class="text-neutral-400"> Sync CRM notes → Google Contacts biographies + groups</span>
            </div>
            <div class="mt-2 text-neutral-600">
              # Review changes at your-domain.com/review<br>
              # Pipeline completed — remaining changes queued for review
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Stats -->
    <section class="py-12 px-6 border-t border-neutral-800/50">
      <div class="max-w-5xl mx-auto grid grid-cols-2 md:grid-cols-5 gap-6 text-center">
        <div>
          <div class="text-2xl md:text-3xl font-bold text-primary-400 tabular-nums">6</div>
          <div class="label-refined mt-1">Pipeline phases</div>
        </div>
        <div>
          <div class="text-2xl md:text-3xl font-bold text-neutral-100 tabular-nums">26</div>
          <div class="label-refined mt-1">Rule categories</div>
        </div>
        <div>
          <div class="text-2xl md:text-3xl font-bold text-neutral-100 tabular-nums">5,500+</div>
          <div class="label-refined mt-1">Contacts managed</div>
        </div>
        <div>
          <div class="text-2xl md:text-3xl font-bold text-cyan-400 tabular-nums">&lt;$0.02</div>
          <div class="label-refined mt-1">Daily AI cost</div>
        </div>
        <div>
          <div class="text-2xl md:text-3xl font-bold text-green-400 tabular-nums">100%</div>
          <div class="label-refined mt-1">Self-hosted</div>
        </div>
      </div>
    </section>

    <!-- Features -->
    <section id="features" class="py-20 px-6 border-t border-neutral-800/50">
      <div class="max-w-5xl mx-auto">
        <div class="text-center mb-14">
          <h2 class="text-2xl md:text-3xl font-bold text-neutral-100">
            What it fixes
          </h2>
          <p class="mt-3 text-neutral-400">
            Rule-based analysis catches the obvious. AI handles the rest.
          </p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          <div
            v-for="(feature, i) in features"
            :key="feature.title"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-neutral-700 card-hover animate-fade-in"
            :style="{ animationDelay: `${i * 60}ms` }"
          >
            <UIcon :name="feature.icon" :class="['size-6', feature.color]" />
            <h3 class="text-sm font-semibold text-neutral-100">{{ feature.title }}</h3>
            <p class="text-sm text-neutral-400 leading-relaxed">{{ feature.description }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- How it works -->
    <section class="py-20 px-6 border-t border-neutral-800/50">
      <div class="max-w-3xl mx-auto">
        <div class="text-center mb-14">
          <h2 class="text-2xl md:text-3xl font-bold text-neutral-100">
            How it works
          </h2>
          <p class="mt-3 text-neutral-400">
            Three steps. You stay in control.
          </p>
        </div>

        <div class="space-y-8">
          <div
            v-for="step in steps"
            :key="step.num"
            class="flex items-start gap-5"
          >
            <div class="shrink-0 size-10 rounded-lg bg-primary-500/10 border border-primary-500/20 flex items-center justify-center text-sm font-bold text-primary-400">
              {{ step.num }}
            </div>
            <div>
              <h3 class="text-sm font-semibold text-neutral-100">{{ step.title }}</h3>
              <p class="mt-1 text-sm text-neutral-400 leading-relaxed">{{ step.description }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Dashboard Preview / Use Cases -->
    <section class="py-20 px-6 border-t border-neutral-800/50">
      <div class="max-w-5xl mx-auto">
        <div class="text-center mb-14">
          <h2 class="text-2xl md:text-3xl font-bold text-neutral-100">
            See it in action
          </h2>
          <p class="mt-3 text-neutral-400">
            Try the live demo — real data, masked for privacy. No login required.
          </p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          <NuxtLink
            to="/review"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-primary-500/40 transition-colors group"
          >
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-check-circle" class="size-5 text-primary-400" />
              <h3 class="text-sm font-semibold text-neutral-100">Review Queue</h3>
            </div>
            <p class="text-sm text-neutral-400 leading-relaxed">Approve or reject contact changes one by one, or use bulk actions. Filter by field, rule category, or confidence level.</p>
            <span class="text-xs text-primary-400 group-hover:text-primary-300">View demo &rarr;</span>
          </NuxtLink>

          <NuxtLink
            to="/changelog"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-cyan-500/40 transition-colors group"
          >
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-history" class="size-5 text-cyan-400" />
              <h3 class="text-sm font-semibold text-neutral-100">Changelog</h3>
            </div>
            <p class="text-sm text-neutral-400 leading-relaxed">Full audit trail of every change applied to your contacts. Searchable, filterable, and paginated.</p>
            <span class="text-xs text-cyan-400 group-hover:text-cyan-300">View demo &rarr;</span>
          </NuxtLink>

          <NuxtLink
            to="/analytics"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-amber-500/40 transition-colors group"
          >
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-bar-chart-3" class="size-5 text-amber-400" />
              <h3 class="text-sm font-semibold text-neutral-100">Analytics</h3>
            </div>
            <p class="text-sm text-neutral-400 leading-relaxed">Changes by field, confidence distribution, daily run history, top-changed contacts, and pipeline cost tracking.</p>
            <span class="text-xs text-amber-400 group-hover:text-amber-300">View demo &rarr;</span>
          </NuxtLink>

          <NuxtLink
            to="/social-signals"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-blue-500/40 transition-colors group"
          >
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-radar" class="size-5 text-blue-400" />
              <h3 class="text-sm font-semibold text-neutral-100">LinkedIn Signals</h3>
            </div>
            <p class="text-sm text-neutral-400 leading-relaxed">See job changes, active profiles, and reconnection opportunities across your network. Scanned and scored automatically.</p>
            <span class="text-xs text-blue-400 group-hover:text-blue-300">View demo &rarr;</span>
          </NuxtLink>

          <NuxtLink
            to="/crm"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-purple-500/40 transition-colors group"
          >
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-kanban" class="size-5 text-purple-400" />
              <h3 class="text-sm font-semibold text-neutral-100">Personal CRM</h3>
            </div>
            <p class="text-sm text-neutral-400 leading-relaxed">Kanban board for managing reconnections. AI-generated reach-out prompts, notes, tags, and stage tracking across your network.</p>
            <span class="text-xs text-purple-400 group-hover:text-purple-300">View demo &rarr;</span>
          </NuxtLink>

          <NuxtLink
            to="/pipeline"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-6 space-y-3 hover:border-green-500/40 transition-colors group"
          >
            <div class="flex items-center gap-2">
              <UIcon name="i-lucide-workflow" class="size-5 text-green-400" />
              <h3 class="text-sm font-semibold text-neutral-100">Pipeline</h3>
            </div>
            <p class="text-sm text-neutral-400 leading-relaxed">See all 5 phases, normalizer rules, AI prompts, learning system details, and live run stats in one place.</p>
            <span class="text-xs text-green-400 group-hover:text-green-300">View demo &rarr;</span>
          </NuxtLink>
        </div>
      </div>
    </section>

    <!-- FAQ -->
    <section id="faq" class="py-20 px-6 border-t border-neutral-800/50">
      <div class="max-w-3xl mx-auto">
        <div class="text-center mb-14">
          <h2 class="text-2xl md:text-3xl font-bold text-neutral-100">
            Frequently asked questions
          </h2>
        </div>
        <div class="space-y-4">
          <details
            v-for="faq in faqs"
            :key="faq.q"
            class="rounded-xl border border-neutral-800 bg-neutral-900/50 group"
          >
            <summary class="flex items-center justify-between px-6 py-4 cursor-pointer text-sm font-medium text-neutral-200 hover:text-neutral-100 transition-colors list-none">
              {{ faq.q }}
              <UIcon name="i-lucide-chevron-down" class="size-4 text-neutral-500 transition-transform group-open:rotate-180" />
            </summary>
            <div class="px-6 pb-4 text-sm text-neutral-400 leading-relaxed">
              {{ faq.a }}
            </div>
          </details>
        </div>
      </div>
    </section>

    <!-- CTA -->
    <section class="py-20 px-6 border-t border-neutral-800/50">
      <div class="max-w-2xl mx-auto text-center space-y-6">
        <h2 class="text-2xl md:text-3xl font-bold text-neutral-100">
          Clean up your contacts today
        </h2>
        <p class="text-neutral-400">
          Open source. Self-hosted. Your data stays yours.
        </p>
        <a
          href="https://github.com/peter-fusek/contactrefiner"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary-600 hover:bg-primary-500 text-white text-sm font-medium transition-colors"
        >
          <UIcon name="i-lucide-github" class="size-4" />
          Get started on GitHub
        </a>
      </div>
    </section>

    <!-- Footer -->
    <footer class="py-8 px-6 border-t border-neutral-800/50">
      <div class="max-w-5xl mx-auto flex items-center justify-between text-xs text-neutral-600">
        <span>Contact Refiner by <a href="https://instarea.sk" target="_blank" rel="noopener" class="text-neutral-500 hover:text-neutral-400 transition-colors">Instarea</a></span>
        <div class="flex items-center gap-4">
          <NuxtLink to="/privacy" class="text-neutral-500 hover:text-neutral-400 transition-colors">Privacy</NuxtLink>
          <a
            href="https://github.com/peter-fusek/contactrefiner"
            target="_blank"
            rel="noopener"
            class="text-neutral-500 hover:text-neutral-400 transition-colors"
          >
            GitHub
          </a>
        </div>
      </div>
    </footer>
  </div>
</template>
