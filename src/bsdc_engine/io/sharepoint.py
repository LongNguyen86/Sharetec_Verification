import os
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

from src.bsdc_engine.config import settings
from src.bsdc_engine.errors import SharePointAuthError
from src.bsdc_engine.logging import get_logger
from src.bsdc_engine.text import clean_sharepoint_path

logger = get_logger(__name__)

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xlsm", ".xls"}

# Spoof exact Windows Chrome User-Agent to prevent Microsoft from blocking transferred sessions from Local
WINDOWS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


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
        """Log a warning without automatically deleting state.json from disk."""
        if self.session_file.exists():
            logger.warning(f"Session warning: {reason}. Keeping state.json on disk.")

    def _ensure_authenticated(self):
        """Ensure state.json exists. Never launch browser on Server environment."""
        if not self.session_file.exists():
            logger.error(f"Missing session file at: {self.session_file}")
            raise SharePointAuthError(
                "Missing state.json on Server! "
                "Please copy state.json from Local to workspace/.auth/ on VPS."
            )

    def _get_request_context(self, p):
        """Create Playwright request context matching 100% Windows Chrome fingerprint from Local."""
        self._ensure_authenticated()
        return p.request.new_context(
            storage_state=str(self.session_file),
            user_agent=WINDOWS_USER_AGENT,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            }
        )

    def _is_html_response(self, content_bytes: bytes) -> bool:
        """Check if response body is an HTML login redirect page."""
        content_head = content_bytes[:500].decode("utf-8", errors="ignore").lower()
        return ("<" + "html") in content_head or ("<" + "!doctype") in content_head

    def download_file_by_path(self, server_relative_url: str, output_dir: Path) -> Path | None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
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
            request_context = self._get_request_context(p)
            response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            body_bytes = response.body()
            if response.status in (401, 403) or self._is_html_response(body_bytes):
                self._cleanup_expired_session(f"API status {response.status}")
                raise SharePointAuthError(f"Session state.json expired or rejected while downloading [{file_name}].")

            if response.status == 200:
                dest_file = output_dir / file_name
                dest_file.write_bytes(body_bytes)
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
            request_context = self._get_request_context(p)
            response = request_context.get(api_endpoint, headers=headers, timeout=300000)

            body_bytes = response.body()
            if response.status in (401, 403) or self._is_html_response(body_bytes):
                self._cleanup_expired_session(f"Folder status {response.status}")
                raise SharePointAuthError(f"Session state.json expired while fetching folder [{folder_relative_path}].")

            if response.status != 200:
                logger.warning(f"Failed to fetch folder [{folder_relative_path}]. Status: {response.status}")
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
            request_context = self._get_request_context(p)

            digest_resp = request_context.post(
                context_info_url,
                headers={"Accept": "application/json;odata=verbose"},
                timeout=300000,
            )

            if digest_resp.status in (401, 403) or self._is_html_response(digest_resp.body()):
                self._cleanup_expired_session("Upload digest auth failed.")
                raise SharePointAuthError("SharePoint session expired while fetching FormDigest for upload.")

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
            elif response.status in (401, 403):
                self._cleanup_expired_session("Upload POST auth failed.")
                raise SharePointAuthError("SharePoint session expired during file upload.")
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
            request_context = self._get_request_context(p)
            response = request_context.get(
                api_endpoint,
                headers={"Accept": "application/json;odata=verbose"},
                timeout=300000,
            )

            body_bytes = response.body()
            if response.status in (401, 403) or self._is_html_response(body_bytes):
                self._cleanup_expired_session("resolve_cu_paths auth failed.")
                raise SharePointAuthError("SharePoint session (state.json) expired or rejected.")

            if response.status == 200:
                try:
                    folders = response.json().get("d", {}).get("results", [])
                    for f in folders:
                        folder_name = f.get("Name", "").upper()
                        if cu_clean in folder_name or (cu_token and cu_token in folder_name):
                            target_cu_folder = f.get("ServerRelativeUrl", "")
                            logger.info(f"Dynamically resolved CU [{cu_id}] raw directory -> {target_cu_folder}")
                            break
                except Exception:
                    raise SharePointAuthError("SharePoint returned invalid JSON response.")

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