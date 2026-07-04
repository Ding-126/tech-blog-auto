+++
title = "面向面试之并发编程系列四：CompletableFuture 异步编程——告别 Callback 地狱"
slug = "concurrency-4-completablefuture"
keywords = ["CompletableFuture", "异步"]
difficulty = "进阶"
target_length = 2000
series_name = "面向面试之 并发编程"
series_number = 4
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-07-05
+++

面试的时候，我经常问一个问题："你们项目里异步用 Future 还是 Callback？"

大部分人说用 Future，然后补一句"get() 的时候会阻塞，有点不方便"。少数人说用 Callback，然后抱怨"回调嵌套多了代码就拧成麻花了"。

其实这俩问题，Java 8 的 CompletableFuture 一次性解决了。这篇咱们聊清楚它。

## 为什么需要 CompletableFuture

先想想 Future 的问题在哪。

```java
ExecutorService executor = Executors.newFixedThreadPool(3);
Future<Integer> future = executor.submit(() -> {
    Thread.sleep(1000);
    return 42;
});
Integer result = future.get(); // 阻塞在这里！
```

看到了吗？future.get() 一调，当前线程就卡住了。在 IO 密集型场景里，线程宝贵，卡一个少一个。

那回调呢？可以用 Guava 的 ListenableFuture 或者 Netty 的 FutureListener，但回调嵌套三四层以后，代码基本就告别可读了：

```java
asyncCall1(result1 -> {
    asyncCall2(result2 -> {
        asyncCall3(result3 -> {
            // 这里已经劝退了
        });
    });
});
```

这叫 Callback Hell，或者叫"回调地狱"。

CompletableFuture 解决的就是这两个痛点——不阻塞，不嵌套。

我自己最早是在做一个用户画像服务的时候认真用的。一个请求要查三四个数据源，串行跑要 500ms+，用 CompletableFuture 并行调，压到 150ms 以内。当时就一个感受：真香。

## 基本用法：创建 CompletableFuture

两种创建方式：

```java
// 1. runAsync — 不返回结果
CompletableFuture<Void> future = CompletableFuture.runAsync(() -> {
    System.out.println("跑一个异步任务");
}, executor);

// 2. supplyAsync — 返回结果
CompletableFuture<Integer> future = CompletableFuture.supplyAsync(() -> {
    return 1 + 1;
}, executor);
```

如果不传 executor，默认用 ForkJoinPool.commonPool()。但生产环境我**强烈建议**自己传线程池。为什么？因为 commonPool 是所有并行流和 CompletableFuture 共享的，你一个地方出问题，全应用跟着卡。线上因为这个踩过坑的人应该不少。

## 链式调用：告别嵌套

这是 CompletableFuture 最优雅的地方——它把回调拍平了。

```java
CompletableFuture.supplyAsync(() -> {
    // 第一步：查用户信息
    return userService.getUser(1L);
}, executor)
.thenApply(user -> {
    // 第二步：拿用户信息去查订单
    return orderService.getOrders(user.getId());
})
.thenAccept(orders -> {
    // 第三步：处理订单列表
    orders.forEach(System.out::println);
});
```

每一行都是扁平的，没有嵌套，没有缩进地狱。

常用的链式方法：
- **thenApply** — 拿到上一步的结果，转换后返回新结果
- **thenAccept** — 拿到上一步的结果，消费它但不返回
- **thenRun** — 不需要上一步结果，跑一段逻辑
- **thenCompose** — 如果转换函数返回的是 CompletableFuture，用 thenCompose 扁平化，避免 CompletableFuture<CompletableFuture<T>>

thenCompose 稍微绕一点，看个对比就明白了：

```java
// ❌ 错误的：返回嵌套的 CompletableFuture
CompletableFuture<CompletableFuture<Integer>> bad = 
    future.thenApply(id -> fetchData(id));

// ✅ 正确的：用 thenCompose 拍平
CompletableFuture<Integer> good = 
    future.thenCompose(id -> fetchData(id));
```

面试常考这个区别，记住了。

## 合并多个异步任务

并行调两个接口，等两个都返回再处理结果：

```java
CompletableFuture<String> f1 = CompletableFuture.supplyAsync(() -> getFromServiceA(), executor);
CompletableFuture<String> f2 = CompletableFuture.supplyAsync(() -> getFromServiceB(), executor);

// thenCombine: 两个都完成，合并结果
CompletableFuture<String> result = f1.thenCombine(f2, (a, b) -> a + " | " + b);
```

等任意一个完成：

```java
// anyOf: 两个里最快完成那个
CompletableFuture<Object> first = CompletableFuture.anyOf(f1, f2);
```

等全部完成：

```java
// allOf: 所有都完成
CompletableFuture<Void> all = CompletableFuture.allOf(f1, f2, f3);
all.get(); // 阻塞直到全部完成
// 但是怎么拿结果？要手动 collect
CompletableFuture<List<String>> allResults = 
    CompletableFuture.allOf(f1, f2, f3)
        .thenApply(v -> Stream.of(f1, f2, f3)
            .map(CompletableFuture::join)
            .collect(Collectors.toList()));
```

这里有个细节：allOf 返回的是 CompletableFuture<Void>，结果需要自己从各个 Future 里 join 出来。join() 和 get() 的区别——join 不抛受检异常，代码更干净。

## 异常处理，别让错误静默

异步任务里出了异常怎么办？默认情况下 CompletableFuture 会默默吞掉异常，get() 或者 join() 的时候才抛出来。但链式调用中，你可能想在中间环节就处理。

三个异常处理方法：

```java
CompletableFuture.supplyAsync(() -> {
    if (Math.random() > 0.5) throw new RuntimeException("挂了");
    return "ok";
}, executor)
.exceptionally(ex -> {
    // 出异常了，返回一个默认值
    System.err.println("异常原因: " + ex.getMessage());
    return "fallback";
})
.thenAccept(result -> System.out.println(result));
```

**exceptionally** — 异常时提供降级值，类似 catch
**handle** — 不管有没有异常都执行，类似 finally + 带返回值
**whenComplete** — 不管有没有异常都执行，不改变结果，类似 finally

看 handle 的用法：

```java
future.handle((result, ex) -> {
    if (ex != null) {
        return "啊哦，出错了: " + ex.getMessage();
    }
    return result;
});
```

有一点要注意：handle 返回的是新的 CompletableFuture，别在 handle 里做耗时操作，否则整个链都会被拖慢。

## 超时控制，面试高频题

面试官经常问："CompletableFuture 怎么设置超时？"

Java 9 加了 orTimeout 和 completeOnTimeout：

```java
// Java 9+: 超时后抛 TimeoutException
future.orTimeout(2, TimeUnit.SECONDS);

// Java 9+: 超时后给一个默认值
future.completeOnTimeout("timeout", 2, TimeUnit.SECONDS);
```

但大多数项目还在 Java 8 怎么办？用 get() 的带超时重载：

```java
try {
    String result = future.get(2, TimeUnit.SECONDS);
} catch (TimeoutException e) {
    // 超时处理
    future.cancel(true);
    result = "timeout fallback";
}
```

或者用一个小的 ScheduledExecutorService 结合 applyToEither 实现"竞赛"模式——谁先完成用谁的结果：

```java
CompletableFuture<String> timeoutFuture = 
    CompletableFuture.supplyAsync(() -> {
        sleep(2000); // 模拟超时
        return "timeout";
    }, timeoutExecutor);

future.applyToEither(timeoutFuture, Function.identity());
```

这套路面试聊出来很加分。

## 实战经验和小技巧

**1. 自定义线程池是最佳实践**

不要依赖 commonPool。我见过一个服务，所有异步任务都用 commonPool，其中一个任务因为数据库连接池满了阻塞住，整个服务的 CompletableFuture 全部排队——因为 commonPool 只有 CPU 核数个线程。

建议业务线程池和通用线程池分开，至少两个：

```java
// IO 密集型：线程数设大一点
Executor ioPool = new ThreadPoolExecutor(
    10, 20, 60, TimeUnit.SECONDS,
    new LinkedBlockingQueue<>(1000),
    new ThreadPoolExecutor.CallerRunsPolicy()
);

// CPU 密集型：线程数 = CPU 核数 + 1
Executor cpuPool = new ThreadPoolExecutor(
    Runtime.getRuntime().availableProcessors() + 1,
    Runtime.getRuntime().availableProcessors() + 1,
    60, TimeUnit.SECONDS,
    new LinkedBlockingQueue<>(500),
    new ThreadPoolExecutor.CallerRunsPolicy()
);
```

**2. 链式调用注意线程切换**

thenApply 默认在哪个线程执行？有两种情况：
- 如果上一步还没完成，在完成上一步的线程上执行
- 如果上一步已经完成，在当前调用线程上执行

这会导致线程不固定的问题。如果希望指定线程，用 thenApplyAsync：

```java
future.thenApplyAsync(result -> {
    // 一定在 executor 里执行
    return process(result);
}, executor);
```

**3. 日志链路追踪要单独处理**

异步任务会切换线程，MDC 里的 traceId 会丢失。需要手动传递上下文：

```java
// 包装一下，把 MDC 传过去
CompletableFuture.supplyAsync(() -> {
    MDC.setContextMap(contextMap); // 从外部传入
    return doSomething();
}, executor);
```

这些坑不遇到一次真的不会注意，遇到了修起来又花时间。

---

CompletableFuture 是我用得最频繁的 Java 8 特性之一。它的核心就几件事：异步执行、链式编排、异常兜底、并行聚合。能把这几条用清楚，应对大多数并发场景足够了。

如果面试官问你"怎么优化接口响应时间"，CompletableFuture.allOf 并行调多数据源，就是这个问题的标准答案。

📌 本文是「面向面试之 并发编程」系列第 4 篇，前一篇：[线程池的核心参数与拒绝策略](https://dushi.tech/posts/concurrency-3-threadpool/)，后一篇：线程安全与锁优化，敬请期待。
