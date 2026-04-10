#!/usr/bin/env python3
"""
浏览器后端抽象层 —— 同时支持 HubStudio 和 BitBrowser

两种浏览器都通过本地 HTTP API 管理容器/配置文件，并提供 Chrome DevTools Protocol (CDP)
调试端口供 Playwright 连接。本模块统一封装了：
  - 获取环境/配置文件列表
  - 根据序号启动浏览器并返回 CDP 调试端口
  - 关闭浏览器

HubStudio API:  http://127.0.0.1:6873
BitBrowser API: http://127.0.0.1:54345

用法：
  from utils import create_backend, log

  backend = create_backend("hubstudio")   # 或 "bitbrowser"
  port = backend.get_port_by_env(10)
  backend.close_browser(10)
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import requests

# ============ 平台检测 ============

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
SELECT_ALL_KEY = "Meta+a" if IS_MAC else "Control+a"


# ============ 日志 ============

def log(msg: str, level: str = "INFO") -> None:
    """统一日志格式。"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    icons = {
        "INFO": "ℹ️", "OK": "✅", "ERR": "❌",
        "WARN": "⚠️", "ACT": "🖱️", "WAIT": "⏳",
    }
    print(f"[{timestamp}] {icons.get(level, '•')} {msg}")


# ============ 浏览器后端抽象基类 ============

class BrowserBackend(ABC):
    """浏览器管理后端的统一接口。"""

    name: str  # "hubstudio" 或 "bitbrowser"

    @abstractmethod
    def get_env_list(self) -> list:
        """获取所有环境/配置文件列表。"""

    @abstractmethod
    def get_port_by_env(self, serial_number: int) -> Optional[int]:
        """根据序号启动浏览器并返回 CDP 调试端口。"""

    @abstractmethod
    def close_browser(self, serial_number: int) -> None:
        """关闭指定序号的浏览器。"""


# ============ HubStudio 后端 ============

class HubStudioBackend(BrowserBackend):
    """
    HubStudio 本地 API（默认 http://127.0.0.1:6873）

    API 文档参考 HubStudio 客户端内置帮助。
    """

    name = "hubstudio"

    def __init__(self, api_base: str = "http://127.0.0.1:6873"):
        self.api_base = api_base

    def get_env_list(self) -> list:
        try:
            resp = requests.post(
                f"{self.api_base}/api/v1/env/list",
                json={"page": 1, "size": 200},
                timeout=30,
            )
            result = resp.json()
            if result.get("code") == 0:
                return result.get("data", {}).get("list", [])
        except Exception as e:
            log(f"[HubStudio] 获取环境列表失败: {e}", "ERR")
        return []

    def _find_container_code(self, serial_number: int) -> Optional[str]:
        envs = self.get_env_list()
        for env in envs:
            if env.get("serialNumber") == serial_number:
                return env.get("containerCode")
        return None

    def get_port_by_env(self, serial_number: int) -> Optional[int]:
        container_code = self._find_container_code(serial_number)
        if not container_code:
            log(f"[HubStudio] 未找到序号 {serial_number} 的环境", "ERR")
            return None

        try:
            resp = requests.post(
                f"{self.api_base}/api/v1/browser/start",
                json={"containerCode": container_code},
                timeout=60,
            )
            result = resp.json()
            if result.get("code") == 0:
                port = result.get("data", {}).get("debuggingPort")
                log(f"[HubStudio] 环境 {serial_number} 的调试端口: {port}", "OK")
                return port
            log(f"[HubStudio] 启动浏览器失败: {result.get('msg')}", "ERR")
        except Exception as e:
            log(f"[HubStudio] 启动浏览器失败: {e}", "ERR")
        return None

    def close_browser(self, serial_number: int) -> None:
        container_code = self._find_container_code(serial_number)
        if not container_code:
            return
        try:
            requests.post(
                f"{self.api_base}/api/v1/browser/stop",
                json={"containerCode": container_code},
                timeout=30,
            )
            log(f"[HubStudio] [{serial_number}] 浏览器已关闭", "INFO")
        except Exception as e:
            log(f"[HubStudio] [{serial_number}] 关闭浏览器失败: {e}", "WARN")


# ============ BitBrowser 后端 ============

class BitBrowserBackend(BrowserBackend):
    """
    BitBrowser (比特浏览器) 本地 API（默认 http://127.0.0.1:54345）

    API 文档：https://doc.bitbrowser.cn/
    关键区别：
      - BitBrowser 用 UUID 作为配置文件 ID，用 seq (整数序号) 对应用户可见的编号
      - 列表接口 page 从 0 开始（HubStudio 从 1 开始）
      - 启动接口返回的端口在 ws URL 里，需要解析
    """

    name = "bitbrowser"

    def __init__(self, api_base: str = "http://127.0.0.1:54345"):
        self.api_base = api_base

    def get_env_list(self) -> list:
        try:
            resp = requests.post(
                f"{self.api_base}/browser/list",
                json={"page": 0, "pageSize": 200},
                timeout=30,
            )
            result = resp.json()
            if result.get("success"):
                return result.get("data", {}).get("list", [])
        except Exception as e:
            log(f"[BitBrowser] 获取配置文件列表失败: {e}", "ERR")
        return []

    def _find_profile_id(self, serial_number: int) -> Optional[str]:
        """根据序号 (seq) 查找 BitBrowser 配置文件的 UUID。"""
        profiles = self.get_env_list()
        for profile in profiles:
            if profile.get("seq") == serial_number:
                return profile.get("id")
        return None

    def get_port_by_env(self, serial_number: int) -> Optional[int]:
        profile_id = self._find_profile_id(serial_number)
        if not profile_id:
            log(f"[BitBrowser] 未找到序号 {serial_number} 的配置文件", "ERR")
            return None

        try:
            resp = requests.post(
                f"{self.api_base}/browser/open",
                json={"id": profile_id},
                timeout=60,
            )
            result = resp.json()
            if result.get("success"):
                data = result.get("data", {})
                # BitBrowser 返回格式：
                #   "http": "127.0.0.1:端口"
                #   "ws": "ws://127.0.0.1:端口/devtools/browser/..."
                http_addr = data.get("http", "")
                if http_addr and ":" in http_addr:
                    port = int(http_addr.split(":")[-1])
                    log(f"[BitBrowser] 配置文件 {serial_number} 的调试端口: {port}", "OK")
                    return port
                # 备用：从 ws URL 解析端口
                ws_addr = data.get("ws", "")
                if ws_addr:
                    # ws://127.0.0.1:9222/devtools/browser/xxx
                    import re
                    m = re.search(r":(\d+)/", ws_addr)
                    if m:
                        port = int(m.group(1))
                        log(f"[BitBrowser] 配置文件 {serial_number} 的调试端口: {port}", "OK")
                        return port
                log(f"[BitBrowser] 启动成功但未获取到端口: {data}", "ERR")
            else:
                log(f"[BitBrowser] 启动浏览器失败: {result.get('msg')}", "ERR")
        except Exception as e:
            log(f"[BitBrowser] 启动浏览器失败: {e}", "ERR")
        return None

    def close_browser(self, serial_number: int) -> None:
        profile_id = self._find_profile_id(serial_number)
        if not profile_id:
            return
        try:
            requests.post(
                f"{self.api_base}/browser/close",
                json={"id": profile_id},
                timeout=30,
            )
            log(f"[BitBrowser] [{serial_number}] 浏览器已关闭", "INFO")
        except Exception as e:
            log(f"[BitBrowser] [{serial_number}] 关闭浏览器失败: {e}", "WARN")


# ============ 工厂函数 ============

_BACKENDS = {
    "hubstudio": HubStudioBackend,
    "bitbrowser": BitBrowserBackend,
}


def create_backend(name: str = "hubstudio", api_base: str | None = None) -> BrowserBackend:
    """创建浏览器后端实例。

    Args:
        name: "hubstudio" 或 "bitbrowser"
        api_base: 自定义 API 地址。不传则用默认值。
    """
    name = name.lower().strip()
    cls = _BACKENDS.get(name)
    if not cls:
        raise ValueError(
            f"未知的浏览器后端: {name!r}。可选: {', '.join(_BACKENDS.keys())}"
        )
    if api_base:
        return cls(api_base=api_base)
    return cls()


# ============ 向后兼容（旧代码直接 from utils import get_port_by_env）============
# 默认使用 HubStudio，除非设置了环境变量 BROWSER_BACKEND

import os as _os

_default_backend_name = _os.environ.get("BROWSER_BACKEND", "hubstudio")
_default_backend = create_backend(_default_backend_name)


def get_env_list() -> list:
    """向后兼容：使用默认后端获取环境列表。"""
    return _default_backend.get_env_list()


def get_port_by_env(serial_number: int) -> int | None:
    """向后兼容：使用默认后端获取调试端口。"""
    return _default_backend.get_port_by_env(serial_number)


def close_browser(serial_number: int) -> None:
    """向后兼容：使用默认后端关闭浏览器。"""
    return _default_backend.close_browser(serial_number)
