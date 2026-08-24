"""GrimTools build-link client used by the save generator."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


BUILD_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class GrimToolsError(ValueError):
    pass


@dataclass(frozen=True)
class GrimToolsBuild:
    build_id: str
    current: dict[str, Any]
    expanded: dict[str, Any]

    @property
    def game_version(self) -> str:
        return str(
            self.current.get("created_for_build")
            or self.expanded.get("created_for_build")
            or "未知"
        )

    @property
    def data(self) -> dict[str, Any]:
        data = self.expanded.get("data")
        if not isinstance(data, dict):
            raise GrimToolsError("GrimTools 返回结果中缺少 data")
        return data

    @property
    def masteries(self) -> list[str]:
        raw = self.current.get("masteries", {})
        if isinstance(raw, dict):
            values = [raw[key] for key in sorted(raw, key=lambda x: int(x))]
        elif isinstance(raw, list):
            values = raw
        else:
            values = []
        result = [str(value).zfill(2) for value in values if value is not None]
        if result:
            return result

        inferred: list[str] = []
        for skill in self.data.get("skills", []):
            name = str(skill.get("name", ""))
            match = re.search(r"/playerclass(\d{2})/_classtraining_", name)
            if match and match.group(1) not in inferred:
                inferred.append(match.group(1))
        return inferred


def extract_build_id(url_or_id: str) -> str:
    value = url_or_id.strip()
    if not value:
        raise GrimToolsError("构筑链接不能为空")

    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise GrimToolsError("构筑链接必须使用 http 或 https")
        if parsed.netloc.lower() not in {"grimtools.com", "www.grimtools.com"}:
            raise GrimToolsError("目前只支持 www.grimtools.com 的构筑链接")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0].lower() != "calc":
            raise GrimToolsError("不是有效的 GrimTools 构筑链接")
        value = parts[1]

    if not BUILD_ID_RE.fullmatch(value):
        raise GrimToolsError(f"无效的 GrimTools 构筑 ID：{value!r}")
    return value


def _get_json(url: str, post_data: dict[str, str] | None = None) -> dict[str, Any]:
    encoded = urlencode(post_data).encode("ascii") if post_data is not None else None
    request = Request(
        url,
        data=encoded,
        headers={
            "User-Agent": "GrimDawnGenerateSave/0.1",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except HTTPError as exc:
        raise GrimToolsError(f"GrimTools 请求失败：HTTP {exc.code}") from exc
    except URLError as exc:
        raise GrimToolsError(f"无法连接 GrimTools：{exc.reason}") from exc

    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GrimToolsError("GrimTools 返回了无法识别的数据") from exc
    if not isinstance(result, dict):
        raise GrimToolsError("GrimTools 返回的数据格式不正确")
    if result.get("error"):
        raise GrimToolsError(f"GrimTools 拒绝了该构筑：{result['error']}")
    return result


def fetch_build(url_or_id: str) -> GrimToolsBuild:
    build_id = extract_build_id(url_or_id)
    current = _get_json(
        f"https://www.grimtools.com/load_build.php?id={build_id}",
        {"token": "", "mod": ""},
    )
    expanded = _get_json(
        f"https://www.grimtools.com/get_build_data.php?id={build_id}"
    )
    if "data" not in current or "data" not in expanded:
        raise GrimToolsError("构筑不存在，或 GrimTools 接口格式已经变化")
    return GrimToolsBuild(build_id, current, expanded)
