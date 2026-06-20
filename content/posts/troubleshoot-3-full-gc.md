---
title: "线上问题排查系列三：一次 Full GC 引发的\"血案\"——GC 日志分析实战"
slug: "troubleshoot-3-full-gc"
date: 2026-06-20
draft: false
categories: ["tutorial"]
tags: ["Full GC", "GC 日志", "G1"]
keywords: ["Full GC", "GC 日志", "G1"]
difficulty: "实战"
target_length: 2500
series_name: "线上问题排查"
series_number: 3
series_total: 5
---

上周五下午，我正在摸鱼写周报，群里突然炸了——"订单超时了！""用户进不去页面了！" 我心想完蛋，又得加班了。

上机器一看，好家伙，CPU 直接飙到 300%，所有接口响应时间都在 5 秒以上。第一反应是看堆内存，`jstat -gcutil` 打出来一看：Full GC 次数在疯狂增长，几乎每秒一次。

如果你遇到过类似的情况，这篇文章应该能帮你省下至少一下午的排查时间。

<!--more-->

## 拿到 GC 日志是第一件事

很多同学遇到 Full GC 第一反应是"加内存"或者"调大堆"。先别急，加内存只是延缓症状，真正要治本你得先看 GC 日志。

线上 JVM 启动参数里，把这几行加上：

```
-XX:+PrintGCDetails -XX:+PrintGCDateStamps -XX:+PrintGCTimeStamps
-Xloggc:/var/log/gc/gc-%t.log -XX:+UseGCLogFileRotation
-XX:NumberOfGCLogFiles=10 -XX:GCLogFileSize=100M
```

我们线上用的 G1 垃圾回收器，所以还加了 `-XX:+PrintAdaptiveSizePolicy` 来观察 G1 的区域分配情况。

拿到日志之后，别傻看，先 grep 一下 Full GC 出现的频率：

```bash
grep "Full GC" gc-*.log | wc -l
```

我那次看到的结果是：**半小时内 1800+ 次 Full GC**，平均每秒一次。这不是垃圾回收，这是在自杀。

## 第一眼看停顿时间

GC 日志里最核心的两个指标：**频率**和**停顿时间**。

```
2026-06-17T14:23:15.123+0800: 4321.456: [Full GC (Allocation Failure)  8G->6G(16G), 12.345 secs]
```

上面这一行信息量很大：

- **Allocation Failure** — 分配失败触发的 Full GC，说明堆里确实没空间了
- **8G->6G** — 回收了 2G，但还剩 6G
- **12.345 secs** — STW 停了 12 秒

12 秒的停顿，意味着这段时间内应用完全不可用。如果是支付接口，这就直接超时了。我当时盯着这个数字整个人都不好了——因为我们的接口超时配的是 3 秒。

**个人经验**：看 GC 停顿时间不要只看平均，要看 P99。G1 的目标停顿是 200ms，如果你的 Full GC 动不动就 5 秒以上，说明问题已经非常严重了，不是调个参数能解决的。

## G1 日志里藏着真凶

G1 的 Full GC 日志和 CMS/Parallel 不太一样。G1 的 Full GC 是单线程串行回收，效率很低。如果你看到 G1 频繁 Full GC，通常不是堆太小，而是**产生了大量无法回收的大对象**。

看这一行：

```
2026-06-17T14:23:27.456+0800: 4333.789: [Full GC (Metadata GC Threshold) ...]
```

注意括号里是 `Metadata GC Threshold` 而不是 `Allocation Failure`。这说明触发 Full GC 的原因是**元空间（Metaspace）不够了**。

我当时排查的流程：

1. `jstat -gcmetacapacity <pid>` 看元空间使用情况
2. 发现 Metaspace 一直在涨，从 256M 涨到了 1.2G 还没停
3. 用 `jmap -clstats <pid>` 看类加载器信息
4. 发现有大量的 GroovyClassLoader 实例

破案了——业务方用 Groovy 做动态规则引擎，每次新规则都编译一个新类，但 GroovyClassLoader 没有正确的释放，导致 Metaspace 持续增长。

## 工具比你想的有用

别只会用 `jstat`，这几个组合拳很实用：

| 工具 | 用途 |
|------|------|
| `jstat -gcutil` | 快速查看 GC 各代使用率和次数 |
| `jstat -gccause` | 查看最近一次 GC 的原因 |
| `jmap -dump:live,format=b,file=heap.hprof` | 堆转储（谨慎使用，会 STW） |
| `jcmd <pid> GC.heap_info` | G1 各区域分布 |

**注意**：线上千万别随便 `jmap -dump` 不加 `:live`，不加的话全堆都 dump，文件巨大，还会卡死应用。我吃过这个亏，dump 了一个 32G 的堆文件，传输花了一小时，最后本地 MAT 直接 OOM 了。

如果 G1 频繁 Full GC 是因为 Metaspace，用 `jcmd <pid> GC.class_stats` 看哪些类加载最多，比 `jmap -clstats` 更精细。

## 我怎么解决的那次问题

找到了问题是 GroovyClassLoader 泄漏，解决方法反而很简单：

1. **限制 Metaspace 大小**：`-XX:MaxMetaspaceSize=256m`，让问题提前暴露而不是等到 1.2G 才出问题
2. **缓存 GroovyClassLoader 实例**：按规则 md5 做 key，相同规则复用同一个 loader
3. **添加定时清理**：每天凌晨低峰期卸载过期的 GroovyShell 实例

改完之后，Full GC 从每秒一次降到了**每 3 小时一次**，P99 响应时间从 8 秒降到 120ms。

**另一个坑**：我还发现业务方在循环里创建 `ScriptEngine` 做规则校验，每次 new 一个。这种代码不管 GC 怎么优化都没用，只能改代码。所以遇到频繁 Full GC，先查代码再说，别上来就调 JVM 参数。

## 总结

Full GC 排查其实就三步：

1. **打开 GC 日志**（没有日志就是盲人摸象）
2. **看频率和停顿时间**（判断严重程度）
3. **找到根因**（大多数时候是代码问题，不是 JVM 参数问题）

G1 的 Full GC 是最后的保底机制，它的出现说明正常的 Young GC 和 Concurrent Marking 已经解决不了了。遇到它别慌，一步步排查就好。

📌 本文是「线上问题排查」系列第 3 篇，共 5 篇。下一篇将分享 CPU 高负载的排查思路和常用工具。

