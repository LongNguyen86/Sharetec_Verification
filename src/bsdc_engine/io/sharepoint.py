import os
import re
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

from src.bsdc_engine.config import settings
from src.bsdc_engine.errors import SharePointAuthError
from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.text import clean_sharepoint_path

logger = get_logger(__name__)


class SharePointClient:
    def __init__(self):
        self.site_url = settings.SHAREPOINT_SITE_URL
        self.auth_dir = settings.WORKSPACE_DIR / ".auth"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.auth_dir / "state.json"

    def _cleanup_expired_session(self, reason: str = ""):
        if self.session_file.exists():
            try:
                self.session_file.unlink()
                logger.info(f"Auto-deleted expired session file (state.json). {reason}")
            except Exception as e:
                logger.error(f"Failed to delete state.json: {e}")

    def _ensure_authenticated(self, p):
        if self.session_file.exists():
            return

        logger.info("No valid Session found! Opening browser for SharePoint login...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(self.site_url)
        logger.info("PLEASE LOG IN AND COMPLETE 2FA AUTHENTICATION ON YOUR PHONE (Max 2 mins)...")

        try:
            page.wait_for_url(re.compile(r".*sharepoint\.com.*", re.IGNORECASE), timeout=120000)
            page.wait_for_timeout(3000)
            context.storage_state(path=str(self.session_file))
            logger.info("Authentication successful & saved new Session to workspace/.auth/state.json!")
        except Exception:
            self._cleanup_expired_session("Timeout during login process.")
            raise SharePointAuthError("Exceeded 2 minutes without completing login/2FA on phone!")
        finally:
            browser.close()

    def _check_html_response(self, content_bytes: bytes, file_name: str):
        content_head = content_bytes[:500].decode("utf-8", errors="ignore").lower()
        if "<!doctype html" in content_head or "<html" in content_head:
            self._cleanup_expired_session("SharePoint returned HTML login page instead of data.")
            raise SharePointAuthError(f"Session Expired while fetching [{file_name}]. Old session deleted.")

    def download_file_by_path(self, server_relative_url: str, output_dir: Path) -> Path | None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        file_name = Path(server_relative_url).name

        with sync_playwright() as p:
            self._ensure_authenticated(p)
            request_context = p.request.new_context(storage_state=str(self.session_file))

            encoded_url = urllib.parse.quote(server_relative_url)
            api_endpoint = f"{self.site_url}/_api/web/getfilebyserverrelativeurl('{encoded_url}')/$value"

            response = request_context.get(api_endpoint, headers={"Accept": "application/json;odata=verbose"}, timeout=300000)

            if response.status == 200:
                file_bytes = response.body()
                self._check_html_response(file_bytes, file_name)
                dest_file = output_dir / file_name
                dest_file.write_bytes(file_bytes)
                logger.info(f"Successfully downloaded file: {file_name}")
                return dest_file
            else:
                if response.status in [401, 403]:
                    self._cleanup_expired_session(f"API returned HTTP {response.status}.")
                return None

    def download_folder(self, folder_relative_path: str, output_dir: Path) -> list[Path]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            self._ensure_authenticated(p)
            request_context = p.request.new_context(storage_state=str(self.session_file))

            encoded_folder = urllib.parse.quote(folder_relative_path)
            api_endpoint = f"{self.site_url}/_api/web/getfolderbyserverrelativeurl('{encoded_folder}')/files"

            response = request_context.get(api_endpoint, headers={"Accept": "application/json;odata=verbose"})

            if response.status != 200:
                if response.status in [401, 403]:
                    self._cleanup_expired_session(f"API returned HTTP {response.status}.")
                return []

            body_bytes = response.body()
            self._check_html_response(body_bytes, folder_relative_path)

            try:
                data = response.json()
            except Exception:
                self._cleanup_expired_session("Invalid JSON payload received.")
                return []

            files_list = data.get("d", {}).get("results", [])
            downloaded_files = []
            for file_info in files_list:
                f_name = file_info["Name"]
                f_encoded = urllib.parse.quote(file_info["ServerRelativeUrl"])
                file_val_url = f"{self.site_url}/_api/web/getfilebyserverrelativeurl('{f_encoded}')/$value"

                file_resp = request_context.get(file_val_url, timeout=300000)
                if file_resp.status == 200:
                    f_bytes = file_resp.body()
                    self._check_html_response(f_bytes, f_name)
                    dest_path = output_dir / f_name
                    dest_path.write_bytes(f_bytes)
                    downloaded_files.append(dest_path)
                elif file_resp.status in [401, 403]:
                    self._cleanup_expired_session(f"File download returned HTTP {file_resp.status}.")

            return downloaded_files

    def fetch_paths(self, raw_paths: list[str], output_dir: Path) -> list[Path]:
        all_paths = []
        for item in raw_paths:
            split_items = [clean_sharepoint_path(p) for p in item.replace(',', ';').split(';') if p.strip()]
            all_paths.extend([p for p in split_items if p])

        total_downloaded = []
        for p in all_paths:
            file_name = os.path.basename(p.rstrip('/'))
            if "." in file_name:
                dl = self.download_file_by_path(p, output_dir)
                if dl: total_downloaded.append(dl)
            else:
                dls = self.download_folder(p, output_dir)
                total_downloaded.extend(dls)

        return total_downloaded