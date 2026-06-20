---
title: "线上问题排查系列四：线程池满了怎么办？线程阻塞与死锁排查"
slug: "troubleshoot-4-线程池满了怎么办"
keywords: ["线程池", "死锁", "线程 Dump"]
difficulty: "实战"
target_length: 2500
series_name: "线上问题排查"
series_number: 4
series_total: 5
draft: false
categories: ["tutorial"]
date: 2026-06-21
---

## 线程池满了，接口全挂了

线上最怕什么？接口超时告警一大堆，登录服务器看日志全是 `RejectedExecutionException`。

老板问"怎么回事"，你说"线程池满了"——他知道是什么原因吗？

不管老板知不知道，你得知道。

## 线程池满的标准姿势

先看一个典型的线程池配置：

```java
@Bean
public ThreadPoolTaskExecutor bizExecutor() {
    ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
    executor.setCorePoolSize(10);
    executor.setMaxPoolSize(20);
    executor.setQueueCapacity(200);
    executor.setThreadNamePrefix("biz-pool-");
    executor.setRejectedExecutionHandler(
        new ThreadPoolExecutor.CallerRunsPolicy()
    );
    return executor;
}
```

这个小配置里就埋了三颗雷。

### 雷一：队列太大，线程数永远跑不满

QueueCapacity 设 200。你的请求量平时也就每秒几十，高峰期可能冲到两三百。

问题来了：核心线程 10 个在工作，队列里堆了 200 个任务排队。这时候新请求进来，它**只会进队列，不会触发创建新线程**。队列都没满，线程池认为你还扛得住。

结果呢？每个请求都在队列里等，越等越久。

我踩过这个坑。有次双十二大促，业务方反馈某个接口响应从 50ms 飙到 5s。一看线程池，核心线程 10，最大 30，队列 500。高峰期 400 个请求全在队列里排队。线程数始终只有 10 个。

**解法**：做压测，别让队列掩盖了线程扩展能力。一般队列不要超过 `maxPoolSize * 3`。流量抖动的场景，队列可以小一点，宁可快速拒绝然后降级，也别让用户慢吞吞等超时。

### 雷二：CallerRunsPolicy 是双刃剑

CallerRunsPolicy 的意思是"线程池满了就让调用线程自己跑"。听起来挺好？把压力反压给调用方。

但调用线程是谁？可能是 Tomcat 的 HTTP 处理线程。你用业务线程把 HTTP 线程也堵死了，其他接口也会跟着挂。

曾经一个同事很自信地配置了 CallerRunsPolicy，结果业务线程池满了之后，Tomcat 线程被拉去跑业务逻辑。Tomcat 线程被占着不放，其他请求连 Tomcat 分配线程那一步都过不去。最后整个 Web 容器卡死，所有接口全部超时。

**建议**：能接受丢失的业务用 `DiscardPolicy` 加 MQ 补偿。不能丢失的用 `AbortPolicy` 捕获异常走降级。CallerRunsPolicy 只在明确知道调用线程不会被阻塞的场景用。

## 线程 dump：线上第一武器

好了，现在线程池真的满了。你怎么查？

别重启！重启等于销毁现场。

用 `jstack` 打线程 dump：

```bash
# 找到 Java 进程
top -H -p $(pgrep -f your-app)
# 导出线程堆栈
jstack $(pgrep -f your-app) > /tmp/thread_dump_$(date +%Y%m%d_%H%M%S).log
```

如果你的容器里没有 jstack，用：

```bash
docker exec <container_id> jstack 1 > /tmp/thread_dump.log
```

拿到 dump 之后，看什么？

### 看线程状态分布

```bash
grep "java.lang.Thread.State" /tmp/thread_dump.log | sort | uniq -c
```

输出类似：

```
   45 RUNNABLE
   20 TIMED_WAITING
   30 WAITING
    5 BLOCKED
```

WAITING + BLOCKED 特别多，说明线程被卡住了。

### 看线程池相关的堆栈

如果你给线程设置了名字前缀 `biz-pool-`，直接搜：

```bash
grep -A 20 "biz-pool-" thread_dump.log | grep -E "pool-\d+|Thread\.State|at "
```

看这些线程在执行什么代码。如果大部分都在执行同一个方法，那这个方法是热点。如果全在 `park` 或者 `wait`，就是被什么锁住了。

## 死锁排查：jstack 直接告诉你答案

死锁在 dump 里非常明显。jstack 会在文件末尾打印：

```
Found one Java-level deadlock:
=============================
"biz-pool-1":
    waiting to lock <0x000000076c1f3b20> (a java.lang.Object)
    which is held by "biz-pool-2"
"biz-pool-2":
    waiting to lock <0x000000076c1f3b10> (a java.lang.Object)
    which is held by "biz-pool-1"
```

看到这种输出，死锁坐实了。

但我遇到更多的不是这种"经典死锁"，而是**逻辑死锁**——代码上看不出锁，实际上线程互相等。

### 实战：一个诡异的"死锁"

有次同事说线程池满了，我打 dump 一看，没有死锁标记。但 30 个 WAITING 线程全卡在同一个地方：

```
"biz-pool-5" #15 prio=5 os_prio=0 tid=0x00007f...
    java.lang.Thread.State: WAITING (parking)
        at sun.misc.Unsafe.park(Native Method)
        at java.util.concurrent.locks.LockSupport.park(LockSupport.java:175)
        at java.util.concurrent.CompletableFuture$Signaller.block(CompletableFuture.java:1709)
        at java.util.concurrent.ForkJoinPool.managedBlock(ForkJoinPool.java:3338)
        at java.util.concurrent.CompletableFuture.timedGet(CompletableFuture.java:1787)
        at java.util.concurrent.CompletableFuture.get(CompletableFuture.java:1906)
```

所有线程都在 `CompletableFuture.get()` 上等着。这说明上游服务响应慢，线程池全卡在等下游返回结果上。

**这比死锁常见 10 倍**：不是锁导致的堵塞，而是下游超时设置不合理，全堆在等 I/O。

**解法**：

1. 给 `CompletableFuture.get()` 设超时，别无限等下去
2. 熔断降级：下游响应超过 500ms 直接返回兜底数据或抛异常释放线程
3. 线程池隔离：A 业务的下游超时不至于拖死 B 业务的线程

```java
// 错误的写法：无限等待
CompletableFuture<Result> future = asyncService.call();
Result r = future.get();

// 正确的写法：设超时
CompletableFuture<Result> future = asyncService.call();
try {
    Result r = future.get(500, TimeUnit.MILLISECONDS);
} catch (TimeoutException e) {
    log.warn("下游调用超时，走降级");
    return fallbackResult();
}
```

就这一行 `500ms` 超时，我救了两次线上事故。

## 监控告警不能晚

等到用户投诉了再查 dump，已经晚了。

线程池满了之前，你应该先知道。

### 必加的 3 个指标

```java
// 1. 活跃线程数 / 核心线程数
// 大于 80% 告警
double usage = (double) executor.getActiveCount() / executor.getCorePoolSize();

// 2. 队列积压量
int queueSize = executor.getThreadPoolExecutor().getQueue().size();

// 3. 拒绝次数
// 用 RejectedExecutionHandler 里埋计数器
```

Micrometer + Prometheus + Grafana 一套走起，线程池使用率超过 70% 就告警。

我习惯在线程池外面包一层封装类，暴露这三个指标，统一上报到监控系统。

```java
public class MonitoredThreadPoolExecutor extends ThreadPoolExecutor {
    private final AtomicLong rejectedCount = new AtomicLong(0);

    public MonitoredThreadPoolExecutor(...) {
        super(...);
    }

    @Override
    public void rejectedExecution(Runnable r, ThreadPoolExecutor executor) {
        rejectedCount.incrementAndGet();
        super.rejectedExecution(r, executor);
    }

    public double usageRate() {
        return (double) getActiveCount() / getCorePoolSize();
    }

    public int pendingCount() {
        return getQueue().size();
    }

    public long rejectedCount() {
        return rejectedCount.get();
    }
}
```

这个封装类三个核心指标都有了。配合 Grafana 看板，线程池用到什么程度一清二楚。

## 总结：线程池满了的排查三板斧

1. **先看配置**：队列是否过大？拒绝策略是否合理？超时时间有没有？
2. **dump 说话**：jstack 打 dump，看线程状态分布，看线程卡在哪里
3. **监控前置**：别等满了再查，使用率超 70% 就告警，队列积压超阈值就扩

这套思路排查过不下五次线上问题，每次都能定位到根因。别猜，看数据。

📌 本文是「线上问题排查」系列第 4 篇
