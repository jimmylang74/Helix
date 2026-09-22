# CodeGraph MCP Server (extracted from oh-my-openagent plugin)

自 oh-my-openagent 插件抠出的 **codegraph** MCP（本地 stdio 服务），供 Helix 直接使用。

## 出处 (Provenance)

插件（`packages/omo-opencode/src/mcp/codegraph.ts`）通过 `resolveCodegraphCommand()`
按以下优先级解析 codegraph 可执行文件：

1. 环境变量 `OMO_CODEGRAPH_BIN` / `CODEGRAPH_BIN`
2. 插件内置 npm 包 `@colbymchenry/codegraph`（bundled shim）
3. **provisioned 安装**：`~/.omo/codegraph/bin/codegraph` ← 本机实际使用的来源
4. `PATH` 中的 `codegraph`

本目录的内容即 **provisioned 安装的完整 npm 包**（`~/.omo/codegraph/lib/`，
`@colbymchenry/codegraph` **v1.0.1**，MIT License），外加一个启动脚本。

## 目录结构

```
mcp/codegraph/
├── bin/codegraph                    # 启动脚本：首次运行自动解压 tarball → $DIR/node 缓存；缺失则回退系统 node；--liftoff-only lib/dist/bin/codegraph.js serve --mcp
├── node-v24.16.0-linux-x64.tar.xz   # 官方 Node.js v24.16.0 压缩包（30MB，入库）；首次运行解压出二进制缓存到 node/（gitignored）
├── node                             # 解压缓存（非入库，见 .gitignore）；首次运行由 tarball 生成
└── lib/                             # @colbymchenry/codegraph v1.0.1 npm 包（dist + node_modules + package.json）
```

## 环境要求（自包含，零外部依赖）

- **无需系统安装 Node**：仓库内以官方 tarball（v24.16.0，满足 engines `>=20 <25`）
  形式自带 Node.js。首次运行 `bin/codegraph` 会自动将其解压到 `node/`（gitignored
  缓存，绝对路径 exec，不查 PATH、不污染系统环境，与系统 node 共存无冲突），
  之后直接复用缓存；若 tarball 缺失（如浅克隆时精简了文件），自动回退到 PATH 上的
  系统 `node`。
- 解压需系统具备 `tar` 与 `xz`（Ubuntu 24.04 默认自带），仅首次运行耗时约 1-2 秒。
- `lib/node_modules` 依赖全部内嵌，无需联网安装。

### （可选）apt 托管安装 Node 24（供回退路径使用）

Ubuntu 24.04 官方仓库默认 `nodejs` 为 **18.x**（不满足 engines `>=20 <25`），
需用 NodeSource 的 24.x apt 源安装。装好后 `node` 由 apt 的 `nodejs` 包托管，
位于 `/usr/bin/node`，系统 PATH 必有——Helix 无论以何种方式（终端 / cron /
systemd）拉起都能解析到，且可随 apt 正常升级：

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs
node --version   # 期望输出 v24.x
```

验证确为 apt 托管：

```bash
dpkg -S /usr/bin/node   # 应输出: nodejs: /usr/bin/node
```

## Helix 接入

在 `Helix.json` 的 `mcp_servers` 增加（`type: "local"`，stdio 直连，相对路径以
Helix 工作目录为准）：

```json
"codegraph": {
  "type": "local",
  "enabled": true,
  "command": "mcp/codegraph/bin/codegraph",
  "args": ["serve", "--mcp"]
}
```

启动后 CodeGraph 会在项目根目录生成 `.codegraph/`（已加入 `.gitignore`），
对外暴露 `codegraph_explore` / `codegraph_search` / `codegraph_node` /
`codegraph_callers` 等代码智能工具。

## 验证

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"0.1"}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' \
  | bin/codegraph serve --mcp
```

## 升级

替换 `lib/` 为新版本 npm 包内容即可（`npm pack @colbymchenry/codegraph` 后解压，
或从 `~/.omo/codegraph/lib` 重新同步）。