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

    print("🔑 Loading token from Secret Manager...")
    response = client.access_secret_version(request={"name": name})
    token_data = json.loads(response.payload.data.decode())
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        print("🔄 Refreshing token...")
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
        print("🔄 Refreshing token...")
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"⚠️  Failed to refresh token: {e}")
            creds = None

    if not creds or not creds.valid:
        if not CREDENTIALS_FILE or not CREDENTIALS_FILE.exists():
            print(f"❌ File {CREDENTIALS_FILE} does not exist!")
            print()
            print("How to get credentials.json:")
            print("1. Go to https://console.cloud.google.com/apis/credentials")
            print("2. Create OAuth 2.0 Client ID (type: Desktop application)")
            print("3. Download JSON and save as credentials.json in the project directory")
            print("4. Enable People API: https://console.cloud.google.com/apis/library/people.googleapis.com")
            sys.exit(1)

        print("🔐 Opening browser for Google authorization...")
        print("   (If browser doesn't open, copy the URL from terminal)")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE), SCOPES
        )
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            success_message="✅ Authorization successful! You can close this window.",
        )

        # Save token for next time
        TOKEN_FILE.write_text(creds.to_json())
        TOKEN_FILE.chmod(0o600)
        print(f"✅ Token saved to {TOKEN_FILE}")

    return creds


def test_connection(creds: Credentials) -> bool:
    """
    Test the connection by fetching a few contacts.
    Returns True if successful.
    """
    from googleapiclient.discovery import build

    print("🧪 Testing connection to Google People API...")

    try:
        service = build("people", "v1", credentials=creds)
        result = service.people().connections().list(
            resourceName="people/me",
            pageSize=5,
            personFields="names,emailAddresses",
        ).execute()

        connections = result.get("connections", [])
        total = result.get("totalPeople", 0) or result.get("totalItems", 0) or len(connections)

        print(f"✅ Connection works! Total contacts: ~{total}")
        print()

        if connections:
            print("First 5 contacts (sanity check):")
            for i, person in enumerate(connections[:5], 1):
                names = person.get("names", [{}])
                name = names[0].get("displayName", "(no name)") if names else "(no name)"
                emails = person.get("emailAddresses", [])
                email = emails[0].get("value", "") if emails else ""
                print(f"  {i}. {name} {f'<{email}>' if email else ''}")
            print()

        return True

    except Exception as e:
        print(f"❌ Error during testing: {e}")
        return False


def authenticate_for_activity(account_email: str, force_new: bool = False) -> Credentials:
    """
    Authenticate for Gmail/Calendar access (activity tagging).

    Uses separate token files per account to avoid interfering with
    the existing contacts pipeline auth.

    Args:
        account_email: The Google account email to authenticate.
        force_new: If True, ignore existing token and re-authenticate.

    Returns:
        Valid Google OAuth2 Credentials with gmail.readonly + calendar.readonly.
    """
    from config import ACTIVITY_SCOPES, ACTIVITY_ACCOUNTS, BASE_DIR

    # Find account config
    account = None
    for acct in ACTIVITY_ACCOUNTS:
        if acct["email"] == account_email:
            account = acct
            break
    if not account:
        raise ValueError(f"Unknown activity account: {account_email}")

    if ENVIRONMENT == "cloud":
        return _get_activity_credentials_cloud(account)

    token_path = BASE_DIR / account["token_file"]
    creds = None

    if not force_new and token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), ACTIVITY_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        print(f"🔄 Refreshing activity token for {account_email}...")
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"⚠️  Failed to refresh token: {e}")
            creds = None

    if not creds or not creds.valid:
        if not CREDENTIALS_FILE or not CREDENTIALS_FILE.exists():
            print(f"❌ File {CREDENTIALS_FILE} does not exist!")
            sys.exit(1)

        print(f"🔐 Opening browser for {account_email} (Gmail + Calendar)...")
        print(f"   Sign in as: {account_email}")
        print()

        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE), ACTIVITY_SCOPES
        )
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            login_hint=account_email,
            success_message="✅ Authorization successful! You can close this window.",
        )

        token_path.write_text(creds.to_json())
        token_path.chmod(0o600)
        print(f"✅ Token saved to {token_path}")

    return creds


def _get_activity_credentials_cloud(account: dict) -> Credentials:
    """Load activity credentials from Secret Manager (cloud mode)."""
    from config import ACTIVITY_SCOPES

    secret_name = account.get("secret_name")
    if not secret_name:
        # Personal account uses existing contacts-refresh-token secret
        # but with different scopes — needs separate secret
        secret_name = f"activity-token-{account['email'].split('@')[0]}"

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT}/secrets/{secret_name}/versions/latest"

    print(f"🔑 Loading activity token for {account['email']}...")
    response = client.access_secret_version(request={"name": name})
    token_data = json.loads(response.payload.data.decode())
    creds = Credentials.from_authorized_user_info(token_data, ACTIVITY_SCOPES)

    if creds.expired and creds.refresh_token:
        print("🔄 Refreshing token...")
        creds.refresh(Request())

    return creds


if __name__ == "__main__":
    creds = authenticate()
    test_connection(creds)
