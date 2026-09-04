"""Bangumi → Jellyfin NFO 生成器

通过 Bangumi (bgm.tv) API 查询番剧信息，为本地视频文件生成 Jellyfin 兼容的 NFO 元数据。
仅使用 Python 标准库，无需安装第三方依赖。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from html import escape as html_escape
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer
from threading import Thread
from time import time
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen
from webbrowser import open_new_tab

# ── Bangumi OAuth ──────────────────────────────────────────────────
APP_ID = ""  # https://bgm.tv/dev/app
APP_SECRET = ""

if not APP_ID or not APP_SECRET:
    print("在 https://bgm.tv/dev/app 创建应用并填写 APP_ID 和 APP_SECRET。")
    sys.exit(1)

HEADERS: dict[str, str] = {"User-Agent": "Lancercmd/jellyfin-nfo-generator"}
_BASE_WEB = "https://bgm.tv"
_BASE_API = "https://api.bgm.tv"

OAUTH_AUTHORIZE = f"{_BASE_WEB}/oauth/authorize"
OAUTH_ACCESS_TOKEN = f"{_BASE_WEB}/oauth/access_token"
OAUTH_TOKEN_STATUS = f"{_BASE_WEB}/oauth/token_status"
API_SEARCH_SUBJECT = f"{_BASE_API}/search/subject"
API_SUBJECT = f"{_BASE_API}/subject"
API_SUBJECT_EP = f"{API_SUBJECT}/{{}}/ep"

PORT = 8001

# ── 支持的视频扩展名 ──────────────────────────────────────────────
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".rmvb", ".flv", ".wmv", ".ts"}

# ── 字幕文件规范化 ────────────────────────────────────────────────
SUBTITLE_EXTENSIONS = {".ass", ".ssa", ".srt", ".sub", ".vtt"}
# 需要重命名的后缀 → Jellyfin 标准语言代码
SUBTITLE_RENAME_MAP = {
    ".sc": ".zh",      # Simplified Chinese → Chinese
    ".chs": ".zh",     # Chinese Simplified → Chinese
    ".scjp": ".zh",    # Simplified Chinese + Japanese → Chinese
    ".tc": ".zh-Hant", # Traditional Chinese
    ".cht": ".zh-Hant",
}

# ── 集数提取正则 ──────────────────────────────────────────────────
EPISODE_PATTERNS = [
    # [01], [01v2], [01 END], [OAD01], [OVA01], [SP01]
    re.compile(r"\[(?P<prefix>OAD|OVA|SP)?(?P<ep>[\d.]{2,4})\s?(?P<suffix>v\d|END)?\]"),
    # S01 - 01 , S01 - OVA01
    re.compile(r"(?:S\d{2})?\s*-\s*(?P<prefix>OAD|OVA|SP)?(?P<ep>[\d.]{2,4})\s"),
    # 第01话, 第01集, 第01話
    re.compile(r"第(?P<ep>[\d.]{2,4})[话話集]"),
    # 01 [ (standalone number followed by bracket)
    re.compile(r"(?P<prefix>OAD|OVA|SP)?(?P<ep>[\d.]{2,4})\s*\["),
    # bare number: 01, 001
    re.compile(r"(?P<prefix>OAD|OVA|SP)?(?P<ep>[\d.]{2,4})$"),
    # #01
    re.compile(r"#(?P<ep>[\d.]{1,3})\s"),
]

# ── 状态持久化 ────────────────────────────────────────────────────
STATE: Optional[dict] = None
STATE_PATH = Path("bangumi.json")

if STATE_PATH.exists():
    try:
        STATE = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"⚠ 读取 {STATE_PATH} 失败: {exc}，将重新认证。")
        STATE = None


# ── HTTP 工具（标准库） ───────────────────────────────────────────
def _http_get(url: str, params: Optional[dict] = None, timeout: int = 15) -> dict:
    """发送 GET 请求并返回 JSON。"""
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        print(f"❌ 请求失败 ({url}): HTTP {exc.code} {exc.reason}")
        return {}
    except (URLError, OSError) as exc:
        print(f"❌ 请求失败 ({url}): {exc}")
        return {}
    except json.JSONDecodeError:
        print(f"❌ JSON 解析失败 ({url})")
        return {}


def _http_post(url: str, data: dict, timeout: int = 15) -> dict:
    """发送 POST 请求并返回 JSON。"""
    body = urlencode(data).encode("utf-8")
    headers = {**HEADERS, "Content-Type": "application/x-www-form-urlencoded"}
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"❌ 请求失败 ({url}): HTTP {exc.code} — {error_body}")
        return {}
    except (URLError, OSError) as exc:
        print(f"❌ 请求失败 ({url}): {exc}")
        return {}
    except json.JSONDecodeError:
        print(f"❌ JSON 解析失败 ({url})")
        return {}


# ── OAuth ──────────────────────────────────────────────────────────
def init() -> None:
    """初始化 OAuth 认证，获取或刷新 access_token。"""
    global STATE

    if STATE is None or _is_expired():

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self_inner) -> None:
                global STATE
                self_inner.send_response(200)
                self_inner.send_header("Content-type", "text/html; charset=utf-8")
                self_inner.end_headers()

                if self_inner.path.startswith("/?code="):
                    Thread(target=self_inner.server.shutdown, daemon=True).start()
                    code = urlparse(self_inner.path).query.split("=", 1)[1]
                    _authorization_code(code)
                    self_inner.wfile.write("✅ 认证成功，可以关闭此页面。".encode("utf-8"))
                else:
                    self_inner.wfile.write("等待 OAuth 回调…".encode("utf-8"))

            def log_message(self_inner, format, *args) -> None:
                pass  # 静默 HTTP 日志

        with TCPServer(("", PORT), Handler) as httpd:
            data = {
                "client_id": APP_ID,
                "response_type": "code",
                "redirect_uri": f"http://localhost:{PORT}",
            }
            query_s = "&".join(f"{k}={v}" for k, v in data.items())
            url = f"{OAUTH_AUTHORIZE}?{query_s}"
            print(f"🌐 正在打开浏览器进行认证…\n   {url}")
            open_new_tab(url)
            httpd.serve_forever()
    else:
        _refresh_token()

    HEADERS["Authorization"] = f'{STATE["token_type"]} {STATE["access_token"]}'
    print("✅ 认证完成。")


def _authorization_code(code: str) -> None:
    global STATE
    data = {
        "grant_type": "authorization_code",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "code": code,
        "redirect_uri": f"http://localhost:{PORT}",
    }
    STATE = _http_post(OAUTH_ACCESS_TOKEN, data)
    if STATE:
        _update_state()
    else:
        print("❌ 授权失败。")
        sys.exit(1)


def _update_state() -> None:
    STATE["expires"] = int(time()) + STATE["expires_in"]
    STATE_PATH.write_text(
        json.dumps(STATE, ensure_ascii=False, indent=4), encoding="utf-8"
    )


def _refresh_token() -> None:
    global STATE
    data = {
        "grant_type": "refresh_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "refresh_token": STATE["refresh_token"],
    }
    result = _http_post(OAUTH_ACCESS_TOKEN, data)
    if result:
        STATE = result
        _update_state()
        print("🔄 Token 已刷新。")
    else:
        print("❌ 刷新 Token 失败，请重新运行以重新认证。")
        STATE = None
        sys.exit(1)


def _token_status() -> dict:
    data = {"access_token": STATE["access_token"]}
    return _http_post(OAUTH_TOKEN_STATUS, data, timeout=10)


def _is_expired() -> bool:
    try:
        status = _token_status()
        return status.get("expires", 0) < time()
    except Exception:
        return True


# ── Bangumi API ───────────────────────────────────────────────────
def search_subject(keyword: str) -> Optional[dict]:
    """按关键词搜索番剧，返回用户选择的条目。"""
    data = {"type": 2}
    url = f"{API_SEARCH_SUBJECT}/{quote(keyword)}"
    resp = _http_get(url, data)
    items = resp.get("list")

    if not items:
        print(f"🔍 未找到与「{keyword}」相关的番剧。")
        return None

    # 处理 HTML 实体
    for item in items:
        item["name"] = str(item["name"]).replace("&amp;", "&")
        item["name_cn"] = str(item["name_cn"]).replace("&amp;", "&")

    # 精确匹配优先
    for item in items:
        if item["name"] == keyword or item["name_cn"] == keyword:
            return item

    # 多个结果 → 交互选择
    print(f"\n找到 {len(items)} 个结果：")
    for idx, item in enumerate(items, 1):
        display = item["name_cn"] or item["name"]
        air_date = item.get("air_date", "未知")
        print(f"  {idx}. {display}  ({air_date})")

    while True:
        try:
            choice = input(f"\n请选择 (1-{len(items)})，直接回车取消：").strip()
            if not choice:
                return None
            n = int(choice)
            if 1 <= n <= len(items):
                return items[n - 1]
            print(f"请输入 1-{len(items)} 之间的数字。")
        except ValueError:
            print("请输入有效的数字。")
        except (EOFError, KeyboardInterrupt):
            print()
            return None


def get_subject(subject_id: str) -> dict:
    return _http_get(f"{API_SUBJECT}/{subject_id}")


def get_episodes(subject_id: str) -> list[dict]:
    resp = _http_get(API_SUBJECT_EP.format(subject_id))
    return resp.get("eps", [])


def get_showtitle(subject_id: str) -> str:
    resp = get_subject(subject_id)
    if resp.get("type") == 2:
        return resp.get("name_cn") or resp.get("name", subject_id)
    return resp.get("name_cn") or resp.get("name", subject_id)


# ── 视频文件扫描 ──────────────────────────────────────────────────
def list_all_videos(directory: Path) -> list[Path]:
    """列出目录下所有视频文件，按文件名排序。"""
    videos = [
        f
        for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    ]
    videos.sort(key=lambda f: f.name)
    return videos


# ── 字幕文件规范化 ────────────────────────────────────────────────
def _find_subtitles_to_rename(directory: Path) -> list[tuple[Path, str]]:
    """查找需要规范命名的字幕文件。

    Returns:
        [(原始路径, 新文件名), ...]
    """
    renames: list[tuple[Path, str]] = []
    for f in directory.iterdir():
        if not f.is_file():
            continue
        stem_lower = f.stem.lower()
        for suffix, target in SUBTITLE_RENAME_MAP.items():
            if stem_lower.endswith(suffix):
                new_name = f.stem[: -len(suffix)] + target + f.suffix
                renames.append((f, new_name))
                break
    return renames


def _preview_and_rename_subtitles(renames: list[tuple[Path, str]]) -> None:
    """预览字幕重命名计划，询问用户确认后执行。"""
    if not renames:
        return

    print(f"\n📝 检测到 {len(renames)} 个字幕文件需要规范命名：")
    print("-" * 60)
    for old_path, new_name in renames:
        print(f"  {old_path.name}")
        print(f"    → {new_name}")
    print("-" * 60)

    try:
        choice = input("是否执行重命名？(y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice not in ("y", "yes"):
        print("⏭ 跳过字幕重命名。")
        return

    renamed_count = 0
    for old_path, new_name in renames:
        new_path = old_path.parent / new_name
        try:
            old_path.rename(new_path)
            renamed_count += 1
            print(f"  ✅ {old_path.name} → {new_name}")
        except OSError as exc:
            print(f"  ⚠ 重命名失败 {old_path.name}: {exc}")

    print(f"  完成，共重命名 {renamed_count}/{len(renames)} 个字幕文件。")


# ── NFO 数据类 ────────────────────────────────────────────────────
def _xml_escape(text: str) -> str:
    """转义 XML 特殊字符。"""
    return html_escape(str(text), quote=False)


@dataclass
class Base:
    content: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.content = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'


@dataclass
class TVShow(Base):
    bangumiid: str  # subject_id
    title: Optional[str] = None
    season: str = "-1"
    episode: str = "-1"

    def __post_init__(self) -> None:
        super().__post_init__()
        resolved_title = self.title or get_showtitle(self.bangumiid)
        self.content += "<tvshow>"
        self.content += f"<title>{_xml_escape(resolved_title)}</title>"
        self.content += f"<bangumiid>{_xml_escape(self.bangumiid)}</bangumiid>"
        self.content += f"<season>{self.season}</season>"
        self.content += f"<episode>{self.episode}</episode>"
        self.content += "</tvshow>"


@dataclass
class Episode(Base):
    bangumiid: str  # episode id
    showtitle: Optional[str] = None
    episode: Optional[str] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        self.content += "<episodedetails>"
        self.content += f"<bangumiid>{_xml_escape(self.bangumiid)}</bangumiid>"
        if self.showtitle:
            self.content += f"<title>{_xml_escape(self.showtitle)}</title>"
        if self.episode:
            self.content += f"<episode>{self.episode}</episode>"
        self.content += "</episodedetails>"


# ── 集数提取 ──────────────────────────────────────────────────────
def _extract_episode(filename_stem: str) -> Optional[str]:
    """从文件名中提取集数，返回原始字符串（如 '01', '01.5'）。"""
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(filename_stem)
        if match:
            return match.group("ep")
    return None


def _write_nfo(path: Path, content: str) -> bool:
    """写入 NFO 文件，返回是否成功。"""
    try:
        path.write_text(content, encoding="utf-8-sig")
        return True
    except PermissionError:
        print(f"  ⚠ 权限不足，无法创建 {path.name}")
        return False
    except OSError as exc:
        print(f"  ⚠ 写入 {path.name} 失败: {exc}")
        return False


# ── 主流程 ────────────────────────────────────────────────────────
def process_directory(directory: Path) -> None:
    """处理单个番剧目录：生成 tvshow.nfo 和各集 nfo。"""
    videos = list_all_videos(directory)
    if not videos:
        print("📂 路径下没有视频文件。")
        return

    # 从目录名提取番剧名（去掉年份等后缀）
    name = directory.name.split(" (", 1)[0]
    subject = search_subject(name)
    if subject is None:
        return

    subject_id = subject["id"]
    show_title = subject["name_cn"] or subject["name"]
    print(f"📺 找到番剧：{subject_id} - {show_title}")

    # tvshow.nfo
    nfo_tvshow = directory / "tvshow.nfo"
    _write_nfo(nfo_tvshow, TVShow(subject_id, title=show_title).content)
    print(f"  ✅ {nfo_tvshow.name}")

    # 获取集数列表
    eps = get_episodes(subject_id)
    if not eps:
        print("  ⚠ 无法获取集数信息。")
        return

    print(f"  共 {len(eps)} 话，{len(videos)} 个视频文件。")

    # 处理集数偏移
    offset = 0
    if eps[0]["sort"] not in (0, 1):
        offset = int(1 - eps[0]["sort"])
        print(f"  ℹ 第一话为 ep.{eps[0]['sort']}，自动偏移量：{offset}")
        try:
            c_ = input("  直接回车应用，或输入整数偏移量：").strip()
            if c_:
                if c_.lstrip("-").isdigit():
                    offset = int(c_)
                else:
                    print("  ⚠ 无效输入，使用默认偏移量。")
        except (EOFError, KeyboardInterrupt):
            print()

    # 为每个视频匹配集数
    matched = 0
    missing: list[str] = []

    for ep_info in eps:
        ep_sort = ep_info["sort"]
        success = False

        for video in videos:
            extracted = _extract_episode(video.stem)
            if extracted is None:
                continue

            target = str(ep_sort + offset).zfill(6)
            if extracted.zfill(6) == target or (len(eps) == len(videos) == 1):
                # 匹配成功（或单集剧场版）
                success = True
                nfo_path = directory / f"{video.stem}.nfo"
                _write_nfo(nfo_path, Episode(ep_info["id"]).content)
                matched += 1
                pct = round(matched / len(videos) * 100, 1)
                print(f"\r  处理 {matched}/{len(videos)} ({pct}%)", end="", flush=True)
                break

        if not success:
            missing.append(ep_sort)

    print()  # 换行

    if missing:
        print(f"  ⚠ 未能匹配到以下集数：{', '.join(str(m) for m in missing)}")

    # 字幕文件规范化
    subtitle_renames = _find_subtitles_to_rename(directory)
    _preview_and_rename_subtitles(subtitle_renames)

    print("  ✅ 完成。")


def main() -> None:
    print("=" * 50)
    print("  Bangumi → Jellyfin NFO 生成器")
    print("=" * 50)

    init()

    while True:
        try:
            raw = input("\n请输入番剧目录路径：").strip().strip("\"'")
            if not raw:
                continue

            directory = Path(raw)
            if not directory.exists():
                print("❌ 路径不存在。")
                continue
            if not directory.is_dir():
                print("❌ 路径不是一个目录。")
                continue

            process_directory(directory)

        except KeyboardInterrupt:
            print("\n👋 再见！")
            break
        except json.JSONDecodeError as exc:
            print(f"❌ JSON 解析错误: {exc}")
        except Exception as exc:
            print(f"❌ 未预期的错误: {exc}")


if __name__ == "__main__":
    main()
