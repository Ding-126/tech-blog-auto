+++
date = '2026-06-14T06:01:50+08:00'
draft = false
title = '别再裸用 Claude Code 了：6 个 MCP + 4 条工作流，让 AI 真正帮你干活'
tags = ['AI工具', 'Claude Code', 'MCP', '效率工具', '开发者工作流']
categories = ['daily']
source_url = 'https://juejin.cn/post/7648152309277933608'
source_name = 'juejin'
+++

掘金上有篇热文戳中了很多人的痛点：**大多数人用 Claude Code，就是开一个终端、打字、让它写代码、复制粘贴——本质上把一个能操作你真实工作环境的 Agent，硬生生用成了更聪明的搜索引擎。** 作者分享了自己常驻的 6 个 MCP 和 4 条提效工作流，核心观点很直接：**差距不在模型，在你给它装了几条腿。**

## 三句话看懂

- MCP（Model Context Protocol）让 AI 能读你的真实世界——数据库、文档、浏览器、历史记忆，而不只是训练数据
- 作者精选 6 个常驻 MCP：filesystem（读写项目文件）、context7（实时拉最新文档）、playwright（自动验证前端）、postgres（直连数据库查数据）、memory（跨会话记忆）、sequential-thinking（复杂问题分步推理）
- 把 MCP 串起来形成 4 条工作流：文档先行开发、带记忆的长任务、改完即验证闭环、数据驱动调试

## 对你意味着什么

先说结论：**如果你现在还在把 Claude Code / Codex 当「高级补全」用，这篇值得细读。**

裸用 Claude Code 有三个天生的瘸腿：它看不见你的项目结构（只能吃你喂的代码）、记不住昨天的约定（每次新会话从零开始）、不会自己验证（嘴上说改好了，跑起来一堆错）。MCP 就是给这三条腿装假肢。

**第一个最容易被忽略但最关键的：filesystem MCP。** 它让 Claude 能按目录读写文件、跨文件检索，而不是你一段段贴代码。你说一句「把 services/ 下所有调用了旧版鉴权的地方找出来，统一换成新中间件」，它会自己遍历、定位、改。坑点：路径一定写绝对路径，Windows 下用正斜杠。

**第二个解决「幻觉 API」的：context7。** Claude 训练数据有截止日期，写 Next.js / Prisma / Tailwind 这类迭代飞快的库时，经常给你两年前的写法。context7 实时拉官方最新文档喂给它，在 prompt 里加一句「按最新文档写」，框架类代码的幻觉 API 几乎绝迹。

**第三个把「嘴上说改好了」变成「真的验证过了」的：playwright。** 它能开浏览器、点击、填表、截图。你说「改完登录页，自己打开 localhost:3000，走一遍登录流程，截图给我」，它会真的跑一遍，失败了还能看着报错继续修。第一次要 `npx playwright install chromium`。

**第四个直连数据库的：postgres MCP。** 让 Claude 连只读库，自己查 schema、跑 SELECT 验证假设。比如「这个接口返回的金额对不上，你查一下 orders 和 order_items 表，定位是哪一步算错了」——它会自己查表结构、跑聚合、定位到具体行。**务必用只读账号**，给 Agent 一个能 DROP TABLE 的连接串是迟早要出事的。

**第五个解决「跨会话失忆」的：memory MCP。** 让 Claude 把关键事实写进持久化知识图谱，下次新窗口自动加载。第一次告诉它「我们项目禁止用 uv，只用 pip+venv」，它记下来；一周后新开会话依然记得。这一条对长期项目的体验提升是断崖式的。

**第六个让它在难题上「慢下来」的：sequential-thinking。** 复杂重构、棘手 bug，让它显式地分步推理、自我修正，而不是一口气给你一个看着对、跑起来错的方案。

## 4 条工作流才是真正杠杆

单个 MCP 是工具，串起来才是杠杆：

1. **文档先行开发**（context7 + filesystem）：让它先查最新文档，再落地到项目。一句话 prompt：「按官方最新文档，给我在 auth/ 下实现 XXX，写完检查项目里有没有同名旧实现需要替换」
2. **带记忆的长任务**（memory + filesystem）：开工时让它把「目标 / 约束 / 已完成 / 待办」写进 memory，每天新开会话先「加载记忆」，相当于给 Agent 配了个跨天的项目笔记本
3. **改完即验证的闭环**（filesystem + playwright）：任何前端改动，固定追加一句「改完自己用 playwright 打开页面走一遍，截图，有报错就继续修到通过」
4. **数据驱动的调试**（postgres + sequential-thinking）：让它先连库查真实数据，再分步推理哪一步逻辑算错，从「猜」变成「查 + 证」

## 一句话收尾

裸用 Claude Code，你用的是一个聪明的聊天框；配好 MCP，你用的是一个能读你真实世界、能自己验证、还记得住的开发搭子。**差距不在模型，在你给它装了几条腿。**

## 延伸阅读

- 原文：[别再裸用 Claude Code 了：我常驻的 6 个 MCP + 4 条提效工作流（亲测）](https://juejin.cn/post/7648152309277933608)
- MCP 官方文档：[modelcontextprotocol.io](https://modelcontextprotocol.io)
- context7 MCP：[@upstash/context7-mcp](https://www.npmjs.com/package/@upstash/context7-mcp)
