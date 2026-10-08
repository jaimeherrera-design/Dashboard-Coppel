import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock
from collections.abc import Mapping
from dataclasses import dataclass

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account
import requests

from data_cache import FileSignature, source_signature


DRIVE_FOLDER_ID = "1VotCf0HuDIGbmkM88KYAQSZQRI0du-zB"
API_URL = "https://www.googleapis.com/drive/v3"
FILE_FIELDS = "id,name,mimeType,size,md5Checksum,modifiedTime,version,trashed"
_SYNC_LOCK = RLock()
MASTER_NAMES = ("Mae_contacto.xlsx", "Mae_did.xlsx", "Mae_lada.xlsx")


@dataclass(frozen=True)
class DriveSources:
    parquet: tuple[FileSignature, ...]
    masters: dict[str, FileSignature]


class DriveSourceError(ValueError):
    pass


def credentials_path() -> Path:
    configured = os.environ.get("COPPEL_DRIVE_CREDENTIALS")
    return Path(configured) if configured else Path.home() / ".config" / "coppel" / "drive-service-account.json"


def request_metadata(session, resource: str, params: dict) -> dict:
    with session.get(f"{API_URL}/{resource}", params=params, timeout=(15, 90)) as response:
        if response.status_code != 200:
            raise DriveSourceError(
                f"Google Drive respondio HTTP {response.status_code}. Verifique Drive API, "
                "la cuenta de servicio y su permiso de Lector sobre la carpeta."
            )
        return response.json()


def list_sources(session, folder_id: str, *, include_masters: bool = False) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise DriveSourceError("Identificador de carpeta Drive invalido.")
    folder = request_metadata(session, f"files/{folder_id}",
                              {"fields": "mimeType,trashed", "supportsAllDrives": True})
    if folder.get("mimeType") != "application/vnd.google-apps.folder" or folder.get("trashed"):
        raise DriveSourceError("La ruta de Drive no es una carpeta activa.")
    params = {
        "q": f"'{folder_id}' in parents and trashed = false",
        "fields": f"nextPageToken,incompleteSearch,files({FILE_FIELDS})",
        "pageSize": 1000, "supportsAllDrives": True, "includeItemsFromAllDrives": True,
    }
    files = []
    seen = set()
    while True:
        page = request_metadata(session, "files", params)
        if page.get("incompleteSearch"):
            raise DriveSourceError("Drive devolvio una lista incompleta; no se calcularan totales parciales.")
        for item in page.get("files", []):
            selected_master = include_masters and item["name"].casefold() in {name.casefold() for name in MASTER_NAMES}
            if not item["name"].casefold().endswith(".parquet") and not selected_master:
                continue
            if item["mimeType"].startswith("application/vnd.google-apps."):
                if selected_master:
                    raise DriveSourceError(f"{item['name']} debe ser un Excel XLSX, no una hoja nativa de Google.")
                continue
            if (not re.fullmatch(r"[A-Za-z0-9_-]+", item["id"])
                    or "/" in item["name"] or "\\" in item["name"]
                    or any(char in item["name"] for char in '<>:"|?*')
                    or item["name"].endswith((" ", "."))):
                raise DriveSourceError("Drive contiene un nombre Parquet o maestro no valido para la cache local.")
            if not all(key in item for key in ("size", "md5Checksum", "modifiedTime", "version")):
                raise DriveSourceError(f"Drive no proporciona metadatos completos para {item['name']}.")
            if item["id"] in seen:
                raise DriveSourceError("Drive devolvio un archivo repetido entre paginas; recargue.")
            seen.add(item["id"])
            files.append(item)
        token = page.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    if not any(item["name"].casefold().endswith(".parquet") for item in files):
        raise DriveSourceError("No hay archivos Parquet accesibles en la carpeta de Drive.")
    if include_masters:
        for name in MASTER_NAMES:
            matches = [item for item in files if item["name"].casefold() == name.casefold()]
            if not matches:
                raise DriveSourceError(f"Falta el maestro {name} en la carpeta de Drive.")
            if len(matches) != 1:
                raise DriveSourceError(f"Hay varios maestros {name} en Drive. Conserve solo la version activa.")
    return sorted(files, key=lambda item: (item["name"].casefold(), item["id"]))


def list_parquet(session, folder_id: str) -> list[dict]:
    return list_sources(session, folder_id)


def remote_identity(item: dict) -> dict:
    return {key: item[key] for key in ("id", "name", "size", "md5Checksum", "modifiedTime", "version")}


def sync_files(session, cache_root: Path, folder_id: str, status=None,
               *, include_masters: bool = False) -> tuple[FileSignature, ...]:
    with _SYNC_LOCK:
        def listing():
            return list_sources(session, folder_id, include_masters=True) if include_masters else list_parquet(session, folder_id)

        files = listing()
        signatures = []
        for number, item in enumerate(files, start=1):
            # Keep summary paths below the Windows path-length limit.
            key = hashlib.sha256(f"{folder_id}:{item['id']}".encode("utf-8")).hexdigest()[:20]
            directory = cache_root / key
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / item["name"]
            metadata = directory / "source.json"
            identity = remote_identity(item)
            if metadata.exists():
                stored = json.loads(metadata.read_text(encoding="utf-8"))
                if stored["remote"]["id"] != item["id"]:
                    raise DriveSourceError("Conflicto de identificadores en la cache de Drive.")
                if target.exists():
                    signature = source_signature(target)
                    if stored == {"remote": identity, "local": list(signature)}:
                        signatures.append(signature)
                        continue
            if status is not None:
                status.update(label=f"Descargando de Drive ({number}/{len(files)}): {item['name']}", state="running")
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".download", delete=False) as handle:
                temporary = Path(handle.name)
                digest = hashlib.md5()
                size = 0
                try:
                    with session.get(
                        f"{API_URL}/files/{item['id']}", params={"alt": "media", "supportsAllDrives": True},
                        stream=True, timeout=(15, 120),
                    ) as response:
                        if response.status_code != 200:
                            raise DriveSourceError(f"No se pudo descargar {item['name']}: HTTP {response.status_code}.")
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            handle.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                except Exception:
                    handle.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                if size != int(item["size"]) or digest.hexdigest() != item["md5Checksum"]:
                    raise DriveSourceError(f"Descarga incompleta o contenido distinto: {item['name']}. Recargue.")
                latest = request_metadata(session, f"files/{item['id']}",
                                          {"fields": FILE_FIELDS, "supportsAllDrives": True})
                if latest.get("trashed") or remote_identity(latest) != identity:
                    raise DriveSourceError(f"{item['name']} cambio durante la descarga. Recargue.")
                temporary.replace(target)
                signature = source_signature(target)
                metadata.write_text(json.dumps({"remote": identity, "local": list(signature)}), encoding="utf-8")
                signatures.append(signature)
            finally:
                temporary.unlink(missing_ok=True)
        if [remote_identity(item) for item in listing()] != [remote_identity(item) for item in files]:
            raise DriveSourceError("La carpeta Drive cambio durante la sincronizacion; recargue para incluir todas las fuentes.")
        return tuple(signatures)


def load_credentials(info: Mapping[str, str] | None = None):
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    if info is not None:
        try:
            return service_account.Credentials.from_service_account_info(dict(info), scopes=scopes)
        except (ValueError, TypeError, GoogleAuthError) as exc:
            raise DriveSourceError("La seccion gcp_service_account de Secrets no es una credencial valida.") from exc
    credential = credentials_path()
    if not credential.is_file():
        raise DriveSourceError(
            "No se encontro la credencial de Drive. Configure gcp_service_account en Secrets de Streamlit "
            "o COPPEL_DRIVE_CREDENTIALS o ~/.config/coppel/drive-service-account.json en el equipo local."
        )
    try:
        return service_account.Credentials.from_service_account_file(
            str(credential), scopes=scopes,
        )
    except (OSError, ValueError) as exc:
        raise DriveSourceError("No se pudo cargar la credencial de la cuenta de servicio. Verifique el JSON local.") from exc
    

def discover_drive_files(root: Path, status=None, *, service_account_info: Mapping[str, str] | None = None,
                         include_masters: bool = False) -> tuple[FileSignature, ...]:
    credentials = load_credentials(service_account_info)
    try:
        with AuthorizedSession(credentials) as session:
            return sync_files(session, root / ".drive_cache", DRIVE_FOLDER_ID, status, include_masters=include_masters)
    except (GoogleAuthError, requests.RequestException) as exc:
        raise DriveSourceError("No se pudo autenticar o conectar con Drive. Verifique la clave, permisos y conexion.") from exc


def discover_drive_sources(root: Path, status=None, *, service_account_info: Mapping[str, str] | None = None) -> DriveSources:
    signatures = discover_drive_files(root, status, service_account_info=service_account_info, include_masters=True)
    masters = {
        name: next(signature for signature in signatures if Path(signature[0]).name.casefold() == name.casefold())
        for name in MASTER_NAMES
    }
    return DriveSources(
        parquet=tuple(signature for signature in signatures if Path(signature[0]).suffix.casefold() == ".parquet"),
        masters=masters,
    )
