#!/usr/bin/env python3
"""
YouTube 频道新建自动化

通过 HubStudio 或 BitBrowser 容器 + Chrome DevTools Protocol 连接到指定浏览器环境，
自动在 YouTube 上创建新频道并填入指定名称和 Handle。

支持的浏览器后端：
  - HubStudio（默认，API 端口 6873）
  - BitBrowser（比特浏览器，API 端口 54345）

自动适配 macOS / Windows 平台（键盘快捷键等）。

核心流程：
  1) 导航 youtube.com?hl=en（强制英文界面保证选择器稳定）
  2) 点击 "Create" 按钮
  3) 点击 "Upload video" 链接 → 触发创建频道对话框
  4) 清空 Name 输入框，填入原频道名称
  5) 清空 Handle 输入框，填入 @频道名+随机后缀
  6) 点击 "Create channel" 按钮
  7) 验证成功（检查 Studio 页面出现）

用法：
  python3 create_channel.py --containers 10,28,33
  python3 create_channel.py --containers 10,28 --browser bitbrowser
  python3 create_channel.py --dry-run
"""

import argparse
import asyncio
import json
import random
import re
import string
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.append(str(SCRIPT_DIR))
sys.path.append(str(SCRIPT_DIR.parent / "config"))

from utils import create_backend, log, SELECT_ALL_KEY  # noqa: E402

try:
    from channels_to_create import CHANNELS_TO_CREATE  # type: ignore
except ImportError:
    log(
        "未找到 config/channels_to_create.py。请先按模板填好频道列表后再运行。",
        "ERR",
    )
    sys.exit(1)


# ============ 安全机制 ============
YPP_PROTECTED: set[int] = set()   # 有营利权限的容器号，绝对不动
SKIP_CONTAINERS: set[int] = set()  # 出于其他原因跳过的容器号


# 强制英语，保证页面选择器稳定
YOUTUBE_URL = "https://www.youtube.com/?hl=en"


# ============ 工具函数 ============

async def human_delay(min_ms: int = 600, max_ms: int = 1400):
    """模拟真人随机延迟，降低被反爬识别的概率。"""
    await asyncio.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def generate_handle(channel_name: str) -> str:
    """
    根据频道名称生成 YouTube Handle。
    策略：频道名(去除特殊符号) + 短横线 + 3-4 位随机字母数字。
    """
    clean = re.sub(r'[^\w\u4e00-\u9fff]', '', channel_name)
    suffix = ''.join(
        random.choices(string.ascii_lowercase + string.digits, k=random.randint(3, 4))
    )
    return f"{clean}-{suffix}"


def parse_list(raw: Optional[str]) -> List[int]:
    """解析逗号分隔的数字列表。"""
    if not raw:
        return []
    out: List[int] = []
    for x in raw.split(","):
        token = x.strip()
        if token:
            out.append(int(token))
    return out


async def safe_screenshot(page, path: Path, desc: str):
    """截图仅用于复盘，不应让主流程因字体/渲染问题失败。"""
    try:
        await page.screenshot(
            path=str(path),
            timeout=10000,
            animations="disabled",
        )
    except Exception as e:
        log(f"截图跳过 ({desc}): {e}", "WARN")


async def refill_textbox(page, locator, value: str):
    """统一清空并填写文本框（自动适配 macOS/Windows 快捷键）。"""
    await locator.click()
    await human_delay(200, 400)
    await page.keyboard.press(SELECT_ALL_KEY)
    await human_delay(100, 200)
    await page.keyboard.type(value, delay=50)


async def ensure_create_button_ready(
    page,
    container: int,
    channel_name: str,
    handle_box,
    create_channel_btn,
    result: Dict,
    max_attempts: int = 3,
) -> str:
    """Handle 冲突时自动换号，直到 Create channel 可点击。"""
    handle_value = result["handle"]

    for attempt in range(1, max_attempts + 1):
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
            result["handle"] = handle_value
            return handle_value

        if attempt >= max_attempts:
            break

        handle_value = generate_handle(channel_name)
        log(
            f"  [{container}] Create channel 仍灰色，改用新 Handle 重试: {handle_value}",
            "WARN",
        )

    raise Exception("Create channel 按钮持续灰色，可能 handle 冲突或表单未通过校验")


# ============ 核心流程 ============

async def create_channel(container: int, channel_name: str, screenshot_dir: Path, backend) -> Dict:
    """单频道新建完整流程"""
    handle = generate_handle(channel_name)

    result: Dict = {
        "container": container,
        "channel_name": channel_name,
        "handle": handle,
        "status": "failed",
        "started_at": now_iso(),
        "finished_at": None,
        "url_after": None,
        "error": None,
    }

    try:
        # 0) 安全检查
        if container in YPP_PROTECTED:
            raise Exception(f"BLOCKED: Container {container} is YPP protected")
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
            context.set_default_timeout(30000)

            # 找到一个非 extension 页面
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

            # 2) 导航到 YouTube
            current_url = page.url or ""
            if "youtube.com" in current_url and "accounts.google" not in current_url:
                log(f"  [{container}] 已在 YouTube，跳过导航", "INFO")
            else:
                log(f"  [{container}] 导航到 YouTube...", "INFO")
                await page.goto(YOUTUBE_URL, wait_until="domcontentloaded")
                await human_delay(3000, 5000)

            screenshot_dir.mkdir(parents=True, exist_ok=True)
            await safe_screenshot(
                page,
                screenshot_dir / f"{container}_01_youtube_home.png",
                f"{container}_01_youtube_home",
            )

            # 3 & 4) 循环重试：点击 "Create" -> 点击 "Upload video" -> 检测弹窗
            max_retries = 3
            dialog_appeared = False
            name_box = None
            for attempt in range(1, max_retries + 1):
                try:
                    log(
                        f"  [{container}] 尝试第 {attempt} 次点击 Create -> Upload video...",
                        "INFO",
                    )

                    create_btn = page.get_by_role("button", name="Create")
                    await create_btn.click(timeout=8000)
                    await human_delay(2000, 3000)

                    upload_link = page.get_by_role("link", name="Upload video")
                    await upload_link.click(timeout=8000)
                    await human_delay(3000, 5000)

                    # 检查弹窗是否出现
                    name_box = page.get_by_role("textbox", name="Name")
                    await name_box.wait_for(state="visible", timeout=8000)

                    dialog_appeared = True
                    break
                except Exception as e:
                    log(
                        f"  [{container}] ⚠️ 第 {attempt} 次未能弹出窗口: {e}",
                        "WARN",
                    )
                    if attempt < max_retries:
                        log(f"  [{container}] 等待后重试...", "INFO")
                        try:
                            await page.reload(
                                wait_until="domcontentloaded", timeout=15000
                            )
                        except Exception:
                            pass
                        await human_delay(3000, 5000)

            if not dialog_appeared:
                raise Exception("多轮重试后，仍然无法弹出创建频道的对话框")

            await safe_screenshot(
                page,
                screenshot_dir / f"{container}_02_create_dialog.png",
                f"{container}_02_create_dialog",
            )

            # 5) 填写 Name
            log(f"  [{container}] 填写 Name: {channel_name}", "INFO")
            await refill_textbox(page, name_box, channel_name)
            await human_delay(1500, 2500)

            handle_box = page.get_by_role("textbox", name="Handle")
            create_channel_btn = page.get_by_role("button", name="Create channel")
            handle = await ensure_create_button_ready(
                page,
                container,
                channel_name,
                handle_box,
                create_channel_btn,
                result,
            )

            await safe_screenshot(
                page,
                screenshot_dir / f"{container}_03_filled.png",
                f"{container}_03_filled",
            )

            # 7) 点击 "Create channel"
            log(f"  [{container}] 点击 Create channel...", "INFO")
            await create_channel_btn.click()
            await human_delay(7000, 10000)

            # 8) 验证成功
            current_url = page.url or ""
            await safe_screenshot(
                page,
                screenshot_dir / f"{container}_04_result.png",
                f"{container}_04_result",
            )

            body_text = await page.inner_text("body")
            if (
                "studio.youtube.com" in current_url
                or "Upload videos" in body_text
                or "Channel dashboard" in body_text
            ):
                result["status"] = "success"
                log(f"  [{container}] ✅ 频道创建成功！", "OK")
            elif "How you'll appear" in body_text:
                raise Exception("创建对话框仍在显示，可能 Handle 被占用")
            else:
                result["status"] = "success"
                log(
                    f"  [{container}] ✅ 频道可能创建成功（URL: {current_url}）",
                    "OK",
                )

            result["url_after"] = current_url
            log(f"  [{container}] 保留浏览器窗口不关闭", "INFO")

    except Exception as exc:
        result["error"] = str(exc)
        log(f"  [{container}] ❌ 失败: {exc}", "ERR")

    result["finished_at"] = now_iso()
    return result


async def run_batch(containers: List[int], backend) -> List[Dict]:
    """批量新建"""
    results: List[Dict] = []
    screenshot_dir = SCRIPT_DIR / "screenshots" / "create"

    for container in containers:
        channel_name = CHANNELS_TO_CREATE.get(container)
        if not channel_name:
            log(
                f"Container {container} 未在 CHANNELS_TO_CREATE 中找到名称，跳过",
                "ERR",
            )
            results.append(
                {
                    "container": container,
                    "channel_name": "",
                    "handle": "",
                    "status": "failed",
                    "started_at": now_iso(),
                    "finished_at": now_iso(),
                    "url_after": None,
                    "error": "频道名称未找到",
                }
            )
            continue

        log(f"━━━ Container {container}: {channel_name} ━━━", "INFO")
        result = await create_channel(container, channel_name, screenshot_dir, backend)
        results.append(result)

        if result["status"] == "success":
            log(f"Container {container} ({channel_name}) 创建成功 ✅", "OK")
        else:
            log(
                f"Container {container} ({channel_name}) 失败: {result['error']} ❌",
                "ERR",
            )

        # 频道间间隔
        await human_delay(5000, 8000)

    return results


# ============ CLI ============

def build_args():
    parser = argparse.ArgumentParser(description="YouTube 频道新建自动化")
    parser.add_argument(
        "--containers",
        type=str,
        default=",".join(str(x) for x in sorted(CHANNELS_TO_CREATE.keys())),
        help="要创建的容器列表，逗号分隔。默认 CHANNELS_TO_CREATE 里的全部容器。",
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
        default=str(SCRIPT_DIR / "create_results.json"),
        help="结果输出 JSON 路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印创建计划，不实际执行",
    )
    return parser.parse_args()


async def main():
    args = build_args()
    containers = parse_list(args.containers) if args.containers else sorted(
        CHANNELS_TO_CREATE.keys()
    )
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    backend = create_backend(args.browser)

    log("=" * 60, "INFO")
    log("YouTube 频道新建自动化", "INFO")
    log("=" * 60, "INFO")
    log(f"浏览器后端: {backend.name}", "INFO")
    log(f"运行平台: {'macOS' if sys.platform == 'darwin' else 'Windows' if sys.platform == 'win32' else sys.platform}", "INFO")
    log(f"YPP_PROTECTED (不碰): {sorted(YPP_PROTECTED)}", "WARN")
    log(f"SKIP_CONTAINERS (不碰): {sorted(SKIP_CONTAINERS)}", "WARN")
    log(f"待创建频道: {len(containers)} 个", "INFO")

    log("", "INFO")
    log("创建计划：", "INFO")
    for c in containers:
        name = CHANNELS_TO_CREATE.get(c, "❓未知")
        log(f"  Container {c:3d} → {name}", "INFO")

    if args.dry_run:
        log("\n--dry-run 模式，不实际执行。", "WARN")
        return

    log("", "INFO")
    log("5 秒后开始执行...", "WARN")
    await asyncio.sleep(5)

    results = await run_batch(containers, backend)

    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"\n结果已写入: {output_path}", "OK")

    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    log(f"\n汇总: ✅ 成功 {success} | ❌ 失败 {failed}", "INFO")

    if failed > 0:
        log("部分频道创建失败，请检查结果文件！", "ERR")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
