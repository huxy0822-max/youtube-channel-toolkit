# 使用前要准备的东西清单

## 1. 基础环境

- [ ] **macOS 或 Windows**（脚本自动适配两个平台）
- [ ] **Python 3.10 或更高**
  ```bash
  python3 --version   # 确认 3.10+
  ```
- [ ] **浏览器客户端**（二选一）：
  - **HubStudio**：https://www.hubstudio.cn/
  - **BitBrowser（比特浏览器）**：https://www.bitbrowser.cn/

### 确认 API 可用

**HubStudio：**
```bash
curl -X POST http://127.0.0.1:6873/api/v1/env/list -d '{"page":1,"size":10}' -H "Content-Type: application/json"
```

**BitBrowser：**
```bash
curl -X POST http://127.0.0.1:54345/browser/list -d '{"page":0,"pageSize":10}' -H "Content-Type: application/json"
```

能返回 JSON 就行。

## 2. 容器和 Google 账号

- [ ] 在浏览器客户端里建好你要用的容器/配置文件（每个 = 一个独立指纹 + 独立代理）
- [ ] 每个容器至少手动进一次 Chrome，手动登录好 Google 账号
  - 脚本**不负责首次登录**（遇到 reCAPTCHA 就卡住了）
  - 脚本只负责"已登录状态下重新验证密码 + TOTP"这条路径
- [ ] 确认每个 Google 账号都没有"异地登录"的临时限制

### 关于容器编号

- **HubStudio**：用 `serialNumber`（环境序号），在 HubStudio 客户端列表里可以看到
- **BitBrowser**：用 `seq`（配置文件序号），在 BitBrowser 客户端列表里可以看到

两种浏览器的序号是各自独立的，脚本会自动根据 `--browser` 参数去对应的 API 查找。

## 3. 密码和 2FA 密钥

### 密码

- [ ] 统一密码 或 每账号独立密码
- [ ] 填到 `scripts/credentials.py`
  - 如果统一密码：改 `UNIFIED_PASSWORD`
  - 如果每账号不同：填 `credentials` 字典

### 2FA TOTP 密钥（如果账号开了两步验证）

- [ ] 从 Google Authenticator 导出每个账号的 TOTP 密钥
  - 方法 A（最简单）：Authenticator → 菜单 → 转移账号 → 导出 → 扫码得到 `otpauth-migration://...` URL → 用在线解码工具解出每个账号的 Base32 密钥
  - 方法 B：进 Google 账号安全设置 → 身份验证器应用 → 移除再重新添加，添加时显示"手动密钥"那行
- [ ] 填到 `scripts/totp_codes.py` 的 `TOTP_SECRETS` 字典

### 测试 TOTP 填对了

```bash
python3 scripts/totp_codes.py
# 应该打印每个邮箱对应的 6 位当前验证码
```

## 4. 频道清单（新建用）

- [ ] 准备一个 Excel/表格列出每个容器序号对应的频道名
- [ ] 按格式填到 `config/channels_to_create.py`：
  ```python
  CHANNELS_TO_CREATE: dict[int, str] = {
      10: "晨光長笛",
      11: "木琴精靈",
      # ...
  }
  ```

注意：这里的序号对应你所使用的浏览器客户端里的容器/配置文件编号。

## 5. 5sim 接码账号（电话验证用）

- [ ] 注册 https://5sim.net/zh/ 账号
- [ ] 充值（$5 左右够验证 20-30 个频道，按成功率算）
- [ ] 去 https://5sim.net/zh/settings/security 生成 JWT Token
- [ ] 复制 `config/5sim_config.template.json` 为 `config/5sim_config.json`，填入 token

## 6. YPP 保护名单（极重要！）

- [ ] 统计你所有"已开通营利权限（YouTube Partner Program）"的容器号
- [ ] 填到 **两个**脚本的 `YPP_PROTECTED` 集合：
  - `scripts/create_channel.py` 顶部
  - `scripts/delete_channel.py` 顶部

## 7. Python 依赖

```bash
cd youtube-channel-toolkit
python3 -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium  # 必跑，下载 Chromium 驱动
```

## 8. 跑通第一个测试

按顺序（以 HubStudio 为例，BitBrowser 加 `--browser bitbrowser`）：

```bash
# 只用 1 个容器试，先 dry-run
python3 scripts/delete_channel.py --containers 10 --dry-run

# 看着没问题再真跑
python3 scripts/delete_channel.py --containers 10

# 删成功后创建
python3 scripts/create_channel.py --containers 10

# 创建成功后电话验证
python3 scripts/phone_verify.py --container 10
```

**第一次跑一定要一个一个来，不要上来就批量**。

## 9. 不要做的事

- ❌ 不要把 `credentials.py` / `totp_codes.py` / `5sim_config.json` 提交到 git
- ❌ 不要在生产环境上一次跑所有容器，先跑 1-2 个
- ❌ 不要对同一个容器 10 分钟内重复 phone_verify
- ❌ 不要删 `YPP_PROTECTED` 里的容器
- ❌ 不要在网络不稳定时跑
