# tgbot-gui

Telegram 个人号(Userbot)签到 + 水群 + AI 回复的桌面图形工具,基于 Telethon + Tkinter。

## 功能

- 图形界面配置,无需写代码
- 一键登录个人 Telegram 账号(手机号 + 验证码)
- 自动列出你加入的所有群,勾选即可作为「签到群」或「水群」
- 签到:每日定时 / 手动立即签到,支持多个群、多条签到指令
- 水群:随机间隔从发言池抽句发言,可设活跃时段、间隔范围、多个群
- AI 回复:接 OpenAI 兼容接口(DeepSeek / 通义千问 / Kimi / GLM / OpenAI 等),
  群里来新消息时可选「全部回复」或「只在被 @ 时回复」,带频率控制
- 所有配置自动保存到 `config.json`,日志实时显示

## 使用步骤

1. 安装依赖:

   ```bash
   pip install -r requirements.txt
   ```

2. 获取 Telegram API 凭证:登录 <https://my.telegram.org> → API development tools →
   创建一个应用,记下 `api_id` 和 `api_hash`。

3. 首次运行会生成 `config.json`,也可以参考 `config.example.json` 手动创建。

4. 启动:

   ```bash
   python main.py
   ```

5. 在「登录」页填 `api_id` / `api_hash`,点「连接」,输入手机号(带区号,如
   `+86xxxxxxxxxxx`),再输入收到的验证码。

6. 连接成功后,在「群组」页点「刷新群列表」,勾选签到群和水群。

7. 配置签到时间、水群间隔、AI 参数,点「开始运行」即可。

## AI 配置

AI 水群 / AI 回复支持任何 OpenAI 兼容接口:

| 字段 | 示例 |
| --- | --- |
| Base URL |                   |
| API Key | 对应服务商提供的密钥 |
| 模型 | 对应服务商支持的模型 ID,如 `deepseek-v4-flash`、`glm-5.2`、`kimi-k3` |

填好后可在 AI 页点「检测 AI 是否可用」验证配置是否正确。

## 隐私与安全

- 你的 `api_id` / `api_hash` / API Key / 群信息都保存在本地 `config.json`,
  登录凭证保存在 `userbot.session`,这些文件**已被 .gitignore 忽略,不会上传到仓库**。
- 公开分享代码时请勿提交 `config.json`、`*.session`、`*.log`。

## 风险提示

- Telegram 官方禁止自动化个人账号行为,频繁/规律发言可能触发风控,导致限流或封号。
- **强烈建议用小号运行**,不要用常用主号。
- 部分签到群严禁脚本,被管理员发现可能踢出或举报,请自行判断。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `main.py` | 入口,Tkinter 图形界面 |
| `engine.py` | 核心引擎:Telethon 客户端、签到/水群/AI 任务,与 GUI 通过队列通信 |
| `config.py` | 配置读取/保存(`config.json`) |
| `config.example.json` | 配置模板(无敏感信息,可公开) |
| `config.json` | 运行时生成的个人配置(含密钥,已 gitignore) |
