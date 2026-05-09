# cpa-sub2api

当前目录用于维护 CPA/CLIProxyAPI 账号文件的转换、扫描和刷新工具。

## 目录功能

| 路径 | 类型 | 作用 |
|---|---|---|
| `auths/` | 目录 | 本地 CPA/Codex auth JSON 输入目录示例 |
| `sub2api_token/` | 目录 | `cpa2sub2api.py` 的逐文件转换输出目录 |
| `config.json` | 配置 | `cpa2sub2api.py` 自动导入 sub2api 的配置文件 |
| `cpa2sub2api.py` | Python | 把本地 auth JSON 转成 sub2api 可导入 JSON，支持合并输出、正则筛选、可选自动导入 |
| `scanner.py` | Python | 扫描本地 auth JSON，检查 401、quota exceeded、unlimited/no-limit |
| `refresh_token.py` | Python | 通过管理端 API 刷新 Codex auth token，并可优先扫描本地 auth 目录 |

## 快速选择

| 目标 | 使用脚本 |
|---|---|
| 把 auth JSON 转成 sub2api 导入文件 | `cpa2sub2api.py` |
| 扫描本地 auth 是否 401 / quota exceeded | `scanner.py` |
| 通过管理端 API 刷新 token | `refresh_token.py` |

## 运行前提

| 项目 | 说明 |
|---|---|
| Python | 建议使用 `python` 或 `python3` |
| `scanner.py` | 依赖 `aiohttp` |
| `cpa2sub2api.py` | 使用标准库即可运行 |
| `refresh_token.py` | 使用标准库即可运行 |
| 管理端 API | `refresh_token.py` 依赖 CLIProxyAPI 管理端接口 |

## cpa2sub2api.py

用途：
- 扫描本地 auth JSON 文件
- 生成 sub2api 可导入 JSON
- 支持逐文件输出和合并输出
- 支持通过正则筛选目录中的目标文件
- 可选直接调用 sub2api 管理端接口导入

当前脚本入口参数：

```powershell
python .\cpa2sub2api.py --help
```

基础用法：

```powershell
python .\cpa2sub2api.py .\auths --output-dir .\sub2api_token --no-import
```

```powershell
python .\cpa2sub2api.py .\auths --output-dir .\sub2api_token --merge --no-import
```

```powershell
python .\cpa2sub2api.py .\auths --merge-output .\all-in-one.json --no-import
```

按正则筛选目录文件：

```powershell
python .\cpa2sub2api.py .\auths --file-regex "^codex.*\.json$" --merge --no-import
```

```powershell
python .\cpa2sub2api.py .\auths --file-regex "@deg\.hush2u\.com\.json$" --merge --no-import
```

```powershell
python .\cpa2sub2api.py . --file-regex "^auths/.+\.json$" --merge --no-import
```

主要参数：
- `input_path`：输入文件或目录；不传时默认使用脚本同级 `cpa_token`
- `--output-dir`：逐文件输出目录；默认使用脚本同级 `sub2api_token`
- `--merge`：把所有结果合并到 `--output-dir/sub2api-merged.json`
- `--merge-output`：自定义合并输出文件路径
- `--file-regex`：目录模式下只处理正则命中的 `.json` 文件；同时匹配相对路径和文件名
- `--config`：自动导入配置文件路径；默认使用脚本同级 `config.json`
- `--strict`：遇到第一个非法或不支持的输入文件时立即退出
- `--no-import`：只转换，不执行 sub2api 自动导入

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
- 输入路径是目录时，脚本会递归扫描其中的 `*.json`
- 输入路径是单文件时，只处理这一份文件
- `codex` 账号没有 `refresh_token` 时，也允许继续转换；输出中只会省略该字段
- `--file-regex` 是正则，不是 Windows 通配符；例如应写 `^codex.*\.json$`，不是 `codex*.json`
- 使用 `--merge` 或 `--merge-output` 时，逐文件输出会被跳过

## scanner.py

用途：
- 递归扫描 auth 目录中的 JSON 文件
- 识别 Codex 账号
- 探测 401、quota exceeded、no-limit/unlimited
- 可选刷新 token 后再探测
- 可选删除 401 文件，或把 quota exceeded 文件移动到隔离目录

常用命令：

```powershell
python .\scanner.py --auth-dir .\auths
```

```powershell
python .\scanner.py --auth-dir .\auths --refresh-before-check
```

```powershell
python .\scanner.py --auth-dir .\auths --output-json
```

```powershell
python .\scanner.py --auth-dir .\auths --delete-401 --yes
```

```powershell
python .\scanner.py --auth-dir .\auths --exceeded-dir .\auths\exceeded
```

主要参数：
- `--auth-dir`：auth JSON 所在目录，默认 `~/.cli-proxy-api`
- `--base-url`：Codex base URL
- `--quota-path`：鉴权与配额探测路径，默认 `/responses`
- `--model`：探测使用的模型，默认 `gpt-5`
- `--timeout`：HTTP 超时秒数
- `--workers`：最大并发请求数
- `--retry-attempts`：网络错误重试次数
- `--retry-backoff`：指数退避基础秒数
- `--refresh-before-check`：探测前先用 `refresh_token` 刷新 access token
- `--refresh-url`：OAuth refresh 接口
- `--output-json`：用 JSON 输出完整结果
- `--no-progress`：关闭终端实时进度
- `--no-color`：关闭 ANSI 颜色输出
- `--delete-401`：删除返回 401 的 auth 文件
- `--yes`：配合 `--delete-401` 跳过确认
- `--exceeded-dir`：quota exceeded 文件移动到的目录
- `--no-quarantine`：关闭 quota exceeded 自动隔离

说明：
- 该脚本按“文件”处理，适合一文件一账号的本地 auth JSON

## refresh_token.py

用途：
- 通过管理端 `/auth-files` 获取 auth 文件列表
- 过滤可刷新的 Codex 账号
- 调用 OpenAI OAuth refresh 接口刷新 token
- 回写新的 `access_token`、`refresh_token`、`id_token`
- 可在调用管理端前优先扫描本地 auth 目录
- 可选发送 webhook 汇总通知

常用命令：

```powershell
python .\refresh_token.py
```

```powershell
python .\refresh_token.py --auth-dir .\auths
```

```powershell
python .\refresh_token.py --preferred-account you@example.com
```

```powershell
python .\refresh_token.py --preferred-auth-id codex-123
```

```powershell
python .\refresh_token.py --base-url http://localhost:8317/v0/management --management-key 123456
```

```powershell
python .\refresh_token.py --alert-webhook https://your-webhook.example/hook
```

主要参数：
- `--management-key`：管理端 Bearer Token
- `--base-url`：管理端 API 地址
- `--quota-url`：quota 探测地址
- `--refresh-url`：OAuth refresh 地址
- `--client-id`：OAuth `client_id`
- `--auth-dir`：可选本地 auth 目录，提供后会优先扫描本地 JSON
- `--refresh-threshold-days`：仅刷新剩余有效期小于等于该天数的 token，默认 `3`
- `--timeout`：HTTP 超时秒数
- `--preferred-auth-id`：只刷新指定 auth id 或 name
- `--preferred-account`：只刷新指定邮箱或标签
- `--alert-webhook`：可选 webhook 汇总通知
- `--log-dir`：日志目录，默认 `logs`

输出：
- 控制台打印刷新过程与结果
- 日志文件写入 `logs/refresh-*.log`

说明：
- 当提供 `--auth-dir` 时，脚本会先扫描本地目录中的 `*.json`
- 如果本地目录里没有可用账号，才继续走管理端接口
- 默认只刷新“剩余 3 天内过期”的 token

## 当前目录结构

```text
.
├── auths/
├── sub2api_token/
├── config.json
├── cpa2sub2api.py
├── README.md
├── refresh_token.py
└── scanner.py
```
