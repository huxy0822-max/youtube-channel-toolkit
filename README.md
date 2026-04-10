# YouTube 频道管理工具包

## 这是什么

三个独立 Python 脚本，用来自动化管理 YouTube 频道的"重生"流程：

| 脚本 | 干什么 | 什么时候用 |
|------|--------|-----------|
| `delete_channel.py` | 删除已有频道 | 旧频道数据不好，想清空重来 |
| `create_channel.py` | 新建空频道 | 删完之后或开新容器时 |
| `phone_verify.py` | 手机验证（接码） | 新频道首次发视频前必须通过 |

三个脚本都通过 **Chrome DevTools Protocol (CDP)** 连接到已经登录 Google 账号的 Chrome 实例，用 **Playwright** 驱动页面点击。

## 支持的浏览器后端

| 后端 | 本地 API 地址 | `--browser` 参数值 |
|------|-------------|-------------------|
| **HubStudio** | `http://127.0.0.1:6873` | `hubstudio`（默认） |
| **BitBrowser（比特浏览器）** | `http://127.0.0.1:54345` | `bitbrowser` |

所有脚本通过 `--browser` 参数选择后端，不传默认 HubStudio。

## 支持的操作系统

| 平台 | 状态 |
|------|------|
| **macOS** | ✅ 完全支持 |
| **Windows** | ✅ 完全支持 |

脚本自动检测平台，适配键盘快捷键（macOS 用 `Cmd+A`，Windows 用 `Ctrl+A`）。

## 核心依赖

- **HubStudio 本地客户端**（https://www.hubstudio.cn/）或 **BitBrowser 本地客户端**（https://www.bitbrowser.cn/）
- **Python 3.10+**
- **Playwright** — 页面自动化
- **pyotp** — 生成 Google 两步验证码
- **hero-sms.com 账号**（仅 phone_verify 需要） — 接码平台

## 快速上手

### 第一步：装依赖

```bash
cd youtube-channel-toolkit
python3 -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium   # 下载 Playwright 需要的 Chromium 二进制
```

### 第二步：准备账号信息

1. **填密码** —— 编辑 `scripts/credentials.py`：
   - 如果所有账号一个密码：只改 `UNIFIED_PASSWORD`
   - 如果每个账号不同密码：填 `credentials` 字典

2. **填 TOTP 密钥**（如果账号开了 Google 2FA）—— 编辑 `scripts/totp_codes.py`：
   - 把每个账号的 Base32 TOTP 密钥填进 `TOTP_SECRETS` 字典

3. **填频道清单**（只新建时需要）—— 编辑 `config/channels_to_create.py`：
   - 把 `CHANNELS_TO_CREATE` 字典填成你要建的容器号 → 频道名

4. **填 hero-sms API Key**（只电话验证时需要）—— 复制并编辑配置：
   ```bash
   cp config/hero_sms_config.template.json config/hero_sms_config.json
   # 编辑 hero_sms_config.json 填入你的 API Key
   ```

### 第三步：确认浏览器客户端已就绪

**HubStudio 用户：**
1. HubStudio 客户端打开，确认本地 API 监听在 `http://127.0.0.1:6873`
2. 每个要操作的容器里，确认 Google 账号**已经手动登录过**一次

**BitBrowser（比特浏览器）用户：**
1. BitBrowser 客户端打开，确认本地 API 监听在 `http://127.0.0.1:54345`
2. 每个要操作的窗口配置文件里，确认 Google 账号**已经手动登录过**一次

（脚本不处理首次登录 reCAPTCHA）

### 第四步：跑脚本

```bash
# ========== HubStudio（默认，不需要加 --browser）==========

# 先 dry run 看看计划对不对
python3 scripts/delete_channel.py --containers 10,28 --dry-run
python3 scripts/create_channel.py --dry-run

# 真的跑
python3 scripts/delete_channel.py --containers 10,28
python3 scripts/create_channel.py --containers 10,28
python3 scripts/phone_verify.py --containers 10,28 --max-tries 3

# ========== BitBrowser（加 --browser bitbrowser）==========

python3 scripts/delete_channel.py --containers 10,28 --browser bitbrowser --dry-run
python3 scripts/create_channel.py --browser bitbrowser --dry-run

python3 scripts/delete_channel.py --containers 10,28 --browser bitbrowser
python3 scripts/create_channel.py --containers 10,28 --browser bitbrowser
python3 scripts/phone_verify.py --containers 10,28 --max-tries 3 --browser bitbrowser
```

## 三个脚本的典型工作流

```
┌──────────────┐
│ 旧频道效果不好 │
└──────┬───────┘
       ▼
┌──────────────────┐
│ delete_channel.py │ ← 登录 → 删除 → 验证 deletesuccess
└──────┬───────────┘
       ▼
┌──────────────────┐
│ create_channel.py │ ← 打开 youtube → 点 Create → 填名字 Handle
└──────┬───────────┘
       ▼
┌───────────────┐
│ phone_verify.py │ ← 5sim 买号 → 填号 → 等码 → 提交
└──────┬────────┘
       ▼
┌──────────────────┐
│ 新频道可以发视频了 │
└──────────────────┘
```

## 安全红线（务必看）

三个脚本都支持 `YPP_PROTECTED` 和 `SKIP_CONTAINERS` 两个保护集合：

```python
# scripts/delete_channel.py 和 scripts/create_channel.py 顶部
YPP_PROTECTED: set[int] = set()    # 有营利权限的容器号，绝对不动
SKIP_CONTAINERS: set[int] = set()  # 其他原因跳过的容器号
```

**强烈建议把所有"已开通营利（YPP）的频道容器号"都填进 `YPP_PROTECTED`**，这样即使命令行参数写错也不会误删。

## 踩过的坑（重要）

### 删除

- 删完后必须重新导航 `youtubeoptions` 检查 "Channel already deleted"，有的账号有 2-3 个频道需要连删
- `accounts.google.com` 登录页有时没跳出密码框（显示 "Verify it's you"），要用 `input[type="password"]` 兜底
- TOTP 输入后等 8-12 秒再验证（太快会 early fail）

### 创建

- Create channel 按钮有时灰色 → 通常是 Handle 冲突，脚本会自动换 3 次
- Handle 格式建议 `频道名-xxx`（3 位随机小写字母数字），纯数字容易重复
- 创建后不要立刻关浏览器窗口，留着人工检查

### 电话验证

- **hero-sms 接码成功率因运营商而异**，连续失败是正常的，不是 bug
- 默认配置用印尼 (country=6) + Google (service=go)，$0.03/个
- **不要在同一个容器上连续试多个号** —— 会触发 YouTube "too many attempts" 限频 → 等一周才能再试
- YouTube verify 页的按钮是 `tp-yt-paper-button`（不是标准 `<button>`）

### 比特浏览器 vs HubStudio 注意事项

- **容器编号**：HubStudio 用 `serialNumber`（环境序号），BitBrowser 用 `seq`（配置文件序号）。两者在各自客户端 UI 里都可以看到。
- **API 端口**：HubStudio 默认 6873，BitBrowser 默认 54345。
- **配置文件 ID**：BitBrowser 内部用 UUID，脚本已自动处理序号→UUID 的转换。

## 环境变量（可选）

可以通过环境变量设置默认浏览器后端，避免每次都传 `--browser`：

```bash
# macOS / Linux
export BROWSER_BACKEND=bitbrowser

# Windows (CMD)
set BROWSER_BACKEND=bitbrowser

# Windows (PowerShell)
$env:BROWSER_BACKEND = "bitbrowser"
```

设置后，不传 `--browser` 参数时会自动使用该后端。

## 文件结构

```
youtube-channel-toolkit/
├── README.md                          # 这个文件
├── REQUIREMENTS.md                    # 准备工作清单（具体到每一步）
├── requirements.txt                   # Python 依赖
├── scripts/
│   ├── utils.py                       # 浏览器后端抽象层（HubStudio + BitBrowser）
│   ├── credentials.py                 # 密码配置（改成你自己的）
│   ├── totp_codes.py                  # TOTP 密钥配置（改成你自己的）
│   ├── create_channel.py              # 建频道主脚本
│   ├── delete_channel.py              # 删频道主脚本
│   └── phone_verify.py                # 电话验证主脚本
└── config/
    ├── channels_to_create.py          # 频道列表（改成你自己的）
    ├── 5sim_config.template.json      # 5sim 配置模板（旧版备用）
    └── hero_sms_config.template.json  # hero-sms 配置模板（复制为 hero_sms_config.json）
```

## 不提供保证

这是经验脚本，不是商用产品。YouTube/Google 的反爬策略和 UI 一直在变，某个时间跑得通不保证下一天还行。出问题先看截图（`screenshots/` 目录下会自动存每一步），再去对应脚本里找对应的 locator 改。
