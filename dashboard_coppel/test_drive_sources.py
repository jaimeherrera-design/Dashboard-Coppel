import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from drive_sources import DriveSourceError, discover_drive_files, discover_drive_sources, list_parquet, list_sources, load_credentials, sync_files, MASTER_NAMES


def item(identifier, name, content, version="1"):
    return {"id": identifier, "name": name, "mimeType": "application/octet-stream",
            "size": str(len(content)), "md5Checksum": hashlib.md5(content).hexdigest(),
            "modifiedTime": f"2026-10-08T12:00:0{version}Z", "version": version, "trashed": False}


class Response:
    def __init__(self, payload=None, content=b"", code=200):
        self.payload = payload
        self.content = content
        self.status_code = code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def json(self):
        return self.payload

    def iter_content(self, **kwargs):
        yield self.content


class Session:
    def __init__(self):
        self.items = [item("a", "same.parquet", b"first"), item("b", "same.parquet", b"second")]
        self.content = {"a": b"first", "b": b"second"}
        self.downloads = []
        self.deny = False
        self.incomplete = False

    def get(self, url, params, **kwargs):
        if self.deny:
            return Response(code=403)
        resource = url.split("/v3/")[1]
        if resource == "files/folder":
            return Response({"mimeType": "application/vnd.google-apps.folder", "trashed": False})
        if resource == "files":
            index = int(params.get("pageToken", "0"))
            result = {"files": self.items[index:index + 1], "incompleteSearch": self.incomplete}
            if index + 1 < len(self.items):
                result["nextPageToken"] = str(index + 1)
            return Response(result)
        identifier = resource.split("/")[1]
        if params.get("alt") == "media":
            self.downloads.append(identifier)
            return Response(content=self.content[identifier])
        return Response(next(value for value in self.items if value["id"] == identifier))


class DriveSourcesTests(unittest.TestCase):
    def add_masters(self, session):
        for index, name in enumerate(MASTER_NAMES):
            identifier = f"master{index}"
            content = name.encode()
            session.items.append(item(identifier, name, content))
            session.content[identifier] = content

    def test_masters_required_unique_and_excel_not_google_sheets(self):
        session = Session()
        self.add_masters(session)
        self.assertEqual(len(list_sources(session, "folder", include_masters=True)), 5)
        self.assertEqual(len(list_parquet(session, "folder")), 2)
        session.items.append(item("duplicate", MASTER_NAMES[0], b"duplicate"))
        with self.assertRaisesRegex(DriveSourceError, "varios maestros"):
            list_sources(session, "folder", include_masters=True)
        session.items.pop()
        session.items[-1]["mimeType"] = "application/vnd.google-apps.spreadsheet"
        with self.assertRaisesRegex(DriveSourceError, "no una hoja nativa"):
            list_sources(session, "folder", include_masters=True)
        session.items.pop()
        with self.assertRaisesRegex(DriveSourceError, "Mae_lada.xlsx"):
            list_sources(session, "folder", include_masters=True)

    def test_master_change_downloads_only_updated_excel(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session()
            self.add_masters(session)
            root = Path(folder)
            first = sync_files(session, root, "folder", include_masters=True)
            self.assertEqual(len(first), 5)
            session.downloads.clear()
            target = next(value for value in session.items if value["name"] == MASTER_NAMES[0])
            session.items[session.items.index(target)] = item(target["id"], target["name"], b"updated", "2")
            session.content[target["id"]] = b"updated"
            second = sync_files(session, root, "folder", include_masters=True)
            self.assertEqual(session.downloads, [target["id"]])
            self.assertEqual(
                tuple(signature for signature in first if signature[0].endswith(".parquet")),
                tuple(signature for signature in second if signature[0].endswith(".parquet")),
            )
            with patch("drive_sources.discover_drive_files", return_value=second) as discover:
                sources = discover_drive_sources(root, service_account_info={"type": "service_account"})
            self.assertEqual(len(sources.parquet), 2)
            self.assertEqual(set(sources.masters), set(MASTER_NAMES))
            self.assertTrue(discover.call_args.kwargs["include_masters"])

    def test_cloud_credentials_take_priority_and_invalid_secrets_do_not_fallback(self):
        info = {"type": "service_account", "private_key": "test-only-not-a-key"}
        with patch("drive_sources.service_account.Credentials.from_service_account_info") as cloud:
            with patch("drive_sources.credentials_path", side_effect=AssertionError("local fallback")):
                self.assertIs(load_credentials(info), cloud.return_value)
                self.assertEqual(cloud.call_args.args[0], info)
                self.assertEqual(cloud.call_args.kwargs["scopes"], ["https://www.googleapis.com/auth/drive.readonly"])
                cloud.side_effect = ValueError("invalid")
                with self.assertRaisesRegex(DriveSourceError, "Secrets"):
                    load_credentials(info)
        with patch("drive_sources.credentials_path") as path:
            with patch("drive_sources.service_account.Credentials.from_service_account_file") as local:
                path.return_value.is_file.return_value = True
                self.assertIs(load_credentials(), local.return_value)

    def test_pagination_same_names_new_modified_removed_and_concurrent_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = Session()
            self.assertEqual(len(list_parquet(session, "folder")), 2)
            with ThreadPoolExecutor(max_workers=2) as executor:
                outputs = list(executor.map(lambda _: sync_files(session, root, "folder"), range(2)))
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(session.downloads, ["a", "b"])
            self.assertNotEqual(outputs[0][0][0], outputs[0][1][0])
            relative = Path(outputs[0][0][0]).relative_to(root.resolve())
            self.assertEqual(len(relative.parts), 2)
            self.assertEqual(len(relative.parts[0]), 20)
            session.items.append(item("c", "new.PARQUET", b"third"))
            session.content["c"] = b"third"
            self.assertEqual(len(sync_files(session, root, "folder")), 3)
            self.assertEqual(session.downloads, ["a", "b", "c"])
            session.items[0] = item("a", "same.parquet", b"changed", "2")
            session.content["a"] = b"changed"
            files = sync_files(session, root, "folder")
            self.assertEqual(Path(files[1][0]).read_bytes(), b"changed")
            self.assertEqual(session.downloads, ["a", "b", "c", "a"])
            session.items = session.items[:1]
            self.assertEqual(len(sync_files(session, root, "folder")), 1)

    def test_partial_download_does_not_replace_valid_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            session = Session()
            files = sync_files(session, root, "folder")
            session.items[0] = item("a", "same.parquet", b"changed", "2")
            session.content["a"] = b"partial"
            with self.assertRaisesRegex(DriveSourceError, "contenido distinto"):
                sync_files(session, root, "folder")
            self.assertEqual(Path(files[0][0]).read_bytes(), b"first")
            self.assertFalse(list(root.rglob("*.download")))

    def test_permissions_incomplete_empty_unsafe_and_no_local_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "local.parquet").write_bytes(b"ignored")
            with patch("drive_sources.credentials_path", return_value=root / "missing.json"):
                with self.assertRaisesRegex(DriveSourceError, "credencial"):
                    discover_drive_files(root)
            session = Session()
            session.deny = True
            with self.assertRaisesRegex(DriveSourceError, "HTTP 403"):
                sync_files(session, root, "folder")
            session.deny = False
            session.incomplete = True
            with self.assertRaisesRegex(DriveSourceError, "incompleta"):
                sync_files(session, root, "folder")
            session.incomplete = False
            session.items = []
            with self.assertRaisesRegex(DriveSourceError, "No hay archivos"):
                sync_files(session, root, "folder")
            session.items = [item("a", "../escape.parquet", b"x")]
            with self.assertRaisesRegex(DriveSourceError, "nombre Parquet"):
                sync_files(session, root, "folder")

    def test_folder_change_during_sync_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session()
            original = list_parquet
            calls = 0

            def changing(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    session.items = session.items[:1]
                return original(*args)

            with patch("drive_sources.list_parquet", side_effect=changing):
                with self.assertRaisesRegex(DriveSourceError, "carpeta Drive cambio"):
                    sync_files(session, Path(folder), "folder")

    def test_remote_file_change_during_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session()
            get = session.get

            def changing(url, params, **kwargs):
                response = get(url, params, **kwargs)
                if params.get("alt") == "media":
                    session.items[0] = item("a", "same.parquet", b"changed", "2")
                return response

            session.get = changing
            with self.assertRaisesRegex(DriveSourceError, "cambio durante"):
                sync_files(session, Path(folder), "folder")
            self.assertFalse(list(Path(folder).rglob("*.parquet")))


if __name__ == "__main__":
    unittest.main()
