#!/usr/bin/env python3
"""
从零起号账号清单 —— 模板文件。

格式：{容器序号(int): 账号信息(dict)}

容器序号说明：
  - HubStudio 用户：填 HubStudio 客户端里的环境序号
  - BitBrowser 用户：填 BitBrowser 客户端里的配置文件序号

每个账号需要提供：
  - email:        Gmail 邮箱
  - password:     当前密码（登录用，登录后会被改掉）
  - totp_secret:  TOTP Base32 密钥（Google 两步验证）
  - channel_name: 要创建的 YouTube 频道名称

用法：
  1) 复制此文件为 config/accounts.py
  2) 填入真实账号信息
  3) 运行 python3 scripts/bootstrap.py --containers 1,2,3
"""

ACCOUNTS: dict[int, dict] = {
    # ============ 示例（请替换成你自己的账号）============
    # 1: {
    #     "email": "example1@gmail.com",
    #     "password": "current-password-1",
    #     "totp_secret": "ABCDEFGHIJKLMNOP",
    #     "channel_name": "晨光長笛",
    # },
    # 2: {
    #     "email": "example2@gmail.com",
    #     "password": "current-password-2",
    #     "totp_secret": "QRSTUVWXYZ234567",
    #     "channel_name": "木琴精靈",
    # },
}
