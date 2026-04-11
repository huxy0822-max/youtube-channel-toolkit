#!/usr/bin/env python3
"""
YouTube 频道从零起号自动化（一条龙）

完整流程：
  Phase 1 — Google 登录
    1) 打开 accounts.google.com，填邮箱 → Next
    2) 填密码 → Next
    3) 填 TOTP 两步验证码 → Next
    4) 跳过各种恢复/安全提示（recovery phone, enhanced safe browsing 等）

  Phase 2 — 改密码
    5) 导航 myaccount.google.com/signinoptions/password
    6) 重新验证当前密码
    7) 输入新密码（统一改为 NEW_PASSWORD）

  Phase 3 — 创建 YouTube 频道
    8) 导航 youtube.com → Create → Upload video → 创建频道对话框
    9) 填频道名 + Handle → Create channel
    10) 如果已有频道则跳过

  Phase 4 — 手机验证（hero-sms 接码）
    11) 导航 youtube.com/verify
    12) 选 Indonesia → 买号 → 填号 → 等码 → 提交

支持的浏览器后端：HubStudio / BitBrowser
自动适配 macOS / Windows

用法：
  python3 bootstrap.py --containers 1,2,3
  python3 bootstrap.py --containers 1,2,3 --browser bitbrowser
  python3 bootstrap.py --containers 1 --skip-login          # 跳过登录（已登录过）
  python3 bootstrap.py --containers 1 --skip-password        # 跳过改密码
  python3 bootstrap.py --containers 1 --skip-channel         # 跳过建频道
  python3 bootstrap.py --containers 1 --skip-verify          # 跳过电话验证
  python3 bootstrap.py --containers 1 --dry-run              # 只打印计划
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import string
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyotp
import requests
from playwright.async_api import async_playwright, Page
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))
sys.path.append(str(SCRIPT_DIR.parent / "config"))

from utils import create_backend, log, SELECT_ALL_KEY  # noqa: E402

# ============ 账号配置 ============

try:
    from accounts import ACCOUNTS  # type: ignore
except ImportError:
    log(
        "未找到 config/accounts.py。\n"
        "请复制 config/accounts.template.py 为 config/accounts.py 并填入账号信息。",
        "ERR",
    )
    sys.exit(1)

# ============ 常量 ============

NEW_PASSWORD = "hxyhxy1211"  # 统一新密码

# hero-sms 配置
HERO_SMS_CONFIG_PATH = SCRIPT_DIR.parent / "config" / "hero_sms_config.json"
HERO_API_BASE = "https://hero-sms.com/stubs/handler_api.php"


# ============ 工具函数 ============

async def human_delay(min_ms: int = 600, max_ms: int = 1400):
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def generate_handle(channel_name: str) -> str:
    clean = re.sub(r'[^\w\u4e00-\u9fff]', '', channel_name)
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(3, 4)))
    return f"{clean}-{suffix}"


async def safe_screenshot(page: Page, path: Path, desc: str):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), timeout=10000, animations="disabled")
    except Exception as e:
        log(f"  截图跳过 ({desc}): {e}", "WARN")


async def refill_textbox(page: Page, locator, value: str):
    await locator.click()
    await human_delay(200, 400)
    await page.keyboard.press(SELECT_ALL_KEY)
    await human_delay(100, 200)
    await page.keyboard.type(value, delay=50)


async def get_page_for_container(playwright, port: int):
    """CDP 连接并返回一个可用的 page。"""
    browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    if not browser.contexts:
        raise Exception("CDP 已连接但无可用 context")

    context = browser.contexts[0]
    context.set_default_timeout(30000)

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
    return browser, context, page


# ============================================================
#  Phase 1: Google 登录
# ============================================================

async def phase_login(page: Page, container: int, email: str, password: str, totp_secret: str, ss_dir: Path) -> bool:
    """登录 Google 账号。返回 True 表示成功。"""
    log(f"  [{container}] === Phase 1: Google 登录 ===", "INFO")

    # 导航到 Google 登录页
    await page.goto("https://accounts.google.com/signin/v2/identifier?hl=en&flowName=GlifWebSignIn", wait_until="domcontentloaded", timeout=60000)
    await human_delay(2000, 3000)

    await safe_screenshot(page, ss_dir / f"{container}_01_login_page.png", "login_page")

    # ---- 填邮箱 ----
    try:
        email_input = page.locator('input[type="email"]')
        if await email_input.count() > 0:
            log(f"  [{container}] 填入邮箱: {email}", "INFO")
            await email_input.click()
            await human_delay(300, 500)
            await page.keyboard.type(email, delay=40)
            await human_delay(500, 800)
            await page.get_by_role("button", name="Next").click()
            await human_delay(3000, 5000)
        else:
            log(f"  [{container}] 未发现邮箱输入框，可能已登录", "WARN")
    except Exception as e:
        log(f"  [{container}] 填邮箱阶段异常: {e}", "WARN")

    await safe_screenshot(page, ss_dir / f"{container}_02_after_email.png", "after_email")

    # ---- 填密码 ----
    try:
        body = await page.inner_text("body")
        # 等待密码框出现
        pw_selectors = [
            'input[type="password"]',
            'input[name="Passwd"]',
        ]
        pw_input = None
        for sel in pw_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0:
                pw_input = loc.first
                break

        if pw_input:
            log(f"  [{container}] 填入密码", "INFO")
            await pw_input.click()
            await human_delay(300, 500)
            await page.keyboard.type(password, delay=50)
            await human_delay(500, 1000)
            await page.get_by_role("button", name="Next").click()
            await human_delay(4000, 6000)
        elif "Verify it's you" in body or "验证" in body:
            # "Verify it's you" 页面，可能要换方式
            log(f"  [{container}] 出现身份验证页面，尝试寻找密码选项", "WARN")
            try:
                # 有时需要点 "Try another way" 然后选密码
                try_another = page.get_by_text(re.compile(r"Try another way|尝试其他方式", re.IGNORECASE))
                if await try_another.count() > 0:
                    await try_another.first.click()
                    await human_delay(2000, 3000)
                # 选择 "Enter your password"
                enter_pw = page.get_by_text(re.compile(r"Enter your password|输入.*密码", re.IGNORECASE))
                if await enter_pw.count() > 0:
                    await enter_pw.first.click()
                    await human_delay(2000, 3000)
                # 再找密码框
                pw_input = page.locator('input[type="password"]').first
                await pw_input.click()
                await page.keyboard.type(password, delay=50)
                await human_delay(500, 1000)
                await page.get_by_role("button", name="Next").click()
                await human_delay(4000, 6000)
            except Exception as e2:
                log(f"  [{container}] 身份验证页面处理失败: {e2}", "ERR")
                return False
    except Exception as e:
        log(f"  [{container}] 填密码阶段异常: {e}", "ERR")
        return False

    await safe_screenshot(page, ss_dir / f"{container}_03_after_password.png", "after_password")

    # ---- TOTP 两步验证 ----
    try:
        body = await page.inner_text("body")
        is_totp = (
            "2-Step Verification" in body
            or "Enter code" in body
            or "两步验证" in body
            or "challenge/totp" in page.url
        )
        if is_totp:
            totp_code = pyotp.TOTP(totp_secret).now()
            log(f"  [{container}] 填入 TOTP: {totp_code}", "INFO")

            totp_input = None
            for sel in ['#totpPin', 'input[type="tel"]', 'input[name="totpPin"]']:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    totp_input = loc.first
                    break
            if not totp_input:
                totp_input = page.get_by_placeholder(re.compile(r"Enter.*code|验证码|code", re.IGNORECASE))

            await totp_input.click()
            await human_delay(300, 500)
            await page.keyboard.type(totp_code, delay=50)
            await human_delay(500, 1000)
            await page.get_by_role("button", name="Next").click()
            await human_delay(6000, 10000)
    except Exception as e:
        log(f"  [{container}] TOTP 阶段异常: {e}", "WARN")

    await safe_screenshot(page, ss_dir / f"{container}_04_after_totp.png", "after_totp")

    # ---- 跳过各种烦人的提示 ----
    for attempt in range(5):
        try:
            body = await page.inner_text("body")
            url = page.url or ""

            # 已到达 myaccount 主页 = 登录成功
            if "myaccount.google.com" in url and "signin" not in url and "challenge" not in url:
                log(f"  [{container}] ✅ 登录成功", "OK")
                return True

            # 已在 Google 搜索首页或 YouTube = 也算成功
            if any(x in url for x in ["google.com/search", "youtube.com", "mail.google.com"]):
                log(f"  [{container}] ✅ 登录成功 (URL: {url})", "OK")
                return True

            # "Don't ask again on this device" / 跳过信任设备
            skip_trust = page.get_by_text(re.compile(r"Don't ask again|不再询问", re.IGNORECASE))
            if await skip_trust.count() > 0:
                await skip_trust.first.click()
                await human_delay(2000, 3000)
                continue

            # "Add recovery phone" / "Add recovery email" → Skip / Not now
            for skip_text in [
                r"Not now",
                r"Skip",
                r"No thanks",
                r"不用了",
                r"以后再说",
                r"跳过",
                r"稍后再说",
                r"I agree",
                r"Done",
                r"Continue",
                r"Got it",
                r"完成",
                r"继续",
                r"同意",
                r"知道了",
            ]:
                btn = page.get_by_role("button", name=re.compile(skip_text, re.IGNORECASE))
                if await btn.count() > 0:
                    log(f"  [{container}] 跳过提示: 点击 '{skip_text}'", "INFO")
                    await btn.first.click()
                    await human_delay(2000, 3000)
                    break

            # 点击文本链接形式的跳过
            for link_text in [r"Not now", r"Skip", r"No,? thanks", r"不用了", r"跳过"]:
                link = page.get_by_text(re.compile(link_text, re.IGNORECASE))
                if await link.count() > 0:
                    await link.first.click()
                    await human_delay(2000, 3000)
                    break

            # 如果页面没变化，尝试导航到 myaccount
            if attempt >= 3:
                log(f"  [{container}] 尝试强制导航到 myaccount...", "WARN")
                await page.goto("https://myaccount.google.com/?hl=en", wait_until="domcontentloaded", timeout=30000)
                await human_delay(2000, 3000)

        except Exception as e:
            log(f"  [{container}] 跳过提示阶段: {e}", "WARN")
            await human_delay(2000, 3000)

    # 最终检查
    url = page.url or ""
    if "accounts.google.com" in url and ("challenge" in url or "signin" in url):
        log(f"  [{container}] ❌ 登录可能卡在验证页: {url}", "ERR")
        await safe_screenshot(page, ss_dir / f"{container}_05_login_stuck.png", "login_stuck")
        return False

    log(f"  [{container}] ✅ 登录流程完成 (URL: {url})", "OK")
    return True


# ============================================================
#  Phase 2: 改密码
# ============================================================

async def phase_change_password(page: Page, container: int, old_password: str, ss_dir: Path) -> bool:
    """改 Google 账号密码为 NEW_PASSWORD。返回 True 表示成功。"""
    log(f"  [{container}] === Phase 2: 改密码 → {NEW_PASSWORD} ===", "INFO")

    await page.goto(
        "https://myaccount.google.com/signinoptions/password?hl=en",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    await human_delay(3000, 5000)

    await safe_screenshot(page, ss_dir / f"{container}_10_change_pw_page.png", "change_pw_page")

    # ---- 可能需要重新验证密码 ----
    try:
        pw_input = page.locator('input[type="password"]')
        if await pw_input.count() > 0:
            log(f"  [{container}] 重新验证当前密码", "INFO")
            await pw_input.first.click()
            await human_delay(300, 500)
            await page.keyboard.type(old_password, delay=50)
            await human_delay(500, 1000)
            await page.get_by_role("button", name="Next").click()
            await human_delay(4000, 6000)

            await safe_screenshot(page, ss_dir / f"{container}_11_re_auth.png", "re_auth")

            # TOTP 可能再弹一次 — 不太常见但处理一下
            body = await page.inner_text("body")
            if "2-Step" in body or "challenge/totp" in page.url:
                # 需要 totp_secret，但这个函数没传。从 ACCOUNTS 取。
                # 通过页面上的邮箱反查
                log(f"  [{container}] 改密码时触发 TOTP，跳过（需重试）", "WARN")
                return False
    except Exception as e:
        log(f"  [{container}] 验证密码阶段: {e}", "WARN")

    # ---- 等待新密码表单出现 ----
    await human_delay(2000, 3000)

    try:
        # Google 改密码页有两个 password 输入框：New password + Confirm
        pw_fields = page.locator('input[type="password"]')
        count = await pw_fields.count()

        if count >= 2:
            log(f"  [{container}] 填入新密码", "INFO")
            # 第一个：New password
            await pw_fields.nth(0).click()
            await human_delay(200, 400)
            await page.keyboard.type(NEW_PASSWORD, delay=50)
            await human_delay(500, 800)

            # 第二个：Confirm new password
            await pw_fields.nth(1).click()
            await human_delay(200, 400)
            await page.keyboard.type(NEW_PASSWORD, delay=50)
            await human_delay(500, 1000)

            await safe_screenshot(page, ss_dir / f"{container}_12_new_pw_filled.png", "new_pw_filled")

            # 点击 "Change password"
            change_btn = page.get_by_role("button", name=re.compile(r"Change password|更改密码", re.IGNORECASE))
            if await change_btn.count() > 0:
                await change_btn.first.click()
            else:
                # 备用：找 submit 类型的按钮
                submit_btn = page.locator('button[type="submit"]')
                if await submit_btn.count() > 0:
                    await submit_btn.first.click()
                else:
                    # 最后兜底：按回车
                    await page.keyboard.press("Enter")

            await human_delay(5000, 8000)

            await safe_screenshot(page, ss_dir / f"{container}_13_pw_changed.png", "pw_changed")

            # 验证成功：检查页面是否跳转回安全设置或显示成功提示
            body = await page.inner_text("body")
            url = page.url or ""
            if any(x in body for x in ["Password changed", "密码已更改", "saved"]):
                log(f"  [{container}] ✅ 密码已改为 {NEW_PASSWORD}", "OK")
                return True
            if "signinoptions" in url and "password" not in url:
                log(f"  [{container}] ✅ 密码修改成功（页面已跳转）", "OK")
                return True
            if "myaccount.google.com" in url:
                log(f"  [{container}] ✅ 密码可能修改成功", "OK")
                return True

            log(f"  [{container}] ⚠️ 密码修改结果不确定，请人工检查", "WARN")
            return True  # 不阻塞后续流程

        elif count == 1:
            # 只有一个密码框 = 还在验证当前密码
            log(f"  [{container}] 仍在密码验证阶段，可能验证失败", "ERR")
            return False
        else:
            log(f"  [{container}] 未找到密码输入框，页面结构可能变化", "ERR")
            return False

    except Exception as e:
        log(f"  [{container}] 改密码异常: {e}", "ERR")
        await safe_screenshot(page, ss_dir / f"{container}_14_pw_error.png", "pw_error")
        return False


# ============================================================
#  Phase 3: 创建 YouTube 频道
# ============================================================

async def phase_create_channel(page: Page, container: int, channel_name: str, ss_dir: Path) -> bool:
    """创建 YouTube 频道。如果已有频道则跳过。返回 True 表示成功或已存在。"""
    log(f"  [{container}] === Phase 3: 创建频道 '{channel_name}' ===", "INFO")

    # 导航到 YouTube
    await page.goto("https://www.youtube.com/?hl=en", wait_until="domcontentloaded", timeout=60000)
    await human_delay(3000, 5000)

    await safe_screenshot(page, ss_dir / f"{container}_20_youtube_home.png", "youtube_home")

    # 检查是否已有频道（尝试打开 Studio）
    body = await page.inner_text("body")
    url = page.url or ""

    # 尝试点击 Create → Upload video 触发创建频道对话框
    max_retries = 3
    dialog_appeared = False
    name_box = None

    for attempt in range(1, max_retries + 1):
        try:
            log(f"  [{container}] 尝试第 {attempt} 次: Create → Upload video...", "INFO")

            create_btn = page.get_by_role("button", name="Create")
            if await create_btn.count() == 0:
                # 可能已有频道，Studio 入口不同
                log(f"  [{container}] 未发现 Create 按钮，检查是否已有频道...", "INFO")

                # 尝试直接访问 Studio
                await page.goto("https://studio.youtube.com/?hl=en", wait_until="domcontentloaded", timeout=30000)
                await human_delay(3000, 5000)

                studio_body = await page.inner_text("body")
                studio_url = page.url or ""

                if "studio.youtube.com" in studio_url and ("dashboard" in studio_url.lower() or "Channel dashboard" in studio_body or "Upload" in studio_body):
                    log(f"  [{container}] ✅ 已有频道，跳过创建", "OK")
                    return True

                # 回到 YouTube 重试
                await page.goto("https://www.youtube.com/?hl=en", wait_until="domcontentloaded", timeout=30000)
                await human_delay(3000, 5000)
                continue

            await create_btn.click(timeout=8000)
            await human_delay(2000, 3000)

            upload_link = page.get_by_role("link", name="Upload video")
            if await upload_link.count() == 0:
                upload_link = page.get_by_role("menuitem", name="Upload video")
            await upload_link.click(timeout=8000)
            await human_delay(3000, 5000)

            # 检查是否弹出"创建频道"对话框
            name_box = page.get_by_role("textbox", name="Name")
            await name_box.wait_for(state="visible", timeout=8000)
            dialog_appeared = True
            break

        except Exception as e:
            log(f"  [{container}] 第 {attempt} 次未能弹出窗口: {e}", "WARN")

            # 检查是否已直接进入了 Studio（说明已有频道）
            current_url = page.url or ""
            if "studio.youtube.com" in current_url:
                log(f"  [{container}] ✅ 已有频道（直接进入 Studio）", "OK")
                return True

            if attempt < max_retries:
                try:
                    await page.goto("https://www.youtube.com/?hl=en", wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await human_delay(3000, 5000)

    if not dialog_appeared:
        # 最后一次检查：可能有频道但 Create 按钮隐藏了
        try:
            await page.goto("https://studio.youtube.com/?hl=en", wait_until="domcontentloaded", timeout=30000)
            await human_delay(3000, 5000)
            if "studio.youtube.com" in (page.url or ""):
                log(f"  [{container}] ✅ 已有频道", "OK")
                return True
        except Exception:
            pass

        log(f"  [{container}] ❌ 无法弹出创建频道对话框", "ERR")
        await safe_screenshot(page, ss_dir / f"{container}_21_no_dialog.png", "no_dialog")
        return False

    await safe_screenshot(page, ss_dir / f"{container}_22_create_dialog.png", "create_dialog")

    # ---- 填写频道名 ----
    log(f"  [{container}] 填写频道名: {channel_name}", "INFO")
    await refill_textbox(page, name_box, channel_name)
    await human_delay(1500, 2500)

    # ---- 填写 Handle ----
    handle_box = page.get_by_role("textbox", name="Handle")
    create_channel_btn = page.get_by_role("button", name="Create channel")

    handle_value = generate_handle(channel_name)
    for handle_attempt in range(3):
        log(f"  [{container}] 填写 Handle: {handle_value}", "INFO")
        await refill_textbox(page, handle_box, handle_value)
        await human_delay(2000, 3000)
        await page.keyboard.press("Tab")
        await human_delay(800, 1200)

        aria_disabled = await create_channel_btn.get_attribute("aria-disabled")
        is_disabled = False
        try:
            is_disabled = await create_channel_btn.is_disabled()
        except Exception:
            pass

        if aria_disabled != "true" and not is_disabled:
            break
        handle_value = generate_handle(channel_name)
        log(f"  [{container}] Handle 冲突，换一个: {handle_value}", "WARN")

    await safe_screenshot(page, ss_dir / f"{container}_23_filled.png", "filled")

    # ---- 点击 Create channel ----
    log(f"  [{container}] 点击 Create channel...", "INFO")
    await create_channel_btn.click()
    await human_delay(7000, 10000)

    await safe_screenshot(page, ss_dir / f"{container}_24_result.png", "result")

    current_url = page.url or ""
    body_text = await page.inner_text("body")

    if (
        "studio.youtube.com" in current_url
        or "Upload videos" in body_text
        or "Channel dashboard" in body_text
    ):
        log(f"  [{container}] ✅ 频道创建成功！", "OK")
        return True

    log(f"  [{container}] ✅ 频道可能创建成功（URL: {current_url}）", "OK")
    return True


# ============================================================
#  Phase 4: 手机验证（hero-sms）
# ============================================================

def load_hero_sms() -> tuple[str, int, str, float]:
    """加载 hero-sms 配置，返回 (api_key, country, service, max_price)。"""
    if not HERO_SMS_CONFIG_PATH.exists():
        log(
            f"找不到 {HERO_SMS_CONFIG_PATH}\n"
            "请复制 config/hero_sms_config.template.json 为 config/hero_sms_config.json",
            "ERR",
        )
        sys.exit(1)
    cfg = json.loads(HERO_SMS_CONFIG_PATH.read_text(encoding="utf-8"))
    api_key = cfg.get("api_key", "").strip()
    if not api_key or api_key.startswith("CHANGE_ME"):
        log("hero-sms API Key 未配置", "ERR")
        sys.exit(1)
    return api_key, cfg.get("country", 6), cfg.get("service", "go"), cfg.get("max_price", 0.03)


_hero_session: requests.Session | None = None


def hero_session() -> requests.Session:
    global _hero_session
    if _hero_session is None:
        _hero_session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"])
        _hero_session.mount("https://", HTTPAdapter(max_retries=retry))
    return _hero_session


def hero_api(api_key: str, action: str, **params) -> str | dict:
    params["action"] = action
    params["api_key"] = api_key
    try:
        r = hero_session().get(HERO_API_BASE, params=params, timeout=30)
        try:
            return json.loads(r.text.strip())
        except json.JSONDecodeError:
            return r.text.strip()
    except Exception as e:
        return f"ERROR: {e}"


INDONESIA_NAMES = ["Indonesia", "印尼", "印度尼西亚"]
NEXT_BUTTON_TEXTS = ["NEXT", "繼續", "下一步", "继续"]
SUBMIT_BUTTON_TEXTS = ["SUBMIT", "送出", "提交"]
VERIFIED_KEYWORDS = [
    "Your phone number has already been verified",
    "Phone number verified",
    "電話號碼已通過驗證",
    "手机号码已通过验证",
]
TOO_MANY_ACCOUNTS = ["too many accounts", "太多帳戶", "帐号过多"]


async def is_already_verified(page: Page) -> bool:
    try:
        body = await page.inner_text("body")
    except Exception:
        return False
    if not any(kw in body for kw in VERIFIED_KEYWORDS):
        return False
    if any(m in body for m in ["step 1", "Step 1", "NEXT", "下一步"]):
        return False
    return True


async def click_yt_button(page: Page, texts: list[str]) -> bool:
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


async def phase_phone_verify(page: Page, container: int, max_tries: int, ss_dir: Path) -> bool:
    """hero-sms 接码验证。返回 True 表示成功或已验证。"""
    log(f"  [{container}] === Phase 4: 手机验证 (hero-sms) ===", "INFO")

    api_key, country, service, max_price = load_hero_sms()

    # 显示余额
    balance = hero_api(api_key, "getBalance")
    log(f"  [{container}] hero-sms 余额: {balance}", "INFO")

    await page.goto(
        "https://www.youtube.com/verify?f=Q0hBTk5FTF9GRUFUVVJFU19GRUFUVVJFX1VOU1BFQ0lGSUVE&noapp=1",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    await human_delay(3000, 5000)

    await safe_screenshot(page, ss_dir / f"{container}_30_verify_page.png", "verify_page")

    if await is_already_verified(page):
        log(f"  [{container}] ✅ 已验证，跳过", "OK")
        return True

    # 选国家 Indonesia
    try:
        await page.locator("tp-yt-paper-dropdown-menu").first.click()
        await human_delay(800, 1200)
        found = await page.evaluate(
            """(names) => {
              const items = Array.from(document.querySelectorAll('tp-yt-paper-item'));
              for (const item of items) {
                const text = (item.innerText || '').trim();
                for (const name of names) {
                  if (text.includes(name)) { item.click(); return true; }
                }
              }
              return false;
            }""",
            INDONESIA_NAMES,
        )
        if not found:
            log(f"  [{container}] ❌ 无法选择 Indonesia", "ERR")
            return False
        log(f"  [{container}] 已选择 Indonesia", "OK")
        await human_delay(1000, 1500)
    except Exception as e:
        log(f"  [{container}] 选国家失败: {e}", "ERR")
        return False

    # 多次尝试买号
    for try_num in range(1, max_tries + 1):
        log(f"  [{container}] 第 {try_num}/{max_tries} 次尝试", "INFO")

        # 买号
        result = hero_api(api_key, "getNumberV2", service=service, country=country, maxPrice=max_price)
        activation_id = None
        phone = None

        if isinstance(result, dict):
            activation_id = int(result.get("activationId", 0))
            phone = str(result.get("phoneNumber", ""))
        elif isinstance(result, str) and result.startswith("ACCESS_NUMBER:"):
            parts = result.split(":")
            activation_id = int(parts[1])
            phone = parts[2]
        else:
            log(f"  [{container}] 买号失败: {result}", "ERR")
            if "NO_BALANCE" in str(result):
                return False
            continue

        log(f"  [{container}] 买号: {phone} (id={activation_id})", "OK")

        # 填号码
        try:
            phone_input = page.locator('input[placeholder*="0812"], input[placeholder*="555"]').first
            await phone_input.click()
            await human_delay(200, 400)
            await page.keyboard.press(SELECT_ALL_KEY)
            await human_delay(100, 200)
            await page.keyboard.type(phone, delay=40)
            await human_delay(500, 1000)
        except Exception as e:
            log(f"  [{container}] 填号码失败: {e}", "ERR")
            hero_api(api_key, "setStatus", id=activation_id, status=8)
            continue

        # 点 NEXT
        if not await click_yt_button(page, NEXT_BUTTON_TEXTS):
            hero_api(api_key, "setStatus", id=activation_id, status=8)
            log(f"  [{container}] 找不到 NEXT 按钮", "ERR")
            continue
        await human_delay(3000, 5000)

        body = await page.inner_text("body")
        if any(kw in body for kw in TOO_MANY_ACCOUNTS):
            log(f"  [{container}] 号码被用满，换下一个", "WARN")
            hero_api(api_key, "setStatus", id=activation_id, status=8)
            await human_delay(1500, 2500)
            continue

        # 轮询等验证码
        code = None
        for poll in range(15):
            await asyncio.sleep(4)
            status_result = hero_api(api_key, "getStatusV2", id=activation_id)
            if isinstance(status_result, dict):
                sms = status_result.get("sms")
                if sms and isinstance(sms, dict) and sms.get("code"):
                    code = str(sms["code"])
                    break
            # 降级 getStatus
            status_text = hero_api(api_key, "getStatus", id=activation_id)
            if isinstance(status_text, str) and status_text.startswith("STATUS_OK:"):
                code = status_text.split(":", 1)[1]
                break
            if poll % 3 == 2:
                log(f"  [{container}] 等待验证码... ({(poll+1)*4}s)", "WAIT")

        if not code:
            log(f"  [{container}] 60s 没收到码，取消", "WARN")
            hero_api(api_key, "setStatus", id=activation_id, status=8)
            continue

        log(f"  [{container}] 收到验证码: {code}", "OK")

        # 填码 + 提交
        try:
            code_input = page.locator('input[type="text"], input[type="tel"]').last
            await code_input.click()
            await page.keyboard.press(SELECT_ALL_KEY)
            await page.keyboard.type(code, delay=40)
            await click_yt_button(page, SUBMIT_BUTTON_TEXTS)
        except Exception as e:
            log(f"  [{container}] 填码失败: {e}", "ERR")
            hero_api(api_key, "setStatus", id=activation_id, status=8)
            continue

        await human_delay(5000, 8000)

        if await is_already_verified(page):
            hero_api(api_key, "setStatus", id=activation_id, status=6)
            log(f"  [{container}] ✅ 验证成功", "OK")
            return True

        body = await page.inner_text("body")
        if "Incorrect" in body or "錯誤" in body:
            log(f"  [{container}] 验证码错误/过期", "WARN")
            hero_api(api_key, "setStatus", id=activation_id, status=8)
            continue

        hero_api(api_key, "setStatus", id=activation_id, status=6)
        log(f"  [{container}] ✅ 验证可能成功", "OK")
        return True

    log(f"  [{container}] ❌ {max_tries} 次尝试均失败", "ERR")
    return False


# ============================================================
#  主流程
# ============================================================

async def bootstrap_one(
    container: int,
    account: dict,
    backend,
    ss_dir: Path,
    max_tries: int,
    skip_login: bool,
    skip_password: bool,
    skip_channel: bool,
    skip_verify: bool,
) -> dict:
    """单个容器完整起号流程。"""
    email = account["email"]
    password = account["password"]
    totp_secret = account["totp_secret"]
    channel_name = account["channel_name"]

    result = {
        "container": container,
        "email": email,
        "channel_name": channel_name,
        "login": "skipped",
        "password_change": "skipped",
        "channel_create": "skipped",
        "phone_verify": "skipped",
        "started_at": now_iso(),
        "finished_at": None,
        "error": None,
    }

    try:
        port = backend.get_port_by_env(container)
        if not port:
            raise Exception(f"未获取到 container {container} 的调试端口")

        async with async_playwright() as playwright:
            browser, context, page = await get_page_for_container(playwright, port)
            context.set_default_timeout(30000)

            # Phase 1: 登录
            if not skip_login:
                ok = await phase_login(page, container, email, password, totp_secret, ss_dir)
                result["login"] = "success" if ok else "failed"
                if not ok:
                    raise Exception("登录失败")
            else:
                log(f"  [{container}] 跳过登录", "INFO")

            # Phase 2: 改密码
            if not skip_password:
                ok = await phase_change_password(page, container, password, ss_dir)
                result["password_change"] = "success" if ok else "failed"
                if not ok:
                    log(f"  [{container}] ⚠️ 改密码失败，继续后续流程", "WARN")
            else:
                log(f"  [{container}] 跳过改密码", "INFO")

            # Phase 3: 创建频道
            if not skip_channel:
                ok = await phase_create_channel(page, container, channel_name, ss_dir)
                result["channel_create"] = "success" if ok else "failed"
                if not ok:
                    log(f"  [{container}] ⚠️ 创建频道失败，继续后续流程", "WARN")
            else:
                log(f"  [{container}] 跳过创建频道", "INFO")

            # Phase 4: 手机验证
            if not skip_verify:
                ok = await phase_phone_verify(page, container, max_tries, ss_dir)
                result["phone_verify"] = "success" if ok else "failed"
            else:
                log(f"  [{container}] 跳过手机验证", "INFO")

            log(f"  [{container}] 保留浏览器窗口不关闭", "INFO")

    except Exception as exc:
        result["error"] = str(exc)
        log(f"  [{container}] ❌ 起号中断: {exc}", "ERR")

    result["finished_at"] = now_iso()
    return result


# ============ CLI ============

def parse_list(raw: Optional[str]) -> list[int]:
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def build_args():
    parser = argparse.ArgumentParser(description="YouTube 频道从零起号（一条龙）")
    parser.add_argument("--containers", type=str, required=True, help="容器号列表，逗号分隔")
    parser.add_argument("--browser", type=str, choices=["hubstudio", "bitbrowser"], default="hubstudio")
    parser.add_argument("--max-tries", type=int, default=3, help="电话验证每容器最多尝试次数")
    parser.add_argument("--skip-login", action="store_true", help="跳过登录（已登录过）")
    parser.add_argument("--skip-password", action="store_true", help="跳过改密码")
    parser.add_argument("--skip-channel", action="store_true", help="跳过创建频道")
    parser.add_argument("--skip-verify", action="store_true", help="跳过手机验证")
    parser.add_argument("--output", type=str, default=str(SCRIPT_DIR / "bootstrap_results.json"))
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    return parser.parse_args()


async def main():
    args = build_args()
    containers = parse_list(args.containers)
    backend = create_backend(args.browser)
    ss_dir = SCRIPT_DIR / "screenshots" / "bootstrap"
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    log("=" * 60, "INFO")
    log("YouTube 频道从零起号（一条龙）", "INFO")
    log("=" * 60, "INFO")
    log(f"浏览器后端: {backend.name}", "INFO")
    log(f"运行平台: {'macOS' if sys.platform == 'darwin' else 'Windows' if sys.platform == 'win32' else sys.platform}", "INFO")
    log(f"统一新密码: {NEW_PASSWORD}", "INFO")
    log(f"待处理容器: {len(containers)} 个", "INFO")
    log("", "INFO")

    # 打印计划
    for c in containers:
        acc = ACCOUNTS.get(c)
        if acc:
            log(f"  Container {c:3d} → {acc['email']} → 频道: {acc['channel_name']}", "INFO")
        else:
            log(f"  Container {c:3d} → ❌ 未在 accounts.py 中找到", "ERR")

    steps = []
    if not args.skip_login:
        steps.append("登录 Google")
    if not args.skip_password:
        steps.append(f"改密码→{NEW_PASSWORD}")
    if not args.skip_channel:
        steps.append("建频道")
    if not args.skip_verify:
        steps.append("手机验证")
    log(f"\n  执行步骤: {' → '.join(steps)}", "INFO")

    if args.dry_run:
        log("\n--dry-run 模式，不实际执行。", "WARN")
        return

    log("\n5 秒后开始执行...", "WARN")
    await asyncio.sleep(5)

    results = []
    for c in containers:
        acc = ACCOUNTS.get(c)
        if not acc:
            log(f"Container {c} 未在 accounts.py 中，跳过", "ERR")
            results.append({"container": c, "error": "未在 accounts.py 中找到"})
            continue

        log(f"\n{'━' * 50}", "INFO")
        log(f"Container {c}: {acc['email']} → {acc['channel_name']}", "INFO")
        log(f"{'━' * 50}", "INFO")

        result = await bootstrap_one(
            container=c,
            account=acc,
            backend=backend,
            ss_dir=ss_dir,
            max_tries=args.max_tries,
            skip_login=args.skip_login,
            skip_password=args.skip_password,
            skip_channel=args.skip_channel,
            skip_verify=args.skip_verify,
        )
        results.append(result)

        # 容器间间隔
        await human_delay(5000, 8000)

    # 写结果
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n结果已写入: {output_path}", "OK")

    # 汇总
    log("\n" + "=" * 60, "INFO")
    log("汇总", "INFO")
    log("=" * 60, "INFO")
    for r in results:
        c = r.get("container", "?")
        login = r.get("login", "?")
        pw = r.get("password_change", "?")
        ch = r.get("channel_create", "?")
        pv = r.get("phone_verify", "?")
        err = r.get("error", "")
        status_icons = {"success": "✅", "failed": "❌", "skipped": "⏭️"}
        log(
            f"  [{c}] 登录:{status_icons.get(login,'?')} "
            f"改密:{status_icons.get(pw,'?')} "
            f"建频:{status_icons.get(ch,'?')} "
            f"验证:{status_icons.get(pv,'?')}"
            f"{' | 错误: ' + err if err else ''}",
            "INFO",
        )


if __name__ == "__main__":
    asyncio.run(main())
