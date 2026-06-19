# cpu-100-arthas-troubleshoot

周末在家正刷着手机，突然运维群里炸了——"线上支付服务响应超时，CPU 飚到 200% 了！"

这种消息一出来，那种感觉懂的都懂。我放下筷子冲到电脑前，接上跳板机就开始排查。

今天就跟大家聊聊，线上 CPU 飙高的时候，我是怎么一步步定位的。

## 慌归慌，先看"谁"在吃 CPU

接到告警，第一件事不是翻代码，不是重启。先看进程级别。

```bash
top -c
```

按 `Shift + P` 按 CPU 排序，找到最吃 CPU 的进程号。

我那次一看，Java 进程 PID 12345，CPU 使用率 170%（没错，多核）。好，锁定。

接着看线程：

```bash
top -Hp 12345
```

这时候你看到的是一堆线程在争 CPU。记下最肥的线程 PID，比如 24680，转十六进制：

```bash
printf '%x\n' 24680
# 输出: 6068
```

十六进制 `6068` 就是关键线索。为什么？因为 Java 线程 dump 里线程 ID 用的就是十六进制。

去年有个同事，CPU 告警上来直接重启了。重启完事儿了，但第二天同样的问题又来了。**线上问题不找到根因，重启只是给自己挖坑。**

## Arthas 上场，不要只会 jstack

传统做法是 `jstack 12345 > dump.txt`，然后去文件里搜 `0x6068`。但说真的，**生产环境的高并发服务，jstack 是会给服务挂起的**——STW 暂停，万一 GC 也在临界点，直接雪崩。

现在我的标准做法：Arthas。

```bash
# 不用安装，直接 attach
curl -O https://arthas.aliyun.com/arthas-boot.jar
java -jar arthas-boot.jar 12345
```

选进程，进入 Arthas 控制台，先看最热的线程：

```bash
thread -n 5
```

这命令直接按 CPU 消耗排序，前 5 名线程一目了然。比 jstack 优雅太多。

那次查到的结果：

```
"http-nio-8080-exec-42" Id=24680 cpuUsage=78% ...
    at java.util.HashMap.putVal(HashMap.java:xxx)
    at com.dudu.pay.service.PayService.processCallback(PayService.java:125)
    ...
```

78% 的 CPU 消耗在一个线程上，堆栈指向 `PayService.processCallback` 里的 HashMap 操作。第一感觉——死循环或者扩容竞争。

## 直接看方法耗时，别猜

光看堆栈还不够，得看方法级别的执行时间。Arthas 的 trace 命令这时候最好用：

```bash
trace com.dudu.pay.service.PayService processCallback -n 3 --skipJDKMethod false
```

等几秒，触发一次请求，结果就出来了：

```
`---[0.3ms] "http-nio-8080-exec-42" @com.dudu.pay.service.PayService.processCallback()
    `---[0.25ms] validateRequest()
    `---[890ms] processOrder()  # <-- 这里！
        `---[885ms] HashMap.put
```

看到没？`processOrder` 走了 890ms，其中 885ms 耗在 `HashMap.put` 上。**一个 put 操作不应该超过 0.001ms，这个数据本身就不正常。**

那 HashMap 为什么会这么慢？两个常见原因：
1. **多线程并发 put 导致扩容死循环**（JDK 7 经典 bug）
2. **Hash 严重冲突**，链表过长

那次的问题是第一个——并发扩容死循环。

## 细看源码，找到根因

顺着 Arthas 指引，我翻了那一段代码：

```java
// 同事写的，看着就没问题对吧？
private Map<String, Order> orderCache = new HashMap<>();

public void processOrder(String orderId, Order order) {
    // 问题就在这里
    orderCache.put(orderId, order);  // 没有同步！
}
```

这其实是个典型的**无意识地用 HashMap 当缓存**，方法被高并发调用时，put 触发了 resize，而 resize 在 JDK 7 下会造成循环链表。我们用的 JDK 7（公司老项目还没升），所以中招。

修复也很直接：

```java
// 方案一：用 ConcurrentHashMap
private Map<String, Order> orderCache = new ConcurrentHashMap<>();

// 方案二：用 synchronized
public synchronized void processOrder(String orderId, Order order) {
    orderCache.put(orderId, order);
}
```

我选了一，因为 ConcurrentHashMap 分段锁对高并发友好，而且不改业务逻辑。

**一个小细节：线上修完别急着走，观察 15 分钟。** CPU 降下去之后，再跑一遍 `thread -n 5` 看有没有其他异常线程。那次观察完发现 CPU 从 170% 降到 12%，心里才踏实。

## 给 Arthas 写个脚本曲线救急

后来我写了个小脚本，每次 CPU 飙高，一行命令出报告：

```bash
cat > /tmp/cpu-check.sh << 'SCRIPT'
#!/bin/bash
PID=$1
java -jar arthas-boot.jar $PID -c "thread -n 5; trace com.dudu.pay.service.PayService processCallback -n 1; exit"
SCRIPT
```

往系统 PATH 一放，以后运维兄弟自己就会跑了。省得半夜被叫起来。

## 复盘比解决问题更重要

事情过了别急着庆祝，花十分钟想三个问题：

- **为什么这个 bug 进了生产？** —— 代码 review 不够细，同事没注意到 HashMap 并发问题
- **报警为什么没早发现？** —— 之前 CPU 的告警阈值设得太高，85% 才报警，其实 70% 就该关注了
- **怎么防止再犯？** —— 加了代码规约检查，Sonar 扫描出了所有非并发容器的误用

**定位快不算本事，不复盘才算白干。**

---

**说句真心话**：Arthas 这个工具入坑之后，线上问题排查效率翻了好几倍。以前靠猜、靠日志、靠线上堆叠重启，现在直接一梭子指令下去，看方法的执行链路、参数、返回值，爽太多了。

CPU 100% 只是面试里的一个八股题，但在线上是真的会出人命的。希望这篇能帮你省下半夜被叫起来的那 20 分钟慌乱。

📌 本文是「线上问题排查」系列第 1 篇。下一篇聊当 OOM 来了，怎么 3 分钟内定位到具体代码行。

---

发布于：2026-06-19

原文链接：

|> 更多技术干货，欢迎关注公众号「后端实战笔记」
