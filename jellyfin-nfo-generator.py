from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler
from json import dump, load
from pathlib import Path
from re import search
from re import sub as re_sub
from socketserver import TCPServer
from threading import Thread
from time import time
from urllib.parse import quote, urlparse
from webbrowser import open_new_tab

from requests import get, post
from requests.exceptions import JSONDecodeError

APP_ID = ""  # https://bgm.tv/dev/app
APP_SECRET = ""

if not APP_ID or not APP_SECRET:
    print("在 https://bgm.tv/dev/app 创建应用并填写 APP_ID 和 APP_SECRET。")
    exit()

HEADERS = {"User-Agent": "Lancercmd/jellyfin-nfo-generator"}
URL = "https://bgm.tv", "https://api.bgm.tv"
OAUTH_AUTHORIZE = URL[0] + "/oauth/authorize"
OAUTH_ACCESS_TOKEN = URL[0] + "/oauth/access_token"
OAUTH_TOKEN_STATUS = URL[0] + "/oauth/token_status"
API_SEARCH_SUBJECT = URL[1] + "/search/subject"
API_SUBJECT = URL[1] + "/subject"
API_SUBJECT_EP = API_SUBJECT + "/{}/ep"

PORT = 8001
STATE = None
STATE_PATH = Path("bangumi.json")
if STATE_PATH.exists():
    with STATE_PATH.open() as f:
        STATE = load(f)


def init():
    if not STATE or is_expired():

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self):
                global STATE
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()

                if self.path.startswith("/?code="):
                    Thread(target=self.server.shutdown).start()
                    code = urlparse(self.path).query.split("=")[1]
                    authorization_code(code)

        with TCPServer(("", PORT), Handler) as httpd:
            data = {
                "client_id": APP_ID,
                "response_type": "code",
                "redirect_uri": f"http://localhost:{PORT}",
            }
            query_s = "&".join([f"{k}={v}" for k, v in data.items()])
            open_new_tab(f"{OAUTH_AUTHORIZE}?{query_s}")
            httpd.serve_forever()
    else:
        refresh_token()
    HEADERS["Authorization"] = f'{STATE["token_type"]} {STATE["access_token"]}'


def authorization_code(code: str):
    global STATE
    data = {
        "grant_type": "authorization_code",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "code": code,
        "redirect_uri": f"http://localhost:{PORT}",
    }
    STATE = post(OAUTH_ACCESS_TOKEN, data=data, headers=HEADERS).json()
    update_state()


def update_state():
    STATE["expires"] = int(time()) + STATE["expires_in"]
    with STATE_PATH.open("w") as f:
        dump(STATE, f, ensure_ascii=False, indent=4)


def refresh_token():
    global STATE
    data = {
        "grant_type": "refresh_token",
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "refresh_token": STATE["refresh_token"],
    }
    STATE = post(OAUTH_ACCESS_TOKEN, data=data, headers=HEADERS).json()
    update_state()


def token_status():
    data = {"access_token": STATE["access_token"]}
    return post(OAUTH_TOKEN_STATUS, data=data, headers=HEADERS).json()


def is_expired():
    expires = token_status()["expires"]
    return expires < time()


def search_subject(keyword: str):
    data = {"type": 2}
    url = f"{API_SEARCH_SUBJECT}/{quote(keyword)}"
    resp: dict = get(url, params=data, headers=HEADERS).json()
    l = resp.get("list")
    if l:
        for i in l:
            i["name"] = str(i["name"]).replace("&amp;", "&")
            i["name_cn"] = str(i["name_cn"]).replace("&amp;", "&")
            if i["name"] == keyword or i["name_cn"] == keyword:
                return i
        l_ = [(k, v) for k, v in enumerate(l, 1)]
        q = "\n".join([f"{k}. {v['name_cn'] or v['name']}" for k, v in l_])
        return l_[int(input(f"{q}\n找到多个番剧，请选择：")) - 1][1]


def get_subject(sid: str):
    return get(API_SUBJECT + f"/{sid}", headers=HEADERS).json()


def get_subject_ep(sid: str):
    return get(API_SUBJECT_EP.format(sid), headers=HEADERS).json()


def get_showtitle(sid: str) -> str:
    resp: dict = get_subject(sid)
    if resp.get("type") == 2:
        return resp["name_cn"] or resp["name"]
    else:
        print(resp)


def get_episodes(sid: str) -> list[dict]:
    resp = get_subject_ep(sid)
    return resp["eps"]


def get_episodes_count(sid: str):
    return len(get_episodes(sid))


def list_all_videos(p: Path):
    return (
        list(p.glob("*.mp4"))
        + list(p.glob("*.mkv"))
        + list(p.glob("*.avi"))
        + list(p.glob("*.rmvb"))
    )


@dataclass
class Base:
    def __post_init__(self):
        self.content = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'


@dataclass
class TVShow(Base):
    bangumiid: str  # subject_id
    title: str = None
    season: str = "-1"
    episode: str = "-1"

    def __post_init__(self):
        super().__post_init__()
        self.content += "<tvshow>"
        self.content += f"<title>{self.title or get_showtitle(self.bangumiid)}</title>"
        self.content += f"<bangumiid>{self.bangumiid}</bangumiid>"
        self.content += f"<season>{self.season}</season>"
        self.content += f"<episode>{self.episode}</episode>"
        self.content += "</tvshow>"


@dataclass
class Episode(Base):
    bangumiid: str  # episode_id
    showtitle: str = None
    episode: str = None

    def __post_init__(self):
        super().__post_init__()
        self.content += "<episodedetails>"
        self.content += f"<bangumiid>{self.bangumiid}</bangumiid>"
        if self.showtitle:
            self.content += f"<title>{self.showtitle}</title>"
        if self.episode:
            self.content += f"<episode>{self.episode}</episode>"
        self.content += "</episodedetails>"


if __name__ == "__main__":
    init()
    while True:
        try:
            p = Path(re_sub(r"^(\"|\')|(\"|\')$", "", input("请输入路径：")))
            if not p.exists():
                print("路径不存在。")
                continue
            videos = list_all_videos(p)
            if not videos:
                print("路径下没有视频文件。")
                continue
            name = p.name.split(" (", 1)[0]
            subject = search_subject(name)
            id = subject["id"]
            name = subject["name_cn"] or subject["name"]
            print(f"找到番剧：{id}", name)
            n0 = p / "tvshow.nfo"
            try:
                n0.write_text(TVShow(id, title=name).content, encoding="utf-8-sig")
            except PermissionError:
                print(f"无法创建 {n0.name} 文件，权限不足。")
            eps = get_episodes(id)
            print(f"共 {len(eps)} 话，路径下有 {len(videos)} 个视频文件。")
            pattern = "|".join(
                [
                    r"\[(?P<prefix1>OAD|OVA|SP)?(?P<ep1>[\d\.]{2,4}) ?(?P<suffix>v[0-9]|END)?\]",
                    r"(?:S\d\d)? - (?P<prefix2>OAD|OVA|SP)?(?P<ep2>[\d\.]{2,4}) ",
                    r"第(?P<ep3>[\d\.]{2,4})[话話集]",
                    r" (?P<prefix4>OAD|OVA|SP)?(?P<ep4>[\d\.]{2,4}) \[",
                    r"(?P<prefix5>OAD|OVA|SP)?(?P<ep5>[\d\.]{2,4})",
                ]
            )
            n = 0
            missing = []
            offset = 0
            if not eps[0]["sort"] in (0, 1):
                offset = int(1 - eps[0]["sort"])
                print(
                    f"来自 bgm.tv 的第一话是 ep.{eps[0]['sort']}，自动应用偏移量：{offset}"
                )
                c_ = input("直接回车以应用，输入数字以更改偏移量（整数）：")
                if c_.isdigit() or (c_.startswith("-") and c_[1:].isdigit()):
                    offset = int(c_)
                elif c_:
                    print("无效输入，应用默认偏移量。")
            for i in eps:
                success = False
                for f in videos:
                    res = search(pattern, f.stem).groupdict()
                    ep = (
                        res.get("ep1")
                        or res.get("ep2")
                        or res.get("ep3")
                        or res.get("ep4")
                        or res.get("ep5")
                    )
                    if (
                        ep.zfill(6) == str(i["sort"] + offset).zfill(6)
                        or len(eps) == len(videos) == 1  # 针对剧场版等单集情况
                    ):
                        success = True
                        n1 = p / f"{f.stem}.nfo"
                        try:
                            n1.write_text(
                                Episode(i["id"]).content,
                                encoding="utf-8-sig",
                            )
                        except PermissionError:
                            print(f"无法创建 {n1.name} 文件，权限不足。")
                        n += 1
                        if n == len(videos):
                            print(
                                f"处理 {n}/{len(videos)} ({round(n/len(videos)*100, 2)}%)  "
                            )
                        else:
                            print(
                                f"处理 {n}/{len(videos)} ({round(n/len(videos)*100, 2)}%)  ",
                                end="\r",
                            )
                if not success:
                    missing.append(i["sort"])
            if missing:
                for i in missing:
                    print(f"未能匹配到第 {i} 话。")
            print("完成。")
        except KeyboardInterrupt:
            exit()
        except JSONDecodeError as e:
            print(e)
