import json
import re
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright
from config import settings


class SharePointClient:
    def __init__(self):
        self.site_url = settings.SITE_URL
        self.auth_dir = settings.BASE_DIR / "workspace" / ".auth"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.auth_dir / "state.json"

    def _cleanup_expired_session(self, reason: str = ""):
        """Helper to automatically remove expired or invalid state.json session file."""
        if self.session_file.exists():
            try:
                self.session_file.unlink()
                print(f"🧹 Auto-deleted expired/invalid session file (state.json). {reason}")
            except Exception as e:
                print(f"⚠️ Failed to delete state.json: {e}")

    def _ensure_authenticated(self, p):
        """Check authentication session. Trigger browser login if session is missing."""
        if self.session_file.exists():
            print("🔑 Found saved Session, reusing...")
            return

        print("🌐 No valid Session found! Opening browser for SharePoint login...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(self.site_url)
        print("👉 PLEASE LOG IN AND COMPLETE 2FA AUTHENTICATION ON YOUR PHONE")
        print("⏳ System automatically waiting for your authentication (Max 2 minutes)...")

        try:
            page.wait_for_url(
                re.compile(r".*sharepoint\.com.*", re.IGNORECASE), timeout=120000
            )
            page.wait_for_timeout(3000)
            context.storage_state(path=str(self.session_file))
            print("✅ Authentication successful & saved new Session to workspace/.auth/state.json!")
        except Exception:
            self._cleanup_expired_session("Timeout during login process.")
            raise TimeoutError("❌ Exceeded 2 minutes without completing login/2FA on phone!")
        finally:
            browser.close()

    def _check_html_response(self, content_bytes: bytes, file_name: str):
        """Check if SharePoint returned HTML (expired session) -> Delete session and raise error"""
        content_head = content_bytes[:500].decode("utf-8", errors="ignore").lower()
        if "<!doctype html" in content_head or "<html" in content_head:
            self._cleanup_expired_session("SharePoint returned HTML login page instead of data.")
            raise PermissionError(
                f"\n❌ ERROR: SESSION EXPIRED! SharePoint refused to serve [{file_name}] and forced login.\n"
                f"💡 Old Session auto-deleted. Please RE-RUN the command on n8n to open browser again!"
            )

    def download_file_by_path(self, server_relative_url: str, output_dir: Path) -> Path:
        """Download a specific file from SharePoint using raw Playwright API Context"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        file_name = Path(server_relative_url).name

        print(f"📥 Downloading individual file via Playwright API:\n   👉 Path: {server_relative_url}")

        with sync_playwright() as p:
            self._ensure_authenticated(p)

            request_context = p.request.new_context(
                storage_state=str(self.session_file)
            )

            encoded_url = urllib.parse.quote(server_relative_url)
            api_endpoint = f"{self.site_url}/_api/web/getfilebyserverrelativeurl('{encoded_url}')/$value"

            headers = {"Accept": "application/json;odata=verbose"}
            response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            if response.status == 200:
                file_bytes = response.body()
                self._check_html_response(file_bytes, file_name)

                dest_file = output_dir / file_name
                dest_file.write_bytes(file_bytes)
                print(f"   ✅ Successfully downloaded individual file: {file_name}")
                return dest_file
            else:
                print(f"   ❌ SHAREPOINT API ERROR ({response.status}): Failed to download file.")
                if response.status in [401, 403]:
                    self._cleanup_expired_session(f"SharePoint API returned HTTP {response.status}.")
                return None

    def download_folder(self, folder_relative_path: str, output_dir: Path) -> list[Path]:
        """Download all files from a SharePoint folder"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"📂 Scanning file list in directory: [{folder_relative_path}]...")

        with sync_playwright() as p:
            self._ensure_authenticated(p)

            request_context = p.request.new_context(
                storage_state=str(self.session_file)
            )

            encoded_folder = urllib.parse.quote(folder_relative_path)
            api_endpoint = f"{self.site_url}/_api/web/getfolderbyserverrelativeurl('{encoded_folder}')/files"

            headers = {"Accept": "application/json;odata=verbose"}
            response = request_context.get(api_endpoint, headers=headers)

            if response.status != 200:
                print(f"❌ SHAREPOINT API ERROR: Status {response.status}")
                if response.status in [401, 403]:
                    self._cleanup_expired_session(f"SharePoint API returned HTTP {response.status}.")
                return []

            body_bytes = response.body()
            self._check_html_response(body_bytes, folder_relative_path)

            try:
                data = response.json()
            except Exception as e:
                print(f"❌ Failed to parse JSON from SharePoint response: {e}")
                self._cleanup_expired_session("Invalid JSON payload received.")
                return []

            files_list = data.get("d", {}).get("results", [])
            print(f"🎉 Found {len(files_list)} files in directory!")

            downloaded_files = []
            for file_info in files_list:
                f_name = file_info["Name"]
                f_rel_url = file_info["ServerRelativeUrl"]

                f_encoded = urllib.parse.quote(f_rel_url)
                file_val_url = f"{self.site_url}/_api/web/getfilebyserverrelativeurl('{f_encoded}')/$value"

                print(f"⬇️ Downloading: {f_name}...")
                file_resp = request_context.get(file_val_url, timeout=300000)

                if file_resp.status == 200:
                    f_bytes = file_resp.body()
                    self._check_html_response(f_bytes, f_name)

                    dest_path = output_dir / f_name
                    dest_path.write_bytes(f_bytes)
                    downloaded_files.append(dest_path)
                    print(f"   ✅ Saved file: {f_name}")
                else:
                    print(f"   ❌ Error downloading file {f_name}: Status {file_resp.status}")
                    if file_resp.status in [401, 403]:
                        self._cleanup_expired_session(f"File download returned HTTP {file_resp.status}.")

            return downloaded_files

    # Aliases for backward compatibility
    download_folder_by_path = download_folder
    download_file = download_file_by_path
