#!/usr/bin/env python3
"""
YouTube 频道电话验证（中级验证）自动化

通过 HubStudio 或 BitBrowser 容器 + 5sim.net 接码平台自动完成手机验证。

支持的浏览器后端：
  - HubStudio（默认，API 端口 6873）
  - BitBrowser（比特浏览器，API 端口 54345）

自动适配 macOS / Windows 平台（键盘快捷键等）。

工作流程：
  1) 导航 https://www.youtube.com/verify
  2) 检测页面状态：已验证 → 跳过；step 1 → 继续
  3) 选国家 Indonesia
  4) 调 5sim API 买号 → 填号 → 点 NEXT
  5) 轮询等验证码 → 填码 → 点 SUBMIT
  6) 检查是否 "verified"

用法：
  python3 phone_verify.py --container 10
  python3 phone_verify.py --containers 10,28,33 --max-tries 3
  python3 phone_verify.py --containers 10,28 --browser bitbrowser
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from playwright.async_api import async_playwright
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))

from utils import create_backend, log, SELECT_ALL_KEY  # noqa: E402

# ============ 5sim 配置读取 ============

CONFIG_PATH = SCRIPT_DIR.parent / "config" / "5sim_config.json"


def load_5sim_config() -> dict:
    if not CONFIG_PATH.exists():
        log(
            f"找不到配置文件: {CONFIG_PATH}\n"
            "请复制 config/5sim_config.template.json 为 config/5sim_config.json "
            "并填入你的 5sim TOKEN。",
            "ERR",
        )
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


CONFIG = load_5sim_config()
TOKEN: str = CONFIG.get("token", "").strip()
COUNTRY: str = CONFIG.get("country", "indonesia")
OPERATOR: str = CONFIG.get("operator", "virtual53")
PRODUCT: str = CONFIG.get("product", "google")

if not TOKEN or TOKEN.startswith("CHANGE_ME"):
    log("5sim TOKEN 未配置，请编辑 config/5sim_config.json", "ERR")
    sys.exit(1)

# ============ 5sim API Session（带重试）============

def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
        }
    )
    return session


SESSION = make_session()
API_BASE = "https://5sim.net/v1/user"


def buy_number() -> Optional[dict]:
    url = f"{API_BASE}/buy/activation/{COUNTRY}/{OPERATOR}/{PRODUCT}"
    try:
        r = SESSION.get(url, timeout=30)
        if r.status_code != 200:
            log(f"5sim 买号失败: HTTP {r.status_code} {r.text[:200]}", "ERR")
            return None
        data = r.json()
        log(f"5sim 买号成功: {data.get('phone')} (id={data.get('id')})", "OK")
        return data
    except Exception as e:
        log(f"5sim 买号异常: {e}", "ERR")
        return None


def check_sms(order_id: int) -> Optional[str]:
    try:
        r = SESSION.get(f"{API_BASE}/check/{order_id}", timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
        sms_list = data.get("sms") or []
        if sms_list:
            return sms_list[-1].get("code")
    except Exception as e:
        log(f"5sim 查码异常: {e}", "WARN")
    return None


def cancel_order(order_id: int) -> None:
    try:
        SESSION.get(f"{API_BASE}/cancel/{order_id}", timeout=30)
    except Exception:
        pass


# ============ 多语言选择器 ============

INDONESIA_NAMES = ["Indonesia", "印尼", "印度尼西亚"]
NEXT_BUTTON_TEXTS = ["NEXT", "繼續", "下一步", "继续"]
SUBMIT_BUTTON_TEXTS = ["SUBMIT", "送出", "提交"]
VERIFIED_KEYWORDS = [
    "Your phone number has already been verified",
    "Phone number verified",
    "電話號碼已通過驗證",
    "手机号码已通过验证",
]
TOO_MANY_ACCOUNTS = [
    "too many accounts",
    "太多帳戶",
    "帐号过多",
]


async def human_delay(min_ms: int = 600, max_ms: int = 1400):
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


async def is_already_verified(page) -> bool:
    try:
        body = await page.inner_text("body")
    except Exception:
        return False
    if not any(kw in body for kw in VERIFIED_KEYWORDS):
        return False
    bad_markers = ["step 1", "Step 1", "NEXT", "下一步"]
    if any(m in body for m in bad_markers):
        return False
    return True


async def select_country_indonesia(page) -> bool:
    try:
        await page.locator("tp-yt-paper-dropdown-menu").first.click()
        await human_delay(800, 1200)

        found = await page.evaluate(
            """
            (names) => {
              const items = Array.from(document.querySelectorAll('tp-yt-paper-item'));
              for (const item of items) {
                const text = (item.innerText || '').trim();
                for (const name of names) {
                  if (text.includes(name)) {
                    item.click();
                    return true;
                  }
                }
              }
              return false;
            }
            """,
            INDONESIA_NAMES,
        )
        if found:
            log(f"  已选择 Indonesia", "OK")
            await human_delay(1000, 1500)
            return True
        log("  未找到 Indonesia 选项", "ERR")
        return False
    except Exception as e:
        log(f"  选国家失败: {e}", "ERR")
        return False


async def fill_phone(page, phone: str) -> bool:
    try:
        phone_input = page.locator(
            'input[placeholder*="0812"], input[placeholder*="555"]'
        ).first
        await phone_input.click()
        await human_delay(200, 400)
        await page.keyboard.press(SELECT_ALL_KEY)
        await human_delay(100, 200)
        await page.keyboard.type(phone, delay=40)
        await human_delay(500, 1000)
        return True
    except Exception as e:
        log(f"  填电话失败: {e}", "ERR")
        return False


async def click_button(page, texts: list[str]) -> bool:
    for text in texts:
        try:
            buttons = page.locator("tp-yt-paper-button")
            count = await buttons.count()
            for i in range(count):
                btn = buttons.nth(i)
                btn_text = (await btn.inner_text()).strip()
                if text.lower() in btn_text.lower():
                    await btn.click()
                    return True
        except Exception:
            continue
    return False


async def verify_phone(container: int, max_tries: int, backend) -> dict:
    """单容器电话验证完整流程。"""
    result = {
        "container": container,
        "status": "failed",
        "phone": None,
        "tries": 0,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "error": None,
    }

    try:
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
            await page.goto(
                "https://www.youtube.com/verify?f=Q0hBTk5FTF9GRUFUVVJFU19GRUFUVVJFX1VOU1BFQ0lGSUVE&noapp=1",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await human_delay(3000, 5000)

            # 检测是否已验证
            if await is_already_verified(page):
                log(f"  [{container}] 已验证，跳过", "OK")
                result["status"] = "already_verified"
                return result

            # 选国家
            if not await select_country_indonesia(page):
                raise Exception("无法选择 Indonesia")

            # 多次尝试
            for try_num in range(1, max_tries + 1):
                result["tries"] = try_num
                log(f"  [{container}] 第 {try_num}/{max_tries} 次尝试", "INFO")

                order = buy_number()
                if not order:
                    raise Exception("5sim 买号失败")
                order_id = int(order["id"])
                phone = str(order["phone"])
                result["phone"] = phone

                if not await fill_phone(page, phone):
                    cancel_order(order_id)
                    continue

                if not await click_button(page, NEXT_BUTTON_TEXTS):
                    cancel_order(order_id)
                    raise Exception("找不到 NEXT 按钮")
                await human_delay(3000, 5000)

                body = await page.inner_text("body")
                if any(kw in body for kw in TOO_MANY_ACCOUNTS):
                    log(f"  [{container}] 号码已被其他账号用满，换下一个", "WARN")
                    cancel_order(order_id)
                    await human_delay(1500, 2500)
                    continue

                # 轮询等验证码
                code = None
                for _ in range(8):
                    await asyncio.sleep(4)
                    code = check_sms(order_id)
                    if code:
                        break

                if not code:
                    log(f"  [{container}] 30s 内没收到码，取消换号", "WARN")
                    cancel_order(order_id)
                    continue

                log(f"  [{container}] 收到验证码: {code}", "OK")

                # 立刻填码 + 提交（原子操作）
                try:
                    code_input = page.locator(
                        'input[type="text"], input[type="tel"]'
                    ).last
                    await code_input.click()
                    await page.keyboard.press(SELECT_ALL_KEY)
                    await page.keyboard.type(code, delay=40)
                    await click_button(page, SUBMIT_BUTTON_TEXTS)
                except Exception as e:
                    log(f"  [{container}] 填码失败: {e}", "ERR")
                    cancel_order(order_id)
                    continue

                await human_delay(5000, 8000)

                # 检查是否验证成功
                if await is_already_verified(page):
                    result["status"] = "success"
                    log(f"  [{container}] ✅ 验证成功", "OK")
                    return result

                body = await page.inner_text("body")
                if "Incorrect" in body or "錯誤" in body:
                    log(f"  [{container}] 验证码错误/过期", "WARN")
                    cancel_order(order_id)
                    continue

                result["status"] = "success"
                log(f"  [{container}] ✅ 验证可能成功", "OK")
                return result

            raise Exception(f"已尝试 {max_tries} 次仍未成功")

    except Exception as exc:
        result["error"] = str(exc)
        log(f"  [{container}] ❌ {exc}", "ERR")

    return result


def parse_list(raw: Optional[str]) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


async def main():
    parser = argparse.ArgumentParser(description="YouTube 频道电话验证（5sim 接码）")
    parser.add_argument("--container", type=int, help="单个容器号")
    parser.add_argument("--containers", type=str, help="容器列表，逗号分隔")
    parser.add_argument(
        "--browser",
        type=str,
        choices=["hubstudio", "bitbrowser"],
        default="hubstudio",
        help="浏览器后端：hubstudio（默认）或 bitbrowser（比特浏览器）",
    )
    parser.add_argument(
        "--max-tries", type=int, default=3, help="每个容器最多尝试号码数（默认 3）"
    )
    parser.add_argument("--output", type=str, default="verify_results.json")
    args = parser.parse_args()

    containers: list[int] = []
    if args.container:
        containers.append(args.container)
    if args.containers:
        containers.extend(parse_list(args.containers))
    if not containers:
        log("请用 --container 或 --containers 指定容器", "ERR")
        sys.exit(1)

    backend = create_backend(args.browser)

    log(f"浏览器后端: {backend.name}", "INFO")
    log(f"运行平台: {'macOS' if sys.platform == 'darwin' else 'Windows' if sys.platform == 'win32' else sys.platform}", "INFO")
    log(f"待验证容器: {containers}", "INFO")
    log(f"5sim 运营商: {COUNTRY}/{OPERATOR}/{PRODUCT}", "INFO")
    log(f"每个容器最多 {args.max_tries} 个号码", "INFO")

    results = []
    for c in containers:
        result = await verify_phone(c, max_tries=args.max_tries, backend=backend)
        results.append(result)
        await asyncio.sleep(random.uniform(3, 5))

    Path(args.output).write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"结果写入: {args.output}", "OK")

    ok = sum(1 for r in results if r["status"] == "success")
    already = sum(1 for r in results if r["status"] == "already_verified")
    fail = sum(1 for r in results if r["status"] == "failed")
    log(f"汇总：✅ {ok} 成功 / ⏭️ {already} 已验证 / ❌ {fail} 失败", "INFO")


if __name__ == "__main__":
    asyncio.run(main())
