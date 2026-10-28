<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.png">
    <img src="docs/assets/logo.png" alt="Atlas" width="160">
  </picture>
</p>

<h1 align="center">Atlas</h1>

<p align="center">
  <strong>你的个人智能体，也是你所有编程智能体的统一应用：<br>
  Claude Code、Codex、Gemini CLI 和 DeepSeek，覆盖每个账号、每台机器。</strong>
</p>

<p align="center">
  <a href="https://github.com/marqueskaio/atlas/actions/workflows/ci.yml"><img src="https://github.com/marqueskaio/atlas/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT 许可证"></a>
  <img src="https://img.shields.io/badge/python-3.13-3776AB.svg?logo=python&logoColor=white" alt="Python 3.13">
  <img src="https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-555.svg" alt="Windows, macOS, Linux">
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <b>中文</b>
</p>

---

Atlas 是一个开源的个人智能体，带有桌面应用。填入一个 API 密钥，你就拥有一个助手：它保存一份你看得懂的记忆，
在了解你的工作方式后自己编写技能（skills），按计划运行例行任务，还能通过 Telegram 和你对话。

它也是你的编程智能体的家。用个人账号运行 Claude Code，用公司账号运行 Codex，并排工作。
当其中一个达到套餐上限时，会话会切换到下一个账号，或者另一个 harness，接着往下做，不丢上下文。
在同一个窗口里打开你家庭实验室、VPS 或办公室电脑上的 Atlas。它所知道的关于你的一切都保存在一个属于你的文件夹中。

## 所有编程智能体，所有账号，一个应用

Claude Code、Codex、Gemini CLI 和 DeepSeek 的 harness 都是**执行器**：Atlas 运行你自己安装的那一份，
使用你的设置、项目的说明和你自己的登录，并列出它们的所有会话，无论会话是在哪里开始的。

- **多个账号同时运行。** 个人、公司 A、公司 B：每个账号都有自己的文件夹，拥有独立的设置、MCP 服务器、
  插件和历史记录，工作永远不会混在一起。它们可以同时运行，每个项目文件夹都从你为它选定的账号开始。
- **对话中途切换账号。** 一个账号额度用完了？会话带着它已有的一切，在另一个账号上继续。
- **到达上限，继续前进。** 当一个账号达到套餐上限时，下一个已登录的账号会按你设定的顺序接手。默认开启。
- **每个会话一条路线。** 选择一个会话可以使用哪些账号和 harness，以及顺序，可以跨 harness 混用：

  ```
  Claude Code · 个人  →  Claude Code · 公司  →  Codex · 主账号
  ```

  在同一个 harness 内，会话本身切换账号。跨 harness 时，下一个会以一份交接包开始：任务、最新的指令、
  尚未完成的步骤、改动过的文件以及到目前为止发生的事，整个过程不调用任何模型。
- **查看每个套餐的用量。** 五小时窗口、每周额度、每个模型的配额，覆盖所有账号，
  和 harness 自己的 `/usage` 显示的一致，读取时不花一个 token。
- **接着任何会话继续。** 你在终端或编辑器里开始的会话也会出现在列表中。如果它还在那边打开着，
  Atlas 会提醒你，并提议在一份副本中继续。
- **什么都不会丢。** 执行器的权限请求或问题会一直等你，没有期限，在应用里或通过 Telegram 在手机上。
  也可以让 Atlas 来回答：它的安全审查负责权限，对话的模型负责问题，它拿不准的仍会交给你。
- **Atlas 也会把工作交给它们。** 用自然语言提出请求，Atlas 会委派给合适的执行器，用你指定的账号，
  配上它自己的路线（先 `claude-code@company`，再 `codex`），全程跟进，并把结果带回来。

## 所有机器，一个应用

把 Atlas 装在办公室电脑、家庭实验室或公司的 VPS 上，然后在笔记本上的应用里打开它。
那边的 Atlas 负责干活，使用那台机器的文件、密钥和审批；应用只是一个窗口。

- **Tailscale 或 SSH。** 通过 Tailscale 粘贴一个连接码，无需开放端口，也无需配置证书；
  或者用你已有的密钥，通过 SSH 隧道连接服务器。
- **始终在线。** 一个空间（home）可以作为随电脑启动的服务运行，这样关掉应用后例行任务照常运行，
  每个窗口看到的都是同样的对话。
- **每台机器一个终端**，在侧边栏里，无论应用在哪里。
- **独立的空间**让同一台机器上的工作与生活互不干扰，各自拥有自己的记忆、密钥和对话。

## 为什么选择 Atlas

- **框架保持轻薄，工作交给模型。** 模型每进步一次，Atlas 就跟着变好。始终开启的工具只有少数几个，
  其余的通过搜索找到，因此每次调用模型都小而专注。
- **记忆就是纯文本。** 你的个人资料和智能体的笔记都是可以打开、编辑和做版本管理的文件。
  没有隐藏的向量数据库替你决定你说过什么。
- **从设计上保证安全。** 每条命令都要经过审批关卡。有些操作在任何模式下都不会执行。
  凡是代表你离开本机的操作，比如合并 pull request 或发送邮件，都会先征求你的同意。
- **完全属于你。** 无需账号，没有遥测，没有我们的云。使用你喜欢的模型提供商，
  以及你自己的编程智能体的登录，Atlas 从不碰它们。

## 越用越好

每隔几条消息，智能体的一个副本会在后台重读对话，把其中学到的东西保存下来，让你永远不必把同一件事纠正两次：

- **你是谁**写进你的个人资料，**关于你环境的事实**（工具的怪癖、路径、约定）写进智能体的笔记。
- **某类任务怎么做**会变成技能。当你纠正它的风格或工作方式、出现了新的修复或变通办法、
  或者某个技能被证明有误时，它会修改覆盖这类任务的技能，只有在没有合适技能时才新建一个。
- **它会忽略噪音：** 缺失的程序、一次性的任务、自行消失的错误。

它不会在暗中搞坏任何东西。新的事实立即生效，但任何会替换或删除记忆的改动都会作为提议，等你批准。
它从不删除技能，每次修改都会保留上一个版本（`atlas skills revert` 可以恢复），
它自己创建、30 天未使用的技能会被归档，而你的技能永远不会。

## 功能

| | |
|---|---|
| 🧠 **看得懂的记忆** | 一份关于你的资料和智能体自己的笔记，以文本形式保存在固定的容量内。你可以打开、编辑，或直接问 Atlas 它知道什么。 |
| 🛠️ **自己编写技能** | Atlas 学会一件事怎么做时，会保存为技能，支持版本和撤销。内置 GitHub、Gmail 和 Google 日历的技能。 |
| 🤝 **执行器与子智能体** | Claude Code、Codex、Gemini CLI 和 DeepSeek 运行在你所有的账号上，支持路线和自动切换账号。子智能体可以并行拆分大型任务。 |
| ⏰ **例行任务** | 定时运行的提示词和命令，可以用自然语言（"every weekday at 9"）或 cron 表达式编写。 |
| 🤖 **机器人** | 与主智能体并存的具名智能体，各自拥有使命、记忆、技能和例行任务，彼此之间可以通信。 |
| 📱 **Telegram** | 用手机和 Atlas 对话，也可以在手机上回复你的执行器。只有你配对过的人才能进入。 |
| 🎙️ **语音** | 发送语音，Atlas 能读懂；在输入框里口述；需要时用语音消息回复；或者实时语音对话。官方插件，使用你的 OpenAI 密钥。 |
| 🌐 **专属浏览器** | Atlas 驱动自己的 Chrome，你可以随时接管。 |
| 🖥️ **终端与预览** | 侧边栏里有一个运行在 Atlas 所在机器上的终端，还能预览文件、本地页面和智能体构建的内容。 |
| 🔐 **审批关卡与保险库** | 危险命令需要确认，最危险的永远不会执行，被标记的命令由一个审查模型阅读，自动模式让你把方向盘交出去。API 密钥保存在加密的保险库中。 |
| 🧩 **插件** | 可以添加技能、命令、智能体、钩子、监视器、MCP 服务器、工具、通道和执行器。钩子使用 Claude Code 的事件和 JSON 格式。 |
| 🌍 **多台机器，多个空间** | 一个应用即可通过 Tailscale 或 SSH 连接你其他电脑上的 Atlas。独立的空间（home）让工作与生活互不干扰。 |
| 📦 **备份与用量** | 一个包含你数据的 zip（从不包含保险库），恢复前会先完整校验。按天、模型和用途统计花费。 |

## 安装

**Windows**，在 PowerShell 中：

```powershell
irm https://atlasv4.com/install.ps1 | iex
```

**macOS 和 Linux**，在终端中：

```bash
curl -fsSL https://atlasv4.com/install.sh | bash
```

安装程序会带来 Atlas 所需的一切（独立的 Python、Node 和工具，并按固定的哈希值校验），
编译桌面应用并打开它。应用会要求输入一个模型提供商的 API 密钥，然后你就可以和 Atlas 对话了。
在设置的“插件”中开启 Claude Code、Codex、Gemini CLI 或 DeepSeek 的 harness，你已安装的每一个都会出现在“执行器”标签页中。
再次运行同一行命令即可更新；`atlas update` 也可以更新，任何一步失败都会回到原来的状态。

**在容器中：** [docker-compose.yml](docker-compose.yml) 开头列出了三条命令：启动、保存 API 密钥，
以及获取桌面应用所需的令牌。

### 模型提供商

使用 API 密钥接入 OpenAI、Anthropic、Google Gemini 和 OpenRouter，也可以通过 Ollama 或 vLLM
使用本地模型。每个对话都可以切换模型。

## 工作原理

```mermaid
flowchart LR
    App[桌面应用] <-->|WebSocket| Server
    Laptop[另一台机器上的应用] <-->|Tailscale 或 SSH| Server
    Telegram <--> Server
    subgraph Server[atlas serve]
        Loop[智能体循环] --> Gate[审批关卡]
        Loop --> Tools[工具与工具搜索]
        Loop --> Memory[(记忆与技能)]
        Loop --> Executors[Claude Code · Codex · Gemini CLI · DeepSeek<br>覆盖所有账号]
    end
    Server --> Home[(ATLAS_HOME：你的数据，位于代码仓库之外)]
```

Atlas 有自己的智能体循环，底层不依赖任何智能体框架：它直接调用各个提供商的官方 SDK。
服务器是唯一的事实来源，桌面应用、Telegram 和其他机器都是它的客户端。
关于你的一切（资料、记忆、对话、设置、保险库）都保存在 `ATLAS_HOME` 中，从不写进代码。
你的执行器账号保存在它们各自的文件夹里，由 harness 自己完成登录。

## 文档

文档为英文。文档位于 [src/atlas/docs/pages/](src/atlas/docs/pages/)，每个已安装的 Atlas 都可以用 `atlas docs` 阅读与自身版本对应的
文档；Atlas 在回答关于自身的问题之前，也会先在那里查阅。

- [Atlas 是什么](src/atlas/docs/pages/index.md)以及[如何安装](src/atlas/docs/pages/install.md)
- [编程智能体与账号](src/atlas/docs/pages/guides/coding-agents.md)以及[多台机器](src/atlas/docs/pages/guides/machines.md)
- [记忆](src/atlas/docs/pages/guides/memory.md)：Atlas 记住什么、如何记住，以及如何修改
- [后台复盘](src/atlas/docs/pages/guides/background-review.md)：Atlas 如何从对话中学习
- [机器人](src/atlas/docs/pages/guides/bots.md)：创建机器人并与之对话
- [审批关卡](src/atlas/docs/pages/guides/approval-gate.md)：哪些操作需要确认、哪些永远不会执行，以及自动模式
- [命令](src/atlas/docs/pages/reference/commands.md)、[设置](src/atlas/docs/pages/reference/settings.md)和[工具](src/atlas/docs/pages/reference/tools.md)：参考资料，前两项直接由代码生成
- [插件](src/atlas/docs/pages/extending/plugins.md)：编写插件

面向参与代码开发的人：

- [架构](docs/ARCHITECTURE.md)：代码遵循的决策与规则
- [术语表](docs/GLOSSARY.md)：Atlas 使用的词汇

## 当前状态

Atlas 还很年轻，进展很快。它每天都在被使用，每次改动都会运行一整套测试：单元测试、真实桌面应用的端到端测试、
Windows 和 Linux 上的安装程序测试，以及容器镜像测试。版本之间可能会有粗糙之处和变化。
路线图见 [ROADMAP.md](ROADMAP.md)（葡萄牙语）。

## 参与贡献

欢迎提交 issue 和 pull request。请阅读 [AGENTS.md](AGENTS.md) 了解如何参与开发，
并在写代码之前阅读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：CI 会强制执行其中的规则。

```bash
uv sync                 # 安装（uv 负责管理 Python 3.13）
uv run pytest           # 运行测试
uv run atlas serve      # 启动服务器，地址 127.0.0.1:18790
cd apps/desktop && npm ci && npm run dev   # 从源码运行应用
```

## 许可证

[MIT](LICENSE)。少数文件包含来自其他 MIT 项目的规则和测试用例；每个文件都在自己的文件头中注明，
许可证全文见 [LICENSES/](LICENSES/)。
