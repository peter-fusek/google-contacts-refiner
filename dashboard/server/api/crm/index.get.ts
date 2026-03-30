import type { CRMContact, CRMResponse, CRMStage } from '../../utils/types'
import { getFollowUpScores, getLinkedInSignals, getCRMState, getContactNameMap } from '../../utils/gcs'
import { isDemoMode, maskFollowUpScore } from '../../utils/demo'

const ALL_STAGES: CRMStage[] = ['inbox', 'reached_out', 'in_conversation', 'opportunity', 'converted', 'dormant', 'unknown', 'ready_to_delete']

export default defineEventHandler(async (event): Promise<CRMResponse> => {
  const demo = await isDemoMode(event)

  const [followup, { signals }, crmState, nameMap] = await Promise.all([
    getFollowUpScores(),
    getLinkedInSignals(),
    getCRMState(),
    getContactNameMap(),
  ])

  // Build LinkedIn signal lookup
  const signalMap = new Map(signals.map(s => [s.resourceName, s]))

  // Merge followup scores with CRM state
  const contacts: CRMContact[] = []
  const scores = followup.scores ?? []

  for (const score of scores) {
    const state = demo ? undefined : crmState.contacts[score.resourceName]
    const masked = demo ? maskFollowUpScore(score) : score
    const resolvedName = masked.name || nameMap.get(score.resourceName) || score.resourceName.replace('people/', 'Contact ')
    contacts.push({
      resourceName: masked.resourceName,
      name: resolvedName,
      stage: state?.stage ?? 'inbox',
      stageChangedAt: state?.stageChangedAt ?? '',
      notes: demo ? '' : (state?.notes ?? ''),
      tags: demo ? [] : (state?.tags ?? []),
      score_total: masked.score_total,
      score_breakdown: masked.score_breakdown,
      interaction: masked.interaction,
      linkedin: masked.linkedin,
      contact: masked.contact,
      followup_prompt: masked.followup_prompt,
    })
  }

  // Cap inbox at 50: show all contacts with explicit stages, but only top 50 inbox contacts by score
  const INBOX_CAP = 50
  const inboxContacts = contacts.filter(c => c.stage === 'inbox')
  const nonInboxContacts = contacts.filter(c => c.stage !== 'inbox')
  const cappedInbox = inboxContacts
    .sort((a, b) => b.score_total - a.score_total)
    .slice(0, INBOX_CAP)
  const result = [...cappedInbox, ...nonInboxContacts]

  // Count per stage (using capped result)
  const stages = Object.fromEntries(ALL_STAGES.map(s => [s, 0])) as Record<CRMStage, number>
  for (const c of result) {
    stages[c.stage]++
  }

  return { contacts: result, stages }
})
