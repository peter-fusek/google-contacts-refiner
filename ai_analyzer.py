"""
Claude AI integration for smart contact analysis.

Uses the Anthropic SDK to enhance rule-based analysis with AI judgment.
Falls back gracefully to rule-based results if AI is unavailable.
"""
import json
import os
import time
from pathlib import Path
from typing import Optional

from config import (
    BASE_DIR,
    AI_MODEL,
    AI_CONFIDENCE_REVIEW_THRESHOLD,
    AI_COST_LIMIT_PER_SESSION,
    AI_MAX_CONTACTS_PER_BATCH,
    CONFIDENCE_HIGH,
)


class AIAnalyzer:
    """
    Claude-powered contact analyzer.

    Enhances rule-based changes with AI judgment for edge cases:
    - Diacritics correction for names not in dictionary
    - Duplicate merge strategy suggestions
    - Context-aware enrichment
    """

    def __init__(self, api_key: str = None, model: str = None):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "Anthropic SDK nie je nainštalovaný. "
                "Spusti: pip install anthropic"
            )

        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY nie je nastavený. "
                "Nastav premennú prostredia alebo odovzdaj api_key."
            )

        self.model = model or AI_MODEL
        self.client = anthropic.Anthropic(api_key=self.api_key)

        # Load instructions and memory
        self._instructions = self._load_instructions()
        self._memory = self._load_memory()

        # Session tracking
        self._learnings = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._estimated_cost = 0.0

    # ── Public API ────────────────────────────────────────────────

    def enhance_analysis(
        self, contact: dict, rule_changes: list[dict]
    ) -> list[dict]:
        """
        Re-evaluate a contact's rule-based changes with AI judgment.

        Only called for contacts with medium/low confidence changes or
        complex issues. Returns enhanced change list.
        """
        if self._is_cost_exceeded():
            return rule_changes

        prompt = self._build_enhance_prompt(contact, rule_changes)
        response = self._call_api(prompt)
        if not response:
            return rule_changes

        parsed = self._parse_structured_response(response)
        if not parsed:
            return rule_changes

        # Collect learnings
        if parsed.get("learnings"):
            self._learnings.extend(parsed["learnings"])

        # Merge AI changes with rule-based changes
        ai_changes = parsed.get("changes", [])
        if ai_changes:
            return self._merge_changes(rule_changes, ai_changes)

        return rule_changes

    def enhance_batch(
        self, contacts_with_changes: list[tuple[dict, list[dict]]]
    ) -> list[list[dict]]:
        """
        Enhance analysis for a batch of contacts in a single API call.

        Args:
            contacts_with_changes: List of (contact, rule_changes) tuples.

        Returns:
            List of enhanced change lists, one per contact.
        """
        if self._is_cost_exceeded() or not contacts_with_changes:
            return [changes for _, changes in contacts_with_changes]

        prompt = self._build_batch_prompt(contacts_with_changes)
        response = self._call_api(prompt)
        if not response:
            return [changes for _, changes in contacts_with_changes]

        parsed = self._parse_batch_response(response, len(contacts_with_changes))
        if not parsed:
            return [changes for _, changes in contacts_with_changes]

        results = []
        for i, (_, rule_changes) in enumerate(contacts_with_changes):
            if i < len(parsed) and parsed[i].get("changes"):
                merged = self._merge_changes(rule_changes, parsed[i]["changes"])
                results.append(merged)
                if parsed[i].get("learnings"):
                    self._learnings.extend(parsed[i]["learnings"])
            else:
                results.append(rule_changes)

        return results

    def evaluate_duplicates(
        self, dup_groups: list[dict], contacts_lookup: dict
    ) -> list[dict]:
        """
        Evaluate duplicate groups and suggest merge strategies.

        Returns duplicate groups with AI recommendations added.
        """
        if self._is_cost_exceeded() or not dup_groups:
            return dup_groups

        prompt = self._build_duplicates_prompt(dup_groups, contacts_lookup)
        response = self._call_api(prompt)
        if not response:
            return dup_groups

        parsed = self._parse_structured_response(response)
        if not parsed or "groups" not in parsed:
            return dup_groups

        # Add AI recommendations to groups
        for i, group in enumerate(dup_groups):
            if i < len(parsed["groups"]):
                ai_group = parsed["groups"][i]
                group["ai_recommendation"] = ai_group.get("recommendation", "")
                group["ai_confidence"] = ai_group.get("confidence", 0.0)
                group["ai_reason"] = ai_group.get("reason", "")

        return dup_groups

    def get_new_learnings(self) -> list[dict]:
        """Return accumulated learnings from this session."""
        return list(self._learnings)

    def get_usage_stats(self) -> dict:
        """Return token usage and cost estimates."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "estimated_cost_usd": round(self._estimated_cost, 4),
            "cost_limit_usd": AI_COST_LIMIT_PER_SESSION,
        }

    @staticmethod
    def needs_ai_review(changes: list[dict]) -> bool:
        """Determine if a contact's changes need AI review."""
        if not changes:
            return False

        for change in changes:
            conf = change.get("confidence", 1.0)
            # Has medium/low confidence changes
            if conf < AI_CONFIDENCE_REVIEW_THRESHOLD:
                return True
            # Has diacritics changes that are pattern-based (not dictionary)
            if "diakritik" in change.get("reason", "").lower() and conf < 0.90:
                return True

        return False

    # ── Prompt builders ───────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        parts = [
            "Si asistent na čistenie Google kontaktov. Analyzuješ kontakty a navrhuješ opravy.",
            "Špecializuješ sa na slovenské a české mená, diakritiku, telefónne čísla a detekciu duplikátov.",
            "",
            "PRAVIDLÁ:",
            "- Odpovedaj VŽDY v JSON formáte podľa špecifikovanej schémy",
            "- Slovenské/české mená vždy s diakritikou (Štefan, nie Stefan)",
            "- Pri neistote radšej ponechaj pôvodný tvar",
            "- Confidence: 0.95+ pre isté zmeny, 0.70-0.90 pre pravdepodobné, pod 0.60 pre špekulatívne",
        ]

        if self._instructions:
            parts.extend(["", "POUŽÍVATEĽSKÉ PRAVIDLÁ:", self._instructions])

        if self._memory:
            parts.extend(["", "NAUČENÉ VZORY:", json.dumps(self._memory, ensure_ascii=False, indent=2)])

        return "\n".join(parts)

    def _build_enhance_prompt(self, contact: dict, rule_changes: list[dict]) -> str:
        contact_summary = self._summarize_contact(contact)
        changes_json = json.dumps(rule_changes, ensure_ascii=False, indent=2)

        return f"""Analyzuj tento kontakt a jeho navrhované zmeny. Uprav confidence alebo pridaj nové zmeny.

KONTAKT:
{contact_summary}

NAVRHOVANÉ ZMENY (z rule-based analýzy):
{changes_json}

Odpovedz v JSON:
{{
  "changes": [
    {{
      "field": "names[0].givenName",
      "old": "povodna hodnota",
      "new": "nova hodnota",
      "confidence": 0.95,
      "reason": "dovod zmeny",
      "source": "ai"
    }}
  ],
  "learnings": [
    {{
      "type": "diacritics_pattern",
      "key": "ascii_tvar",
      "value": "spravny_tvar",
      "confidence": 0.95
    }}
  ]
}}

Pravidlá:
- Zahrň VŠETKY zmeny (aj pôvodné rule-based, prípadne s upraveným confidence)
- Pridaj nové zmeny len ak si istý (confidence >= 0.70)
- Pre zmeny so source "ai" vždy uveď dôvod
- Learnings pridaj len pre nové vzory, nie pre slovníkové zhody"""

    def _build_batch_prompt(
        self, contacts_with_changes: list[tuple[dict, list[dict]]]
    ) -> str:
        contacts_json = []
        for i, (contact, changes) in enumerate(contacts_with_changes):
            contacts_json.append({
                "index": i,
                "contact": self._summarize_contact_dict(contact),
                "rule_changes": changes,
            })

        return f"""Analyzuj tieto kontakty a ich navrhované zmeny. Pre každý kontakt uprav confidence alebo pridaj nové zmeny.

KONTAKTY:
{json.dumps(contacts_json, ensure_ascii=False, indent=2)}

Odpovedz v JSON (pole s výsledkami pre každý kontakt v rovnakom poradí):
[
  {{
    "index": 0,
    "changes": [...],
    "learnings": [...]
  }},
  ...
]

Rovnaké pravidlá ako pre jednotlivé kontakty — zahrň všetky zmeny, pridávaj len isté nové."""

    def _build_duplicates_prompt(
        self, dup_groups: list[dict], contacts_lookup: dict
    ) -> str:
        groups_data = []
        for group in dup_groups[:20]:  # Limit to 20 groups
            group_contacts = []
            for rn in group.get("resource_names", []):
                person = contacts_lookup.get(rn, {})
                group_contacts.append(self._summarize_contact_dict(person))

            groups_data.append({
                "match_type": group.get("match_type", ""),
                "match_value": group.get("match_value", ""),
                "contacts": group_contacts,
            })

        return f"""Zhodnoť tieto skupiny potenciálnych duplikátov a navrhni stratégiu.

SKUPINY:
{json.dumps(groups_data, ensure_ascii=False, indent=2)}

Odpovedz v JSON:
{{
  "groups": [
    {{
      "recommendation": "merge" alebo "skip" alebo "review",
      "confidence": 0.90,
      "reason": "dôvod odporúčania"
    }}
  ]
}}

Pravidlá:
- "merge" len ak si istý že ide o rovnakú osobu
- "skip" ak sú to rôzne osoby (napr. iná organizácia)
- "review" ak treba manuálne posúdenie"""

    # ── Contact summarization ─────────────────────────────────────

    def _summarize_contact(self, person: dict) -> str:
        """Create a text summary of a contact for the prompt."""
        d = self._summarize_contact_dict(person)
        return json.dumps(d, ensure_ascii=False, indent=2)

    def _summarize_contact_dict(self, person: dict) -> dict:
        """Create a dict summary of a contact (only relevant fields)."""
        summary = {}

        names = person.get("names", [])
        if names:
            n = names[0]
            summary["name"] = {
                k: v for k, v in {
                    "givenName": n.get("givenName", ""),
                    "familyName": n.get("familyName", ""),
                    "displayName": n.get("displayName", ""),
                    "honorificPrefix": n.get("honorificPrefix", ""),
                }.items() if v
            }

        phones = person.get("phoneNumbers", [])
        if phones:
            summary["phones"] = [
                {"value": p.get("value", ""), "type": p.get("type", "")}
                for p in phones
            ]

        emails = person.get("emailAddresses", [])
        if emails:
            summary["emails"] = [
                {"value": e.get("value", ""), "type": e.get("type", "")}
                for e in emails
            ]

        orgs = person.get("organizations", [])
        if orgs:
            summary["organizations"] = [
                {k: v for k, v in {
                    "name": o.get("name", ""),
                    "title": o.get("title", ""),
                }.items() if v}
                for o in orgs
            ]

        bios = person.get("biographies", [])
        if bios:
            summary["notes"] = bios[0].get("value", "")[:500]

        addresses = person.get("addresses", [])
        if addresses:
            summary["addresses"] = [
                {k: v for k, v in {
                    "city": a.get("city", ""),
                    "country": a.get("country", ""),
                    "postalCode": a.get("postalCode", ""),
                }.items() if v}
                for a in addresses
            ]

        return summary

    # ── API communication ─────────────────────────────────────────

    def _call_api(self, user_prompt: str) -> Optional[str]:
        """Call Claude API and return the text response."""
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self._build_system_prompt(),
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Track usage
            usage = message.usage
            self._total_input_tokens += usage.input_tokens
            self._total_output_tokens += usage.output_tokens
            self._estimated_cost += self._estimate_cost(
                usage.input_tokens, usage.output_tokens
            )

            # Extract text
            text = ""
            for block in message.content:
                if block.type == "text":
                    text += block.text

            return text

        except Exception as e:
            print(f"   ⚠️  AI chyba: {e}")
            return None

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD based on model pricing (Sonnet rates)."""
        # Sonnet pricing: $3/M input, $15/M output (approximate)
        input_cost = (input_tokens / 1_000_000) * 3.0
        output_cost = (output_tokens / 1_000_000) * 15.0
        return input_cost + output_cost

    def _is_cost_exceeded(self) -> bool:
        """Check if session cost limit has been reached."""
        if self._estimated_cost >= AI_COST_LIMIT_PER_SESSION:
            return True
        return False

    # ── Response parsing ──────────────────────────────────────────

    def _parse_structured_response(self, response: str) -> Optional[dict]:
        """Parse a JSON response from Claude."""
        if not response:
            return None

        # Try to extract JSON from the response
        text = response.strip()

        # Remove markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (```json and ```)
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            return None

    def _parse_batch_response(
        self, response: str, expected_count: int
    ) -> Optional[list[dict]]:
        """Parse a batch response (JSON array)."""
        if not response:
            return None

        text = response.strip()

        # Remove markdown code fences
        if text.startswith("```"):
            lines = text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                elif line.strip() == "```" and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "results" in result:
                return result["results"]
        except json.JSONDecodeError:
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass

        return None

    # ── Change merging ────────────────────────────────────────────

    def _merge_changes(
        self, rule_changes: list[dict], ai_changes: list[dict]
    ) -> list[dict]:
        """
        Merge AI changes with rule-based changes.

        AI changes can:
        - Override confidence for existing changes
        - Add new changes
        - Remove changes (by setting confidence to 0)
        """
        # Index rule changes by field
        rule_by_field = {c["field"]: c for c in rule_changes}
        merged = []

        # Process AI changes
        ai_fields_seen = set()
        for ai_change in ai_changes:
            field = ai_change.get("field", "")
            if not field:
                continue

            ai_fields_seen.add(field)

            # Validate AI change has required fields
            if "new" not in ai_change or "confidence" not in ai_change:
                # Keep the rule-based change if it exists
                if field in rule_by_field:
                    merged.append(rule_by_field[field])
                continue

            # Skip if AI sets confidence to 0 (means "remove this change")
            if ai_change["confidence"] <= 0:
                continue

            # Mark as AI-enhanced
            ai_change.setdefault("source", "ai")
            merged.append(ai_change)

        # Add rule-based changes that AI didn't touch
        for field, change in rule_by_field.items():
            if field not in ai_fields_seen:
                merged.append(change)

        return merged

    # ── File loading ──────────────────────────────────────────────

    def _load_instructions(self) -> str:
        """Load instructions.md as plain text."""
        path = BASE_DIR / "instructions.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _load_memory(self) -> dict:
        """Load memory.json as structured data."""
        path = BASE_DIR / "memory.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
