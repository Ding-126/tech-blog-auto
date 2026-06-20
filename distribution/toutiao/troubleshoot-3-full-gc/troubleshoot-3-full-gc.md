# troubleshoot-3-full-gc

又是个周五傍晚，正准备下班，告警群又响了。

> [告警] 订单服务 POD order-svc-5d4f8a 响应延迟 P99 从 20ms 飙升到 5.8s

5.8 秒的 P99，用户早就超时重试了。赶紧看监控——CPU 不高，内存不高，但 GC 活动曲线像心电图骤停一样——Full GC 每 30 秒一次。

这篇文章记录一次 Full GC 导致服务抖动的完整排查过程：从 GC 日志看不懂到逐行分析，找到根因，修复上线。

## 第一步：开 GC 日志

发现 Full GC 频繁的第一件事：确认 GC 日志打开了。如果没开，动态打开：

```bash
# 查看当前 JVM 是否开了 GC 日志
jcmd 12345 VM.command_line | grep -i gc

# 如果没开，动态开启（JDK 11+）
jcmd 12345 VM.log output=gc.log
```

我们当时 JDK 11 默认开了，文件在 `logs/gc.log`。看一眼：

```bash
# 先看最后 50 行
tail -50 logs/gc.log
```

输出大概长这样（简化版）：

```
[2026-06-18T17:32:15.123+0800] GC pause (G1 Evacuation Pause) young 32M->8M(512M) 15ms
[2026-06-18T17:32:45.456+0800] GC pause (G1 Evacuation Pause) young 28M->6M(512M) 12ms
[2026-06-18T17:33:15.789+0800] GC pause (G1 Humongous Allocation) young 18M->12M(512M) 25ms
[2026-06-18T17:33:45.012+0800] GC pause (G1 Evacuation Pause) young 36M->18M(512M) 580ms  ← 变慢了
[2026-06-18T17:34:15.345+0800] GC pause (Initial Mark) 18M->28M(512M) 320ms
[2026-06-18T17:34:16.789+0800] GC pause (G1 Evacuation Pause) young 42M->30M(512M) 1550ms ← 越来越慢
[2026-06-18T17:34:45.123+0800] GC pause (G1 Evacuation Pause) young 58M->42M(512M) 2800ms
[2026-06-18T17:35:15.456+0800] GC pause (G1 Evacuation Pause) young 72M->58M(512M) 4500ms
[2026-06-18T17:35:45.789+0800] GC pause (Full) 512M->180M(512M) 8200ms  ← Full GC 来了
```

看趋势：Young GC 的停顿时间从 15ms → 580ms → 2.8s → 4.5s，最后触发 Full GC。每次 Young GC 后存活对象越来越大（8M→18M→30M→42M→58M），说明有对象一直在逃逸，升到老年代了。

## 第二步：用工具分析 GC 日志

裸眼看 GC 日志太难了，上工具：

**GCeasy（在线，推荐）**：https://gceasy.io — 上传 gc.log，自动出报告
**GCViewer（本地）**：`brew install gcviewer` 然后 `gcviewer gc.log`

我那次用的 GCeasy，上传后给的报告关键信息：

```
总吞吐量: 92%（正常应 > 99%）
Full GC 次数: 12 次 / 小时
Full GC 平均停顿: 6.2s
老年代增长速率: 18MB/次 GC 周期
```

最关键的是第三条——**每次 GC 周期老年代增加 18MB**。这说明有对象从 Young 逃逸到 Old，而且不被回收。结合 Young GC 后存活对象持续上升，结论是：**有对象在 leak**。

## 第三步：定位问题对象

GC 日志告诉你了「有什么问题」，但没告诉「是谁的问题」。得结合堆转储。

加 `-XX:+HeapDumpBeforeFullGC` 参数，下次 Full GC 前会自动 dump：

```bash
# 动态加参数
jcmd 12345 GC.heap_dump /tmp/pre-full-gc.hprof
```

拿到 dump 后用 MAT 打开，看 **支配树（Dominator Tree）**：

```
Class: com.dudu.order.service.OrderCacheService$CacheEntry
Shallow Heap: 1.2MB
Retained Heap: 342MB
```

一个 `CacheEntry` 类保留了 342MB！进去看：

```java
@Component
public class OrderCacheService {
    // 本地缓存
    private Map<String, CacheEntry> cache = new ConcurrentHashMap<>();

    @Scheduled(fixedDelay = 3600000) // 每小时清理一次
    public void cleanup() {
        // 只清理了过期超过 1 天的
        cache.entrySet().removeIf(e -> e.getValue().isExpired(24, TimeUnit.HOURS));
    }
}
```

看到问题了吗？`cleanup()` 每小时执行一次，但只清理过期超过 24 小时的 entry。而系统每小时产生约 5 万条订单缓存，都往这个 Map 里塞。24 小时就是 120 万条，等于 342MB，老年代根本扛不住。

修复方案很简单——缩短过期时间：

```java
@Scheduled(fixedDelay = 600000) // 每 10 分钟清理一次
public void cleanup() {
    // 只保留最近 30 分钟的缓存
    cache.entrySet().removeIf(e -> e.getValue().isExpired(30, TimeUnit.MINUTES));
}
```

## 第四步：上线验证

修完上线，观察 GC 日志：

```bash
# 用 jstat 实时看
jstat -gcutil 12345 2000 10
 S0  S1  E   O   M  YGC  YGCT  FGC  FGCT
 0.00 0.00 12.3 35.2 87.5 1248 28.5 12  72.3  ← 修复前
 0.00 0.00 8.2  28.5 87.3 1258 12.3 0   0.0   ← 修复后 1 小时
```

看 `FGC` 列：从 12 次降到 0 次。`FGCT`（Full GC 总耗时）从 72.3s 归零。老年代 `O` 从 35% 降到 28% 并稳定。

再跑一遍 GCeasy 报告：吞吐量 99.8%，Full GC 0 次，正常了。

## 用脚本快速分析 GC 日志

排查多了可以写个简单脚本，下次秒出结论：

```bash
#!/bin/bash
# gc-quick-check.sh — 快速分析 GC 日志
LOG=$1
echo "=== GC 日志快速分析 ==="
echo "总行数: $(wc -l < $LOG)"
echo "Full GC 次数: $(grep -c 'Full' $LOG)"
echo "Young GC 次数: $(grep -c 'GC pause' $LOG)"
echo "平均停顿: $(grep 'GC pause' $LOG | awk '{print $NF}' | sed 's/ms//' | awk '{s+=$1;c++} END{print s/c \"ms\"}')"
echo "最大停顿: $(grep 'GC pause' $LOG | awk '{print $NF}' | sed 's/ms//' | sort -n | tail -1)ms"
```

存到服务器上，下次告警来了一条命令出报告，省得每次都人肉翻日志。

## 复盘的三个教训

1. **定时任务不等于 cleanup——得设合理的 TTL**。`@Scheduled` + Map 做缓存，最容易被忽视的内存泄漏模式。规约：所有 Map 缓存必须设上限，没有上限的 Map 就是定时炸弹。
2. **GC 日志是免费的性能分析工具，很多团队没打开**。JDK 11+ 默认开了，但 JDK 8 默认没开。线上 JDK 8 服务务必加上 `-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:/path/gc.log`。
3. **Full GC 不可怕，可怕的是不知道为什么 Full GC**。必须分析每次 Full GC 的根因——是内存泄漏还是流量突增——然后针对性解决。

**我的建议：**
- 所有 Java 服务设置 `-XX:+HeapDumpBeforeFullGC`，Full GC 发生时自动 dump
- 每台服务器放一个 `gc-quick-check.sh`，告警来了先跑一遍
- GCeasy 的在线报告可以直接分享给组里，省去复述的时间

---

📌 本文是「线上问题排查」系列第 3 篇。下一篇聊：Connection Reset 问题排查——从网络层到应用层，一层层剥开。

---

发布于：2026-06-20

|> 更多技术干货，欢迎关注公众号「后端实战笔记」
