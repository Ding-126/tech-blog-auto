+++
date = '2026-06-18T18:37:52+08:00'
draft = false
title = 'Anthropic 限制中国使用 Claude：别骂了，这3件事现在就得动手'
description = 'Anthropic 正式限制中国大陆 IP 访问 Claude，对用 AI 写代码、做数据分析的开发者影响不小。本文不说情绪，只说接下来三个月你该做什么。'
tags = ['AI编码', 'Claude', 'Anthropic', '出口管制', '开发者工具', '替代方案']
categories = ['hot-take']
source_url = 'https://juejin.cn/post/7649977961550987314'
source_name = '掘金'
+++

## 开篇：Claude 走了，但你的工作流不该停

Anthropic 正式收紧了中国大陆的访问限制，Claude API 和 Web 端都开始拦截来自大陆 IP 的请求。如果你平时用 Claude Code 写业务代码、用 Claude API 做数据处理、或者靠它跑代码审查，这直接影响到你的日常开发流。但比起骂 Anthropic "双标"，不如花 3 分钟想清楚：你的技术栈里依赖 Claude 的部分，该怎么平滑替换。

## 三句话看懂核心变化

- Anthropic 在服务端增加了 IP 地理封锁，大陆直连 Claude API 返回 403
- 已付费的企业账户同样受影响，不存在 "老用户豁免"
- 这不是临时抽查，是配合美国 AI 出口管制框架的长期策略调整

## 核心观点：对普通开发者的 3 个影响

### 影响 1：你的 AI 编码工具链需要一次 "去 Claude 化" 审计

过去一年，很多 Java 后端和数据分析同学的习惯是 "写 SQL 问 GPT，写代码问 Claude"。Claude 在代码生成质量上确实领先半个身位，尤其是 Java/C++ 这种强类型语言的上下文理解。但现在依赖 Claude 的环节越多，你的切换成本越高。

建议现在就把工具链过一遍：

- **IDE 插件**：Cursor / Codex 是否绑定了 Claude 模型？看看能不能切到 DeepSeek 或通义千问
- **CLI 工具**：Claude Code 用的哪个 API endpoint？如果是直接调 Anthropic，需要换中转服务或者切到其他方案
- **CI/CD 集成**：有没有用 Claude API 做自动化 Code Review？如果是，准备个备用方案

不需要全部换掉，但至少要有一路 "B计划" 能随时顶上。

### 影响 2：模型切换的成本比想象中低，但坑也不少

如果你之前只用 Claude，现在切到 DeepSeek V4 Pro 或通义千问 Max，差别没有你想象的那么大。对于 Java 代码生成、SQL 编写、日志分析这类常见场景，国产模型已经能做到 Claude Sonnet 4 的 90% 水平。

但有几个坑需要提前踩：

- **长上下文场景**：Claude 100K token 的上下文窗口仍然领先，国产模型在 32K+ 时质量下降明显。如果你的 prompt 习惯写一大段上下文，需要裁剪
- **工具调用 (Function Calling)**：Claude 的 tool use 格式跟 OpenAI 不兼容，如果你自己写了 tool calling 的封装层，迁移时要改 schema
- **代码审查质量**：DeepSeek 在找 NullPointerException 这种显式 bug 上没问题，但在 "这段代码有没有并发安全问题" 这种隐式判断上，跟 Claude 还有差距

### 影响 3：AI 工具的 "供应链安全" 成了新课题

这件事的核心不是 "Anthropic 坏不坏"，而是一个更大的趋势：**AI 工具正在变成基础设施，而基础设施有地缘政治属性**。

去年是 GPU 禁运，今年是模型服务封锁，明年呢？如果你所在的公司正在用 AI 做核心业务逻辑（比如智能客服、自动化数据处理、代码生成流水线），你应该推动团队做两件事：

1. **模型层抽象**：不要直接调某个模型的 API，中间加一层 adapter，换模型只改配置不改代码
2. **离线兜底能力**：至少有一个能用本地模型跑通的核心场景，万一断网也能干活

这不是过度设计——去年 OpenAI 封号潮和今年 Anthropic 限制 IP，本质是同一件事。

## 明天上班能做什么？

1. **检查你的所有 API Key 和 endpoint 配置**，确认哪些流依赖 Anthropic 直连，列个清单
2. **注册 DeepSeek / 通义千问 / GLM 的 API 账号**，拿到 Key 存到环境变量里，今天先跑通一个 "Hello World" 级别的调用
3. **在你的代码生成 prompt 里加一段兼容性测试**：把同一个需求发给两个模型，对比输出质量，确认 "B计划" 可用
4. **如果你的项目用 Claude Code CLI**，看看能不能通过 `--model` 参数切换到其他后端，不行的话装个 Codex 作为备份
5. **跟团队同步一次**：至少让组里知道 "Claude 可能随时不可用"，别等人被卡住了才开会

## 延伸阅读

- [Anthropic 为何限制中国大陆使用 Claude？](https://juejin.cn/post/7649977961550987314)（原始讨论）
- [小米开源 Claude Code 变体：3 个信号开发者必须关注](/posts/xiaomi-claude-code-opensource/) — 如果你倾向于继续用 Claude 生态，小米的定制版值得看看
- [Spring Boot 测试实战：Unit Test + Integration Test + Testcontainers](/posts/spring-boot-testcontainers-practice/) — 不用 AI 也能把质量做好

你对这件事怎么看？欢迎在评论区说说你的想法。
