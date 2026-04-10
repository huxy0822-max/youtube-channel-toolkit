#!/bin/bash
# ============================================================
#  YouTube Channel Toolkit — macOS / Linux 一键配置脚本
# ============================================================
#
#  用法：
#    chmod +x setup.sh
#    ./setup.sh
#
#  这个脚本会：
#    1) 创建 Python 虚拟环境 + 安装依赖
#    2) 安装 Playwright Chromium
#    3) 从模板生成配置文件（如果还没有）
#    4) 提示你填写关键配置
# ============================================================

set -e

echo "============================================================"
echo "  YouTube Channel Toolkit — 自动配置"
echo "============================================================"
echo ""

# 1) Python 检查
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python 3.10+，请先安装 Python"
    exit 1
fi
echo "✅ 使用 Python: $($PYTHON --version)"

# 2) 虚拟环境
if [ ! -d "venv" ]; then
    echo ""
    echo "📦 创建虚拟环境..."
    $PYTHON -m venv venv
fi
source venv/bin/activate
echo "✅ 虚拟环境已激活"

# 3) 安装依赖
echo ""
echo "📦 安装 Python 依赖..."
pip install -r requirements.txt -q

echo ""
echo "📦 安装 Playwright Chromium..."
playwright install chromium

# 4) 生成配置文件
echo ""
echo "📋 检查配置文件..."

if [ ! -f "scripts/credentials.py" ]; then
    cat > scripts/credentials.py << 'PYEOF'
#!/usr/bin/env python3
"""Google 账号密码映射 —— 请填入你自己的密码。"""

credentials: dict[str, str] = {
    # "example1@gmail.com": "your-password-here",
}

UNIFIED_PASSWORD = "CHANGE_ME_UNIFIED_PASSWORD"

def get_password(email: str | None) -> str:
    if email and email in credentials:
        return credentials[email]
    return UNIFIED_PASSWORD
PYEOF
    echo "  📝 已生成 scripts/credentials.py（请编辑填入密码）"
else
    echo "  ✅ scripts/credentials.py 已存在"
fi

if [ ! -f "scripts/totp_codes.py" ]; then
    cat > scripts/totp_codes.py << 'PYEOF'
#!/usr/bin/env python3
"""TOTP 密钥映射 —— 请填入你自己的 TOTP 密钥。"""
from __future__ import annotations
import sys, time
import pyotp

TOTP_SECRETS: dict[str, str] = {
    # "example@gmail.com": "YOUR_BASE32_SECRET",
}

def get_totp_code(email: str) -> str | None:
    secret = TOTP_SECRETS.get(email)
    if not secret:
        return None
    try:
        return pyotp.TOTP(secret).now()
    except Exception as exc:
        print(f"❌ TOTP 生成失败 ({email}): {exc}")
        return None

def print_all_codes(filter_keyword: str = "") -> None:
    now = time.time()
    remaining = 30 - int(now % 30)
    print(f"\n{'=' * 60}")
    print(f"  TOTP 验证码  |  剩余 {remaining} 秒刷新")
    print(f"{'=' * 60}")
    count = 0
    for email, secret in TOTP_SECRETS.items():
        if filter_keyword and filter_keyword.lower() not in email.lower():
            continue
        try:
            code = pyotp.TOTP(secret).now()
            print(f"  {code}  ←  {email}")
            count += 1
        except Exception:
            print(f"  ??????  ←  {email}  (密钥格式错误)")
    print(f"\n  共 {count} 个账号")
    print(f"{'=' * 60}\n")

def main() -> None:
    filter_keyword = ""
    watch_mode = False
    for arg in sys.argv[1:]:
        if arg == "--watch":
            watch_mode = True
        else:
            filter_keyword = arg
    if watch_mode:
        try:
            while True:
                print("\033[2J\033[H", end="")
                print_all_codes(filter_keyword)
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n退出")
    else:
        print_all_codes(filter_keyword)

if __name__ == "__main__":
    main()
PYEOF
    echo "  📝 已生成 scripts/totp_codes.py（请编辑填入 TOTP 密钥）"
else
    echo "  ✅ scripts/totp_codes.py 已存在"
fi

if [ ! -f "config/channels_to_create.py" ]; then
    cp config/channels_to_create.py.template config/channels_to_create.py 2>/dev/null || \
    cat > config/channels_to_create.py << 'PYEOF'
#!/usr/bin/env python3
"""要新建的频道清单 —— 请填入你自己的频道。"""
CHANNELS_TO_CREATE: dict[int, str] = {
    # 10: "晨光長笛",
    # 11: "木琴精靈",
}
PYEOF
    echo "  📝 已生成 config/channels_to_create.py（请编辑填入频道清单）"
else
    echo "  ✅ config/channels_to_create.py 已存在"
fi

if [ ! -f "config/5sim_config.json" ]; then
    cp config/5sim_config.template.json config/5sim_config.json
    echo "  📝 已生成 config/5sim_config.json（请编辑填入 5sim TOKEN）"
else
    echo "  ✅ config/5sim_config.json 已存在"
fi

# 5) 完成
echo ""
echo "============================================================"
echo "  ✅ 配置完成！"
echo "============================================================"
echo ""
echo "  接下来你需要手动编辑以下文件："
echo ""
echo "  1. scripts/credentials.py    ← 填 Google 账号密码"
echo "  2. scripts/totp_codes.py     ← 填 TOTP 密钥（如果开了 2FA）"
echo "  3. config/channels_to_create.py  ← 填频道清单（新建频道用）"
echo "  4. config/5sim_config.json   ← 填 5sim TOKEN（电话验证用）"
echo ""
echo "  用法示例："
echo "    # HubStudio"
echo "    python3 scripts/create_channel.py --containers 10,28 --dry-run"
echo ""
echo "    # BitBrowser"
echo "    python3 scripts/create_channel.py --containers 10,28 --browser bitbrowser --dry-run"
echo ""
