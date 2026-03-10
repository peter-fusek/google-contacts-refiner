"""
Code Table Manager — loads and refreshes reference data from JSON seed files.

Seed files live in code_tables/ (git-tracked). Refreshed files are cached in
data/code_tables/ (gitignored). If a refreshed version exists and is fresh,
it's used; otherwise falls back to seed.

Usage:
    from code_tables import tables
    tables.get("free_email_domains")  → set of domain strings
    tables.get("name_diacritics")     → dict of ASCII → diacritical
    tables.refresh()                  → refresh all tables from external sources
"""
import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories — computed without importing config to avoid circular imports
_APP_DIR = Path(__file__).parent.resolve()
_SEED_DIR = _APP_DIR / "code_tables"

# Data dir mirrors config.py logic
import os as _os
if _os.getenv("ENVIRONMENT", "local") == "cloud":
    _DATA_DIR = Path(_os.getenv("DATA_MOUNT", "/mnt/data")) / "data"
else:
    _DATA_DIR = _APP_DIR / "data"
_CACHE_DIR = _DATA_DIR / "code_tables"


# ── Table definitions ────────────────────────────────────────────────────────

TABLES = {
    "free_email_domains": {
        "seed": "free_email_domains.json",
        "type": "set",          # entries is a list → return as set
        "refresh_url": "https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/main/allowlist.conf",
        "max_age_days": 30,
    },
    "phone_prefixes": {
        "seed": "phone_prefixes.json",
        "type": "dict_of_lists",  # {"SK": [...], "CZ": [...]}
        "refresh_url": None,      # manual only
        "max_age_days": 365,
    },
    "name_prefixes": {
        "seed": "name_prefixes.json",
        "type": "list",
        "refresh_url": None,
        "max_age_days": 365,
    },
    "name_diacritics": {
        "seed": "name_diacritics.json",
        "type": "dict",
        "refresh_url": None,
        "max_age_days": 365,
    },
    "surname_suffixes": {
        "seed": "surname_suffixes.json",
        "type": "dict",
        "refresh_url": None,
        "max_age_days": 365,
    },
    "company_legal_forms": {
        "seed": "company_legal_forms.json",
        "type": "dict",
        "refresh_url": None,
        "max_age_days": 365,
    },
    "generic_emails": {
        "seed": "generic_emails.json",
        "type": "raw",  # Custom format: {prefixes: [...], exact: [...]}
        "refresh_url": None,
        "max_age_days": 365,
    },
}


class CodeTableManager:
    """Manages loading and refreshing of code tables."""

    def __init__(self):
        self._cache = {}

    def get(self, name: str):
        """Get a code table by name. Returns set, list, or dict depending on type."""
        if name in self._cache:
            return self._cache[name]

        if name not in TABLES:
            raise KeyError(f"Unknown code table: {name}")

        data = self._load(name)
        self._cache[name] = data
        return data

    def _load(self, name: str):
        """Load table: prefer fresh cached version, fall back to seed."""
        spec = TABLES[name]
        table_type = spec["type"]

        # Try cached (refreshed) version first
        cached_file = _CACHE_DIR / spec["seed"]
        if cached_file.exists():
            try:
                raw = json.loads(cached_file.read_text(encoding="utf-8"))
                entries = raw if table_type == "raw" else raw.get("entries", raw)
                return self._coerce(entries, table_type)
            except Exception as e:
                logger.warning("Failed to load cached %s: %s", name, e)

        # Fall back to seed
        seed_file = _SEED_DIR / spec["seed"]
        if seed_file.exists():
            raw = json.loads(seed_file.read_text(encoding="utf-8"))
            entries = raw if table_type == "raw" else raw.get("entries", raw)
            return self._coerce(entries, table_type)

        raise FileNotFoundError(f"No seed file for {name}: {seed_file}")

    def _coerce(self, entries, table_type: str):
        """Coerce entries to the expected Python type."""
        if table_type == "set":
            return set(entries) if isinstance(entries, list) else set(entries)
        if table_type == "list":
            return list(entries) if not isinstance(entries, list) else entries
        if table_type == "dict" or table_type == "dict_of_lists":
            return dict(entries) if not isinstance(entries, dict) else entries
        if table_type == "raw":
            return entries  # Return as-is (custom structure)
        return entries

    def refresh(self, name: str = None, force: bool = False) -> dict:
        """
        Refresh code tables from external sources.

        Args:
            name: Specific table to refresh, or None for all.
            force: Refresh even if cached version is fresh.

        Returns:
            Dict of {table_name: {"status": "updated"|"skipped"|"error", ...}}
        """
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        results = {}

        targets = {name: TABLES[name]} if name else TABLES
        for tname, spec in targets.items():
            results[tname] = self._refresh_one(tname, spec, force)

        # Clear in-memory cache for refreshed tables
        for tname, result in results.items():
            if result["status"] == "updated":
                self._cache.pop(tname, None)

        return results

    def _refresh_one(self, name: str, spec: dict, force: bool) -> dict:
        """Refresh a single table."""
        url = spec.get("refresh_url")
        if not url:
            return {"status": "skipped", "reason": "no refresh URL"}

        # Check freshness
        cached_file = _CACHE_DIR / spec["seed"]
        if not force and cached_file.exists():
            max_age = timedelta(days=spec.get("max_age_days", 30))
            mtime = datetime.fromtimestamp(cached_file.stat().st_mtime)
            if datetime.now() - mtime < max_age:
                return {"status": "skipped", "reason": f"fresh (< {spec['max_age_days']}d)"}

        # Fetch
        try:
            new_entries = self._fetch_and_parse(name, url)
            if not new_entries:
                return {"status": "error", "reason": "empty result from fetch"}
        except Exception as e:
            logger.warning("Failed to refresh %s: %s", name, e)
            return {"status": "error", "reason": str(e)}

        # Merge with seed (union)
        seed_file = _SEED_DIR / spec["seed"]
        seed_entries = []
        if seed_file.exists():
            raw = json.loads(seed_file.read_text(encoding="utf-8"))
            seed_entries = raw.get("entries", [])

        # Apply custom overrides
        custom_file = _CACHE_DIR / f"custom_{spec['seed']}"
        add_entries = []
        remove_entries = []
        if custom_file.exists():
            custom = json.loads(custom_file.read_text(encoding="utf-8"))
            add_entries = custom.get("add", [])
            remove_entries = custom.get("remove", [])

        # Merge: seed ∪ fetched ∪ custom_add - custom_remove
        if isinstance(seed_entries, list):
            merged = list(set(seed_entries) | set(new_entries) | set(add_entries) - set(remove_entries))
            merged.sort()
        else:
            merged = {**seed_entries, **new_entries}
            for k in add_entries:
                if isinstance(add_entries, dict):
                    merged.update(add_entries)
            for k in remove_entries:
                merged.pop(k, None)

        # Write cached version
        output = {
            "version": "1.0",
            "refreshed_at": datetime.now().isoformat(),
            "source_url": url,
            "seed_count": len(seed_entries),
            "fetched_count": len(new_entries),
            "merged_count": len(merged),
            "entries": merged,
        }
        cached_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

        old_count = len(seed_entries)
        return {
            "status": "updated",
            "old_count": old_count,
            "new_count": len(merged),
            "added": len(merged) - old_count,
        }

    def _fetch_and_parse(self, name: str, url: str) -> list:
        """Fetch from URL and parse into entries list."""
        req = urllib.request.Request(url, headers={"User-Agent": "contacts-refiner/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8")

        if name == "free_email_domains":
            return self._parse_domain_list(content)

        # Default: try JSON
        try:
            data = json.loads(content)
            return data.get("entries", data) if isinstance(data, dict) else data
        except json.JSONDecodeError:
            # One entry per line
            return [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    def _parse_domain_list(self, content: str) -> list:
        """Parse a one-domain-per-line list, validate domain format."""
        domain_re = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$")
        domains = []
        for line in content.splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#") and domain_re.match(line):
                domains.append(line)
        return domains

    def is_generic_email(self, email: str) -> bool:
        """Check if an email address matches generic/transactional patterns."""
        data = self.get("generic_emails")
        email_lower = email.strip().lower()

        # Exact match
        if email_lower in data.get("exact", []):
            return True

        # Prefix match
        local = email_lower.split("@")[0] + "@" if "@" in email_lower else ""
        for prefix in data.get("prefixes", []):
            if local == prefix:
                return True

        return False

    def info(self) -> dict:
        """Return status info for all tables."""
        result = {}
        for name, spec in TABLES.items():
            cached_file = _CACHE_DIR / spec["seed"]
            seed_file = _SEED_DIR / spec["seed"]

            info = {"has_seed": seed_file.exists(), "has_cache": cached_file.exists()}

            if cached_file.exists():
                mtime = datetime.fromtimestamp(cached_file.stat().st_mtime)
                info["cached_at"] = mtime.isoformat()
                info["age_days"] = (datetime.now() - mtime).days
                try:
                    raw = json.loads(cached_file.read_text(encoding="utf-8"))
                    info["count"] = len(raw.get("entries", []))
                except Exception:
                    pass
            elif seed_file.exists():
                try:
                    raw = json.loads(seed_file.read_text(encoding="utf-8"))
                    info["count"] = len(raw.get("entries", []))
                except Exception:
                    pass

            info["refresh_url"] = spec.get("refresh_url")
            info["max_age_days"] = spec.get("max_age_days")
            result[name] = info

        return result

    def refresh_if_stale(self, max_age_days: int = None):
        """Refresh only tables that are stale. Safe for cloud pipeline pre-phase."""
        for name, spec in TABLES.items():
            if not spec.get("refresh_url"):
                continue
            cached_file = _CACHE_DIR / spec["seed"]
            age_limit = max_age_days or spec.get("max_age_days", 30)
            if cached_file.exists():
                mtime = datetime.fromtimestamp(cached_file.stat().st_mtime)
                if datetime.now() - mtime < timedelta(days=age_limit):
                    continue
            try:
                result = self._refresh_one(name, spec, force=False)
                if result["status"] == "updated":
                    logger.info("Refreshed code table %s: %s", name, result)
                    self._cache.pop(name, None)
            except Exception as e:
                logger.warning("Stale refresh of %s failed (non-fatal): %s", name, e)


# Module-level singleton
tables = CodeTableManager()
