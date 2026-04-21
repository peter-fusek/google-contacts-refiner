"""
Configuration and constants for Google Contacts Cleanup tool.
"""
import json
import os
import logging
from pathlib import Path

_logger = logging.getLogger(__name__)

# ── Environment ──────────────────────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")  # "local" | "cloud"
GCP_PROJECT = os.getenv("GCP_PROJECT", "contacts-refiner")

# ── Paths ──────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent.resolve()  # Always the code directory

if ENVIRONMENT == "cloud":
    BASE_DIR = Path(os.getenv("DATA_MOUNT", "/mnt/data"))
    DATA_DIR = BASE_DIR / "data"
    CREDENTIALS_FILE = None  # Not used in cloud — auth via Secret Manager
    TOKEN_FILE = None        # Not used in cloud — token in Secret Manager
else:
    BASE_DIR = APP_DIR
    DATA_DIR = BASE_DIR / "data"
    CREDENTIALS_FILE = BASE_DIR / "credentials.json"
    TOKEN_FILE = BASE_DIR / "token.json"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Google API ─────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/contacts"]

# All person fields we want to read/write
PERSON_FIELDS = ",".join([
    "metadata", "names", "emailAddresses", "phoneNumbers", "addresses",
    "organizations", "biographies", "birthdays", "calendarUrls", "clientData",
    "coverPhotos", "events", "externalIds", "genders", "imClients",
    "interests", "locales", "locations", "memberships", "miscKeywords",
    "nicknames", "occupations", "photos", "relations", "sipAddresses",
    "skills", "urls", "userDefined",
])

# Fields mask for update operations
# NOTE: "memberships" intentionally excluded — group assignments must NEVER
# be overwritten. The dynamic field mask in batch_processor only includes
# fields with actual changes, so this is a safety net.
UPDATE_PERSON_FIELDS = ",".join([
    "names", "emailAddresses", "phoneNumbers", "addresses",
    "organizations", "biographies", "birthdays", "events",
    "externalIds", "nicknames", "occupations", "relations",
    "urls", "userDefined",
])

PAGE_SIZE = 1000  # Max contacts per API page

# ── Rate Limiting ──────────────────────────────────────────────────────────
READ_RATE_LIMIT = 180       # requests per minute for reads
MUTATION_RATE_LIMIT = 90    # requests per minute for writes
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0      # seconds, exponential backoff base

# ── Batch Processing ──────────────────────────────────────────────────────
BATCH_SIZE = 50  # contacts per batch for user approval

# ── Confidence Thresholds ──────────────────────────────────────────────────
CONFIDENCE_HIGH = 0.90
CONFIDENCE_MEDIUM = 0.60

# ── Claude AI ─────────────────────────────────────────────────────────
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() == "true"
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-4-5-20251001")  # Haiku: ~10x cheaper
AI_CONFIDENCE_REVIEW_THRESHOLD = 0.90  # Send to AI if below this
AI_MAX_CONTACTS_PER_BATCH = 10         # Contacts per AI API call
AI_COST_LIMIT_PER_SESSION = 1.00       # USD safety cap per run (tightened for weekly+monthly cadence)

# ── Auto Mode ─────────────────────────────────────────────────────────
AUTO_CONFIDENCE_THRESHOLD = 0.90       # Min confidence for auto-apply
AUTO_MAX_CHANGES_PER_RUN = 200         # Safety limit for auto-mode

# ── Dashboard Config Overrides ────────────────────────────────────────
def load_pipeline_config_overrides():
    """Load config overrides from GCS (written by dashboard Config page)."""
    global BATCH_SIZE, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM
    global AI_COST_LIMIT_PER_SESSION, AUTO_CONFIDENCE_THRESHOLD, AUTO_MAX_CHANGES_PER_RUN

    config_path = DATA_DIR / "pipeline_config.json"
    if not config_path.exists():
        return

    try:
        with open(config_path, encoding="utf-8") as f:
            overrides = json.load(f)

        def _clamp(val, lo, hi):
            return max(lo, min(hi, val))

        if "batchSize" in overrides:
            BATCH_SIZE = _clamp(int(overrides["batchSize"]), 10, 500)
        if "confidenceHigh" in overrides:
            CONFIDENCE_HIGH = _clamp(float(overrides["confidenceHigh"]), 0.50, 1.00)
        if "confidenceMedium" in overrides:
            CONFIDENCE_MEDIUM = _clamp(float(overrides["confidenceMedium"]), 0.30, 0.99)
        if "aiCostLimit" in overrides:
            AI_COST_LIMIT_PER_SESSION = _clamp(float(overrides["aiCostLimit"]), 0.10, 50.00)
        if "autoThreshold" in overrides:
            AUTO_CONFIDENCE_THRESHOLD = _clamp(float(overrides["autoThreshold"]), 0.50, 1.00)
        if "autoMaxChanges" in overrides:
            AUTO_MAX_CHANGES_PER_RUN = _clamp(int(overrides["autoMaxChanges"]), 1, 1000)

        _logger.info("Loaded pipeline config overrides from %s", config_path)
    except Exception as e:
        _logger.warning("Failed to load pipeline config overrides (using defaults): %s", e)

# ── AI Review (Phase 2) ──────────────────────────────────────────────
AI_REVIEW_CHECKPOINT = DATA_DIR / "ai_review_checkpoint.json"
AI_REVIEW_HISTORY = DATA_DIR / "ai_review_history.json"

# ── Code Table Loading ─────────────────────────────────────────────────────
def _load_table(name, default):
    """Load from code_tables with fallback to inline default."""
    try:
        from code_tables import tables
        result = tables.get(name)
        if result:
            return result
    except Exception as e:
        _logger.debug("Code table %s unavailable, using default: %s", name, e)
    return default

# ── Phone Number Defaults ──────────────────────────────────────────────────
DEFAULT_REGION = "SK"
SUPPORTED_REGIONS = ["SK", "CZ"]

# SK mobile prefixes (after country code)
_DEFAULT_SK_MOBILE = ["90", "91", "92", "93", "94", "95"]
_DEFAULT_CZ_MOBILE = ["60", "70", "72", "73", "77", "78"]
_phone_prefixes = _load_table("phone_prefixes", {"SK": _DEFAULT_SK_MOBILE, "CZ": _DEFAULT_CZ_MOBILE})
SK_MOBILE_PREFIXES = _phone_prefixes.get("SK", _DEFAULT_SK_MOBILE)
CZ_MOBILE_PREFIXES = _phone_prefixes.get("CZ", _DEFAULT_CZ_MOBILE)

# ── Email Domains ──────────────────────────────────────────────────────────
# Free/personal email providers (not corporate)
_DEFAULT_FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "azet.sk", "post.sk",
    "zoznam.sk", "centrum.sk", "stonline.sk", "pobox.sk",
    "mail.t-com.sk", "t-com.sk", "orangemail.sk",
    "seznam.cz", "email.cz", "centrum.cz", "volny.cz",
    "atlas.cz", "quick.cz", "tiscali.cz",
    "yahoo.com", "yahoo.co.uk", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "protonmail.com", "proton.me", "tutanota.com",
    "mail.com", "gmx.com", "gmx.de", "web.de", "yandex.com",
    "zoho.com", "fastmail.com",
}
FREE_EMAIL_DOMAINS = _load_table("free_email_domains", _DEFAULT_FREE_EMAIL_DOMAINS)

# ── Name Prefixes (Titles) ────────────────────────────────────────────────
_DEFAULT_NAME_PREFIXES = [
    "Ing.", "Mgr.", "Bc.", "MUDr.", "JUDr.", "PhDr.", "RNDr.",
    "PaedDr.", "ThDr.", "MVDr.", "PhD.", "CSc.", "DrSc.",
    "Doc.", "Prof.", "Dr.", "MBA", "MSc.", "BSc.",
    "Ing. arch.", "ThLic.", "ArtD.",
]
NAME_PREFIXES = _load_table("name_prefixes", _DEFAULT_NAME_PREFIXES)

# ── SK/CZ Diacritics Dictionary ───────────────────────────────────────────
# Maps ASCII versions to proper diacritical forms.
# Covers given names, surnames, and common patterns.
# NOTE: This inline dict serves as the default fallback. The code_tables/
# version is the canonical source — edit that JSON file to add new names.
SK_CZ_NAMES_DIACRITICS = _load_table("name_diacritics", {
    # ═══════════════════════════════════════════════════════════════
    # MUŽSKÉ MENÁ (Male given names)
    # ═══════════════════════════════════════════════════════════════
    "Stefan": "Štefan",
    "Lubomir": "Ľubomír",
    "Dusan": "Dušan",
    "Ludovit": "Ľudovít",
    "Tomas": "Tomáš",
    "Jan": "Ján",
    "Matus": "Matúš",
    "Lukas": "Lukáš",
    "Vladimir": "Vladimír",
    "Simon": "Šimon",
    "Mikulas": "Mikuláš",
    "Nikolas": "Nikolás",
    "Dominik": "Dominik",
    "Marian": "Marián",
    "Luboslav": "Ľuboslav",
    "Ludek": "Luděk",
    "Radovan": "Radovan",
    "Bohdan": "Bohdan",
    "Dalibor": "Dalibor",
    "Eduard": "Eduard",
    "Eugen": "Eugen",
    "Fedor": "Fedor",
    "Filip": "Filip",
    "Gustav": "Gustáv",
    "Igor": "Igor",
    "Ivan": "Ivan",
    "Jakub": "Jakub",
    "Karol": "Karol",
    "Kristian": "Kristián",
    "Martin": "Martin",
    "Milan": "Milan",
    "Norbert": "Norbert",
    "Oliver": "Oliver",
    "Pavol": "Pavol",
    "Peter": "Peter",
    "Richard": "Richard",
    "Robert": "Robert",
    "Roman": "Roman",
    "Samuel": "Samuel",
    "Stanislav": "Stanislav",
    "Tibor": "Tibor",
    "Viktor": "Viktor",
    "Viliam": "Viliam",
    "Zdenko": "Zdenko",
    "Jozef": "Jozef",
    "Maros": "Maroš",
    "Svätopluk": "Svätopluk",
    "Svatopluk": "Svätopluk",
    "Rastislav": "Rastislav",
    "Miroslav": "Miroslav",
    "Jaroslav": "Jaroslav",
    "Branislav": "Branislav",
    "Ondrej": "Ondrej",
    "Patrik": "Patrik",
    "Matej": "Matej",
    "Adam": "Adam",
    "Radoslav": "Radoslav",
    "Ladislav": "Ladislav",
    "Michal": "Michal",
    "Marek": "Marek",
    "Andrej": "Andrej",
    "Juraj": "Juraj",
    "Julius": "Július",
    "Blazej": "Blažej",
    "Alojz": "Alojz",
    "Arnost": "Arnošt",
    "Bedrich": "Bedřich",
    "Bohumir": "Bohumír",
    "Bohuslav": "Bohuslav",
    "Cestmir": "Čestmír",
    "Dalimil": "Dalimil",
    "Drahomir": "Drahomír",
    "Emanuel": "Emanuel",
    "Evzen": "Evžen",
    "Hynek": "Hynek",
    "Jaromir": "Jaromír",
    "Jindrich": "Jindřich",
    "Jiri": "Jiří",
    "Premysl": "Přemysl",
    "Vojtech": "Vojtěch",
    "Zbynek": "Zbyněk",
    "Vaclav": "Václav",
    "Frantisek": "František",
    "Oldrich": "Oldřich",
    "Otakar": "Otakar",
    "Radomir": "Radomír",
    "Slavomir": "Slavomír",
    "Svatomir": "Svatomír",
    "Vilem": "Vilém",
    "Vlastimil": "Vlastimil",
    "Zdenek": "Zdeněk",
    "Ales": "Aleš",
    "Benes": "Beneš",
    "Lubomír": "Lubomír",
    "Milos": "Miloš",
    "Tobiás": "Tobiáš",
    "Tobias": "Tobiáš",
    "Denis": "Denis",
    "Sebastián": "Sebastián",
    "Sebastian": "Sebastián",
    "Damian": "Damián",
    "Adrian": "Adrián",
    "Alex": "Alex",
    "Alexander": "Alexander",

    # ═══════════════════════════════════════════════════════════════
    # ŽENSKÉ MENÁ (Female given names)
    # ═══════════════════════════════════════════════════════════════
    "Zuzana": "Zuzana",
    "Katarina": "Katarína",
    "Maria": "Mária",
    "Martina": "Martina",
    "Monika": "Monika",
    "Lubica": "Ľubica",
    "Ruzena": "Ružena",
    "Natalia": "Natália",
    "Ludmila": "Ľudmila",
    "Zaneta": "Žaneta",
    "Sarka": "Šárka",
    "Dagmar": "Dagmar",
    "Jaroslava": "Jaroslava",
    "Tereza": "Tereza",
    "Barbora": "Barbora",
    "Lenka": "Lenka",
    "Petra": "Petra",
    "Alzbeta": "Alžbeta",
    "Andrea": "Andrea",
    "Anna": "Anna",
    "Beata": "Beáta",
    "Bozena": "Božena",
    "Dana": "Dana",
    "Daniela": "Daniela",
    "Denisa": "Denisa",
    "Diana": "Diana",
    "Dominika": "Dominika",
    "Eva": "Eva",
    "Gabriela": "Gabriela",
    "Hana": "Hana",
    "Helena": "Helena",
    "Ivana": "Ivana",
    "Jana": "Jana",
    "Jarmila": "Jarmila",
    "Juliana": "Juliana",
    "Klara": "Klára",
    "Kristina": "Kristína",
    "Lucia": "Lucia",
    "Lucie": "Lucie",
    "Magdalena": "Magdaléna",
    "Marcela": "Marcela",
    "Michaela": "Michaela",
    "Miroslava": "Miroslava",
    "Nina": "Nina",
    "Olga": "Oľga",
    "Paulina": "Paulína",
    "Radka": "Radka",
    "Renata": "Renáta",
    "Simona": "Simona",
    "Slavka": "Slavka",
    "Slavomira": "Slavomíra",
    "Sona": "Soňa",
    "Stanislava": "Stanislava",
    "Tatiana": "Tatiana",
    "Veronika": "Veronika",
    "Vladimira": "Vladimíra",
    "Zdenka": "Zdenka",
    "Zelmira": "Želmíra",
    "Zofie": "Žofie",
    "Zofia": "Žofia",
    "Antonia": "Antónia",
    "Berta": "Berta",
    "Blazena": "Blažena",
    "Cecilia": "Cecília",
    "Drahomira": "Drahomíra",
    "Emilia": "Emília",
    "Erika": "Erika",
    "Filomena": "Filomena",
    "Henrieta": "Henrieta",
    "Izabela": "Izabela",
    "Karolina": "Karolína",
    "Kveta": "Kveta",
    "Livia": "Lívia",
    "Malgorzata": "Malgorzata",
    "Milada": "Milada",
    "Milena": "Milena",
    "Nikola": "Nikola",
    "Patricia": "Patrícia",
    "Sabina": "Sabína",
    "Sandra": "Sandra",
    "Silvia": "Silvia",
    "Tamara": "Tamara",
    "Valeria": "Valéria",
    "Viktoria": "Viktória",
    "Viola": "Viola",
    "Zita": "Zita",

    # ═══════════════════════════════════════════════════════════════
    # PRIEZVISKÁ (Surnames)
    # ═══════════════════════════════════════════════════════════════
    "Novak": "Novák",
    "Novakova": "Nováková",
    "Horvath": "Horváth",
    "Horvathova": "Horváthová",
    "Kovac": "Kováč",
    "Kovacova": "Kováčová",
    "Kovacs": "Kovács",
    "Toth": "Tóth",
    "Tothova": "Tóthová",
    "Varga": "Varga",
    "Nemec": "Nemec",
    "Nemcova": "Nemcová",
    "Cernak": "Černák",
    "Cernakova": "Černáková",
    "Simko": "Šimko",
    "Simkova": "Šimková",
    "Sulik": "Sulík",
    "Hudak": "Hudák",
    "Hudakova": "Hudáková",
    "Polak": "Polák",
    "Polakova": "Poláková",
    "Kollar": "Kollár",
    "Kollarova": "Kollárová",
    "Hrusovsky": "Hrušovský",
    "Dvorak": "Dvořák",
    "Dvorakova": "Dvořáková",
    "Svoboda": "Svoboda",
    "Pospisil": "Pospíšil",
    "Pospisilova": "Pospíšilová",
    "Horak": "Horák",
    "Horakova": "Horáková",
    "Nemecek": "Němeček",
    "Nemeckova": "Němečková",
    "Kucera": "Kučera",
    "Kucerova": "Kučerová",
    "Urbanek": "Urbánek",
    "Vlcek": "Vlček",
    "Vlcekova": "Vlčeková",
    "Blazek": "Blažek",
    "Blazekova": "Blažeková",
    "Hajek": "Hájek",
    "Hajkova": "Hájková",
    "Macek": "Macek",
    "Mach": "Mach",
    "Machova": "Machová",
    "Ruzicka": "Růžička",
    "Ruzickova": "Růžičková",
    "Sedlacek": "Sedláček",
    "Sedlackova": "Sedláčková",
    "Spacek": "Špaček",
    "Spackova": "Špačková",
    "Stastny": "Šťastný",
    "Stastna": "Šťastná",
    "Cerny": "Černý",
    "Cerna": "Černá",
    "Cervenka": "Červenka",
    "Cervenkova": "Červenková",
    "Dolezal": "Doležal",
    "Dolezalova": "Doležalová",
    "Fiala": "Fiala",
    "Fialova": "Fialová",
    "Jelinek": "Jelínek",
    "Jelinkova": "Jelínková",
    "Kadlec": "Kadlec",
    "Kadlecova": "Kadlecová",
    "Kovar": "Kovář",
    "Kovarova": "Kovářová",
    "Kralik": "Králik",
    "Kralikova": "Králiková",
    "Kral": "Kráľ",
    "Kralova": "Kráľová",
    "Maly": "Malý",
    "Mala": "Malá",
    "Masek": "Mašek",
    "Maskova": "Mašková",
    "Medved": "Medveď",
    "Medvedova": "Medveďová",
    "Mucha": "Mucha",
    "Muchova": "Muchová",
    "Navratil": "Navrátil",
    "Navratilova": "Navrátilová",
    "Orsag": "Oršág",
    "Palka": "Pálka",
    "Pavlik": "Pavlík",
    "Pavlikova": "Pavlíková",
    "Pesek": "Pešek",
    "Pesekova": "Pešeková",
    "Plasek": "Plášek",
    "Prochazka": "Procházka",
    "Prochazkova": "Procházková",
    "Reznik": "Řezník",
    "Reznikova": "Řezníková",
    "Safarik": "Šafárik",
    "Safarikova": "Šafáriková",
    "Simek": "Šimek",
    "Simekova": "Šimeková",
    "Sladek": "Sládek",
    "Sladkova": "Sládková",
    "Sokol": "Sokol",
    "Sokolova": "Sokolová",
    "Stefanik": "Štefánik",
    "Stefanikova": "Štefániková",
    "Straka": "Straka",
    "Strakova": "Straková",
    "Sykora": "Sýkora",
    "Sykorova": "Sýkorová",
    "Tucek": "Tuček",
    "Tuckova": "Tučková",
    "Vaculik": "Vaculík",
    "Vesely": "Veselý",
    "Vesela": "Veselá",
    "Vlach": "Vlach",
    "Vlachova": "Vlachová",
    "Vondracek": "Vondráček",
    "Vondráckova": "Vondráčková",
    "Zak": "Žák",
    "Zakova": "Žáková",
    "Zeman": "Zeman",
    "Zemanova": "Zemanová",
    "Adamec": "Adamec",
    "Baran": "Barán",
    "Baranova": "Baránová",
    "Benovic": "Benovič",
    "Bielik": "Bielik",
    "Blaho": "Blaho",
    "Bohac": "Boháč",
    "Bohacova": "Boháčová",
    "Bukovec": "Bukovec",
    "Capek": "Čapek",
    "Capkova": "Čapková",
    "Celko": "Celko",
    "Cisarik": "Cisárik",
    "Cizmadia": "Čižmádia",
    "Danko": "Danko",
    "Drgon": "Drgoň",
    "Duris": "Ďuriš",
    "Durovic": "Ďurovič",
    "Fabian": "Fabián",
    "Gajdos": "Gajdoš",
    "Gajdosova": "Gajdošová",
    "Gavlas": "Gavlaš",
    "Halasik": "Halašík",
    "Hrbek": "Hrbek",
    "Hrnciar": "Hrnčiar",
    "Hrnko": "Hrnko",
    "Huba": "Huba",
    "Husak": "Husák",
    "Chovanec": "Chovanec",
    "Ivanovic": "Ivanovič",
    "Jancek": "Janček",
    "Jancovic": "Jančovič",
    "Janosik": "Jánošík",
    "Janosikova": "Jánošíková",
    "Kohut": "Kohút",
    "Kohutova": "Kohútová",
    "Kolar": "Kolár",
    "Kolarova": "Kolárová",
    "Kolesik": "Kolesík",
    "Konecny": "Konečný",
    "Konecna": "Konečná",
    "Koscak": "Koščák",
    "Kosc": "Košč",
    "Kubik": "Kubík",
    "Kubikova": "Kubíková",
    "Lacko": "Lacko",
    "Laciak": "Laciak",
    "Lajcak": "Lajčák",
    "Langos": "Langoš",
    "Liska": "Liška",
    "Liskova": "Lišková",
    "Luptak": "Lupták",
    "Luptakova": "Luptáková",
    "Machacek": "Macháček",
    "Majer": "Majer",
    "Majerova": "Majerová",
    "Majtan": "Majtán",
    "Mazur": "Mazúr",
    "Mikula": "Mikula",
    "Mikulova": "Mikulová",
    "Mikulik": "Mikulík",
    "Minarik": "Minárik",
    "Misik": "Mišík",
    "Moravcik": "Moravčík",
    "Ondrus": "Ondruš",
    "Ondrusova": "Ondrušová",
    "Palovic": "Palovič",
    "Pavelka": "Pavelka",
    "Petras": "Petraš",
    "Pilar": "Pilár",
    "Pilkova": "Pilková",
    "Pokorny": "Pokorný",
    "Pokorna": "Pokorná",
    "Rabek": "Rabek",
    "Richtar": "Richtár",
    "Rusnak": "Rusnák",
    "Rusnakova": "Rusnáková",
    "Sedlak": "Sedlák",
    "Sedlakova": "Sedláková",
    "Siska": "Šiška",
    "Siskova": "Šišková",
    "Slezak": "Slezák",
    "Slezakova": "Slezáková",
    "Smid": "Šmid",
    "Smidova": "Šmidová",
    "Soltes": "Šoltes",
    "Stoklasa": "Stoklasa",
    "Strba": "Štrba",
    "Strbova": "Štrbová",
    "Svec": "Švec",
    "Svecova": "Švecová",
    "Tkac": "Tkáč",
    "Tkacova": "Tkáčová",
    "Tokos": "Tokoš",
    "Turcan": "Turčan",
    "Uhlar": "Uhlár",
    "Urban": "Urban",
    "Urbanova": "Urbanová",
    "Valent": "Valent",
    "Valentova": "Valentová",
    "Vanko": "Vaňko",
    "Vankova": "Vaňková",
    "Vlk": "Vlk",
    "Zavodny": "Závodný",
    "Zavodna": "Závodná",
    "Zelinka": "Zelinka",
    "Zelinkova": "Zelinková",
    "Zilka": "Žilka",
    "Zilkova": "Žilková",
    "Zubek": "Žubek",
})

# Surname patterns: ASCII suffix → diacritical suffix
# Applied when exact match not in dictionary
SURNAME_SUFFIX_PATTERNS = _load_table("surname_suffixes", {
    # -ak → -ák  (very common)
    "ak": "ák",
    "akova": "áková",
    # -ik → -ík
    "ik": "ík",
    "ikova": "íková",
    # -ar → -ár
    "ar": "ár",
    "arova": "árová",
    # -an → -án (only some)
    # careful — too many false positives
    # -ek usually stays -ek (not -ék)
    # -cek → -ček
    "cek": "ček",
    "ckova": "čková",
    # -sek → -šek
    "sek": "šek",
    "skova": "šková",
})

# ── Activity Tagging ──────────────────────────────────────────────────────
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
ACTIVITY_SCOPES = GMAIL_SCOPES + CALENDAR_SCOPES

ACTIVITY_ACCOUNTS = json.loads(os.getenv("ACTIVITY_ACCOUNTS", "[]")) or [
    {"email": os.getenv("OWNER_EMAIL_PERSONAL", ""), "token_file": "token_personal.json", "secret_name": None},
    {"email": os.getenv("OWNER_EMAIL_WORK", ""), "token_file": "token_work.json", "secret_name": "work-gmail-refresh-token"},
]

# Owner emails — used to detect when the authenticated user's email
# incorrectly appears on other contacts (e.g. copy-paste errors)
OWNER_EMAILS = {acct["email"].lower() for acct in ACTIVITY_ACCOUNTS}

INTERACTIONS_CACHE = DATA_DIR / "interactions_cache.json"
RESCAN_INTERVAL_DAYS = 7  # Skip emails scanned within this many days
GMAIL_RATE_LIMIT = 2500   # Gmail API requests per minute (generous)
CALENDAR_EVENTS_SINCE = "2015-01-01T00:00:00Z"  # Fetch events since this date

# Year labels
ACTIVITY_LABEL_PREFIX = "Y"  # e.g. Y2025, Y2024
NEVER_IN_TOUCH_LABEL = "Never in touch"

# LTNS (Long Time No See) — reconnect feature
LTNS_MIN_INTERACTIONS = 2         # Min personal interactions to qualify
LTNS_MONTHS_THRESHOLD = 12       # No contact for this many months
LTNS_TOP_N = 50                  # Top N candidates for reconnect list
LTNS_GROUP_NAME = "LTNS"         # Contact group name
LTNS_NOTE_MARKER = "── Reconnect Prompt"

# ── FollowUp Scoring ─────────────────────────────────────────────────────
FOLLOWUP_TOP_N = 200
FOLLOWUP_MIN_INTERACTIONS = 1          # Lower than LTNS — LinkedIn signals compensate
FOLLOWUP_MIN_MONTHS = 6               # Lower than LTNS (12) — more proactive
FOLLOWUP_GROUP_NAME = "FollowUp"
FOLLOWUP_NOTE_MARKER = "── FollowUp Prompt"
FOLLOWUP_SCORES_FILE = DATA_DIR / "followup_scores.json"
FOLLOWUP_LINKEDIN_WEIGHTS: dict[str, float] = {
    "job_change": 30.0,
    "active":     10.0,
    "profile":     3.0,
    "no_activity": 0.0,
}
FOLLOWUP_COMPLETENESS_WEIGHT = 2.0    # Per completeness point (0-4 scale)
FOLLOWUP_MAX_MONTHS_CONTRIBUTION = 24.0   # Cap gap-aging bonus; 7-year silence is not 3.5× more actionable than 2-year
FOLLOWUP_EXEC_TITLE_BONUS = 15.0          # Bonus for C-level/founder/director titles
FOLLOWUP_PERSONAL_PENALTY = 0.3           # Multiplier when no org, no title, no LinkedIn (likely personal)
FOLLOWUP_MIN_JOB_CHANGE_HEADLINE_LEN = 15 # "Oh yeah" (7 chars) should not count as a job_change signal
FOLLOWUP_EXEC_TITLE_KEYWORDS = (
    "ceo", "cto", "cfo", "coo", "cmo", "cpo", "ciso", "cro",
    "founder", "co-founder", "cofounder", "owner", "president",
    "managing director", "managing partner", "partner",
    "head of", "vp ", "vice president", "director",
    "zakladatel", "majitel", "spolumajitel", "riaditel", "konatel",  # SK
)
FOLLOWUP_PERSONAL_EMAIL_DOMAINS = (
    "gmail.com", "hotmail.com", "yahoo.com", "icloud.com", "me.com",
    "zoznam.sk", "azet.sk", "pobox.sk", "centrum.sk", "centrum.cz",
    "seznam.cz", "post.cz", "email.cz",
)
FOLLOWUP_OWN_COMPANY_DOMAINS = ("instarea.com", "instarea.sk")  # Peter's own — exclude from lead digest
FOLLOWUP_OWN_COMPANY_ORG_KEYWORDS = ("instarea",)
FOLLOWUP_MAX_AGE_MONTHS = 60.0  # Drop contacts whose last interaction is older than 5 years

# ── CRM Tag Sync (#172) ──────────────────────────────────────────────────
# Controls how crm_sync.sync_tags routes raw CRM tags to Google contact groups.
# Default (post-#172): reuse Peter's existing bare labels ("IS", "TB", "Ďatelinka")
# instead of creating parallel CRM:* duplicates. See #172 for full rationale.
CRM_TAG_USE_PREFIX = os.getenv("CRM_TAG_USE_PREFIX", "false").lower() in ("1", "true", "yes")
CRM_TAG_PREFIX_STRING = "CRM:"  # only used if CRM_TAG_USE_PREFIX is true
CRM_TAG_FUZZY_THRESHOLD = 90    # rapidfuzz token_sort_ratio: >= this → reuse existing group
# Canonical alias map: raw tag (lowercased, ASCII-folded) → existing Google label
# Extend here as Peter establishes more shorthand conventions.
CRM_TAG_ALIASES: dict[str, str] = {
    "instarea": "IS",
    "datelinka": "Ďatelinka",
}

# ── Multi-channel Beeper scoring (#150) ─────────────────────────────────────
# Weights applied to ContactKPI rollups computed by harvester/scoring_signals.py
# from data/interactions/*.jsonl. See docs/schemas/scoring-signals.md for derivation.
# Cap FOLLOWUP_BEEPER_MAX protects long-term LinkedIn context from being
# drowned by short-term message activity.
FOLLOWUP_BEEPER_AWAITING_MY_REPLY = 15.0      # They wrote me, I haven't replied — most actionable
FOLLOWUP_BEEPER_MULTICHANNEL = 10.0           # ≥2 distinct channels active in 30d
FOLLOWUP_BEEPER_BUSINESS_KEYWORDS = 20.0      # "meeting"|"demo"|"price"|"proposal"|… hit in 30d
FOLLOWUP_BEEPER_BUSINESS_HOURS = 5.0          # business-hours ratio ≥ 0.7 in 30d
FOLLOWUP_BEEPER_INBOUND_HEAVY = 8.0           # (in - out) > 5 in 30d — they want something
FOLLOWUP_BEEPER_STALE_SENT_PENALTY = -10.0    # I'm spamming without reply (3+ stale sent msgs)
FOLLOWUP_BEEPER_LONG_SILENCE_PENALTY = -15.0  # Last inbound > 180d
FOLLOWUP_BEEPER_MAX = 40.0                    # Upper cap on total beeper_bonus
FOLLOWUP_BEEPER_MIN = -20.0                   # Lower cap on total beeper_bonus
FOLLOWUP_BEEPER_KPI_FILE = DATA_DIR / "interactions" / "contact_kpis.json"
FOLLOWUP_BEEPER_BUSINESS_HOURS_TZ = "Europe/Bratislava"
FOLLOWUP_BEEPER_BUSINESS_HOURS_START = 9       # 09:00 local
FOLLOWUP_BEEPER_BUSINESS_HOURS_END = 18        # 18:00 local
FOLLOWUP_BEEPER_STALE_SENT_DAYS = 7            # Outbound counted as "stale" after N days without reply
FOLLOWUP_BEEPER_LONG_SILENCE_DAYS = 180        # Trigger FOLLOWUP_BEEPER_LONG_SILENCE_PENALTY
FOLLOWUP_BEEPER_BUSINESS_HOURS_RATIO = 0.7     # Trigger FOLLOWUP_BEEPER_BUSINESS_HOURS weight
FOLLOWUP_BEEPER_INBOUND_HEAVY_DELTA = 5        # (msgs_in - msgs_out) threshold in 30d
FOLLOWUP_BEEPER_STALE_SENT_MIN_COUNT = 3       # Trigger stale-sent penalty
FOLLOWUP_BEEPER_BUSINESS_KEYWORDS_LIST = (
    "meeting", "demo", "price", "pricing", "proposal", "quote",
    "invoice", "contract", "payment", "deal", "kickoff", "timeline",
    "scope", "sow", "rfp", "po", "offer", "agreement",
)

# ── PSČ Patterns ───────────────────────────────────────────────────────────
# Slovak PSČ: 0xxxx, 8xxxx, 9xxxx
# Czech PSČ: 1xxxx-7xxxx
SK_PSC_RANGES = range(0, 10)  # first digit 0-9, but mostly 0,8,9
CZ_PSC_RANGES = range(1, 8)   # first digit 1-7
