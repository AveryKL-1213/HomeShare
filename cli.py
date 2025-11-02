#!/usr/bin/env python3
"""
Command-line companion for HomeShare server.

Provides basic terminal access to list directories, download files (with HTTP
range resume), upload using resumable sessions, and trigger ZIP bundles.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Optional


class HomeShareClient:
    def __init__(self, base_url: str, chunk_size: int = 1024 * 512) -> None:
        self.base_url = base_url.rstrip("/")
        self.chunk_size = chunk_size

    def _url(self, path: str) -> str:
        return urllib.parse.urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        url = self._url(path)
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        return urllib.request.urlopen(req)

    def info(self) -> dict:
        with self._request("GET", "/api/info") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def list(self, path: str) -> dict:
        query = urllib.parse.urlencode({"path": path})
        with self._request("GET", f"/api/list?{query}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def mkdir(self, path: str) -> dict:
        payload = json.dumps({"path": path}).encode("utf-8")
        with self._request("POST", "/api/mkdir", data=payload, headers={"Content-Type": "application/json"}) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def delete(self, path: str) -> dict:
        payload = json.dumps({"path": path}).encode("utf-8")
        with self._request("POST", "/api/delete", data=payload, headers={"Content-Type": "application/json"}) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def move(self, source: str, destination: str) -> dict:
        payload = json.dumps({"source": source, "destination": destination}).encode("utf-8")
        with self._request("POST", "/api/move", data=payload, headers={"Content-Type": "application/json"}) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def download(self, remote_path: str, local_path: Path, resume: bool = True) -> None:
        local_path = local_path.expanduser()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = local_path.with_name(local_path.name + ".part")
        existing = tmp_path.stat().st_size if resume and tmp_path.exists() else 0
        headers = {}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        encoded_path = "/".join(urllib.parse.quote(part) for part in remote_path.lstrip("/").split("/"))
        url_path = "/files/" + encoded_path
        with self._request("GET", url_path, headers=headers) as resp:
            if resp.status in (200, 206):
                mode = "ab" if existing else "wb"
                with tmp_path.open(mode) as fp:
                    while True:
                        chunk = resp.read(self.chunk_size)
                        if not chunk:
                            break
                        fp.write(chunk)
                tmp_path.rename(local_path)
                return
            raise RuntimeError(f"Unexpected status {resp.status}")

    def start_upload(self, remote_path: str, total_size: int, resume: bool, overwrite: bool) -> dict:
        payload = {
            "path": remote_path,
            "size": total_size,
            "resume": resume,
            "overwrite": overwrite,
        }
        data = json.dumps(payload).encode("utf-8")
        with self._request("POST", "/api/upload/session", data=data, headers={"Content-Type": "application/json"}) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def upload(self, local_path: Path, remote_path: str, resume: bool = True, overwrite: bool = False) -> None:
        local_path = local_path.expanduser()
        file_size = local_path.stat().st_size
        session = self.start_upload(remote_path, file_size, resume, overwrite)
        upload_id = session["upload_id"]
        offset = session.get("received", 0)
        with local_path.open("rb") as fp:
            fp.seek(offset)
            while offset < file_size:
                to_send = fp.read(min(self.chunk_size, file_size - offset))
                if not to_send:
                    break
                end_offset = offset + len(to_send) - 1
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Range": f"bytes {offset}-{end_offset}/{file_size}",
                }
                resp = self._request(
                    "PUT",
                    f"/api/upload/{upload_id}",
                    data=to_send,
                    headers=headers,
                )
                with resp as response:
                    data = json.loads(response.read().decode("utf-8"))
                offset = data.get("received", offset + len(to_send))

    def zip_download(self, remote_paths: Iterable[str], local_path: Path) -> None:
        payload = json.dumps({"paths": list(remote_paths)}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        local_path = local_path.expanduser()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with self._request("POST", "/api/zip", data=payload, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Unexpected status {resp.status}")
            with local_path.open("wb") as fp:
                while True:
                    chunk = resp.read(self.chunk_size)
                    if not chunk:
                        break
                    fp.write(chunk)


def _handle_error(exc: urllib.error.HTTPError) -> None:
    try:
        payload = exc.read()
        message = payload.decode("utf-8")
    except Exception:  # pylint: disable=broad-except
        message = str(exc)
    print(f"HTTP error {exc.code}: {message}", file=sys.stderr)


def _cmd_list(client: HomeShareClient, args: argparse.Namespace) -> None:
    data = client.list(args.path)
    print(f"Directory: {data['path']}")
    for entry in data["entries"]:
        line = f"{entry['type']:>4}  {entry['modified']:>19}  {entry['size']:>12}  {entry['name']}"
        print(line)


def _cmd_download(client: HomeShareClient, args: argparse.Namespace) -> None:
    client.download(args.remote, Path(args.local), resume=not args.no_resume)
    print(f"Downloaded {args.remote} -> {args.local}")


def _cmd_upload(client: HomeShareClient, args: argparse.Namespace) -> None:
    remote = args.remote
    if remote.endswith("/"):
        remote = remote.rstrip("/") + "/" + Path(args.local).name
    client.upload(Path(args.local), remote, resume=not args.no_resume, overwrite=args.overwrite)
    print(f"Uploaded {args.local} -> {remote}")


def _cmd_mkdir(client: HomeShareClient, args: argparse.Namespace) -> None:
    client.mkdir(args.path)
    print(f"Created {args.path}")


def _cmd_delete(client: HomeShareClient, args: argparse.Namespace) -> None:
    client.delete(args.path)
    print(f"Deleted {args.path}")


def _cmd_move(client: HomeShareClient, args: argparse.Namespace) -> None:
    client.move(args.source, args.destination)
    print(f"Moved {args.source} -> {args.destination}")


def _cmd_zip(client: HomeShareClient, args: argparse.Namespace) -> None:
    client.zip_download(args.paths, Path(args.output))
    print(f"Saved ZIP to {args.output}")


def _cmd_info(client: HomeShareClient, args: argparse.Namespace) -> None:
    info = client.info()
    print(json.dumps(info, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HomeShare CLI")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="Base URL of the HomeShare server (default: http://127.0.0.1:8000)",
    )
    parser.add_argument("--chunk-size", type=int, default=1024 * 512, help="Transfer chunk size in bytes")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="List directory contents")
    p_list.add_argument("path", nargs="?", default="", help="Remote directory path")
    p_list.set_defaults(func=_cmd_list)

    p_dl = subparsers.add_parser("download", help="Download a file")
    p_dl.add_argument("remote", help="Remote file path")
    p_dl.add_argument("local", help="Local destination path")
    p_dl.add_argument("--no-resume", action="store_true", help="Disable resume support")
    p_dl.set_defaults(func=_cmd_download)

    p_ul = subparsers.add_parser("upload", help="Upload a file")
    p_ul.add_argument("local", help="Local file to upload")
    p_ul.add_argument("remote", help="Remote destination path (file or directory)")
    p_ul.add_argument("--no-resume", action="store_true", help="Disable resume support")
    p_ul.add_argument("--overwrite", action="store_true", help="Overwrite if the remote file exists")
    p_ul.set_defaults(func=_cmd_upload)

    p_mkdir = subparsers.add_parser("mkdir", help="Create a directory remotely")
    p_mkdir.add_argument("path", help="Remote directory path to create")
    p_mkdir.set_defaults(func=_cmd_mkdir)

    p_delete = subparsers.add_parser("delete", help="Delete a remote file or directory")
    p_delete.add_argument("path", help="Remote path to remove")
    p_delete.set_defaults(func=_cmd_delete)

    p_move = subparsers.add_parser("move", help="Move or rename a remote entry")
    p_move.add_argument("source", help="Existing remote path")
    p_move.add_argument("destination", help="Destination remote path")
    p_move.set_defaults(func=_cmd_move)

    p_zip = subparsers.add_parser("zip", help="Download selected files/directories as a ZIP")
    p_zip.add_argument("output", help="Local output ZIP file path")
    p_zip.add_argument("paths", nargs="+", help="Remote paths to include")
    p_zip.set_defaults(func=_cmd_zip)

    p_info = subparsers.add_parser("info", help="Show server configuration info")
    p_info.set_defaults(func=_cmd_info)

    args = parser.parse_args(argv)
    client = HomeShareClient(args.url, chunk_size=args.chunk_size)
    try:
        args.func(client, args)
    except urllib.error.HTTPError as exc:
        _handle_error(exc)
        return 1
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
