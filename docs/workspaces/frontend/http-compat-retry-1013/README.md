# HTTP-COMPAT-RETRY-1013 验证证据

基线为 HEAD `9711123`、应用版本 `8a52ee7`。本目录保存本轮合成验证结果和页面截图。

## SHA-256 与 UUID

`vector-evidence.json` 记录 10 个向量：空串、`abc`、中文、emoji、55/56/63/64/65 字节边界，以及 19,800 字节的合成长原因。SHA-256 fallback 和 Web Crypto `subtle` 输出均与 Python `hashlib.sha256(utf8)` 一致。另验证 32 个 getRandomValues UUID 的 v4 版本/变体位；缺少 getRandomValues 时明确失败。

## 浏览器与页面动作

`browser-evidence.json` 来自独立 Chrome 155 headless profile。Chrome 将 `audit.invalid` 映射到本机专用 Django 模板测试服务器 `127.0.0.1:18914`；页面实际报告 `isSecureContext=false`、`crypto.subtle` 和 `crypto.randomUUID` 不可用、`crypto.getRandomValues` 可用。相同 signature 在该上下文与安全的 `127.0.0.1` 上得到相同摘要。

页面由当前 Django 模板真实渲染；API 请求全部由本次合成服务处理。Chrome DevTools Protocol 在服务记录一次业务效果并生成响应后丢弃首个响应，用来复现“服务端已成功、客户端没收到回执”。合成服务按 endpoint 和 operation_key 去重。验证客户新增/修改/转交、限制创建/释放、期初确认双击与同预览重试、调整页刷新后恢复幂等键并在成功后清除存储；另验证变更输入或 preview_token 生成新 key，期初一次性保护不绕过。Admin base_site 与 CRM base 的同步 head 加载顺序均实测。

页面截图中的客户、地址、库存和原因均为合成内容：

- `customer-form-insecure.png`
- `customer-create-dropped-response.png`
- `holds-create-dropped-response.png`
- `opening-confirm-dropped-response.png`
- `adjustment-split-dropped-response.png`

## 范围限制

这轮没有使用 PostgreSQL、真实 API、运行中的 8001/8765 演示站或云端服务，也没有执行真实期初。合成服务验证了页面产生和重用的 key 及客户端恢复；后端真实幂等效果仍由独立 QA 对固定组合负责确认。
