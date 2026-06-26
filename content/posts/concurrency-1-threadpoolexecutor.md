+++
title = "面向面试之并发编程系列一：线程池——核心参数、拒绝策略、最佳实践"
slug = "concurrency-1-threadpoolexecutor"
keywords = ["线程池", "ThreadPoolExecutor"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之并发编程"
series_number = 1
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-06-27
+++

写 Java 的人，面试逃不过线程池。日常开发也逃不过。

但说真的，很多人用了好几年线程池，碰到线上问题还是一脸懵——队列满了为啥不抛异常？核心线程数配 8 还是 80？拒绝策略选哪个不丢任务？

这篇文章不讲废话，直接掰开线程池的核心参数、拒绝策略、最佳实践。面试够用，干活也够用。

---

## 一、七个核心参数，记不住也得记住

`ThreadPoolExecutor` 构造器最长有七个参数。不用死背，理解逻辑就能推出来。

```java
public ThreadPoolExecutor(
    int corePoolSize,           // 核心线程数
    int maximumPoolSize,        // 最大线程数
    long keepAliveTime,         // 空闲线程存活时间
    TimeUnit unit,              // 时间单位
    BlockingQueue<Runnable> workQueue,  // 阻塞队列
    ThreadFactory threadFactory,        // 线程工厂
    RejectedExecutionHandler handler    // 拒绝策略
)
```

**参数逻辑记忆法**：

1. 线程先建 `corePoolSize` 个，不够用就丢进 `workQueue`
2. 队列也满了，最多再建 `maximumPoolSize - corePoolSize` 个临时线程
3. 临时线程空闲 `keepAliveTime` 后回收
4. 连最大线程都跑满了 → 走 `handler` 拒绝策略

就这么简单。**核心数管稳定水位，队列管缓冲，最大线程管峰值，拒绝策略管兜底。**

## 二、核心参数怎么配？一线踩坑经验

### 核心线程数

网上最常见的公式是 **CPU 密集型 = CPU 核数 + 1，IO 密集型 = CPU 核数 × 2**。理论没问题，但只适合纯计算或纯 IO。

**现实情况是：** 没有纯粹的 CPU 密集型或 IO 密集型。你写的服务里，总有一部分查数据库，一部分做计算，一部分调下游接口。

我自己在线上踩过的坑：把一个风控接口的线程池核心线程数从 8 改到 16，QPS 没涨，RT 翻倍了。因为线程多了，上下文切换抢 CPU，反而更慢。

**我现在的做法：**

- 先压测，找到 RT 和 QPS 的交叉点
- 用 `- CPU核数 / (1 - 阻塞系数)` 这个公式估算，阻塞系数靠监控算
- 上线后留告警，慢慢调

没有一刀切的配置，**调线程池的本质是调资源分配**。

### 队列选型

| 队列 | 特点 | 适合场景 |
|------|------|----------|
| `ArrayBlockingQueue` | 有界，公平/非公平 | 能接受有限等待 |
| `LinkedBlockingQueue` | 有界（默认 Int.MAX！） | 任务之间互相独立 |
| `SynchronousQueue` | 不存任务，直接交线程 | 高并发、短任务 |
| `PriorityBlockingQueue` | 优先级排序 | 有优先级的任务 |

**需要注意：** `LinkedBlockingQueue` 如果不传容量，默认是 `Integer.MAX_VALUE`。这意味着任务可以无限堆积——内存撑爆了都不知道怎么死的。线上务必指定容量。

### 线程工厂

大部分人不配 `threadFactory`，用默认。默认的坑：线程名是 `pool-1-thread-1`，出问题你连是哪个线程池都不知道。

**自己配一个：**

```java
new ThreadFactoryBuilder()
    .setNameFormat("order-async-pool-%d")
    .setDaemon(true)
    .build()
```

Guava 的 `ThreadFactoryBuilder` 一行搞定。线上看线程 dump 一眼就知道是谁家的线程。

---

## 三、拒绝策略，选错就是线上事故

JDK 内置四种拒绝策略：

### 1. AbortPolicy（默认）

直接抛 `RejectedExecutionException`。最暴力，但也最安全——**你一定会发现任务丢了**。

适合场景：你确定任务绝对不能丢，抛异常后上层有兜底（比如重试队列）。

### 2. CallerRunsPolicy

谁提交任务谁执行。线程池满了，提交线程自己跑。

这招的好处是能反向压提交方。比如线程池满了，HTTP 请求线程自己跑任务——它跑任务去了，就不处理新请求了，自然就限流了。

我有个老朋友他们公司的短信发送服务就用这个策略。大促流量冲上来时线程池满，提交线程自己发短信，发完再处理新请求。系统从来没崩过。

### 3. DiscardPolicy

静默丢弃。注意，是**静默**。任务丢了没有任何反馈。

**我个人极度不建议用这个。** 线上丢了什么完全不知道，排查定位困难得要命。

### 4. DiscardOldestPolicy

丢弃队列中等待最久的任务，把位置让给新任务。

适合对时效性敏感的场景。比如实时推荐，老数据发了也没意义，新数据更重要。

### 真实业务场景怎么选？

| 业务类型 | 推荐策略 | 理由 |
|----------|----------|------|
| 支付/订单 | AbortPolicy + 重试 | 不能丢，必须兜底 |
| 日志上报 | DiscardPolicy | 丢几条日志没关系 |
| 实时推荐 | DiscardOldestPolicy | 老数据过期了 |
| 通用后台 | CallerRunsPolicy | 反向压一下提交方 |

---

## 四、线程池 + 异常处理，一个容易翻车的细节

`execute()` 提交的任务如果抛异常，线程池不会帮你打印。

```java
threadPool.execute(() -> {
    int i = 1 / 0;  // 静默失败
});
```

这行代码跑完，线程池正常工作，但异常没了。你完全不知道业务对错了。

**经验：** 包装一层，自己打日志。

```java
threadPool.execute(() -> {
    try {
        doBiz();
    } catch (Exception e) {
        log.error("业务执行异常", e);
    }
});
```

或者用 `submit()` 代替 `execute()`，拿 `Future.get()` 的时候异常会抛出来。

---

## 五、监控比配参数更重要

线程池配对参数只是开始，真正的问题往往在运行后才暴露。

**必须监控的几个指标：**

- **活跃线程数**：持续接近 `maximumPoolSize` 说明瓶颈了
- **队列积压**：持续上涨说明处理速度跟不上
- **拒绝次数**：不为 0 就要立刻介入
- **任务执行耗时**：平均值 + P99，识别慢任务

**怎么监控？**

最简单：用 `ThreadPoolExecutor` 提供的 `getPoolSize()`、`getActiveCount()`、`getQueue().size()` 定时打入监控系统。

Spring Boot 项目直接用 `Micrometer`，一行配置就自带线程池指标。

```yaml
management.metrics.binders.thread-pool.enabled=true
```

---

## 六、一个完整的线程池封装

最后放一个我在生产用的模板。

```java
public class ThreadPoolFactory {

    public static ThreadPoolExecutor create(String poolName, int cores, int max, int queueSize) {
        return new ThreadPoolExecutor(
            cores,
            max,
            60L, TimeUnit.SECONDS,
            new LinkedBlockingQueue<>(queueSize),
            new ThreadFactoryBuilder().setNameFormat(poolName + "-%d").build(),
            (r, executor) -> {
                log.warn("{} 线程池满了，任务提交者自己执行", poolName);
                r.run();
            }
        );
    }
}
```

拒绝策略用 `CallerRunsPolicy` 保底 + 一条 WARN 日志。既不会丢任务，也能在监控系统里看到告警。

---

## 面试高频题

### Q：execute() 和 submit() 的区别？

| execute() | submit() |
|-----------|----------|
| 无返回值 | 返回 Future |
| 抛的异常直接吃掉 | 异常放 Future.get() 里 |
| 参数 Runnable | 参数 Runnable/Callable |

**建议：** 如果你需要知道任务有没有成功，用 `submit()`。堆异常排查比无脑 catch 方便太多了。

### Q：为什么禁止用 Executors 创建线程池？

`Executors.newFixedThreadPool()` 用 `LinkedBlockingQueue` 不设上限，`newCachedThreadPool()` 最大线程数为 `Integer.MAX_VALUE`。

阿里巴巴手册明确禁止——**一个是队列无限撑爆内存，一个是线程无限撑爆 CPU**。老老实实手动配 `ThreadPoolExecutor`。

---

## 结尾

线程池不难，但细节多。配错参数、选错队列、不加监控——任何一个环节翻车，线上就是事故。

系列预告：下一篇讲 **AQS 与 Lock**，ReentrantLock 的公平/非公平实现，看完面试不怕问源码。

📌 本文是「面向面试之并发编程」系列第 1 篇
