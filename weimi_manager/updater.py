from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import unquote, urljoin, urlsplit

import requests

from . import __version__
from .hot_update_store import UpdateInstallLock
from .update_health import (
    begin_update_attempt,
    update_health_path,
    update_root_for_install,
)
from .update_notes import (
    build_update_summary,
    normalize_update_summary,
    release_notes_url_from_download,
)
from .update_protocol import load_trust_config, verify_manifest_signature

ProgressCallback = Callable[[str, int, int], None]
DEFAULT_UPDATE_SOURCE = (
    "https://github.com/Zpf365/langlang-updates/"
    "releases/latest/download/update_manifest.json"
)
GITHUB_LATEST_MANIFEST = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/releases/latest/download/"
    r"update_manifest\.json$",
    re.IGNORECASE,
)
GITHUB_RELEASE_ASSET = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/releases/download/"
    r"([^/]+)/([^/?#]+)$",
    re.IGNORECASE,
)
DEFAULT_REQUIRED_UPDATE_FILES = (
    "LangLang.exe",
    "_internal/update_manifest.json",
    "_internal/webui/dist/index.html",
    "_internal/webui/dist/assets/ActivityPage-*.js",
    "_internal/webui/dist/assets/StoresPage-*.js",
    "_internal/webui/dist/assets/OrdersParityPage-*.js",
    "_internal/webui/dist/assets/ProductsPage-*.js",
    "_internal/webui/dist/assets/autoRefresh-*.js",
    "_internal/Qt6WebEngineCore.dll",
    "_internal/Qt6WebEngineWidgets.dll",
)


def _updates_root(project_dir: Path) -> Path:
    return update_root_for_install(project_dir)


@dataclass(slots=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    available: bool
    download_url: str = ""
    notes: str = ""
    source: str = ""
    sha256: str = ""
    mandatory: bool = False
    published_at: str = ""
    required_files: tuple[str, ...] = ()
    channel: str = ""
    prerelease: bool = False
    summary: list[dict[str, object]] = field(default_factory=list)
    release_notes_url: str = ""


@dataclass(slots=True)
class StagedUpdate:
    version: str
    archive_path: Path
    staging_root: Path
    package_root: Path


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.replace("-", ".").split("."):
        digits = "".join(char for char in part if char.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def is_development_preview_version(version: str) -> bool:
    """Return whether a version belongs to the unpublished 0.x channel."""
    return _version_tuple(str(version or ""))[:1] == (0,)


def _resolve_download_url(source: str, download_url: str) -> str:
    value = download_url.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://", "file://")):
        return value
    if source.startswith(("http://", "https://")):
        return urljoin(source, value)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(source).expanduser().resolve().parent / path
    return str(path.resolve())


def _requests_session() -> requests.Session:
    session = requests.Session()
    direct = os.environ.get(
        "LANGLANG_UPDATE_DIRECT",
        "",
    ).strip().lower()
    session.trust_env = direct not in {"1", "true", "yes"}
    return session


def _get_with_retries(
    session: requests.Session,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    stream: bool = False,
    timeout: tuple[int, int] = (8, 25),
) -> requests.Response:
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(
                url,
                headers=headers,
                stream=stream,
                timeout=timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", 0)
            if status not in {408, 425, 429, 500, 502, 503, 504}:
                raise
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    assert last_error is not None
    raise last_error


def _github_manifest_via_api(source: str) -> dict:
    match = GITHUB_LATEST_MANIFEST.match(source)
    if not match:
        raise ValueError("不是 GitHub latest manifest 地址")
    owner, repository = match.group(1), match.group(2)
    session = _requests_session()
    release_response = _get_with_retries(
        session,
        f"https://api.github.com/repos/{owner}/{repository}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    release = release_response.json()
    asset = next(
        (
            item
            for item in release.get("assets", [])
            if str(item.get("name") or "").casefold()
            == "update_manifest.json"
        ),
        None,
    )
    if not asset:
        raise ValueError("最新 Release 中没有 update_manifest.json")
    asset_response = _get_with_retries(
        session,
        str(asset["url"]),
        headers={
            "Accept": "application/octet-stream",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    data = asset_response.json()
    if not isinstance(data, dict):
        raise ValueError("GitHub 更新清单格式不正确")
    return data


def _github_manifest_via_latest_redirect(source: str) -> dict:
    match = GITHUB_LATEST_MANIFEST.match(source)
    if not match:
        raise ValueError("不是 GitHub latest manifest 地址")
    owner, repository = match.group(1), match.group(2)
    session = _requests_session()
    response = session.get(
        f"https://github.com/{owner}/{repository}/releases/latest",
        allow_redirects=False,
        timeout=(8, 25),
    )
    location = str(response.headers.get("Location") or "")
    tag_match = re.search(r"/releases/tag/([^/?#]+)", location)
    if response.status_code not in {301, 302, 303, 307, 308} or not tag_match:
        raise ValueError("GitHub Latest 重定向中没有稳定 tag")
    tag = unquote(tag_match.group(1))
    manifest_url = (
        f"https://github.com/{owner}/{repository}/releases/download/"
        f"{tag}/update_manifest.json"
    )
    manifest_response = _get_with_retries(
        session,
        manifest_url,
        timeout=(8, 25),
    )
    data = manifest_response.json()
    if not isinstance(data, dict):
        raise ValueError("GitHub tag 更新清单格式不正确")
    return data


def _github_download_via_api(download_url: str) -> str | None:
    match = GITHUB_RELEASE_ASSET.match(download_url)
    if not match:
        return None
    owner, repository, tag, asset_name = match.groups()
    session = _requests_session()
    response = _get_with_retries(
        session,
        f"https://api.github.com/repos/{owner}/{repository}/releases/tags/{unquote(tag)}",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    release = response.json()
    asset = next(
        (
            item
            for item in release.get("assets", [])
            if str(item.get("name") or "") == unquote(asset_name)
        ),
        None,
    )
    if not asset:
        return None
    return str(asset.get("url") or "")


def _read_manifest(source: str, project_dir: Path | None) -> tuple[dict, str]:
    source = source.strip()
    if source.startswith(("http://", "https://")):
        if GITHUB_LATEST_MANIFEST.match(source):
            data: dict | None = None
            try:
                data = _github_manifest_via_api(source)
            except Exception:
                try:
                    data = _github_manifest_via_latest_redirect(source)
                except Exception:
                    data = None
            if data is None:
                response = _get_with_retries(
                    _requests_session(),
                    source,
                    timeout=(8, 30),
                )
                data = response.json()
        else:
            response = _get_with_retries(
                _requests_session(),
                source,
            )
            data = response.json()
        return (data if isinstance(data, dict) else {}), source
    candidates = []
    if source:
        candidates.append(Path(source).expanduser())
    if project_dir is not None:
        candidates.append(project_dir / "update_manifest.json")
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return (data if isinstance(data, dict) else {}), str(path)
    return {}, source


def check_for_update(
    source: str = "",
    project_dir: Path | None = None,
    *,
    current_version: str | None = None,
    public_keys: Mapping[str, str] | None = None,
) -> UpdateInfo:
    source = (
        source.strip()
        or os.environ.get("LANGLANG_UPDATE_SOURCE", "").strip()
        or DEFAULT_UPDATE_SOURCE
    )
    if public_keys is None and project_dir is not None:
        trust = load_trust_config(project_dir)
        if trust.enabled:
            public_keys = trust.public_keys
    payload, resolved_source = _read_manifest(source, project_dir)
    if not payload:
        return UpdateInfo(
            current_version=str(current_version or __version__),
            latest_version=str(current_version or __version__),
            available=False,
            notes="没有配置更新源，当前已是最新本地版本。",
            source=resolved_source,
        )
    if public_keys:
        verify_manifest_signature(payload, public_keys)
    latest = str(payload.get("version") or payload.get("latest_version") or "")
    if not latest:
        raise ValueError("更新清单缺少 version")
    required_files = payload.get("required_files")
    if not isinstance(required_files, (list, tuple)):
        required_files = ()
    current = str(current_version or __version__)
    notes = str(payload.get("notes") or "")
    download_url = _resolve_download_url(
        resolved_source,
        str(payload.get("download_url") or ""),
    )
    summary = normalize_update_summary(payload.get("summary"))
    if not summary:
        summary = build_update_summary(notes)
    release_notes_url = str(
        payload.get("release_notes_url")
        or payload.get("release_url")
        or release_notes_url_from_download(download_url)
    ).strip()
    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        available=_version_tuple(latest) > _version_tuple(current),
        download_url=download_url,
        notes=notes,
        source=resolved_source,
        sha256=str(payload.get("sha256") or "").strip().lower(),
        mandatory=bool(payload.get("mandatory", False)),
        published_at=str(payload.get("published_at") or ""),
        required_files=tuple(
            str(value).strip()
            for value in required_files
            if str(value).strip()
        ),
        channel=str(payload.get("channel") or "").strip().casefold(),
        prerelease=bool(payload.get("prerelease", False)),
        summary=summary,
        release_notes_url=release_notes_url,
    )


def _safe_extract_zip(
    archive: Path,
    destination: Path,
    progress: ProgressCallback | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        for member in members:
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError("更新包包含非法路径")
        total = sum(max(0, member.file_size) for member in members)
        extracted = 0
        if progress:
            progress("正在解压更新包", 0, total)
        for member in members:
            target = (destination / member.filename).resolve()
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member, "r") as source, target.open("wb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
                    extracted += len(chunk)
                    if progress:
                        progress("正在解压更新包", extracted, total)
        if progress:
            progress("更新包已解压完成", total, total)


def _find_package_root(staging_root: Path) -> Path:
    if (staging_root / "LangLang.exe").is_file():
        return staging_root
    candidates = [
        path
        for path in staging_root.iterdir()
        if path.is_dir() and (path / "LangLang.exe").is_file()
    ]
    if len(candidates) != 1:
        raise ValueError("更新包结构不正确：未找到唯一的 LangLang.exe")
    return candidates[0]


def _required_update_files(info: UpdateInfo) -> tuple[str, ...]:
    values = info.required_files or DEFAULT_REQUIRED_UPDATE_FILES
    normalized: list[str] = []
    for value in values:
        pattern = str(value).strip().replace("\\", "/")
        if not pattern:
            continue
        path = Path(pattern)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"更新清单包含非法必需文件路径：{value}")
        normalized.append(pattern)
    if not normalized:
        raise ValueError("更新清单没有可校验的必需文件")
    return tuple(dict.fromkeys(normalized))


def _verify_staged_package(
    package_root: Path,
    info: UpdateInfo,
) -> None:
    missing = [
        pattern
        for pattern in _required_update_files(info)
        if not any(
            path.is_file()
            for path in package_root.glob(pattern)
        )
    ]
    if missing:
        raise ValueError(
            "更新包缺少必需文件：" + ", ".join(missing)
        )
    package_manifest = package_root / "_internal" / "update_manifest.json"
    if not package_manifest.is_file():
        package_manifest = package_root / "update_manifest.json"
    if not package_manifest.is_file():
        raise ValueError("更新包缺少 update_manifest.json")
    bundled = json.loads(
        package_manifest.read_text(encoding="utf-8-sig")
    )
    bundled_version = str(bundled.get("version") or "")
    if (
        _version_tuple(bundled_version)
        != _version_tuple(info.latest_version)
    ):
        raise ValueError("更新包版本与更新清单不一致")


def _download_archive(
    info: UpdateInfo,
    destination: Path,
    progress: ProgressCallback | None,
    *,
    retry_hash_mismatch: bool = True,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep each download isolated. Multiple installed copies share the same
    # %LOCALAPPDATA% update directory and previously raced on one fixed
    # ".part" file, producing Windows error 32.
    shared_partial = destination.with_suffix(destination.suffix + ".part")
    partial = destination.with_name(
        f"{destination.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.part"
    )
    if shared_partial.is_file():
        try:
            shutil.copy2(shared_partial, partial)
        except OSError:
            # A running updater may still hold the legacy shared file. Start a
            # private download instead of failing with WinError 32.
            pass
    if info.download_url.startswith("file://"):
        source_path = Path(unquote(urlsplit(info.download_url).path))
        _copy_file_with_progress(source_path, partial, progress)
    elif info.download_url.startswith(("http://", "https://")):
        session = _requests_session()
        download_url = info.download_url
        try:
            api_asset_url = _github_download_via_api(download_url)
        except Exception:
            api_asset_url = None
        if api_asset_url:
            download_url = api_asset_url
        base_headers = (
            {"Accept": "application/octet-stream"}
            if api_asset_url
            else {}
        )
        last_error: Exception | None = None
        for attempt in range(5):
            existing = partial.stat().st_size if partial.is_file() else 0
            request_headers = {
                **base_headers,
                "Accept-Encoding": "identity",
            }
            if existing:
                request_headers["Range"] = f"bytes={existing}-"
            try:
                with _get_with_retries(
                    session,
                    download_url,
                    headers=request_headers,
                    stream=True,
                    timeout=(10, 90),
                ) as response:
                    status = int(getattr(response, "status_code", 200))
                    content_range = str(
                        response.headers.get("Content-Range") or ""
                    )
                    total_match = re.search(
                        r"/(\d+)\s*$",
                        content_range,
                    )
                    total = (
                        int(total_match.group(1))
                        if total_match
                        else 0
                    )
                    content_length = int(
                        response.headers.get("Content-Length") or 0
                    )
                    content_encoding = str(
                        response.headers.get("Content-Encoding") or ""
                    ).strip().casefold()
                    append = status == 206 and existing > 0
                    if append:
                        downloaded = existing
                        mode = "ab"
                    else:
                        downloaded = 0
                        partial.unlink(missing_ok=True)
                        mode = "wb"
                    with partial.open(mode) as handle:
                        for chunk in response.iter_content(1024 * 512):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            downloaded += len(chunk)
                            if progress:
                                progress(
                                    "正在下载更新包",
                                    downloaded,
                                    total,
                                )
                    actual_size = partial.stat().st_size
                    if total and actual_size < total:
                        raise http.client.IncompleteRead(
                            b"",
                            total - actual_size,
                        )
                    if total and actual_size > total:
                        partial.unlink(missing_ok=True)
                        raise ValueError("更新包长度超过服务器声明大小")
                    if (
                        not total
                        and content_length
                        and content_encoding in {"", "identity"}
                    ):
                        response_size = (
                            actual_size - existing
                            if append
                            else actual_size
                        )
                        if response_size < content_length:
                            raise http.client.IncompleteRead(
                                b"",
                                content_length - response_size,
                            )
                        if response_size > content_length:
                            partial.unlink(missing_ok=True)
                            raise ValueError(
                                "更新包响应长度超过服务器声明大小"
                            )
                    last_error = None
                    break
            except (
                http.client.IncompleteRead,
                OSError,
                requests.RequestException,
            ) as exc:
                if (
                    isinstance(exc, requests.HTTPError)
                    and getattr(exc.response, "status_code", 0) == 416
                    and existing
                ):
                    last_error = None
                    break
                last_error = exc
                if attempt < 4:
                    time.sleep(min(4.0, 0.5 * (2**attempt)))
        if last_error is not None:
            raise RuntimeError(
                "更新包下载连接多次中断，已保留下载进度。"
                "请稍后重试，更新器会从断点继续下载。"
            ) from last_error
    else:
        source_path = Path(info.download_url)
        if not source_path.is_file():
            raise FileNotFoundError(f"更新包不存在：{source_path}")
        _copy_file_with_progress(source_path, partial, progress)
    if progress:
        completed_size = partial.stat().st_size
        progress("下载完成，正在校验更新包", completed_size, completed_size)
    for verification_attempt in range(2):
        if not info.sha256:
            break
        digest = hashlib.sha256()
        verification_total = partial.stat().st_size
        verification_done = 0
        if progress:
            progress("正在校验更新包 SHA256", 0, verification_total)
        with partial.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                verification_done += len(chunk)
                if progress:
                    progress(
                        "正在校验更新包 SHA256",
                        verification_done,
                        verification_total,
                    )
        actual = digest.hexdigest().lower()
        if actual != info.sha256:
            partial.unlink(missing_ok=True)
            if (
                retry_hash_mismatch
                and verification_attempt == 0
                and info.download_url.startswith(("http://", "https://"))
            ):
                _download_archive(
                    UpdateInfo(
                        current_version=info.current_version,
                        latest_version=info.latest_version,
                        available=info.available,
                        download_url=info.download_url,
                        notes=info.notes,
                        source=info.source,
                        sha256=info.sha256,
                        mandatory=info.mandatory,
                        published_at=info.published_at,
                        required_files=info.required_files,
                        channel=info.channel,
                        prerelease=info.prerelease,
                    ),
                    destination,
                    progress,
                    retry_hash_mismatch=False,
                )
                return
            raise ValueError("更新包 SHA256 校验失败，已取消安装")
        break
    partial.replace(destination)


def _copy_file_with_progress(
    source: Path,
    destination: Path,
    progress: ProgressCallback | None,
) -> None:
    total = source.stat().st_size
    copied = 0
    if progress:
        progress("正在读取更新包", 0, total)
    with source.open("rb") as input_handle, destination.open("wb") as output_handle:
        for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
            output_handle.write(chunk)
            copied += len(chunk)
            if progress:
                progress("正在读取更新包", copied, total)
    if progress:
        progress("更新包读取完成", total, total)


def download_and_stage_update(
    info: UpdateInfo,
    project_dir: Path,
    progress: ProgressCallback | None = None,
) -> StagedUpdate:
    if not info.available:
        raise ValueError("当前没有可安装的新版本")
    if not info.download_url:
        raise ValueError("更新清单没有 download_url")
    updates_root = _updates_root(project_dir)
    safe_version = "".join(
        char for char in info.latest_version if char.isalnum() or char in ".-_"
    )
    archive_path = updates_root / f"LangLang-{safe_version}.zip"
    _download_archive(info, archive_path, progress)
    staging_root = updates_root / (
        f"staging-{safe_version}-{uuid.uuid4().hex[:8]}"
    )
    if staging_root.exists():
        shutil.rmtree(staging_root)
    _safe_extract_zip(archive_path, staging_root, progress)
    try:
        package_root = _find_package_root(staging_root)
        _verify_staged_package(package_root, info)
    except (OSError, ValueError, json.JSONDecodeError):
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    package_manifest = package_root / "update_manifest.json"
    if not package_manifest.is_file():
        package_manifest = package_root / "_internal" / "update_manifest.json"
    if package_manifest.is_file():
        try:
            bundled = json.loads(
                package_manifest.read_text(encoding="utf-8-sig")
            )
            bundled_version = str(bundled.get("version") or "")
            if (
                bundled_version
                and _version_tuple(bundled_version)
                != _version_tuple(info.latest_version)
            ):
                raise ValueError("更新包版本与更新清单不一致")
        except (OSError, ValueError, json.JSONDecodeError):
            shutil.rmtree(staging_root, ignore_errors=True)
            raise
    return StagedUpdate(
        version=info.latest_version,
        archive_path=archive_path,
        staging_root=staging_root,
        package_root=package_root,
    )


def launch_update_installer(
    staged: StagedUpdate,
    project_dir: Path,
) -> Path:
    if os.name != "nt":
        raise RuntimeError("自动安装仅支持 Windows")
    target = project_dir.resolve()
    source = staged.package_root.resolve()
    if not (target / "LangLang.exe").is_file():
        raise ValueError("当前不是完整免安装版本，不能自动替换")
    if not (source / "LangLang.exe").is_file():
        raise ValueError("更新包缺少 LangLang.exe")
    updates_root = _updates_root(project_dir)
    updates_root.mkdir(parents=True, exist_ok=True)
    script_path = updates_root / "install-update.ps1"
    log_path = updates_root / "update.log"
    script = r"""
param(
    [Parameter(Mandatory=$true)][int]$WaitPid,
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$LogPath,
    [string]$ExpectedVersion = "",
    [int]$WaitSeconds = 300,
    [string]$HealthPath = "",
    [int]$HealthSeconds = 120,
    [string]$AttemptId = ""
)
$ErrorActionPreference = "Stop"
function Write-UpdateLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}
$backup = ""
$parent = Split-Path -Parent $Target
$targetName = Split-Path -Leaf $Target
$targetExe = Join-Path $Target "LangLang.exe"
$updatesRoot = Split-Path -Parent $LogPath
Set-Location -LiteralPath $updatesRoot
$lockStream = $null
$newProcess = $null
function Get-TargetProcessIds {
    @(
        Get-Process -Name "LangLang" -ErrorAction SilentlyContinue |
            ForEach-Object {
                try {
                    if ($_.Path -and ($_.Path -ieq $targetExe)) {
                        [int]$_.Id
                    }
                } catch {
                }
            }
    )
}
function Invoke-WithRetry(
    [Parameter(Mandatory=$true)][scriptblock]$Action,
    [Parameter(Mandatory=$true)][string]$Label
) {
    for ($retry = 0; $retry -lt 20; $retry++) {
        try {
            & $Action
            return
        } catch {
            if ($retry -ge 19) {
                throw
            }
            Write-UpdateLog (
                "Retry " + ($retry + 1) + " after lock during " +
                $Label + ": " + $_.Exception.Message
            )
            Start-Sleep -Milliseconds (250 * ($retry + 1))
        }
    }
}
function Copy-TreeWithRetry(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$Label
) {
    for ($retry = 0; $retry -lt 20; $retry++) {
        & robocopy $Source $Destination /E /COPY:DAT `
            /R:3 /W:1 /NFL /NDL /NJH /NJS
        if ($LASTEXITCODE -lt 8) {
            return
        }
        if ($retry -ge 19) {
            throw "复制失败（$Label），Robocopy 退出码 $LASTEXITCODE"
        }
        Write-UpdateLog (
            "Retry copy " + ($retry + 1) + " during " + $Label +
            ": Robocopy exit " + $LASTEXITCODE
        )
        Start-Sleep -Milliseconds (250 * ($retry + 1))
    }
}
try {
    $lockPath = Join-Path $updatesRoot "install.lock"
    for ($lockRetry = 0; $lockRetry -lt 120; $lockRetry++) {
        try {
            $lockStream = [IO.File]::Open(
                $lockPath,
                [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::None
            )
            break
        } catch {
            if ($lockRetry -ge 119) {
                throw "等待全局更新安装锁超时"
            }
            Start-Sleep -Milliseconds 500
        }
    }
    $attempts = [Math]::Max(1, $WaitSeconds * 2)
    $loggedWait = $false
    for ($i = 0; $i -lt $attempts; $i++) {
        $runningIds = @(Get-TargetProcessIds)
        if ($runningIds.Count -eq 0) {
            break
        }
        if (!$loggedWait) {
            Write-UpdateLog (
                "Waiting for target processes: " +
                ($runningIds -join ",")
            )
            $loggedWait = $true
        }
        Start-Sleep -Milliseconds 500
    }
    $runningIds = @(Get-TargetProcessIds)
    if ($runningIds.Count -gt 0) {
        throw (
            "等待旧进程退出超时：" +
            ($runningIds -join ",")
        )
    }

    if (!(Test-Path -LiteralPath $targetExe)) {
        $orphans = @(
            Get-ChildItem -LiteralPath $parent -Directory `
                -Filter ($targetName + ".update-backup-*") `
                -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        )
        if ($orphans.Count -gt 0) {
            Move-Item -LiteralPath $orphans[0].FullName -Destination $Target
            Write-UpdateLog (
                "Recovered orphaned backup: " + $orphans[0].FullName
            )
        }
    }

    $sourceExe = Join-Path $Source "LangLang.exe"
    if (!(Test-Path -LiteralPath $sourceExe)) {
        throw "暂存目录缺少 LangLang.exe"
    }
    if (!(Test-Path -LiteralPath $targetExe)) {
        throw "安装目录缺少 LangLang.exe"
    }

    if ($ExpectedVersion) {
        $manifestPaths = @(
            (Join-Path $Source "update_manifest.json"),
            (Join-Path $Source "_internal\update_manifest.json")
        )
        $manifestPath = $manifestPaths |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if (!$manifestPath) {
            throw "更新包缺少 update_manifest.json"
        }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        if ([string]$manifest.version -ne $ExpectedVersion) {
            throw (
                "更新包版本不一致，期望 " + $ExpectedVersion +
                "，实际 " + [string]$manifest.version
            )
        }
    }

    $backup = Join-Path $updatesRoot (
        "rollback-" + $targetName + "-" +
        (Get-Date -Format "yyyyMMdd-HHmmss-fff")
    )
    Write-UpdateLog (
        "Preparing update: expected=" + $ExpectedVersion +
        " source=" + $Source + " target=" + $Target
    )
    $legacyInternalAuth = Join-Path $Target "_internal\assets\auth_config.json"
    $legacySidecarAuth = Join-Path $Target "auth_config.json"
    $legacyEnv = Join-Path $Target ".env.local"
    $preservedAuth = ""
    if (Test-Path -LiteralPath $legacySidecarAuth) {
        $preservedAuth = $legacySidecarAuth
    } elseif (Test-Path -LiteralPath $legacyInternalAuth) {
        $preservedAuth = $legacyInternalAuth
    }
    if ($preservedAuth) {
        $targetSidecarAuth = Join-Path $Target "auth_config.json"
        if (
            [IO.Path]::GetFullPath($preservedAuth) -ne
            [IO.Path]::GetFullPath($targetSidecarAuth)
        ) {
            Copy-Item -LiteralPath $preservedAuth `
                -Destination $targetSidecarAuth -Force
        }
        $newInternalAssets = Join-Path $Target "_internal\assets"
        New-Item -ItemType Directory -Path $newInternalAssets -Force | Out-Null
        $targetInternalAuth = Join-Path $newInternalAssets "auth_config.json"
        if (
            [IO.Path]::GetFullPath($preservedAuth) -ne
            [IO.Path]::GetFullPath($targetInternalAuth)
        ) {
            Copy-Item -LiteralPath $preservedAuth `
                -Destination $targetInternalAuth -Force
        }
    }
    if (Test-Path -LiteralPath $legacyEnv) {
        Copy-Item -LiteralPath $legacyEnv `
            -Destination (Join-Path $Target ".env.local") -Force
    }

    Write-UpdateLog "Rollback copy: $Target -> $backup"
    Copy-TreeWithRetry $Target $backup "copy rollback before in-place update"
    Copy-TreeWithRetry $Source $Target "in-place update"
    Write-UpdateLog "In-place update completed in original folder"
    $newProcess = Start-Process `
        -FilePath (Join-Path $Target "LangLang.exe") `
        -WorkingDirectory $Target `
        -PassThru
    Start-Sleep -Milliseconds 2500
    if ($newProcess -and $newProcess.HasExited) {
        throw (
            "新版启动后立即退出，退出码 " +
            [string]$newProcess.ExitCode
        )
    }
    if ($HealthPath) {
        $healthy = $false
        $healthAttempts = [Math]::Max(1, $HealthSeconds * 2)
        for ($healthIndex = 0; $healthIndex -lt $healthAttempts; $healthIndex++) {
            if ($newProcess -and $newProcess.HasExited) {
                throw (
                    "新版在健康标记前退出，退出码 " +
                    [string]$newProcess.ExitCode
                )
            }
            if (Test-Path -LiteralPath $HealthPath) {
                try {
                    $health = Get-Content -LiteralPath $HealthPath -Raw -Encoding UTF8 |
                        ConvertFrom-Json
                    if (
                        ([string]$health.version -eq $ExpectedVersion) -and
                        ([string]$health.attempt_id -eq $AttemptId)
                    ) {
                        $healthy = $true
                        break
                    }
                } catch {
                }
            }
            Start-Sleep -Milliseconds 500
        }
        if (!$healthy) {
            throw "新版未在限定时间内写入健康标记"
        }
        Write-UpdateLog "New version health marker verified"
    } else {
        Write-UpdateLog "New version started without health marker"
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Invoke-WithRetry {
            Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction Stop
        } "remove successful rollback copy"
        $backup = ""
        Write-UpdateLog "Verified rollback copy removed"
    }
} catch {
    Write-UpdateLog ("Update failed: " + $_.Exception.Message)
    if ($newProcess -and !$newProcess.HasExited) {
        try {
            Stop-Process -Id $newProcess.Id -Force -ErrorAction Stop
            Wait-Process -Id $newProcess.Id -Timeout 30 -ErrorAction SilentlyContinue
            Write-UpdateLog "Stopped unhealthy new process before rollback"
        } catch {
            Write-UpdateLog (
                "Could not stop unhealthy new process before rollback: " +
                $_.Exception.Message
            )
        }
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        Copy-TreeWithRetry $backup $Target "restore previous version in-place"
        Write-UpdateLog "Previous version restored"
    }
    if (
        (Test-Path -LiteralPath $targetExe) -and
        (@(Get-TargetProcessIds).Count -eq 0)
    ) {
        try {
            Start-Process -FilePath (Join-Path $Target "LangLang.exe") `
                -WorkingDirectory $Target
        } catch {
            Write-UpdateLog (
                "Previous version restored but could not restart: " +
                $_.Exception.Message
            )
        }
    } elseif (Test-Path -LiteralPath $targetExe) {
        Write-UpdateLog (
            "Target process is still running; not starting another instance"
        )
    }
} finally {
    if ($lockStream) {
        $lockStream.Dispose()
    }
}
"""
    script_path.write_text(script, encoding="utf-8-sig")
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    with UpdateInstallLock(updates_root):
        attempt = begin_update_attempt(target, staged.version)
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-WaitPid",
                str(os.getpid()),
                "-Source",
                str(source),
                "-Target",
                str(target),
                "-LogPath",
                str(log_path),
                "-ExpectedVersion",
                staged.version,
                "-HealthPath",
                str(update_health_path(target)),
                "-AttemptId",
                attempt.attempt_id,
                "-WaitSeconds",
                "300",
            ],
            cwd=str(updates_root),
            close_fds=True,
            creationflags=creation_flags,
        )
    return log_path
