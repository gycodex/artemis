# ARTEMIS · 以 Direct 为核心的 Android 测试

**让你正在使用的 AI 助手操作 Android 设备、验证实际结果并留下测试报告，无需再为 Artemis 配置一份模型 API Key。**

[English](README.md) · [快速上手](#quick-start) · [Direct 测试流程](#direct-workflow) · [回归用例](#regression-cases) · [常见问题](#troubleshooting)

本仓库基于 [Google ARTEMIS](https://github.com/google/artemis)，重点增强 **Direct / Host-Driven（宿主驱动）模式**。通过 MCP 连接 Android 真机或模拟器，由当前 AI 助手查看屏幕、执行操作，并将截图、检查点、日志和可选录像保存在同一测试会话中。

本文推荐优先使用 Direct，但 CLI 默认仍是 Agent 模式：**配置 Direct MCP 时，务必显式指定 `--type direct`。**

## 为什么优先选择 Direct？

- **一个模型负责思考。** 当前 MCP 宿主理解界面、决定下一步；Artemis 负责执行设备工具，不再启动第二个模型。
- **操作之后验证。** 测试会话默认自动返回并保存操作后的观察结果，不能把“点击成功”当作“业务成功”。
- **报告有证据支撑。** 显式记录检查点，区分失败与未覆盖，输出 Markdown/JSON 报告。
- **减少坐标维护。** 根据实时 UI 层级，按文本、资源 ID、内容描述或包名定位；仅在没有匹配元素时使用显式坐标兜底。
- **探索结果可复用。** 将已经验证的交互整理为 JSON 回归用例，支持有时限的等待、失败即停、失败用例重跑和 JUnit 输出。
- **可选连续录像。** 将动作、截图、检查点和视频分段串联到离线时间线，方便回看问题发生的上下文。

“无需额外 API Key”指 Direct 核心设备工具不需要 Artemis 模型凭据。宿主自身的订阅、模型用量和数据政策仍然适用。Direct 不会提取或复用宿主的登录令牌。

## Direct 与 Agent 的区别

| | Direct / 宿主驱动 | Agent / Flash 与 Pro |
| --- | --- | --- |
| 谁决定下一步 | MCP 宿主；Web Direct 中的人工操作；JSON 用例中的明确步骤 | Artemis 配置的模型 |
| 是否需要单独配置 Artemis 模型 | 核心 Direct 工具不需要 | 需要云端凭据或兼容的本地模型端点 |
| 典型工具 | `observe_screen`、`click_element`、`session_checkpoint`、`run_case` | `mobile_run_task`、`mobile_manage_task`、`mobile_inspect_trace` |
| 适用场景 | 交互式测试、问题复现、证据采集、确定性回归 | 委托式自主探索、长时间任务 |
| 测试报告 | 宿主判定检查点，Artemis 归档证据 | 按执行配置提供规划、验证和报告 |

Direct 不是换了名称的 Flash/Pro 自主任务。交互式测试需要宿主保持连接；固定步骤的回归任务可以独立使用用例 CLI。

<a id="quick-start"></a>
## 快速上手：把设备接到 AI 助手

### 1. 准备环境

必需：Git、Python **3.12+**、`uv`、Android Platform Tools（`adb`），以及已授权的 Android 真机或运行中的模拟器。

按需准备：

- Direct 录像：`scrcpy` 和 FFmpeg，需加入 `PATH`。
- 从源码构建 Web 控制台：Node.js/npm。仅使用 MCP 不需要启动 Web 控制台。
- 让 AI 根据截图交互：支持图像的 MCP 宿主。

下面使用 PowerShell，并克隆包含本文 Direct 扩展的 fork：

```powershell
git clone https://github.com/gycodex/artemis.git
Set-Location ".\artemis"
uv sync
adb devices -l
```

USB 真机需开启 USB 调试，并在设备上接受授权。设备状态应为 `device`，而不是 `offline` 或 `unauthorized`。

MuMu 等 TCP 模拟器需连接到实际 ADB 地址：

```powershell
adb connect 127.0.0.1:7555
adb devices -l
```

`127.0.0.1:7555` 只是示例，并非所有 MuMu 实例都使用此端口。以当前环境的设备序列号为准；连接多台设备时，明确选择测试目标。

### 2. 安装 Direct MCP

根据宿主选择**其中一条**命令，并替换设备序列号：

```powershell
# Codex
uv run artemis mcp --type direct --device "127.0.0.1:7555" --install codex

# Claude Code / Claude Desktop
uv run artemis mcp --type direct --device "127.0.0.1:7555" --install claude
```

安装器会更新宿主的 MCP 配置，**同时安装 Artemis 测试规则**。也支持 `cursor`、`windsurf`、`vscode`、`antigravity` 等目标。只查看配置、不安装：

```powershell
uv run artemis mcp --type direct --device "127.0.0.1:7555" --generate-config codex
```

**当前 Codex 安装器注意事项：**生成的 `enabled_tools` 白名单仍只包含原有 Direct 工具，尚未包含下面六个新增工具。重新加载前，请在生成的 Artemis 服务配置中，将这些名称补充到原有列表：

```text
recording_start
recording_stop
click_element
wait_for_element
wait_for_app
run_case
```

保留列表中的原有项。再次运行安装器可能重新生成旧列表，安装后需要复核。此限制影响宿主显示的工具，不影响服务端或用例 CLI 中的功能。

安装完成后，重新加载 MCP 服务或重启宿主。Direct 每个服务进程绑定一台设备；修改 `--device` 或 `ARTEMIS_DEVICE_ID` 后，需要重启该服务。

<a id="mcp-setup"></a>
<a id="mcp"></a>
自定义 MCP 客户端可使用以下 stdio 入口：

```powershell
uv run python -m artemis mcp --type direct --device "127.0.0.1:7555"
```

这条命令会启动持续运行的 MCP 服务，不是交互式聊天窗口，也不会打开 Web 页面。通常应由 MCP 宿主启动。

### 3. 先观察，再操作

可以先向助手发送：

> 使用 Artemis Direct 列出设备，确认当前绑定的是 `127.0.0.1:7555`，然后观察当前屏幕。暂时不要点击、输入或修改应用数据。

出现 `observe_screen`、`session_start` 和设备动作工具，说明是 Direct 模式；如果只有 `mobile_run_task` 等工具，则是 Agent 模式。

MCP 握手不要求设备在线，但首次调用设备工具时，仍需要可用的 ADB 连接。

<a id="direct-workflow"></a>
## 从界面探索到测试报告

1. **开始会话：**调用 `session_start(name, package_name, auto_observe=true)`。
2. **观察界面：**调用 `observe_screen`，查看当前截图和可用的 UI 信息。
3. **操作并验证：**调用 `manage_app`、语义定位或设备动作工具，检查操作后的观察结果。
4. **记录断言：**由宿主判断结果，再调用 `session_checkpoint` 记录检查点。
5. **采集诊断：**使用 `read_logcat`；需要录像时，在会话内开启并停止录制。
6. **结束会话：**调用 `session_finish`，生成报告并返回产物路径。

常用工具：

| 目的 | 工具与行为 |
| --- | --- |
| 查看设备和界面 | `list_devices`、`get_device_info`、`observe_screen`、`get_ui_hierarchy`、`take_screenshot` |
| 操作应用 | `manage_app`、`click`、`click_sequence`、`long_press`、`swipe`、`input_text`、`press_key`、`open_link` |
| 稳定定位 | `click_element`：条件精确匹配且取交集；匹配歧义或层级不可用时返回错误 |
| 等待状态 | `wait_for_element`：出现、消失、启用或稳定；`wait_for_app`：等待指定包进入前台 |
| 管理测试会话 | `session_start`、`session_checkpoint`、`session_status`、`session_finish` |
| 诊断与录像 | `read_logcat`、`recording_start`、`recording_stop` |
| 执行回归 | `run_case`：校验 JSON 步骤、执行断言、生成报告与 JUnit |

坐标工具使用 **0–1000 归一化坐标**，不是屏幕物理像素。优先使用已验证的语义定位条件。“元素稳定”表示节点属性稳定，不等于像素画面完全静止。

### 示例：MuMu 上的 RYB

以下是建议的测试提示词，**不表示当前 App 版本已经通过验证**：

> 在 MuMu `127.0.0.1:7555` 上启动已安装的 `com.ronghui.ryb`，开启 Artemis Direct 测试会话。检查当前能够到达的启动页、隐私授权页和首页，保存截图并分别记录检查点。读取与该包相关的 Logcat，最后生成测试报告。不要清除数据、重装、退出登录、提交隐私同意、登录、发送短信或修改账号数据。如果已经授权或登录，导致某个页面无法到达，应记录为 skipped 或 blocked 并解释原因，不要标为通过。需要额外交互才能到达页面时，先请求授权。

启动应用本身可能触发正常的应用或网络行为。“只读测试”是给操作者的范围约束，**不是 Direct 强制执行的技术沙箱**。

## 检查点、测试结论与产物

`session_checkpoint` 支持 `outcome`：`passed`、`failed`、`blocked`、`skipped`、`not_run`，并兼容旧的 `passed=true/false` 调用。检查分类为 `functional`、`visual`、`performance`；分类用于标记检查内容，不会自动进行性能测量。

请求 `session_finish(status="passed")` 不能覆盖失败事实：

- 有失败检查点时，最终结果为 `failed`，除非明确中止会话。
- 没有检查点、存在未验证项或所需证据采集失败时，原本请求通过的结果会变为 `incomplete`。
- 动作返回 `ok=true` 只代表执行成功，不代表业务需求通过。
- Logcat 是有边界的日志快照。“未匹配到错误”不等于“没有发生错误”。

产物保存在配置的 Artemis traces 目录中，以工具返回的路径为准：

| 产物 | 生成条件 |
| --- | --- |
| `report.md`、`session.json`、`timeline.html` | 会话结束，且相应证据或报告生成成功 |
| `screenshots/` | 会话中的截图采集成功 |
| `logcat.txt` | 结束会话时请求采集 Logcat，且采集成功 |
| `video-NNN/` | 请求录像，需要 scrcpy/FFmpeg |
| `case-results.json`、`junit.xml` | 执行 JSON 回归用例 |
| `suite-results.json` | 用例 CLI 的运行汇总，默认写入当前目录 |

结束的 Direct 会话会进入 Workspace 历史记录。旧版历史状态将 `incomplete` 映射为 `cancelled`，同时保留详细的 Direct 状态。

### 录像与离线时间线

在 `session_start` 后调用 `recording_start`。调用 `recording_stop` 或 `session_finish` 会收尾录像。正常关闭 MCP 时，未完成的会话会中止，并尝试收尾其拥有的录像；强制结束进程不能保证清理完成。

本地打开 `timeline.html`，可以查看事件并定位对应的视频分段。分享时复制整个会话文件夹，以保留相对路径。时间以动作完成、证据采集以及录像首帧估算为基准；它不是精确触摸耗时分析器，也不会自动判定闪屏。

<a id="regression-cases"></a>
## 将已验证的交互固化为回归用例

先在真实应用中确认界面、定位条件和跳转路径，再编写带明确等待与断言的 JSON 用例。`run_case` 会在执行前校验全部步骤，首次失败后停止后续动作，并将剩余断言标记为 `not_run`。

- 每个用例至少包含一个断言。
- 只有 `wait_for_element` 和 `wait_for_app` 可以作为断言步骤。
- 不允许任意 shell/Python 或嵌套会话。
- 将必要的前置步骤写入用例：失败重跑会重新执行整个用例。

参见[用例格式与示例](docs/direct-regression.md#executable-json-cases)。将已验证的用例保存为 `smoke.json` 后，可执行：

```powershell
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555"

# 覆盖用例中定义的参数
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555" --param "label=首页"

# 重跑上次汇总中未通过的用例
uv run python -m artemis.mcp.direct_cases .\smoke.json --device "127.0.0.1:7555" --failed-from .\suite-results.json
```

用例套件格式为 `{"cases": [...]}`，套件内用例名称应唯一。CLI 执行这些预定义步骤时不需要 MCP 宿主或模型。只要有已执行用例未通过，退出码就非零；证据不完整也会在 JUnit 中体现为错误。

## Web Direct：不通过 AI 宿主也能操作

```powershell
uv run python -m artemis ui
```

打开 [localhost:8000](http://localhost:8000)，选择 **Direct**，选定设备后开启会话。面板支持点击截图操作、启动应用、输入/按键/滑动、检查点结果与分类、录像和报告路径。由人决定下一步，Web Direct 本身不需要模型 API Key。

Web UI 与 MCP 服务是不同进程。更新代码或配置后，需要重启对应进程。不要让 Web、MCP 和 CLI 同时控制同一设备。

<a id="troubleshooting"></a>
## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 提示需要模型 API Key | 确认配置中有 `--type direct`。Direct 不提供 Agent 的 `mobile_diagnose`，请运行 `uv run artemis doctor`。仅缺少模型凭据不会阻止核心 Direct 工具。 |
| 找不到新增工具 | 检查上文 Codex 安装器白名单说明、服务实际使用的代码目录及宿主工具过滤配置，然后重新加载 MCP。 |
| 设备不可用 | 运行 `adb devices -l`，检查授权、模拟器状态、实际 TCP 地址和绑定的设备序列号。 |
| 能截图但定位失败 | 检查 UI 层级。Canvas 或未暴露可访问节点的界面可能需要看图和已验证坐标；不能把层级读取失败当作元素消失。 |
| 录像启动失败 | 检查 `scrcpy --version` 和 `ffmpeg -version`，并查看会话中的录像错误。 |
| 报告为 `incomplete` | 检查未验证的检查点和失败的证据采集，不要强行改为通过。 |

<a id="on-device-helper"></a>
### 设备辅助服务与敏感数据

Artemis 可能部署 Accessibility Helper 来读取 UI 层级，并以 UIAutomator2 作为回退方案。这可能在测试设备上安装辅助应用并启用服务。参见[辅助服务管理](artemis/interfaces/cli/commands/helper.py)，或运行 `uv run artemis helper --help` 查看命令。设置 `ARTEMIS_HELPER_AUTO_INSTALL=false` 可关闭自动安装，修改后需重启服务。

截图、UI 文本、动作参数、日志和录像可能包含个人信息或密钥。请使用测试账号，分享前检查产物，并了解 MCP 宿主的数据处理方式。“不需要 Artemis 模型密钥”不等于“证据永远不会离开本机”。

## 需要自主执行时，使用 Agent

**Agent 模式**仍保留 Flash 与 Pro：

```powershell
uv run artemis init
uv run artemis mcp --type agent --install codex
uv run artemis run "打开设置，检查电池页面" --profile flash
```

Agent 需要独立的模型配置。Flash 采用响应式执行循环；Pro 增加规划、验证和更丰富的诊断。同一宿主安装 Agent 会修改其 Artemis MCP 配置项，请明确选择需要的模式。

更多内容见 [MCP 文档](mcp_server/README.md)、[移动测试规则](mcp_server/rules.md)与 [Python 客户端](packages/artemis-client)。

## 项目与许可

- [Direct 回归与证据参考](docs/direct-regression.md)
- [本 fork：问题反馈与贡献](https://github.com/gycodex/artemis)
- [官方上游：Google ARTEMIS](https://github.com/google/artemis)

项目使用 [Apache 2.0](LICENSE) 许可，保留上游归属与声明。第三方源码位于 [`third_party/`](third_party/)，其中 [`third_party/mobile_use/`](third_party/mobile_use/) 包含 [Minitap mobile-use](https://github.com/minitap-ai/mobile-use) 的部分代码，并附有独立的许可与元数据。
