"""
OAuth2 authentication for Google People API.

Local: Opens Chrome for the consent screen, saves token for reuse.
Cloud: Reads refresh token from Secret Manager.
"""
import json
import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import SCOPES, CREDENTIALS_FILE, TOKEN_FILE, ENVIRONMENT, GCP_PROJECT


def _get_credentials_cloud() -> Credentials:
    """
    Load OAuth2 credentials from Secret Manager (cloud mode).

    Reads the refresh token stored as JSON in the 'contacts-refresh-token' secret.
    """
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT}/secrets/contacts-refresh-token/versions/latest"

    print("🔑 Načítavam token zo Secret Manager...")
    response = client.access_secret_version(request={"name": name})
    token_data = json.loads(response.payload.data.decode())
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        print("🔄 Obnovujem token...")
        creds.refresh(Request())

    return creds


def authenticate(force_new: bool = False) -> Credentials:
    """
    Authenticate with Google using OAuth2.

    Cloud mode: Load from Secret Manager (no browser flow).
    Local mode:
        1. If token.json exists and is valid, reuse it.
        2. If token is expired, refresh it.
        3. Otherwise, open Chrome for consent screen.

    Args:
        force_new: If True, ignore existing token and re-authenticate.

    Returns:
        Valid Google OAuth2 Credentials.
    """
    if ENVIRONMENT == "cloud":
        return _get_credentials_cloud()

    creds = None

    # Try loading existing token
    if not force_new and TOKEN_FILE and TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # Refresh or re-authenticate
    if creds and creds.expired and creds.refresh_token:
        print("🔄 Obnovujem token...")
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"⚠️  Nepodarilo sa obnoviť token: {e}")
            creds = None

    if not creds or not creds.valid:
        if not CREDENTIALS_FILE or not CREDENTIALS_FILE.exists():
            print(f"❌ Súbor {CREDENTIALS_FILE} neexistuje!")
            print()
            print("Postup na získanie credentials.json:")
            print("1. Choď na https://console.cloud.google.com/apis/credentials")
            print("2. Vytvor OAuth 2.0 Client ID (typ: Desktop application)")
            print("3. Stiahni JSON a ulož ako credentials.json do priečinka projektu")
            print("4. Zapni People API: https://console.cloud.google.com/apis/library/people.googleapis.com")
            sys.exit(1)

        print("🔐 Otváram prehliadač pre Google autorizáciu...")
        print("   (Ak sa prehliadač neotvorí, skopíruj URL z terminálu)")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE), SCOPES
        )
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            success_message="✅ Autorizácia úspešná! Môžeš zavrieť toto okno.",
        )

        # Save token for next time
        TOKEN_FILE.write_text(creds.to_json())
        print(f"✅ Token uložený do {TOKEN_FILE}")

    return creds


def test_connection(creds: Credentials) -> bool:
    """
    Test the connection by fetching a few contacts.
    Returns True if successful.
    """
    from googleapiclient.discovery import build

    print("🧪 Testujem pripojenie k Google People API...")

    try:
        service = build("people", "v1", credentials=creds)
        result = service.people().connections().list(
            resourceName="people/me",
            pageSize=5,
            personFields="names,emailAddresses",
        ).execute()

        connections = result.get("connections", [])
        total = result.get("totalPeople", 0) or result.get("totalItems", 0) or len(connections)

        print(f"✅ Pripojenie funguje! Celkom kontaktov: ~{total}")
        print()

        if connections:
            print("Prvých 5 kontaktov (sanity check):")
            for i, person in enumerate(connections[:5], 1):
                names = person.get("names", [{}])
                name = names[0].get("displayName", "(bez mena)") if names else "(bez mena)"
                emails = person.get("emailAddresses", [])
                email = emails[0].get("value", "") if emails else ""
                print(f"  {i}. {name} {f'<{email}>' if email else ''}")
            print()

        return True

    except Exception as e:
        print(f"❌ Chyba pri testovaní: {e}")
        return False


if __name__ == "__main__":
    creds = authenticate()
    test_connection(creds)
