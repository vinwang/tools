# cliproxyapi Scripts

当前目录脚本说明与用法。

## 脚本总览

| 文件 | 类型 | 作用 |
|---|---|---|
| `scanner.py` | Python | 扫描 CLIProxyAPI/Codex auth JSON，检查 401、quota exceeded、unlimited |
| `sub2api_scanner.py` | Python | 扫描 `sub2api_accounts_import*.json` 中的账号状态 |
| `cpa2sub2api.py` | Python | 把本地 token JSON 批量转换成 sub2api 导入 JSON，支持合并输出、正则筛选、可选自动导入 |
| `refresh_token.py` | Python | 通过管理端 API 批量刷新 Codex auth token，并回写本地 auth 文件 |
| `refresh_token.txt` | PowerShell | Windows 版原始刷新脚本，含计划任务安装/卸载能力 |

## 运行前提

| 项目 | 说明 |
|---|---|
| Python | 建议 `python3` |
| `scanner.py` | 依赖 `aiohttp`，未安装时脚本无法启动 |
| 其他 Python 脚本 | 使用标准库即可运行 |
| 管理端 API | `refresh_token.py` 和 `refresh_token.txt` 依赖 `http://localhost:8317/v0/management` 这类管理接口 |

## scanner.py

用途：
- 递归扫描 auth 目录下的 JSON 文件
- 识别 Codex 账号
- 探测 401、quota exceeded、no-limit/unlimited
- 可选删除 401 文件，或把 quota exceeded 文件移动到隔离目录

常用命令：

```bash
python3 scanner.py --auth-dir ~/.cli-proxy-api
```

```bash
python3 scanner.py --auth-dir ~/.cli-proxy-api --refresh-before-check
```

```bash
python3 scanner.py --auth-dir ~/.cli-proxy-api --output-json
```

```bash
python3 scanner.py --auth-dir ~/.cli-proxy-api --delete-401 --yes
```

主要参数：
- `--auth-dir`：auth JSON 所在目录
- `--base-url`：Codex base URL
- `--quota-path`：探测接口路径，默认 `/responses`
- `--model`：探测使用的模型，默认 `gpt-5`
- `--refresh-before-check`：探测前先用 `refresh_token` 刷新 access token
- `--output-json`：JSON 格式输出
- `--delete-401`：删除返回 401 的 auth 文件
- `--exceeded-dir`：quota exceeded 文件移动到的目录
- `--no-quarantine`：关闭 quota exceeded 自动隔离

说明：
- 该脚本按“文件”处理，适用于一文件一账号的 CLIProxyAPI auth JSON。

## sub2api_scanner.py

用途：
- 扫描 `sub2api_accounts_import*.json`
- 从 `accounts[].credentials` 中提取 `access_token`、`refresh_token`、`chatgpt_account_id`
- 对每个账号逐条探测 401、quota exceeded、unlimited

常用命令：

```bash
python3 sub2api_scanner.py --input ./sub2api_accounts_import.json
```

```bash
python3 sub2api_scanner.py --input . --recursive
```

```bash
python3 sub2api_scanner.py --input ./sub2api_accounts_import.json --refresh-before-check
```

```bash
python3 sub2api_scanner.py --input ./sub2api_accounts_import.json --output-json
```

主要参数：
- `--input`：Sub2API 导入 JSON 文件或目录
- `--include`：目录扫描时的文件匹配规则，默认 `sub2api_accounts_import*.json`
- `--recursive`：递归扫描目录
- `--base-url`：Codex base URL
- `--quota-path`：探测接口路径，默认 `/responses`
- `--refresh-before-check`：探测前刷新 access token
- `--output-json`：JSON 格式输出

说明：
- 该脚本按“账号”处理，不会删除或移动导入文件。

## cpa2sub2api.py

用途：
- 扫描本地 token JSON 文件
- 批量生成 Sub2API 可导入 JSON
- 支持逐文件输出、单文件合并输出
- 支持通过正则筛选目录下要处理的文件
- 可选直接调用 sub2api 管理端接口导入

常用命令：

```bash
python3 cpa2sub2api.py ./group-5 --output-dir ./out --no-import
```

```bash
python3 cpa2sub2api.py ./group-5 --output-dir ./out --merge --no-import
```

```bash
python3 cpa2sub2api.py ./group-5 --merge-output ./all-in-one.json --no-import
```

```bash
python3 cpa2sub2api.py . --file-regex '^group-5/.+\.json$' --merge --no-import
```

```bash
python3 cpa2sub2api.py . --file-regex 'bakki.*\.json$' --merge --no-import
```

主要参数：
- `input_path`：输入文件或目录；不传时默认使用脚本同级 `cpa_token`
- `--output-dir`：逐文件输出目录；默认使用脚本同级 `sub2api_token`
- `--merge`：把所有转换结果合并到 `--output-dir/sub2api-merged.json`
- `--merge-output`：自定义合并输出文件路径
- `--file-regex`：目录模式下只处理正则命中的 `.json` 文件；同时匹配相对路径和文件名
- `--config`：配置文件路径；默认使用脚本同级 `config.json`
- `--strict`：遇到第一个非法或不支持的输入文件时立即退出
- `--no-import`：只转换，不执行 sub2api 自动导入

正则筛选说明：
- `--file-regex '^group-5/.+\.json$'`：只处理 `group-5` 目录下的 JSON
- `--file-regex '@deg\.hush2u\.com\.json$'`：只处理指定邮箱域名结尾的文件
- `--file-regex 'bakki.*\.json$'`：只处理文件名中包含 `bakki` 的文件

自动导入配置：
- 首次运行时，如果 `config.json` 不存在，脚本会自动生成模板
- 开启自动导入时，需要把 `sub2api.auto_import` 设为 `true`
- 同时配置 `sub2api.base_url` 和鉴权字段

示例配置：

```json
{
  "version": 1,
  "sub2api": {
    "auto_import": true,
    "base_url": "http://127.0.0.1:8000",
    "auth_mode": "admin_api_key",
    "admin_api_key": "your-admin-key",
    "bearer_token": "",
    "timeout_seconds": 30,
    "verify_tls": true,
    "skip_default_group_bind": true
  }
}
```

说明：
- 输入路径是目录时，会递归扫描其中的 `*.json`
- 输入路径是单文件时，只处理这一份文件
- 使用 `--merge` 或 `--merge-output` 时，逐文件输出会被跳过
- 生成结果默认是 sub2api 当前可导入的账号结构，而不是旧版 `sub2api_accounts_import.json` 命名风格

## refresh_token.py

用途：
- 可直接扫描本地 `auths` 目录下的 JSON 文件
- 通过管理端 `/auth-files` 拉取本地 auth 文件列表
- 过滤 active 的 codex 账号
- 如果 auth 文件中存在 `refresh_token`，调用 OpenAI 刷新接口
- 回写新的 `access_token`、`id_token`、`refresh_token`
- 再请求 quota 接口确认 token 仍可用

常用命令：

```bash
python3 refresh_token.py
```

```bash
python3 refresh_token.py --auth-dir ~/.cli-proxy-api
```

```bash
python3 refresh_token.py --auth-dir ~/.cli-proxy-api --preferred-account you@example.com
```

```bash
python3 refresh_token.py --base-url http://localhost:8317/v0/management --management-key 123456
```

```bash
python3 refresh_token.py --preferred-auth-id codex-123
```

```bash
python3 refresh_token.py --preferred-account you@example.com
```

```bash
python3 refresh_token.py --alert-webhook https://your-webhook.example/hook
```

主要参数：
- `--management-key`：管理端 Bearer Token
- `--base-url`：管理端 API 地址
- `--quota-url`：quota 探测地址
- `--refresh-url`：OAuth refresh 地址
- `--client-id`：OAuth `client_id`
- `--auth-dir`：本地 auth 目录，提供后会先扫描本地目录，找不到有效账号再回退到管理端
- `--refresh-threshold-days`：仅在 token 剩余有效期小于等于该天数时才刷新，默认 `3`
- `--timeout`：请求超时秒数
- `--preferred-auth-id`：只刷新指定 auth id 或 name
- `--preferred-account`：只刷新指定邮箱/标签
- `--alert-webhook`：可选 webhook 汇总通知
- `--log-dir`：日志目录，默认 `logs`

输出：
- 控制台打印进度与结果
- 日志文件写入 `logs/refresh-*.log`

说明：
- 当提供 `--auth-dir` 时，脚本会优先递归扫描目录中的 `*.json`。
- 如果目录里没有可用的 Codex auth，脚本会显式记录日志，然后回退到管理端 `/auth-files`。
- 默认只刷新“还有 3 天内过期”的 token；未接近过期时会跳过刷新，只继续做 quota 探测。

## refresh_token.txt

用途：
- `refresh_token.py` 的 Windows PowerShell 原版
- 除了刷新 token，还支持安装/卸载 Windows 计划任务

PowerShell 用法：

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh_token.txt
```

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh_token.txt -PreferredAuthId codex-123
```

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh_token.txt -PreferredAccount you@example.com
```

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh_token.txt -InstallTask
```

```powershell
powershell -ExecutionPolicy Bypass -File .\refresh_token.txt -UninstallTask
```

说明：
- `-InstallTask` 会创建名为 `CLIProxyAPI-Codex-Refresh` 的计划任务，默认每 `6` 小时执行一次。
- 该脚本包含 Windows 专属能力，例如 `schtasks.exe` 和弹窗提醒。

## 快速选择

| 目标 | 使用脚本 |
|---|---|
| 扫描普通 CLIProxyAPI auth JSON | `scanner.py` |
| 扫描 Sub2API 导入文件中的账号 | `sub2api_scanner.py` |
| 生成 Sub2API 导入文件 | `cpa2sub2api.py` |
| 刷新管理端返回的 Codex auth 文件 | `refresh_token.py` |
| 在 Windows 上安装自动刷新计划任务 | `refresh_token.txt` |
