#!/usr/bin/env python3
"""
HomeShare - lightweight LAN file sharing service for macOS gateways.

This module exposes an HTTP server that provides a NAS-like experience with:
* Web UI and JSON API for browsing and downloading files (with HTTP range support).
* Optional write features (uploads, delete, mkdir, move) guarded by a read-only flag.
* Resumable uploads that persist state across restarts.
* On-demand ZIP packaging for multi-file downloads.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import mimetypes
import os
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, Tuple


DEFAULT_CHUNK_SIZE = 1024 * 256  # 256 KiB


def _human_mtime(ts: float) -> str:
    """Convert epoch seconds to ISO timestamp."""
    return _dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _safe_join(root: Path, relative: str) -> Path:
    """Resolve a user supplied relative path against the share root safely."""
    # posixpath norms keep browser style paths predictable.
    relative = posixpath.normpath(relative.strip("/"))
    if relative in ("", "."):
        candidate = root
    else:
        parts = [p for p in relative.split("/") if p not in (".", "")]
        candidate = root.joinpath(*parts) if parts else root
    try:
        resolved = candidate.resolve(strict=False)
    except FileNotFoundError:
        resolved = candidate
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PermissionError("Requested path escapes share root")
    return resolved


@dataclass(frozen=True)
class ServerConfig:
    share_root: Path
    static_dir: Path
    state_dir: Path
    host: str
    port: int
    read_only: bool
    overwrite: bool


class UploadManager:
    """Tracks resumable uploads on disk."""

    def __init__(self, state_dir: Path, share_root: Path, overwrite: bool) -> None:
        self._state_dir = state_dir
        self._upload_dir = state_dir / "uploads"
        self._share_root = share_root
        self._overwrite = overwrite
        self._lock = threading.Lock()
        self._upload_dir.mkdir(parents=True, exist_ok=True)

    def _state_file(self, upload_id: str) -> Path:
        return self._upload_dir / f"{upload_id}.json"

    def _temp_file(self, upload_id: str) -> Path:
        return self._upload_dir / f"{upload_id}.part"

    def _load_state(self, upload_id: str) -> Dict:
        state_file = self._state_file(upload_id)
        if not state_file.exists():
            raise FileNotFoundError("Upload session not found")
        with state_file.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _store_state(self, upload_id: str, data: Dict) -> None:
        state_file = self._state_file(upload_id)
        with state_file.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())

    @staticmethod
    def _public_state(meta: Dict) -> Dict:
        clean = {k: v for k, v in meta.items() if k not in {"temp_file"}}
        clean["completed"] = clean.get("received", 0) >= clean.get("total_size", 0) > 0
        return clean

    def _list_sessions(self) -> Dict[str, Dict]:
        sessions: Dict[str, Dict] = {}
        for entry in self._upload_dir.glob("*.json"):
            with entry.open("r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    continue
            sessions[data["upload_id"]] = data
        return sessions

    def create_session(
        self,
        target_relative: str,
        total_size: int,
        resume: bool = False,
        overwrite: bool | None = None,
    ) -> Dict:
        target_path = _safe_join(self._share_root, target_relative)
        if target_path.is_dir():
            raise ValueError("Target path points to a directory")
        if overwrite is None:
            overwrite = self._overwrite
        with self._lock:
            if resume:
                for state in self._list_sessions().values():
                    if state["target_path"] == target_relative and state["total_size"] == total_size:
                        return self._public_state(state)
            upload_id = uuid.uuid4().hex
            temp_file = self._temp_file(upload_id)
            meta = {
                "upload_id": upload_id,
                "target_path": target_relative,
                "temp_file": str(temp_file),
                "total_size": total_size,
                "received": 0,
                "overwrite": overwrite,
                "created_at": time.time(),
            }
            self._store_state(upload_id, meta)
            temp_file.touch()
            return self._public_state(meta)

    def append_chunk(self, upload_id: str, start: int, end_exclusive: int, chunk: bytes) -> Dict:
        with self._lock:
            meta = self._load_state(upload_id)
            if meta["received"] != start:
                raise ValueError(f"Unexpected chunk start: expected {meta['received']}, got {start}")
            if end_exclusive < start:
                raise ValueError("Invalid range end")
            if end_exclusive > meta["total_size"]:
                raise ValueError("Chunk exceeds declared file size")
            with open(meta["temp_file"], "r+b") as temp_fp:
                temp_fp.seek(start)
                temp_fp.write(chunk)
                temp_fp.flush()
                os.fsync(temp_fp.fileno())
            meta["received"] = end_exclusive
            self._store_state(upload_id, meta)
            if meta["received"] >= meta["total_size"]:
                self._finalize(meta)
                meta["completed"] = True
            return self._public_state(meta)

    def _finalize(self, meta: Dict) -> None:
        target_path = _safe_join(self._share_root, meta["target_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists() and not meta["overwrite"]:
            raise FileExistsError(f"Target file exists: {meta['target_path']}")
        temp_file = Path(meta["temp_file"])
        shutil.move(str(temp_file), target_path)
        self._delete_state(meta["upload_id"])

    def _delete_state(self, upload_id: str) -> None:
        for path in (self._state_file(upload_id), self._temp_file(upload_id)):
            if path.exists():
                path.unlink()

    def status(self, upload_id: str) -> Dict:
        with self._lock:
            meta = self._load_state(upload_id)
            return self._public_state(meta)

    def cancel(self, upload_id: str) -> None:
        with self._lock:
            self._delete_state(upload_id)


class HomeShareHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing the NAS-like API surface."""

    server_version = "HomeShare/1.0"

    def __init__(self, *args, config: ServerConfig, upload_manager: UploadManager, **kwargs):
        self.config = config
        self.uploads = upload_manager
        super().__init__(*args, **kwargs)

    # -- Helpers ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 - keep quiet unless verbose
        sys.stderr.write(f"{self.log_date_time_string()} [{self.address_string()}] {fmt % args}\n")

    def _send_json(self, status: HTTPStatus, data: Dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body") from exc

    def _serve_static(self, rel_path: str) -> None:
        static_path = _safe_join(self.config.static_dir, rel_path)
        if not static_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Static asset not found")
            return
        if static_path.is_dir():
            static_path = static_path / "index.html"
        mime, _ = mimetypes.guess_type(static_path.name)
        mime = mime or "application/octet-stream"
        data = static_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _list_directory(self, rel_path: str) -> Dict:
        path = _safe_join(self.config.share_root, rel_path)
        if not path.exists():
            raise FileNotFoundError("Path does not exist")
        if not path.is_dir():
            raise NotADirectoryError("Path is not a directory")
        entries = []
        for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if entry.name.startswith("."):
                continue
            stat = entry.stat()
            entries.append(
                {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size": stat.st_size,
                    "modified": _human_mtime(stat.st_mtime),
                }
            )
        return {
            "path": rel_path.strip("/") or "/",
            "entries": entries,
        }

    def _send_file(self, rel_path: str) -> None:
        file_path = _safe_join(self.config.share_root, rel_path)
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        file_size = file_path.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = file_size - 1
        status = HTTPStatus.OK
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                start_str, end_str = match.groups()
                if start_str:
                    start = int(start_str)
                if end_str:
                    end = int(end_str)
                if not end_str:
                    end = file_size - 1
                if start >= file_size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.end_headers()
                    return
                if end >= file_size:
                    end = file_size - 1
                status = HTTPStatus.PARTIAL_CONTENT
        chunk_size = DEFAULT_CHUNK_SIZE
        mime, _ = mimetypes.guess_type(file_path.name)
        mime = mime or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        content_length = end - start + 1
        self.send_header("Content-Length", str(content_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        with file_path.open("rb") as f:
            f.seek(start)
            remaining = content_length
            while remaining > 0:
                to_read = min(chunk_size, remaining)
                data = f.read(to_read)
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def _require_write(self) -> bool:
        if self.config.read_only:
            self.send_error(HTTPStatus.FORBIDDEN, "Server running in read-only mode")
            return False
        return True

    # -- HTTP method handlers -------------------------------------------

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Upload-Id, Content-Range",
        )
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._serve_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            rel = parsed.path[len("/static/") :]
            self._serve_static(rel)
            return
        if parsed.path.startswith("/files/"):
            rel = urllib.parse.unquote(parsed.path[len("/files/") :])
            self._send_file(rel)
            return
        if parsed.path == "/api/list":
            rel_path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            try:
                payload = self._list_directory(rel_path)
            except (FileNotFoundError, NotADirectoryError) as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            except PermissionError as exc:
                self.send_error(HTTPStatus.FORBIDDEN, str(exc))
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        if parsed.path == "/api/info":
            payload = {
                "share_root": str(self.config.share_root),
                "read_only": self.config.read_only,
                "overwrite": self.config.overwrite,
            }
            self._send_json(HTTPStatus.OK, payload)
            return
        if parsed.path.startswith("/api/upload/") and parsed.path.endswith("/status"):
            upload_id = parsed.path.split("/")[3]
            try:
                status = self.uploads.status(upload_id)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND, "Upload session not found")
                return
            self._send_json(HTTPStatus.OK, status)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/upload/session":
            if not self._require_write():
                return
            try:
                body = self._read_json()
                target = body["path"]
                size = int(body["size"])
                resume = bool(body.get("resume", False))
                overwrite = body.get("overwrite")
                meta = self.uploads.create_session(target, size, resume=resume, overwrite=overwrite)
            except (KeyError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except PermissionError as exc:
                self.send_error(HTTPStatus.FORBIDDEN, str(exc))
                return
            self._send_json(HTTPStatus.CREATED, meta)
            return
        if parsed.path == "/api/mkdir":
            if not self._require_write():
                return
            try:
                body = self._read_json()
                rel_path = body["path"]
                target = _safe_join(self.config.share_root, rel_path)
                target.mkdir(parents=True, exist_ok=True)
            except (KeyError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except PermissionError as exc:
                self.send_error(HTTPStatus.FORBIDDEN, str(exc))
                return
            self._send_json(HTTPStatus.OK, {"created": rel_path})
            return
        if parsed.path == "/api/delete":
            if not self._require_write():
                return
            try:
                body = self._read_json()
                rel_path = body["path"]
                target = _safe_join(self.config.share_root, rel_path)
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                else:
                    raise FileNotFoundError("Target not found")
            except FileNotFoundError as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            except (KeyError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except PermissionError as exc:
                self.send_error(HTTPStatus.FORBIDDEN, str(exc))
                return
            self._send_json(HTTPStatus.OK, {"deleted": rel_path})
            return
        if parsed.path == "/api/move":
            if not self._require_write():
                return
            try:
                body = self._read_json()
                source = _safe_join(self.config.share_root, body["source"])
                dest = _safe_join(self.config.share_root, body["destination"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(dest))
            except FileNotFoundError as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
                return
            except (KeyError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except PermissionError as exc:
                self.send_error(HTTPStatus.FORBIDDEN, str(exc))
                return
            self._send_json(HTTPStatus.OK, {"moved": body["source"], "to": body["destination"]})
            return
        if parsed.path == "/api/zip":
            try:
                body = self._read_json()
                paths = body["paths"]
                if not isinstance(paths, list) or not paths:
                    raise ValueError("paths must be a non-empty list")
            except (KeyError, ValueError) as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            try:
                self._send_zip(paths)
            except FileNotFoundError as exc:
                self.send_error(HTTPStatus.NOT_FOUND, str(exc))
            except PermissionError as exc:
                self.send_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_PUT(self) -> None:
        if not self.path.startswith("/api/upload/"):
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        if not self._require_write():
            return
        upload_id = self.path.split("/")[-1]
        length = int(self.headers.get("Content-Length", "0"))
        content_range = self.headers.get("Content-Range")
        if not content_range:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing Content-Range header")
            return
        match = re.match(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if not match:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid Content-Range header")
            return
        start, end, total = map(int, match.groups())
        if length != end - start + 1:
            self.send_error(HTTPStatus.BAD_REQUEST, "Content-Length mismatch")
            return
        chunk = self.rfile.read(length)
        try:
            meta = self.uploads.append_chunk(upload_id, start, end + 1, chunk)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "Upload session not found")
            return
        except (ValueError, FileExistsError) as exc:
            self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except PermissionError as exc:
            self.send_error(HTTPStatus.FORBIDDEN, str(exc))
            return
        self._send_json(HTTPStatus.OK, meta)

    def do_DELETE(self) -> None:
        if not self.path.startswith("/api/upload/"):
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return
        upload_id = self.path.split("/")[-1]
        try:
            self.uploads.cancel(upload_id)
        except FileNotFoundError:
            pass
        self._send_json(HTTPStatus.OK, {"deleted": upload_id})

    # -- Internal helpers ------------------------------------------------

    def _send_zip(self, rel_paths: Iterable[str]) -> None:
        spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
        with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel in rel_paths:
                path = _safe_join(self.config.share_root, rel)
                if path.is_dir():
                    dir_arcname = f"{Path(rel).as_posix().rstrip('/')}/"
                    dir_info = zipfile.ZipInfo(dir_arcname)
                    dir_info.external_attr = 0o40775 << 16
                    zf.writestr(dir_info, "")
                    for root, dirs, files in os.walk(path):
                        root_path = Path(root)
                        for item in files:
                            abs_file = root_path / item
                            arcname = str(Path(rel) / abs_file.relative_to(path))
                            zf.write(abs_file, arcname)
                        # include empty directories explicitly
                        for d in dirs:
                            dir_path = Path(rel) / (root_path / d).relative_to(path)
                            sub_info = zipfile.ZipInfo(f"{dir_path.as_posix().rstrip('/')}/")
                            sub_info.external_attr = 0o40775 << 16
                            zf.writestr(sub_info, "")
                elif path.is_file():
                    zf.write(path, rel)
                else:
                    raise FileNotFoundError(f"Path not found: {rel}")
        spool.seek(0)
        data = spool.read()
        filename = f"homeshare-{int(time.time())}.zip"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)


def _parse_args(argv: list[str]) -> ServerConfig:
    parser = argparse.ArgumentParser(description="HomeShare LAN file server")
    parser.add_argument("--share-root", required=True, help="Base directory exposed by the server")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--static-dir", default="static", help="Location of static web assets")
    parser.add_argument(
        "--state-dir",
        default=".homeshare_state",
        help="Directory for server state (upload metadata)",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Expose the share as read-only (default behaviour)",
    )
    parser.add_argument(
        "--read-write",
        action="store_true",
        help="Enable write endpoints (overrides --read-only)",
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Permit uploads to overwrite existing files (default: False)",
    )
    args = parser.parse_args(argv)
    share_root = Path(args.share_root).expanduser().resolve()
    if not share_root.exists() or not share_root.is_dir():
        parser.error(f"share root must be an existing directory: {share_root}")
    static_dir = Path(args.static_dir).expanduser().resolve()
    if not static_dir.exists():
        parser.error(f"static asset directory not found: {static_dir}")
    state_dir = Path(args.state_dir).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    read_only = True
    if args.read_write:
        read_only = False
    elif args.read_only:
        read_only = True
    config = ServerConfig(
        share_root=share_root,
        static_dir=static_dir,
        state_dir=state_dir,
        host=args.host,
        port=args.port,
        read_only=read_only,
        overwrite=bool(args.allow_overwrite),
    )
    return config


def run_server(config: ServerConfig) -> None:
    upload_manager = UploadManager(config.state_dir, config.share_root, config.overwrite)

    def handler_factory(*args, **kwargs):
        kwargs.update({"config": config, "upload_manager": upload_manager})
        return HomeShareHandler(*args, **kwargs)

    server = ThreadingHTTPServer((config.host, config.port), handler_factory)
    server.daemon_threads = True
    bind_host = config.host if config.host else "0.0.0.0"
    print(
        f"HomeShare serving {config.share_root} on http://{bind_host}:{config.port} "
        f"(read_only={config.read_only})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        server.server_close()


def main() -> None:
    config = _parse_args(sys.argv[1:])
    run_server(config)


if __name__ == "__main__":
    main()
