# Google Contacts Refiner

Automatizovaný nástroj na čistenie a opravu Google Kontaktov s dôrazom na slovenské a české mená, diakritiku, telefónne čísla a detekciu duplikátov.

## Funkcie

- **Diakritika** — automatická oprava slovenských/českých mien (600+ slovníkových záznamov)
- **Telefónne čísla** — normalizácia do medzinárodného formátu (+421, +420, ...)
- **Emaily** — validácia a normalizácia (lowercase, formát)
- **Adresy** — PSČ formátovanie, detekcia krajiny
- **Organizácie** — zjednotenie názvov
- **Duplikáty** — fuzzy detekcia podľa mena, telefónu a emailu
- **Obohatenie** — extrakcia štruktúrovaných dát z poznámok a biografií
- **Zálohy** — plný backup/restore pred akýmikoľvek zmenami
- **Rollback** — možnosť vrátiť zmeny pomocou changelogu

## Inštalácia

```bash
# Klonovanie repozitára
git clone https://github.com/YOUR_USERNAME/google-contacts-refiner.git
cd google-contacts-refiner

# Virtuálne prostredie
python3 -m venv .venv
source .venv/bin/activate

# Závislosti
pip install -r requirements.txt
```

### Google API Credentials

1. Choď na [Google Cloud Console](https://console.cloud.google.com/)
2. Vytvor projekt a povol **People API**
3. Vytvor OAuth 2.0 credentials (Desktop App)
4. Stiahni `credentials.json` do koreňa projektu

## Použitie

```bash
python main.py auth         # Autentifikácia a test spojenia
python main.py backup       # Vytvorenie zálohy kontaktov
python main.py analyze      # Analýza kontaktov a generovanie plánu opráv
python main.py fix          # Aplikovanie opráv (interaktívne schvaľovanie po dávkach)
python main.py verify       # Overenie zmien oproti zálohe
python main.py rollback     # Vrátenie zmien z changelogu
python main.py resume       # Pokračovanie prerušenej relácie
python main.py info         # Zobrazenie info o relácii/zálohe/pláne
```

### Typický workflow

```bash
python main.py auth         # 1. Prihlásenie
python main.py backup       # 2. Záloha (vždy pred opravami!)
python main.py analyze      # 3. Analýza — nájde problémy, vytvorí plán
python main.py fix          # 4. Oprava — schvaľuješ po dávkach (~50 kontaktov)
python main.py verify       # 5. Overenie — porovná so zálohou
```

## Architektúra

```
main.py              CLI vstupný bod
├── auth.py          OAuth2 autentifikácia
├── api_client.py    Google People API wrapper (rate limiting, retry)
├── backup.py        Záloha/obnova kontaktov
├── analyzer.py      Orchestrácia analýzy
│   ├── normalizer.py    Normalizácia polí (diakritika, telefóny, emaily, adresy)
│   ├── enricher.py      Extrakcia dát z poznámok
│   ├── deduplicator.py  Detekcia duplikátov
│   └── labels_manager.py  Správa skupín/štítkov
├── workplan.py      Generovanie dávok na schválenie
├── batch_processor.py  Interaktívne spracovanie dávok
├── changelog.py     Sledovanie zmien (append-only JSONL)
├── recovery.py      Obnovenie po prerušení
├── config.py        Konfigurácia a konštanty
└── utils.py         Pomocné funkcie
```

## Systém dôveryhodnosti

Každá navrhovaná zmena má skóre dôveryhodnosti:
- **HIGH** (>= 0.90) — presné zhody, slovníkové vyhľadávanie
- **MEDIUM** (>= 0.60) — vzorové zhody, fuzzy matching
- **LOW** (< 0.60) — špekulatívne návrhy

## Dáta

Všetky runtime dáta sa ukladajú v `data/` (gitignored):
- `backup_TIMESTAMP.json` — plná záloha kontaktov
- `workplan_TIMESTAMP.json` — plán dávok na schválenie
- `changelog_TIMESTAMP.jsonl` — audit trail zmien
- `checkpoint.json` — stav aktuálnej relácie

## Licencia

Súkromný projekt.
