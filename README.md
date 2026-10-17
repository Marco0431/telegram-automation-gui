# Telegram Automation GUI · Telegram 签到与 AI 回复桌面工具

用图形界面管理 Telegram 个人账号的定时签到、定时群聊消息与可选 AI 回复，基于 **Python、Telethon 和 Tkinter**。界面为中文，使用手机号登录个人账号（Userbot）。

A Python desktop GUI for Telegram user-account automation: scheduled check-ins, configurable group messages, and optional replies through an OpenAI-compatible API. Built with Telethon and Tkinter, with a Chinese-language interface.

**入口 / Start here:** [快速开始](#快速开始--quick-start) · [AI 配置](#ai-配置) · [配置示例](config.example.json) · [项目索引 / llms.txt](llms.txt)

## 快速开始 / Quick start

需要 Python 3、Tkinter、Telegram 账号，以及在 [my.telegram.org](https://my.telegram.org) 的 **API development tools** 中创建的 `api_id` / `api_hash`。AI 功能另需服务商的 API Key；普通签到和固定句子池不需要 AI 服务。

```bash
git clone https://github.com/Marco0431/telegram-automation-gui.git
cd telegram-automation-gui
python -m pip install -r requirements.txt
python main.py
```

Fill in your Telegram API credentials on the login tab, connect with your phone number and verification code, then refresh the group list. Select the groups and features you want to use before clicking **开始运行** (Start). If two-step verification is enabled, the app also prompts for its password.

没有 Git 时，可使用仓库的 **Code → Download ZIP**，解压后在该目录执行最后两条命令。启动前可运行 `python -m tkinter` 检查 Tkinter 是否可用；缺失时需通过所用 Python 发行版或系统包管理器安装。

## 功能

- 图形界面配置,无需写代码
- 一键登录个人 Telegram 账号(手机号 + 验证码)
- 自动列出你加入的所有群,勾选即可作为「签到群」或「水群」
- 签到:每日定时 / 手动立即签到,支持多个群、多条签到指令
- 水群:随机间隔从发言池抽句发言,可设活跃时段、间隔范围、多个群
- AI 回复:通过 OpenAI 兼容的 Chat Completions 接口生成回复，可选全部消息或 @ / 回复消息触发，带频率控制
- AI 群聊:读取近期群聊上下文，按设定间隔生成消息；也可使用固定句子池
- HTTP / SOCKS5 代理、二步验证登录、系统托盘后台运行
- 所有配置自动保存到 `config.json`,日志实时显示

## 使用步骤

1. 安装依赖:

   ```bash
   python -m pip install -r requirements.txt
   ```

2. 获取 Telegram API 凭证:登录 <https://my.telegram.org> → API development tools →
   创建一个应用,记下 `api_id` 和 `api_hash`。

3. 配置保存到 `config.json`，也可以参考 [config.example.json](config.example.json) 手动创建。模板中的代理已开启，使用前请按自己的网络环境调整。

4. 启动:

   ```bash
   python main.py
   ```

5. 在「登录」页填 `api_id` / `api_hash`,点「连接」,输入手机号(带区号,如
   `+86xxxxxxxxxxx`),再输入收到的验证码。

6. 连接成功后,在「群组」页点「刷新群列表」,勾选签到群和水群。

7. 配置签到时间、水群间隔、AI 参数,点「开始运行」即可。

## AI 配置

AI 水群 / AI 回复使用 OpenAI Python SDK 的 Chat Completions 接口，需要填写服务商支持的接口地址与模型：

| 字段 | 示例 |
| --- | --- |
| Base URL | 服务商提供的 OpenAI 兼容 API 地址，例如 `https://api.deepseek.com/v1` |
| API Key | 对应服务商提供的密钥 |
| 模型 | 对应服务商支持的模型 ID；程序默认配置为 `deepseek-chat` |

填好后可在 AI 页点「检测 AI 是否可用」验证配置是否正确。

## 隐私与安全

- 你的 `api_id` / `api_hash` / API Key / 群信息保存在本地 `config.json`，登录凭证保存在 `userbot.session`。这些文件已被 `.gitignore` 忽略；这不等于加密保护，也不能阻止手动分享或强制提交。
- AI 模式会将近期群聊文本及发送者名称发送给你配置的 AI 服务商；不启用 AI 时，不会发起这些 AI 请求。
- 公开分享代码时请勿提交 `config.json`、`*.session`、`*.log`。

## 风险提示

- 请仅在允许自动化的群中使用，并遵守 Telegram 的服务条款与群规则。
- 频繁自动发言可能触发限流、账号限制或群管理员处置；间隔设置不保证避免这些问题。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| [main.py](main.py) | 入口,Tkinter 图形界面 |
| [engine.py](engine.py) | 核心引擎:Telethon 客户端、签到/水群/AI 任务,与 GUI 通过队列通信 |
| [config.py](config.py) | 默认值与配置读取/保存(`config.json`) |
| [config.example.json](config.example.json) | 配置模板(无敏感信息,可公开) |
| `config.json` | 运行时生成的个人配置(含密钥,已 gitignore) |

## 项目索引 / Project index

[llms.txt](llms.txt) 提供公开、简短的英文项目摘要与源码入口，方便读者和工具定位资料。
