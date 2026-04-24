import type { ContactDetailsResponse } from '../utils/types'
import {
  getCRMState,
  getFollowUpScores,
  getLinkedInSignals,
  getLeadSignalsState,
  getContactNameMap,
} from '../utils/gcs'
import { isDemoMode } from '../utils/demo'

const RESOURCE_RE = /^people\/c?\d+$/

export default defineEventHandler(async (event): Promise<ContactDetailsResponse> => {
  if (await isDemoMode(event)) {
    throw createError({ statusCode: 403, statusMessage: 'Demo mode: contact details disabled' })
  }

  const query = getQuery(event)
  const resourceName = typeof query.resourceName === 'string' ? query.resourceName.trim() : ''
  if (!RESOURCE_RE.test(resourceName)) {
    throw createError({ statusCode: 400, statusMessage: 'Invalid resourceName' })
  }

  const [crmState, followup, { signals }, leadState, nameMap] = await Promise.all([
    getCRMState(),
    getFollowUpScores(),
    getLinkedInSignals(),
    getLeadSignalsState(),
    getContactNameMap(),
  ])

  const followupScore = followup.scores.find(s => s.resourceName === resourceName) ?? null
  const linkedinSignal = signals.find(s => s.resourceName === resourceName) ?? null
  const crmContact = crmState.contacts[resourceName] ?? null
  const leadSignalRecord = leadState.contacts[resourceName] ?? null

  const resolvedName =
    followupScore?.name
    || crmContact?.name
    || linkedinSignal?.name
    || nameMap.get(resourceName)
    || resourceName.replace('people/', 'Contact ')

  // u/1 = peterfusek1980@gmail.com per CLAUDE.md — strip `people/` prefix.
  const contactId = resourceName.replace(/^people\//, '')
  const googleContactsUrl = `https://contacts.google.com/u/1/person/${contactId}`

  return {
    resourceName,
    name: resolvedName,
    googleContactsUrl,
    crmState: crmContact,
    leadSignalRecord,
    followupScore,
    linkedinSignal,
  }
})
