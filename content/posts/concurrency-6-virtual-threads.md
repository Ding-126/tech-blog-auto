+++
title = "面向面试之并发编程系列六：虚拟线程（Virtual Threads）——一次搞懂"
slug = "concurrency-6-virtual-threads"
keywords = ["虚拟线程", "Virtual Threads"]
difficulty = "进阶"
target_length = 2000
series_name = "面向面试之 并发编程"
series_number = 6
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-07-10
+++

去年一个老朋友跟我吐槽，他们团队用 Java 21 重写了部分高并发服务，结果上线第二天，同事说"虚拟线程好像没什么用啊，压测结果差不多"。

我看了一眼代码——他们在虚拟线程里调用了线程池。这就像买了辆电动车，然后找匹马在前面拉。

虚拟线程是 Java 近几年最重要的更新之一，但很多人要么理解错了，要么用错了。这篇我们一次搞清楚。

## 虚拟线程解决的核心问题

先记住一个数字：操作系统线程的创建成本大约是 1MB 的栈空间。一个 4G 内存的服务器，理论上最多也就几千个线程，实际运行中 2000-3000 就顶天了。

那为什么像 Go 和 Kotlin 的协程能轻松撑起几十万并发？因为它们的"线程"不在 OS 层，而是在 JVM 层。

虚拟线程做的就是同样的事——让 Java 的线程从"OS 线程"变成"JVM 管理的轻量级线程"。

一个虚拟线程的内存开销大约是几百字节，一台普通机器跑几十万个不是问题。

## 虚拟线程 ≠ 异步编程

这是我见到最多的误解。

很多人以为虚拟线程是替代 CompletableFuture 或者 Reactor 的，其实不是。两者的目标不同：

- 异步编程（CompletableFuture、WebFlux）：把一个任务拆成多个阶段，每个阶段都返回，不阻塞线程。写起来像 callback 地狱（或者 chain 地狱）。
- 虚拟线程：还是同步阻塞的写法，线程该等就等，但等的不是 OS 线程，是 JVM 里的"轻量级线程"。

说白了，虚拟线程让你用同步的代码写出异步的效果。不用学响应式编程那一套，不用写 flatMap。

我之前过一个项目，用 WebFlux 写了个网关，代码可读性差到新同事不敢改。换成虚拟线程后，同一个功能用同步写，代码量少了三分之一。不是说 WebFlux 不好，而是对大多数人来说，维护成本太高。

## 怎么用

Java 21 开始虚拟线程正式发布（JEP 444）。用法非常简单：

```java
// 方式一：直接创建
Thread.startVirtualThread(() -> {
    System.out.println("Hello from virtual thread");
});

// 方式二：使用 Executors
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    executor.submit(() -> doSomething());
    executor.submit(() -> doSomethingElse());
}
// try-with-resources 会自动等待所有任务完成
```

关键 API 是 `Executors.newVirtualThreadPerTaskExecutor()`，它会为每个任务创建一个新的虚拟线程。

如果你的应用用 Spring Boot 3.2+，还可以直接配：

```yaml
spring:
  threads:
    virtual:
      enabled: true
```

Spring Boot 会自动把 Tomcat 的请求处理线程换成虚拟线程。改一行配置就行。

## 虚拟线程的工作原理（面试重点）

面试官问到原理，抓住这三个关键词就行：**Carrier Thread、Mount、Unmount**。

虚拟线程跑在实际的 OS 线程上，这个 OS 线程叫 Carrier Thread。虚拟线程不是一直在跑——它执行到阻塞操作（比如 IO、sleep、lock）时，JVM 会把它从 Carrier Thread 上"卸下来"（Unmount），然后 Carrier Thread 去执行另一个虚拟线程。等到阻塞结束，JVM 再把虚拟线程"装上去"（Mount）继续执行。

这个过程对应用代码完全透明，你写的就是普通的同步代码。

关键区别：
- 平台线程（传统线程）：1:1 映射到 OS 线程，阻塞就是 OS 线程阻塞
- 虚拟线程：M:N 映射，很多虚拟线程共享少量 Carrier Thread，阻塞时 Carrier Thread 不会阻塞

## 三个坑，面到了能加分

### 坑一：synchronized 导致 pinning

虚拟线程里用 synchronized 会"固定"（pinning）到 Carrier Thread 上——也就是说，这块代码执行期间，虚拟线程不能被卸下来。

```java
public synchronized void doSomething() {
    // 如果在虚拟线程里调这个，Carrier Thread 会被固定住
}
```

原因是 JVM 的 synchronized 实现跟 Carrier Thread 有绑定关系。JDK 21 还没有完全解决这个问题。

**解决方案**：用 `ReentrantLock` 代替 `synchronized`。

```java
private final Lock lock = new ReentrantLock();

public void doSomething() {
    lock.lock();
    try {
        // 虚拟线程友好
    } finally {
        lock.unlock();
    }
}
```

Spring Boot 3.2 的某些内部组件如果用了 synchronized，也会有这个问题。线上踩过一次坑，一个接口响应慢了几十倍，dump 一看，线程全卡在 synchronized 块里 pin 住了。

### 坑二：ThreadLocal 别滥用

虚拟线程数量可以非常大，而 ThreadLocal 是跟线程绑定的。如果每个虚拟线程都创建大量 ThreadLocal 变量，内存消耗会很大。

JDK 21 引入了 `ScopedValue`（JEP 429）作为替代方案，但目前还只是预览。

**实用建议**：虚拟线程场景下，ThreadLocal 只放必要数据，用完就清理。

### 坑三：线程池 + 虚拟线程 = 画蛇添足

开头那个朋友的案例——在虚拟线程里又调了线程池。因为虚拟线程本身就轻量，你再包一层线程池，反而增加了调度开销，还容易把虚拟线程塞到有限的平台线程里，完全失去了虚拟化的意义。

**原则**：用了虚拟线程，99% 的场景不需要手动管理线程池了。

## 面试常考题

**Q：虚拟线程和平台线程的区别？**
A：平台线程是 OS 线程的一层包装，开销大、数量有限；虚拟线程是 JVM 管理的轻量线程，开销小、数量巨大，阻塞时不会占用 Carrier Thread。

**Q：虚拟线程适合什么场景？**
A：高 IO 密集型场景——Web 服务、微服务、数据库操作、远程调用等。不适合 CPU 密集型场景（计算密集的用平台线程+并行流更稳妥）。

**Q：虚拟线程比 CompletableFuture 好吗？**
A：不是替代关系。如果团队熟悉响应式编程且已经用了很久，没必要换。但如果是新项目，团队同步编程经验多，虚拟线程的维护成本更低。

**Q：虚拟线程的底层实现？**
A：JVM 通过 Continuation（延续）实现。当虚拟线程阻塞时，保存执行状态到堆上，Carrier Thread 切换执行另一个虚拟线程，阻塞解除后恢复状态继续执行。

## 写在最后

虚拟线程不是银弹。它的目标很明确——让 Java 的高并发编程回归同步风格，降低异步编程的心智负担。

如果你在做一个 IO 密集型的新项目，或者现有的同步代码面临线程扩展瓶颈，值得一试。但如果你的系统已经是响应式那套架构而且跑得很好，没必要硬换。

记得一点：虚拟线程的核心是"用同步的写法，拿异步的性能"。别把它想复杂了。

📌 本文是「面向面试之 并发编程」系列第 6 篇。本系列完结，共 6 篇。
