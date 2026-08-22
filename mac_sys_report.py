#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mac_sys_report.py — 把 Mac 状态上报给「幻梦 Bot」的 /sys

适用：Mac 没有快捷指令（或不想用），用本脚本手动上报。
数据：电池 / CPU / 内存 / 正在播放 / 网络 / 前台 App
无需 pip 安装：全部用系统命令 + 标准库。

用法：
  终端运行：  python3 mac_sys_report.py
  （脚本会自动从 GitHub 固定发现入口拉取当前隧道 URL）

  可选覆盖（优先级从低到高）：
    1. 改脚本顶部 TUNNEL_URL
    2. 环境变量 SYS_TUNNEL_URL
    3. 命令行：python3 mac_sys_report.py --url https://xxx.localhost.run/phone

关于隧道 URL 自动切换：
  VM 上的 phone_tunnel.sh 会在 localhost.run/lhr.life 域名轮换后，
  自动把最新 URL 同步到 GitHub 仓库（wss542/huanmeng-kook-bot）的 phone_tunnel_url.txt。
  本脚本默认从固定的 raw.githubusercontent.com 地址读取它，
  所以你不用手动改文件里的 URL。
"""
import os
import re
import sys
import time
import json
import subprocess
import urllib.request
import urllib.parse

# ══ 配置 ══
# 方式零（默认）：自动从 GitHub 固定发现入口读取当前隧道 URL。
# VM 上的 phone_tunnel.sh 会在域名轮换后自动更新这个文件。
# 国内访问 raw.githubusercontent.com 较慢，用 gh-proxy 加速；如果 Mac 能直连，可改回官方地址。
DISCOVERY_URL = "https://gh-proxy.com/https://raw.githubusercontent.com/wss542/huanmeng-kook-bot/master/phone_tunnel_url.txt"

# 方式一：直接改这里（如果为空则走 DISCOVERY_URL 自动发现）
TUNNEL_URL = ""   # 例：https://abcd1234.localhost.run
# 方式二：环境变量 SYS_TUNNEL_URL 覆盖
# 方式三：命令行 --url 覆盖（优先级最高）

# 与 VM 端 BOT_PHONE_KEY 一致（默认 HMphone2026_kQ2，如改过请同步）
KEY = "HMphone2026_kQ2"

# GitHub PAT（可选）：如果配置了，发现入口会走 GitHub API（无缓存，最快）。
# 不配置则走 gh-proxy 镜像（有缓存，约 1–5 分钟延迟）。
# 可从环境变量读取：export GITHUB_PAT=ghp_xxx
GITHUB_PAT = os.environ.get("GITHUB_PAT", "")

DEVICE = "Mac"


def sh(cmd: str, timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def get_battery() -> dict:
    out = sh("pmset -g batt")
    if not out:
        return {"level": None, "charging": False, "low_power": False}
    m = re.search(r"(\d+)%", out)
    level = int(m.group(1)) if m else None
    charging = "AC Power" in out
    return {"level": level, "charging": charging, "low_power": False}


def get_cpu() -> float | None:
    out = sh("top -l 1 -n 0 | grep 'CPU usage'")
    m = re.search(r"(\d+(?:\.\d+)?)%\s*idle", out)
    if m:
        idle = float(m.group(1))
        return round(100 - idle, 1)
    return None


def get_mem() -> float | None:
    total = sh("sysctl -n hw.memsize")
    try:
        total_b = int(total)
    except (ValueError, TypeError):
        return None
    if total_b <= 0:
        return None
    vm = sh("vm_stat")
    ms = re.search(r"page size of (\d+)", vm)
    pagesize = int(ms.group(1)) if ms else 4096
    def pages(name: str) -> int:
        mm = re.search(rf"{name}:\s+(\d+)", vm)
        return int(mm.group(1)) if mm else 0
    used_pages = (pages("Pages active") + pages("Pages inactive")
                  + pages("Pages wired down") + pages("Pages occupied by compressor"))
    used_b = used_pages * pagesize
    return round(used_b / total_b * 100, 1)


def get_music() -> dict:
    # Apple Music
    out = sh('osascript -e \'tell application "Music" to if running then tell current track to return (name & "|||" & artist & "|||" & (get player state as text))\' 2>/dev/null')
    if out and "|||" in out:
        p = out.split("|||")
        song = p[0].strip()
        artist = p[1].strip() if len(p) > 1 else ""
        state = p[2].strip().lower() if len(p) > 2 else ""
        if song:
            return {"song": song, "artist": artist, "playing": "play" in state, "app": "Music"}
    # Spotify
    out2 = sh('osascript -e \'tell application "Spotify" to if running then return (current track\\\'s name & "|||" & current track\\\'s artist & "|||" & (player state as text))\' 2>/dev/null')
    if out2 and "|||" in out2:
        p = out2.split("|||")
        song = p[0].strip()
        artist = p[1].strip() if len(p) > 1 else ""
        state = p[2].strip().lower() if len(p) > 2 else ""
        if song:
            return {"song": song, "artist": artist, "playing": "play" in state, "app": "Spotify"}
    return {}


def get_network() -> dict:
    ssid = sh("/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport -I | awk -F': ' '/ SSID/{print $2}'")
    if ssid:
        return {"wifi": ssid, "cellular": False}
    return {"wifi": "", "cellular": False}


def get_front_app() -> str:
    return sh('osascript -e "tell application \\"System Events\\" to get name of first application process whose frontmost is true" 2>/dev/null')


def _fetch_from_github_api() -> str:
    """通过 GitHub API 读文件内容（无 CDN 缓存，最快最准）。需要 GITHUB_PAT。"""
    import base64, json
    api = "https://api.github.com/repos/wss542/huanmeng-kook-bot/contents/phone_tunnel_url.txt"
    req = urllib.request.Request(
        api,
        headers={
            "Authorization": f"token {GITHUB_PAT}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "huanmeng-mac-reporter",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        content = data.get("content", "")
        return base64.b64decode(content).decode("utf-8").strip()


def _fetch_from_ghproxy() -> str:
    """通过 gh-proxy 镜像读 raw 文件（无需 token，但有缓存延迟）。"""
    req = urllib.request.Request(
        DISCOVERY_URL,
        headers={"User-Agent": "huanmeng-mac-reporter", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read().decode("utf-8").strip()


def resolve_tunnel_url(preferred: str = "") -> str:
    """如果用户没显式给 URL，就从 GitHub 固定发现入口拉取当前隧道 URL。"""
    if preferred:
        return preferred

    # 优先 GitHub API（无缓存）
    if GITHUB_PAT:
        print("正在通过 GitHub API 读取当前隧道 URL（无缓存）")
        try:
            url = _fetch_from_github_api()
            if url.startswith("http"):
                print(f"GitHub API 返回：{url}")
                return url
        except Exception as e:
            print(f"GitHub API 失败，回退 gh-proxy：{e}")

    print(f"正在从 gh-proxy 镜像读取当前隧道 URL：{DISCOVERY_URL}")
    try:
        url = _fetch_from_ghproxy()
        if not url:
            raise ValueError("发现入口返回空")
        if not url.startswith("http"):
            raise ValueError(f"发现入口返回非法 URL: {url}")
        print(f"发现入口返回：{url}")
        return url
    except Exception as e:
        raise RuntimeError(f"无法从发现入口获取隧道 URL：{e}")


def ensure_phone_endpoint(url: str) -> str:
    """发现入口返回的是根地址，需要补成 /phone 上报端点。"""
    if "/phone" not in url:
        url = url.rstrip("/") + "/phone"
    return url


def post(url: str, key: str, payload: dict) -> str:
    sep = "?" if "?" not in url else "&"
    full = f"{url}{sep}key={urllib.parse.quote(key)}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(full, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8")


def main():
    url = os.environ.get("SYS_TUNNEL_URL") or TUNNEL_URL
    if len(sys.argv) > 2 and sys.argv[1] in ("-u", "--url"):
        url = sys.argv[2]
    try:
        url = resolve_tunnel_url(url)
        url = ensure_phone_endpoint(url)
    except Exception as e:
        print("✗", e)
        print("  提示：可以直接改脚本顶部 TUNNEL_URL，或设环境变量 SYS_TUNNEL_URL，"
              "或加 --url https://xxx.localhost.run/phone")
        return

    payload = {
        "device": DEVICE,
        "ts": time.time(),
        "battery": get_battery(),
        "cpu": get_cpu(),
        "mem": get_mem(),
        "music": get_music(),
        "network": get_network(),
        "app": get_front_app(),
    }
    print("采集到：", json.dumps({k: v for k, v in payload.items() if k != "ts"},
                                 ensure_ascii=False))
    try:
        result = post(url, KEY, payload)
        print("✓ 上报成功：", result)
    except Exception as e:
        print("✗ 上报失败：", e)


if __name__ == "__main__":
    main()
