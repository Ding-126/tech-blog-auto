+++
title = "面向面试之并发编程系列一：线程池——核心参数、拒绝策略、最佳实践"
slug = "concurrency-1-threadpoolexecutor"
keywords = ["线程池", "ThreadPoolExecutor"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之 并发编程"
series_number = 1
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-06-30
+++

## 线程池这东西，面试必问，线上必用

Java 的线程池，可以说是面试里的常客了。从校招到 P8 面都会问，而且问法越来越刁钻。

但说实话，很多人背了参数就能过面试，上线一压就出问题。我见过好几个项目，线程池配得不对，流量一起来直接拒绝请求，事故通报写了两页。

这篇不搞虚的，直接从面试+实战两个角度把线程池说透。

## 核心参数：就七个字

`corePoolSize`、`maximumPoolSize`、`keepAliveTime`、`unit`、`workQueue`、`threadFactory`、`handler`。

面试官让你说参数，能把这七个背出来只是基本分。关键在于——**你知道每个参数怎么影响行为**。

### corePoolSize 和 maximumPoolSize

默认情况下，提交任务时：
1. 线程数 < `corePoolSize` → 创建新线程
2. 线程数 >= `corePoolSize` → 任务进队列
3. 队列满了，且线程数 < `maximumPoolSize` → 创建新线程（直到 max）
4. 队列满了，且线程数 == `maximumPoolSize` → 执行拒绝策略

有面试经验的人会追问一句：**`prestartAllCoreThreads()` 知道吗？**

这个方法会提前把核心线程都启动好，而不是等任务来了才创建。如果你的服务对**首次请求延迟**有要求，或者你明确知道这个池子一定会被大量使用，可以调一下。我自己在压测场景里就用过这个，能省掉启动阶段的线程创建开销。

### workQueue：决定了线程池的性格

队列不同，线程池的行为完全不同：

- **LinkedBlockingQueue**：默认大小 Integer.MAX_VALUE，任务几乎不会触发创建 max 线程，适合任务量平稳的场景
- **ArrayBlockingQueue**：有界，到达容量后开始创建 max 线程，适合需要控制背压的场景
- **SynchronousQueue**：不存任务，直接交给线程。来一个任务如果没空闲线程就尝试创建新线程到 max，适合处理速度极快、任务量忽高忽低的场景
- **DelayQueue**：定时/延迟任务的专用队列

我在一个对账系统里用过 ArrayBlockingQueue + 较小的 maxPoolSize。好处是队列满了直接触发拒绝策略，整个流程不会因为堆积太多任务而 OOM。代价是一部分请求会被丢弃，需要在上游做好重试。

## 拒绝策略：别只会说 AbortPolicy

JDK 给了四种：

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| AbortPolicy | 抛 RejectedExecutionException | 默认，适合必须处理的场景 |
| CallerRunsPolicy | 提交任务的线程自己跑 | 降低提交速度，适合需要削峰的 |
| DiscardPolicy | 悄悄丢掉 | 日志不重要可丢的场景 |
| DiscardOldestPolicy | 丢队列里最老的任务 | 追求新数据、愿意丢弃旧任务的 |

有一次我踩过一个坑：用 CallerRunsPolicy，结果回调线程是 Netty 的 IO worker。任务跑太久，IO worker 卡住了，整个服务的连接处理都停了。

**CallerRunsPolicy 不是银弹**，你得搞清楚"调用者"是谁。如果是业务线程池还行，如果是框架的 IO 线程，千万别用。

## ThreadFactory：最小的自定义点，最大的Debug价值

默认的线程工厂创建的线程名字是 `pool-1-thread-1`。出问题的时候看线程 dump，完全不知道这个线程在干嘛。

**自定义 ThreadFactory，给线程起一个有意义的名字**，比如 `order-async-worker-%d`。看过一次线上线程 dump 就会知道这个有多重要。

```java
ThreadFactory factory = new ThreadFactoryBuilder()
    .setNameFormat("my-pool-%d")
    .setDaemon(true)
    .build();
```

Guava 的 ThreadFactoryBuilder 一行搞定。用 Apache Common 的 BasicThreadFactory 也行。别偷懒省这行代码。

## 核心线程数设多少？这不是数学题

网上流传的各种公式（CPU 密集型=N+1，IO 密集型=2N）只能作为起步参考，不能当结论。

我自己的经验是：

1. **CPU 密集型**：从 `N+1` 开始，压测，看 CPU 利用率。如果 CPU 一直跑不满就加。
2. **IO 密集型**：从一个比较大的值开始（比如 200），观察线程等待时间和响应时间，反向调整。
3. **混合型**：现在大部分应用都是这种。用动态线程池或者分不同线程池隔离不同类型任务。

**压测才是最终的答案**，任何公式只是起点。你在面试里说"具体看压测数据"比背公式更能拿分。

## 一个真实的踩坑经历

去年我接手过一个定时任务系统，每个任务都用自己的线程池，有的池子配了几百个线程。跑了一段时间后系统越来越慢，重启就好。

查线程 dump 发现：线程数超过 3000+，大部分在 BLOCKED 或者 WAITING 状态。CPU 全花在线程上下文切换上，实际干活的不到 20%。

最后把线程池统一管理，限制全局线程数在 500 以内，核心业务配单独的隔离池，降配后吞吐反而涨了 40%。

线程不是越多越好。这个教训花了我一个通宵。

## 总结一下面试重点

- 核心参数的作用和执行流程（必须**结合代码讲**）
- 四种拒绝策略及各自陷阱
- 线程池大小怎么设（带上压测，别只背公式）
- 自定义 ThreadFactory 的意义
- 任务队列如何影响行为（Linked vs Array vs Synchronous）
- 1 个真实踩坑经历（加分项）

📌 本文是「面向面试之 并发编程」系列第 1 篇。下一篇我们会深入线程池的源码，看看 execute() 到底是怎么把任务分配出去的。
