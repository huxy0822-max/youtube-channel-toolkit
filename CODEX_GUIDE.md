# 给 Claude Code / Codex 的操作指南

## 第一步：在新电脑上克隆代码

打开终端（macOS Terminal / Windows PowerShell），运行：

```bash
git clone https://github.com/huxy0822-max/youtube-channel-toolkit.git
cd youtube-channel-toolkit
```

## 第二步：运行一键配置脚本

### macOS / Linux

```bash
chmod +x setup.sh
./setup.sh
```

### Windows

```cmd
setup.bat
```

脚本会自动：
- 创建 Python 虚拟环境
- 安装所有依赖（Playwright、pyotp、requests）
- 下载 Chromium 浏览器驱动
- 生成空白配置文件模板

## 第三步：填写配置文件

配置脚本跑完后，你需要手动编辑 **4 个配置文件**（这些文件被 .gitignore 忽略，不会上传到 GitHub）：

### 3.1 填密码：`scripts/credentials.py`

```python
# 方式 A：所有账号用同一个密码（最简单）
UNIFIED_PASSWORD = "你的统一密码"

# 方式 B：每个账号不同密码
credentials: dict[str, str] = {
    "account1@gmail.com": "密码1",
    "account2@gmail.com": "密码2",
}
```

### 3.2 填 TOTP 密钥：`scripts/totp_codes.py`

如果你的 Google 账号开了两步验证（2FA），需要填 TOTP 密钥：

```python
TOTP_SECRETS: dict[str, str] = {
    "account1@gmail.com": "ABCDEFGHIJKLMNOP",  # Base32 密钥
    "account2@gmail.com": "QRSTUVWXYZ234567",
}
```

填完后验证：
```bash
python3 scripts/totp_codes.py
# 应该打印出和你手机 Authenticator 上一样的 6 位码
```

### 3.3 填频道清单：`config/channels_to_create.py`

```python
CHANNELS_TO_CREATE: dict[int, str] = {
    10: "晨光長笛",   # 容器序号: 频道名
    11: "木琴精靈",
    28: "深夜節拍",
}
```

**容器序号** = 你在浏览器客户端（HubStudio 或 BitBrowser）里看到的编号。

### 3.4 填 hero-sms 接码 API Key：`config/hero_sms_config.json`

```json
{
  "api_key": "你的 hero-sms API Key",
  "country": 6,
  "service": "go",
  "max_price": 0.03
}
```

- `api_key`：从 https://hero-sms.com/ 个人中心获取
- `country`：6 = 印尼（Indonesia），$0.03/个，库存充足
- `service`：`go` = Google/YouTube
- `max_price`：限价 $0.03，不会买到贵的号

## 第四步：选择浏览器后端

### 如果用 HubStudio

不需要额外配置，HubStudio 是默认后端：

```bash
python3 scripts/create_channel.py --containers 10,28
```

### 如果用 BitBrowser（比特浏览器）

每条命令加 `--browser bitbrowser`：

```bash
python3 scripts/create_channel.py --containers 10,28 --browser bitbrowser
```

或者设置环境变量免得每次都写：

```bash
# macOS / Linux
export BROWSER_BACKEND=bitbrowser

# Windows CMD
set BROWSER_BACKEND=bitbrowser

# Windows PowerShell
$env:BROWSER_BACKEND = "bitbrowser"
```

## 第五步：开始使用

**务必先 dry-run 再真跑！**

```bash
# 激活虚拟环境
# macOS:   source venv/bin/activate
# Windows: venv\Scripts\activate

# ==================== 删除频道 ====================
# dry-run（只看计划不执行）
python3 scripts/delete_channel.py --containers 10,28 --dry-run

# 真的删
python3 scripts/delete_channel.py --containers 10,28

# ==================== 新建频道 ====================
python3 scripts/create_channel.py --containers 10,28 --dry-run
python3 scripts/create_channel.py --containers 10,28

# ==================== 电话验证 ====================
python3 scripts/phone_verify.py --containers 10,28 --max-tries 3
```

## 给 Claude Code 的 Prompt 模板

如果你想让 Claude Code 帮你操作，可以直接粘贴以下 prompt：

---

> 我有一个 YouTube 频道管理工具包在 `youtube-channel-toolkit/` 目录。
> 请帮我：
>
> 1. 先读一下 README.md 了解项目结构
> 2. 检查 `scripts/credentials.py` 和 `scripts/totp_codes.py` 是否已配置
> 3. 我要用 **[HubStudio / BitBrowser]** 浏览器
> 4. 帮我对容器 **[填你的容器号]** 执行 **[删除 / 新建 / 电话验证]**
> 5. 先 dry-run 给我看计划，确认后再真的跑

---

## 常见问题

### Q: 换了电脑后容器序号变了怎么办？

容器序号取决于你在浏览器客户端里的环境列表顺序。换电脑后需要：
1. 打开浏览器客户端确认新的序号
2. 更新 `config/channels_to_create.py` 和命令行参数

### Q: HubStudio 和 BitBrowser 的容器能混用吗？

不能。每次运行只能选一个后端。但你可以在同一台电脑上装两个客户端，分开跑。

### Q: API 端口不是默认的怎么办？

修改 `scripts/utils.py` 里的默认端口，或在代码中手动指定：
```python
backend = create_backend("bitbrowser", api_base="http://127.0.0.1:你的端口")
```
