#!/usr/bin/env python3
"""
YouTube 频道删除自动化

通过 HubStudio 或 BitBrowser 容器 + Chrome DevTools Protocol 连接到指定浏览器环境，
自动登录 Google 账号并删除该账号下的 YouTube 频道。

支持的浏览器后端：
  - HubStudio（默认，API 端口 6873）
  - BitBrowser（比特浏览器，API 端口 54345）

自动适配 macOS / Windows 平台（键盘快捷键等）。

核心流程：
  1) 导航 myaccount.google.com/youtubeoptions?hl=en
  2) 如在登录页 → 填密码 + TOTP 两步验证
  3) 检查是否已删除（"Channel already deleted"）
  4) 点 "I want to permanently delete"
  5) 勾选两个同意条款
  6) 点 "Delete my content" 打开确认对话框
  7) 从确认对话框提取确认文本（邮箱或频道名）
  8) 填入确认文本
  9) 对话框内点 "Delete my content" 完成删除
  10) 验证 URL 含 "deletesuccess"

用法：
  python3 delete_channel.py --containers 10,28
  python3 delete_channel.py --containers 10,28 --browser bitbrowser
  python3 delete_channel.py --containers 10,28 --dry-run
"""

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pyotp
from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

from credentials import get_password  # noqa: E402
from totp_codes import TOTP_SECRETS, get_totp_code  # noqa: E402
from utils import create_backend, log, SELECT_ALL_KEY  # noqa: E402

# 统计一下 TOTP 映射规模
log(f"TOTP 密钥已加载: {len(TOTP_SECRETS)} 个账号", "INFO")


# ============ 安全机制（必须硬编码，不要留空）============
YPP_PROTECTED: set[int] = set()    # 有营利权限的容器号，绝对不动
SKIP_CONTAINERS: set[int] = set()  # 出于其他原因跳过的容器号


# 强制英语，保证页面选择器稳定
DELETE_URL = "https://myaccount.google.com/u/0/youtubeoptions?hl=en"


async def human_delay(min_ms: int = 600, max_ms: int = 1400):
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_list(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    out: List[int] = []
    for x in raw.split(","):
        token = x.strip()
        if token:
            out.append(int(token))
    return out


def extract_email(body: str) -> Optional[str]:
    """从页面文本里抓邮箱。"""
    m = re.search(r"[\w.+-]+@gmail\.com", body, re.IGNORECASE)
    return m.group(0) if m else None


def extract_confirm_text(body: str) -> Optional[str]:
    """从确认对话框文本里抓邮箱或频道名。"""
    m = re.search(
        r"(?:email address|channel name)\s*\(([^)]+)\)", body, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()

    m2 = re.search(r"\(([^)]+@gmail\.com)\)", body, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()

    all_parens = re.findall(r"\(([^)]+)\)", body)
    for item in all_parens:
        t = item.strip()
        if not t:
            continue
        if "@gmail.com" in t or any("\u4e00" <= c <= "\u9fff" for c in t):
            return t
    return None


async def delete_channel(container: int, screenshot_dir: Path, backend) -> Dict:
    """单频道删除完整流程"""
    result: Dict = {
        "container": container,
        "status": "failed",
        "started_at": now_iso(),
        "finished_at": None,
        "email": None,
        "confirm_text": None,
        "url_after": None,
        "error": None,
    }

    try:
        # 0) 安全检查
        if container in YPP_PROTECTED:
            raise Exception(f"BLOCKED: Container {container} is YPP")
        if container in SKIP_CONTAINERS:
            raise Exception(f"SKIPPED: Container {container} is in SKIP_CONTAINERS")

        # 1) CDP 连接
        port = backend.get_port_by_env(container)
        if not port:
            raise Exception(f"未获取到 container {container} 的调试端口")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
            if not browser.contexts:
                raise Exception("CDP 已连接但无可用 context")

            context = browser.contexts[0]
            context.set_default_timeout(45000)

            page = None
            if context.pages:
                for pg in context.pages:
                    if not (pg.url or "").startswith("chrome-extension://"):
                        page = pg
                        break
                if page is None:
                    page = context.pages[0]
            else:
                page = await context.new_page()

            await page.bring_to_front()

            # 2) 导航删除页（强制英文）
            current_url = page.url or ""
            if (
                "accounts.google.com" not in current_url
                and "youtubeoptions" not in current_url
            ):
                await page.goto(DELETE_URL, wait_until="domcontentloaded", timeout=120000)
            await human_delay(2000, 3000)

            # 3) Google 登录流程（密码 + TOTP）
            if "accounts.google.com" in page.url:
                body = await page.inner_text("body")
                email = extract_email(body)
                result["email"] = email

                # 3a) 密码页
                pw_box = page.get_by_role("textbox", name="Enter your password")
                if await pw_box.count() > 0:
                    password = get_password(email)
                    if not password:
                        raise Exception(f"credentials.py 未找到邮箱密码: {email}")

                    await pw_box.click()
                    await human_delay(300, 600)
                    await page.keyboard.type(password, delay=50)
                    await human_delay(500, 1000)
                    await page.get_by_role("button", name="Next").click()
                    await human_delay(5000, 8000)

                # 3b) TOTP 页
                body_now = await page.inner_text("body")
                is_totp = (
                    "2-Step Verification" in body_now
                    or "Enter code" in body_now
                    or "challenge/totp" in page.url
                )
                if is_totp:
                    if not email:
                        email = extract_email(body_now)
                    totp_code = get_totp_code(email) if email else None
                    if not totp_code:
                        raise Exception(f"需要 TOTP 但无密钥: {email}")

                    log(
                        f"  [{container}] 填入 TOTP: {totp_code} ({email})",
                        "INFO",
                    )

                    totp_input = None
                    for selector in [
                        '#totpPin',
                        'input[type="tel"]',
                        'input[name="totpPin"]',
                    ]:
                        loc = page.locator(selector)
                        if await loc.count() > 0:
                            totp_input = loc.first
                            break
                    if not totp_input:
                        totp_input = page.get_by_placeholder(
                            re.compile(r"Enter.*code|code", re.IGNORECASE)
                        )

                    await totp_input.click()
                    await human_delay(300, 600)
                    await page.keyboard.type(totp_code, delay=50)
                    await human_delay(500, 1000)

                    await page.get_by_role("button", name="Next").click()
                    await human_delay(8000, 12000)

            # 4) 到达 youtubeoptions
            if "youtubeoptions" not in page.url:
                await human_delay(5000, 8000)
            if "youtubeoptions" not in page.url:
                raise Exception(f"未到达 youtubeoptions 页面: {page.url}")
            await human_delay(3000, 5000)

            body_now = await page.inner_text("body")
            if "Channel already deleted" in body_now:
                result["status"] = "already_deleted"
                result["url_after"] = page.url
                result["finished_at"] = now_iso()
                return result

            # 5) B1 展开
            await page.get_by_role(
                "button", name="I want to permanently delete"
            ).click()
            await human_delay(1000, 2000)

            # 6) B2-B3 勾选
            await page.get_by_role(
                "checkbox", name="The following will be"
            ).check()
            await human_delay(500, 1000)
            await page.get_by_role(
                "checkbox", name="Any paid subscriptions that"
            ).check()
            await human_delay(500, 1000)

            # 7) B4 点击第一层 Delete
            await page.get_by_role(
                "button", name="Delete my content", exact=True
            ).click()
            await human_delay(2000, 3000)

            # 8) B5 提取确认文本
            body = await page.inner_text("body")
            confirm_text = extract_confirm_text(body)
            if not confirm_text:
                raise Exception("未提取到确认文本（邮箱/频道名）")
            result["confirm_text"] = confirm_text

            # 9) B6 输入确认文本
            textbox = page.get_by_role(
                "textbox",
                name=re.compile(
                    r"Type in your (channel name|email address)", re.IGNORECASE
                ),
            )
            await textbox.click()
            await human_delay(300, 600)
            await page.keyboard.type(confirm_text, delay=50)
            await human_delay(500, 1000)

            # 10) B7 对话框内最终确认
            await page.locator('[role="dialog"]').get_by_role(
                "button", name="Delete my content"
            ).click()
            await human_delay(5000, 8000)

            # 11) 验证成功
            if "deletesuccess" not in page.url:
                raise Exception(f"未进入 deletesuccess 页面: {page.url}")

            screenshot_dir.mkdir(parents=True, exist_ok=True)
            await page.screenshot(
                path=str(screenshot_dir / f"{container}_success.png")
            )
            result["status"] = "success"
            result["url_after"] = page.url

    except Exception as exc:
        result["error"] = str(exc)

    result["finished_at"] = now_iso()
    return result


async def run_batch(containers: List[int], backend) -> List[Dict]:
    results: List[Dict] = []
    screenshot_dir = SCRIPT_DIR / "screenshots" / "delete"

    for container in containers:
        result = await delete_channel(container, screenshot_dir, backend)
        results.append(result)

        if result["status"] == "success":
            log(f"Container {container} 删除成功 ✅", "OK")
        elif result["status"] == "already_deleted":
            log(f"Container {container} 之前已删除 ⏭️", "WARN")
        else:
            log(f"Container {container} 失败: {result['error']} ❌", "ERR")

        await human_delay(3000, 5000)

    return results


def build_args():
    parser = argparse.ArgumentParser(description="YouTube 频道删除自动化")
    parser.add_argument(
        "--containers",
        type=str,
        required=True,
        help="要删除的容器号列表，逗号分隔（例：10,28,33）",
    )
    parser.add_argument(
        "--browser",
        type=str,
        choices=["hubstudio", "bitbrowser"],
        default="hubstudio",
        help="浏览器后端：hubstudio（默认）或 bitbrowser（比特浏览器）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(SCRIPT_DIR / "delete_results.json"),
        help="结果输出 JSON 路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印删除计划，不实际执行",
    )
    return parser.parse_args()


async def main():
    args = build_args()
    containers = parse_list(args.containers)
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    backend = create_backend(args.browser)
    log(f"浏览器后端: {backend.name}", "INFO")
    log(f"运行平台: {'macOS' if sys.platform == 'darwin' else 'Windows' if sys.platform == 'win32' else sys.platform}", "INFO")

    log(f"YPP_PROTECTED: {sorted(YPP_PROTECTED)}", "WARN")
    log(f"SKIP_CONTAINERS: {sorted(SKIP_CONTAINERS)}", "WARN")
    log(f"待删除容器: {containers}", "INFO")

    if args.dry_run:
        log("\n--dry-run 模式，不实际执行。", "WARN")
        return

    log("\n⚠️ 删除是不可逆操作。5 秒后开始执行...", "WARN")
    await asyncio.sleep(5)

    results = await run_batch(containers, backend)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"结果已写入: {output_path}", "OK")

    if any(x.get("status") == "failed" for x in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
