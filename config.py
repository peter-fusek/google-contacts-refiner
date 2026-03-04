"""
Configuration and constants for Google Contacts Cleanup tool.
"""
import os
from pathlib import Path

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
UPDATE_PERSON_FIELDS = ",".join([
    "names", "emailAddresses", "phoneNumbers", "addresses",
    "organizations", "biographies", "birthdays", "events",
    "externalIds", "nicknames", "occupations", "relations",
    "urls", "userDefined", "memberships",
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
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")
AI_CONFIDENCE_REVIEW_THRESHOLD = 0.90  # Send to AI if below this
AI_MAX_CONTACTS_PER_BATCH = 10         # Contacts per AI API call
AI_COST_LIMIT_PER_SESSION = 1.00       # USD safety cap

# ── Auto Mode ─────────────────────────────────────────────────────────
AUTO_CONFIDENCE_THRESHOLD = 0.90       # Min confidence for auto-apply
AUTO_MAX_CHANGES_PER_RUN = 200         # Safety limit for auto-mode

# ── Phone Number Defaults ──────────────────────────────────────────────────
DEFAULT_REGION = "SK"
SUPPORTED_REGIONS = ["SK", "CZ"]

# SK mobile prefixes (after country code)
SK_MOBILE_PREFIXES = ["90", "91", "92", "93", "94", "95"]
CZ_MOBILE_PREFIXES = ["60", "70", "72", "73", "77", "78"]

# ── Email Domains ──────────────────────────────────────────────────────────
# Free/personal email providers (not corporate)
FREE_EMAIL_DOMAINS = {
    # Slovak
    "gmail.com", "googlemail.com", "azet.sk", "post.sk",
    "zoznam.sk", "centrum.sk", "stonline.sk", "pobox.sk",
    "mail.t-com.sk", "t-com.sk", "orangemail.sk",
    # Czech
    "seznam.cz", "email.cz", "centrum.cz", "volny.cz",
    "atlas.cz", "quick.cz", "tiscali.cz",
    # International
    "yahoo.com", "yahoo.co.uk", "hotmail.com", "outlook.com",
    "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "protonmail.com", "proton.me", "tutanota.com",
    "mail.com", "gmx.com", "gmx.de", "web.de", "yandex.com",
    "zoho.com", "fastmail.com",
}

# ── Name Prefixes (Titles) ────────────────────────────────────────────────
NAME_PREFIXES = [
    "Ing.", "Mgr.", "Bc.", "MUDr.", "JUDr.", "PhDr.", "RNDr.",
    "PaedDr.", "ThDr.", "MVDr.", "PhD.", "CSc.", "DrSc.",
    "Doc.", "Prof.", "Dr.", "MBA", "MSc.", "BSc.",
    "Ing. arch.", "ThLic.", "ArtD.",
]

# ── SK/CZ Diacritics Dictionary ───────────────────────────────────────────
# Maps ASCII versions to proper diacritical forms.
# Covers given names, surnames, and common patterns.
SK_CZ_NAMES_DIACRITICS = {
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
    "Dusan": "Dušan",
    "Lubos": "Ľuboš",
    "Luboslav": "Ľuboslav",
    "Lubomir": "Ľubomír",
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
    "Marian": "Marián",
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
    "Lubos": "Ľuboš",
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
    "Nikolas": "Nikolás",
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
    "Ruzena": "Ružena",
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
}

# Surname patterns: ASCII suffix → diacritical suffix
# Applied when exact match not in dictionary
SURNAME_SUFFIX_PATTERNS = {
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
}

# ── PSČ Patterns ───────────────────────────────────────────────────────────
# Slovak PSČ: 0xxxx, 8xxxx, 9xxxx
# Czech PSČ: 1xxxx-7xxxx
SK_PSC_RANGES = range(0, 10)  # first digit 0-9, but mostly 0,8,9
CZ_PSC_RANGES = range(1, 8)   # first digit 1-7
