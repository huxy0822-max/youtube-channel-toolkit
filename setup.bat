@echo off
chcp 65001 >nul 2>&1
REM ============================================================
REM  YouTube Channel Toolkit — Windows 一键配置脚本
REM ============================================================
REM
REM  用法：双击运行或在命令行执行 setup.bat
REM
REM  这个脚本会：
REM    1) 创建 Python 虚拟环境 + 安装依赖
REM    2) 安装 Playwright Chromium
REM    3) 从模板生成配置文件（如果还没有）
REM    4) 提示你填写关键配置
REM ============================================================

echo ============================================================
echo   YouTube Channel Toolkit — 自动配置
echo ============================================================
echo.

REM 1) Python 检查
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

python --version
echo.

REM 2) 虚拟环境
if not exist "venv" (
    echo 📦 创建虚拟环境...
    python -m venv venv
)
call venv\Scripts\activate.bat
echo ✅ 虚拟环境已激活
echo.

REM 3) 安装依赖
echo 📦 安装 Python 依赖...
pip install -r requirements.txt -q
echo.

echo 📦 安装 Playwright Chromium...
playwright install chromium
echo.

REM 4) 生成配置文件
echo 📋 检查配置文件...

if not exist "scripts\credentials.py" (
    (
        echo #!/usr/bin/env python3
        echo """Google 账号密码映射 -- 请填入你自己的密码。"""
        echo.
        echo credentials: dict[str, str] = {
        echo     # "example1@gmail.com": "your-password-here",
        echo }
        echo.
        echo UNIFIED_PASSWORD = "CHANGE_ME_UNIFIED_PASSWORD"
        echo.
        echo def get_password^(email: str ^| None^) -^> str:
        echo     if email and email in credentials:
        echo         return credentials[email]
        echo     return UNIFIED_PASSWORD
    ) > scripts\credentials.py
    echo   📝 已生成 scripts\credentials.py（请编辑填入密码）
) else (
    echo   ✅ scripts\credentials.py 已存在
)

if not exist "scripts\totp_codes.py" (
    echo   📝 请手动创建 scripts\totp_codes.py（参考 README.md）
) else (
    echo   ✅ scripts\totp_codes.py 已存在
)

if not exist "config\channels_to_create.py" (
    echo   📝 请手动创建 config\channels_to_create.py（参考 README.md）
) else (
    echo   ✅ config\channels_to_create.py 已存在
)

if not exist "config\5sim_config.json" (
    if exist "config\5sim_config.template.json" (
        copy config\5sim_config.template.json config\5sim_config.json >nul
        echo   📝 已生成 config\5sim_config.json（请编辑填入 5sim TOKEN）
    )
) else (
    echo   ✅ config\5sim_config.json 已存在
)

echo.
echo ============================================================
echo   ✅ 配置完成！
echo ============================================================
echo.
echo   接下来你需要手动编辑以下文件：
echo.
echo   1. scripts\credentials.py        ← 填 Google 账号密码
echo   2. scripts\totp_codes.py         ← 填 TOTP 密钥
echo   3. config\channels_to_create.py  ← 填频道清单
echo   4. config\5sim_config.json       ← 填 5sim TOKEN
echo.
echo   用法示例：
echo     python scripts\create_channel.py --containers 10,28 --dry-run
echo     python scripts\create_channel.py --containers 10,28 --browser bitbrowser --dry-run
echo.
pause
