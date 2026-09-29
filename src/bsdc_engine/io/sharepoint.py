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

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".xls"}


class SharePointClient:
    def __init__(self):
        self.site_url = settings.SHAREPOINT_SITE_URL
        self.username = os.getenv("SHAREPOINT_USERNAME") or getattr(settings, "SHAREPOINT_USERNAME", None)
        self.password = os.getenv("SHAREPOINT_PASSWORD") or getattr(settings, "SHAREPOINT_PASSWORD", None)
        self.auth_dir = settings.WORKSPACE_DIR / ".auth"
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.session_file = self.auth_dir / "state.json"

    def _ensure_server_relative_url(self, path_str: str) -> str:
        """Ensure path starts with site server relative URL prefix."""
        clean_p = path_str.replace("\\", "/").strip("/")
        site_path = urllib.parse.urlparse(self.site_url).path.rstrip("/")
        if clean_p.startswith(site_path.lstrip("/")):
            return f"/{clean_p}"
        if clean_p.startswith("sites/"):
            return f"/{clean_p}"
        return f"{site_path}/{clean_p}"

    def _cleanup_expired_session(self, reason: str = ""):
        """Delete the expired state.json file from disk so a new session can be generated."""
        if self.session_file.exists():
            try:
                self.session_file.unlink()
                logger.info(f"Auto-deleted expired session file (state.json). Reason: {reason}")
            except Exception as e:
                logger.error(f"Failed to delete state.json: {e}")

    def _is_html_response(self, content_bytes: bytes) -> bool:
        """Check if response is HTML login page, indicating expired authentication."""
        content_head = content_bytes[:500].decode("utf-8", errors="ignore").lower()
        return "doctype" in content_head or "html" in content_head

    def _ensure_authenticated(self, p=None, force_reauth: bool = False):
        """Check if state.json exists. If missing or force_reauth is True, open browser for login and 2FA."""
        if self.session_file.exists() and not force_reauth:
            return

        logger.info("No valid session found or re-auth forced! Launching browser for SharePoint authentication...")

        def _login_flow(playwright_obj):
            browser = playwright_obj.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(self.site_url)

            # 1. Auto-fill Username/Email if prompt is visible
            try:
                email_input = page.wait_for_selector('input[type="email"], input[name="loginfmt"]', timeout=8000)
                if email_input and self.username:
                    logger.info("Auto-filling Username...")
                    email_input.fill(self.username)
                    page.click('input[type="submit"], #idSIButton9')
                    page.wait_for_timeout(2000)
            except Exception:
                logger.info("Username prompt skipped or already populated.")

            # 2. Auto-fill Password if prompt is visible
            try:
                password_input = page.wait_for_selector('input[type="password"], input[name="passwd"]', timeout=8000)
                if password_input and self.password:
                    logger.info("Auto-filling Password...")
                    password_input.fill(self.password)
                    page.click('input[type="submit"], #idSIButton9')
            except Exception:
                logger.info("Password prompt skipped.")

            logger.info("PLEASE APPROVE 2FA AUTHENTICATION ON YOUR PHONE (Max 2 minutes)...")

            try:
                page.wait_for_url(re.compile(r".*sharepoint\.com.*", re.IGNORECASE), timeout=120000)
                try:
                    stay_signed_in = page.query_selector("#idSIButton9")
                    if stay_signed_in:
                        stay_signed_in.click()
                except Exception:
                    pass

                page.wait_for_timeout(5000)
                context.storage_state(path=str(self.session_file))
                logger.info("Authentication successful! Saved new session to workspace/.auth/state.json")
            except Exception:
                self._cleanup_expired_session("Timeout during 2FA login process.")
                raise SharePointAuthError("Exceeded 2 minutes without completing login/2FA on phone!")
            finally:
                browser.close()

        if p is not None:
            _login_flow(p)
        else:
            with sync_playwright() as pw:
                _login_flow(pw)

    def download_file_by_path(self, server_relative_url: str, output_dir: Path) -> Path | None:
        output_dir = Path(output_dir)
        file_name = Path(server_relative_url).name
        ext = Path(file_name).suffix.lower()

        if file_name.startswith("~$") or ext not in ALLOWED_EXTENSIONS:
            logger.info(f"Skipping non-data file: {file_name}")
            return None

        full_server_rel = self._ensure_server_relative_url(server_relative_url)
        escaped_url = full_server_rel.replace("'", "''")
        encoded_url = urllib.parse.quote(escaped_url, safe='/$()')
        api_endpoint = f"{self.site_url}/_api/web/getfilebyserverrelativeurl('{encoded_url}')/$value"

        headers = {
            "Accept": "application/json;odata=verbose",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        with sync_playwright() as p:
            self._ensure_authenticated(p)
            request_context = p.request.new_context(storage_state=str(self.session_file))
            response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            # Auto trigger 2FA re-authentication if session expired
            if response.status in (401, 403) or self._is_html_response(response.body()):
                logger.warning(f"Session expired fetching [{file_name}]. Launching 2FA login automatically...")
                self._cleanup_expired_session("Expired session detected")
                self._ensure_authenticated(p, force_reauth=True)
                request_context = p.request.new_context(storage_state=str(self.session_file))
                response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            if response.status == 200:
                file_bytes = response.body()
                if self._is_html_response(file_bytes):
                    raise SharePointAuthError(f"Failed to fetch [{file_name}]: Received HTML after re-authentication.")
                dest_file = output_dir / file_name
                dest_file.write_bytes(file_bytes)
                logger.info(f"Successfully downloaded file: {file_name}")
                return dest_file
            else:
                logger.warning(f"Failed to download [{file_name}] (Status: {response.status})")
                return None

    def download_folder(self, folder_relative_path: str, output_dir: Path) -> list[Path]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        full_folder_path = self._ensure_server_relative_url(folder_relative_path)
        escaped_folder = full_folder_path.replace("'", "''")
        encoded_folder = urllib.parse.quote(escaped_folder, safe='/$()')
        api_endpoint = f"{self.site_url}/_api/web/getfolderbyserverrelativeurl('{encoded_folder}')/files"

        headers = {
            "Accept": "application/json;odata=verbose",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }

        with sync_playwright() as p:
            self._ensure_authenticated(p)
            request_context = p.request.new_context(storage_state=str(self.session_file))
            response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            # Auto trigger 2FA re-authentication if session expired
            if response.status in (401, 403) or self._is_html_response(response.body()):
                logger.warning(f"Session expired fetching folder [{folder_relative_path}]. Launching 2FA login automatically...")
                self._cleanup_expired_session("Expired session detected")
                self._ensure_authenticated(p, force_reauth=True)
                request_context = p.request.new_context(storage_state=str(self.session_file))
                response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            if response.status != 200:
                logger.warning(f"Failed to fetch folder [{folder_relative_path}]. Status: {response.status}")
                return []

            body_bytes = response.body()
            if self._is_html_response(body_bytes):
                return []

            try:
                data = response.json()
            except Exception:
                return []

            files_list = data.get("d", {}).get("results", [])
            downloaded_files = []
            for file_info in files_list:
                f_name = file_info.get("Name", "")
                ext = Path(f_name).suffix.lower()

                if f_name.startswith("~$") or ext not in ALLOWED_EXTENSIONS:
                    logger.info(f"Skipping non-data file inside folder: {f_name}")
                    continue

                unique_id = file_info.get("UniqueId")
                if unique_id:
                    file_val_url = f"{self.site_url}/_api/web/getfilebyid('{unique_id}')/$value"
                else:
                    f_server_url = file_info.get("ServerRelativeUrl", "")
                    escaped_file_url = f_server_url.replace("'", "''")
                    encoded_file_url = urllib.parse.quote(escaped_file_url, safe='/$()')
                    file_val_url = f"{self.site_url}/_api/web/getfilebyserverrelativeurl('{encoded_file_url}')/$value"

                file_resp = request_context.get(
                    file_val_url,
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                    },
                    timeout=300000,
                )
                if file_resp.status == 200:
                    f_bytes = file_resp.body()
                    if not self._is_html_response(f_bytes):
                        dest_path = output_dir / f_name
                        dest_path.write_bytes(f_bytes)
                        downloaded_files.append(dest_path)
                        logger.info(f"Successfully downloaded file: {f_name}")
                else:
                    logger.warning(f"Failed to download [{f_name}] (Status: {file_resp.status})")

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
                if dl:
                    total_downloaded.append(dl)
            else:
                dls = self.download_folder(p, output_dir)
                total_downloaded.extend(dls)

        return total_downloaded

    def upload_file(self, local_file_path: Path, target_folder_path: str) -> bool:
        """Upload a local file to a specified SharePoint folder path using FormDigest validation."""
        local_file_path = Path(local_file_path)
        if not local_file_path.exists():
            logger.error(f"Local file to upload does not exist: {local_file_path}")
            return False

        folder_clean = clean_sharepoint_path(target_folder_path)
        full_folder_path = self._ensure_server_relative_url(folder_clean)
        escaped_folder = full_folder_path.replace("'", "''")
        encoded_folder = urllib.parse.quote(escaped_folder, safe='/$()')

        file_name = local_file_path.name
        escaped_filename = file_name.replace("'", "''")
        encoded_filename = urllib.parse.quote(escaped_filename, safe='/$()')

        context_info_url = f"{self.site_url}/_api/contextinfo"
        upload_endpoint = (
            f"{self.site_url}/_api/web/getfolderbyserverrelativeurl('{encoded_folder}')"
            f"/files/add(url='{encoded_filename}',overwrite=true)"
        )

        with sync_playwright() as p:
            self._ensure_authenticated(p)
            request_context = p.request.new_context(storage_state=str(self.session_file))

            digest_resp = request_context.post(
                context_info_url,
                headers={"Accept": "application/json;odata=verbose"},
                timeout=30000,
            )

            # Auto trigger 2FA re-authentication if session expired
            if digest_resp.status in (401, 403) or self._is_html_response(digest_resp.body()):
                logger.warning(f"Session expired fetching FormDigest. Launching 2FA login automatically...")
                self._cleanup_expired_session("Expired session detected")
                self._ensure_authenticated(p, force_reauth=True)
                request_context = p.request.new_context(storage_state=str(self.session_file))
                digest_resp = request_context.post(
                    context_info_url,
                    headers={"Accept": "application/json;odata=verbose"},
                    timeout=30000,
                )

            request_digest = ""
            if digest_resp.status == 200:
                try:
                    digest_data = digest_resp.json()
                    request_digest = (
                        digest_data.get("d", {})
                        .get("GetContextWebInformation", {})
                        .get("FormDigestValue", "")
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse FormDigestValue: {e}")

            headers = {
                "Accept": "application/json;odata=verbose",
                "Content-Type": "application/octet-stream",
            }
            if request_digest:
                headers["X-RequestDigest"] = request_digest

            file_bytes = local_file_path.read_bytes()
            response = request_context.post(
                upload_endpoint,
                data=file_bytes,
                headers=headers,
                timeout=300000,
            )

            if response.status in [200, 201]:
                logger.info(f"Successfully uploaded [{file_name}] to SharePoint: {target_folder_path}")
                return True
            else:
                logger.error(f"Failed to upload file to SharePoint. Status: {response.status}")
                return False

    def resolve_cu_paths(
        self,
        cu_id: str,
        base_parent_dir: str | None = None
    ) -> dict[str, str]:
        """Dynamically search SharePoint parent directory for a matching CU folder name using global settings."""
        cu_clean = cu_id.strip().upper()
        cu_token = cu_clean.split()[0] if cu_clean else ""

        base_dir = base_parent_dir or settings.SHAREPOINT_BASE_CONVERSIONS_DIR
        clean_parent = base_dir.strip().replace("\\", "/").strip("/")
        full_parent_path = self._ensure_server_relative_url(clean_parent)
        escaped_parent = full_parent_path.replace("'", "''")
        encoded_parent = urllib.parse.quote(escaped_parent, safe='/$()')
        api_endpoint = f"{self.site_url}/_api/web/getfolderbyserverrelativeurl('{encoded_parent}')/folders"

        target_cu_folder = None
        with sync_playwright() as p:
            self._ensure_authenticated(p)
            request_context = p.request.new_context(storage_state=str(self.session_file))
            response = request_context.get(api_endpoint, headers={"Accept": "application/json;odata=verbose"})

            # Auto trigger 2FA re-authentication if session expired
            if response.status in (401, 403) or self._is_html_response(response.body()):
                logger.warning("Session expired in resolve_cu_paths. Launching 2FA login automatically...")
                self._cleanup_expired_session("Expired session detected")
                self._ensure_authenticated(p, force_reauth=True)
                request_context = p.request.new_context(storage_state=str(self.session_file))
                response = request_context.get(api_endpoint, headers={"Accept": "application/json;odata=verbose"})

            if response.status == 200:
                folders = response.json().get("d", {}).get("results", [])
                for f in folders:
                    folder_name = f.get("Name", "").upper()
                    if cu_clean in folder_name or (cu_token and cu_token in folder_name):
                        target_cu_folder = f.get("ServerRelativeUrl", "")
                        logger.info(f"Dynamically resolved CU [{cu_id}] raw directory -> {target_cu_folder}")
                        break

        if not target_cu_folder:
            logger.error(f"Could not locate any SharePoint folder matching CU ID: {cu_id}")
            return {}

        if "Shared Documents" in target_cu_folder:
            rel_cu_folder = "Shared Documents" + target_cu_folder.split("Shared Documents", 1)[1]
        else:
            rel_cu_folder = target_cu_folder.lstrip("/")

        logger.info(f"Normalized CU [{cu_id}] path -> {rel_cu_folder}")

        return {
            "mapping_path": f"{rel_cu_folder}/{settings.SHAREPOINT_QA_FOLDER_REL}",
            "matrix_path": f"{rel_cu_folder}/{settings.SHAREPOINT_MATRIX_FOLDER_REL}",
        }