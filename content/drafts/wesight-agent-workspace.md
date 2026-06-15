+++
date = '2026-06-12T11:56:00+08:00'
draft = true
title = 'AI Agent 多了桌面乱？WeSight 统一工位来管'
tags = ['AI Agent', '开源工具', 'Electron', '开发者效率', '桌面应用']
categories = ['daily']
source_url = 'https://github.com/freestylefly/wesight'
source_name = 'catalog'
+++

一个叫 WeSight 的开源桌面应用今天在 GitHub 取得了近 600 Star，它能在一个窗口里统一管理 Claude Code、Codex、Hermes Agent 等几乎所有主流 AI Agent 工具。你如果手头已经同时用两三个 Agent 写代码或跑任务，应该体验过窗口切来切去、模型配置各弄一套的烦恼——WeSight 要解决的就是这个。

## 三句话看懂

- 一个桌面入口管理所有主流 AI Agent 工具
- macOS 一键下载即用，新手也能轻松安装
- 实时监控 token 和工具执行，代码完全开源可部署

## 对你意味着什么

多 Agent 工作流正在从一个"极客玩法"变成日常。你可能已经在用 Claude Code 写代码、Codex 做原型、Hermes Agent 跑定时任务——每个工具都有自己的终端窗口、配置文件和模型密钥。这种各自为政的状态，越往后越像开发环境里开了十个浏览器标签页，改配置要一个个切。

WeSight 的思路说得直白点：给所有这些 Agent 一个统一工位。它做了一层桌面入口，你从这一个窗口启动、切换、观察所有 Agent，模型也能统一配置。对于平时需要同时跑多个 Agent 场景的人（比如先让一个写代码框架、另一个写测试、再一个检查安全），这种"少切一次窗口"的体验提升是真实的。

另一个实用点是可视化监控。Token 消耗、TPS、工具调用、文件变更都有了面板，不用再对着黑乎乎的终端日志估算用了多少额度。对用 Token 计费模型的人来说，这能省下一笔"看不见的钱"。

技术栈是 TypeScript + Electron，如果你想改界面或加功能，门槛不高。MIT 协议，本地部署无云依赖，数据隐私也不用担心。

## 想动手的话

1. 去 [GitHub Releases](https://github.com/freestylefly/wesight/releases) 下载 macOS DMG（选 Apple Silicon 或 Intel）
2. 拖到 Applications 文件夹完成安装
3. 启动后一键配置 Agent 和模型，即可开用

## 延伸阅读

- [WeSight GitHub 仓库](https://github.com/freestylefly/wesight)
- [WeSight 官网](https://wesight.ai)