+++
title = "面向面试之并发编程系列五：并发容器——ConcurrentHashMap 从源码到面试题"
slug = "concurrency-5-concurrenthashmap"
keywords = ["ConcurrentHashMap", "并发容器"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之并发编程"
series_number = 5
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-07-07
+++

面试聊并发，ConcurrentHashMap 基本跑不掉。

不是因为面试官特别喜欢它，而是这个类把并发编程的几个核心问题都串起来了：锁的粒度怎么控制？数据一致性怎么保证？性能和安全之间怎么取舍？

能把它讲明白，说明你对并发的理解不是停留在背 API 的层面。

## 为什么要搞这么复杂

HashMap 不安全，多线程 put 可能死循环或者丢数据。那用 HashTable 不就行了？全表加一把 synchronized，简单粗暴。

问题是性能太差了。一个线程在 put，其他线程全都等着，并发度直接变成 1。

ConcurrentHashMap 要解决的就是这个问题：**在保证线程安全的前提下，尽可能提高并发度**。

## JDK 7 的分段锁思路

JDK 7 的实现思路很直观：把整张表切成多段，每段有自己的锁。

核心结构是 Segment 数组，每个 Segment 继承 ReentrantLock。put 一个 key 的时候，先算出它应该落在哪个 Segment，然后只锁这个 Segment，其他 Segment 的读写不受影响。

默认并发度是 16，也就是 16 个 Segment。理论上最多 16 个线程可以同时 put，比 HashTable 的全表锁强多了。

但有个限制：Segment 的数量在创建时就定死了，后续不能扩容。如果你一开始并发度设小了，后面只能重新创建对象。

## JDK 8 的进化：锁住链表头

JDK 8 做了一个大胆的决定：**抛弃 Segment，直接锁桶的头节点**。

put 操作的流程大致是这样：

1. 计算 hash，定位到数组的某个位置
2. 如果桶是空的，CAS 直接插入，不加锁
3. 如果桶不为空，锁住头节点，在链表或红黑树中插入
4. 如果发现正在扩容（hash == MOVED），帮忙一起扩

锁的粒度从 Segment 变成了单个桶，并发度等于数组长度。默认初始容量 16，那并发度就是 16，而且扩容后并发度自动提升。

这个改进的关键点在于：锁住头节点后，其他桶的操作完全不受影响。两个线程往不同桶里 put，连锁竞争都不会有。

## get 为什么不用加锁

很多人面试被问到：ConcurrentHashMap 的 get 为什么不需要加锁？

答案很简单：**get 读取的整个过程都是基于 volatile 的**。

Node 节点的 val 和 next 字段都用 volatile 修饰，保证了可见性。get 操作就是顺着链表或红黑树遍历，读到哪个节点就用哪个节点的值，不需要加锁。

这里有个细节：即使在你遍历的过程中，别的线程修改了某个节点的 val，你读到的也是修改后的最新值（volatile 保证）。如果你读的是旧值，那也只是因为你读的时候它还没被改，这在并发场景下是可以接受的。

这种"读不加锁"的设计，让 get 的性能非常高。大多数场景下，读操作远多于写操作，这个取舍很划算。

## size() 的精妙设计

size() 看起来简单，在并发环境下其实很棘手。

JDK 7 的做法是：先不加锁尝试统计，如果发现总和有变化（说明期间有 put/remove），再加锁统计。重试超过 3 次就直接全表加锁。

JDK 8 换了思路：用一个 baseCount 加上一个 CounterCell 数组来记录元素个数。

每次 put 或删除操作，会更新对应的计数器。线程少的时候直接 CAS 更新 baseCount，竞争多了就把计数分散到 CounterCell 数组里，每个 Cell 存一部分计数。最终 size() 就是 baseCount + 所有 CounterCell 的和。

这个思路和 LongAdder 一脉相承。JDK 8 的 ConcurrentHashMap 内部其实就是用了一个类似 LongAdder 的机制来做计数。

我之前写日志聚合服务的时候，统计 QPS 用的就是 LongAdder。单线程场景下 AtomicLong 就够了，但并发上来的时候 LongAdder 性能明显更好，原理就是把计数分散到多个 Cell 里，减少竞争。

## 扩容：多线程一起搬数据

HashMap 扩容的时候，需要把旧数组的数据搬到新数组。ConcurrentHashMap 的扩容更复杂，因为要支持多线程并发。

JDK 8 的扩容机制是这样的：

1. 扩容时先把旧数组标记为 forwarding（hash 值设为 MOVED）
2. 新数组提前分配好，大小是旧数组的 2 倍
3. 遇到 MOVED 标记的线程，会主动帮忙搬数据
4. 每个线程负责搬迁一部分桶，搬完的桶在新数组中标记

这个"帮忙搬"的设计很巧妙。多个线程可以并行搬迁数据，而不是一个线程搬完所有数据、其他线程干等着。

不过实际开发中，扩容本身还是个比较重的操作。如果你的场景能预估数据量，最好在创建 ConcurrentHashMap 时指定初始容量，减少扩容次数。

## 面试高频题

**Q：为什么 ConcurrentHashMap 的 key 和 value 不能为 null？**

HashMap 允许 key 为 null，会放到 0 号桶。但 ConcurrentHashMap 不允许，原因是：在并发环境下，如果 get(key) 返回 null，你无法区分是"这个 key 没有对应的 value"还是"这个 key 的 value 就是 null"。HashMap 单线程环境可以用 containsKey 确认，但 ConcurrentHashMap 在 containsKey 和 get 之间可能被其他线程修改，这个歧义没法消除。

Doug Lea 本人在邮件列表里也解释过这个设计决策。说白了就是为了消除并发歧义，宁可不用 null。

**Q：ConcurrentHashMap 能完全替代 Hashtable 吗？**

绝大多数场景可以。但如果你需要一个操作的原子性保证，比如"先判断 key 是否存在，不存在就 put"，单独用 containsKey + put 不是原子的，得用 putIfAbsent 或者 computeIfAbsent。

computeIfAbsent 这个方法很常用，但有个坑：如果 mapping function 执行时间很长，它会一直持有锁，阻塞其他线程访问同一个桶。我见过有人在里面做数据库查询，直接导致线上接口超时，排查了半天才定位到。

**Q：JDK 7 和 JDK 8 的 ConcurrentHashMap 哪个性能更好？**

绝大多数场景 JDK 8 更好。锁粒度更细，读操作完全无锁，扩容支持并发。但如果你用的是 JDK 7 的遗留系统，了解分段锁的思路也很重要，面试可能会问。

## 小结

ConcurrentHashMap 的核心演进路径：

- **JDK 7**：Segment 分段锁，并发度固定为 16
- **JDK 8**：锁桶头节点 + CAS，并发度等于数组长度，读操作无锁
- **计数**：从 baseCount 到 CounterCell 数组，借鉴 LongAdder 的分散计数思路
- **扩容**：多线程并行搬迁，ForwardingNode 标记

面试的时候，能把这几个点讲清楚，再结合自己的项目经验说一两个实际踩过的坑，基本就够了。关键是要理解设计背后的取舍：为什么选择这种锁粒度？为什么读不加锁？为什么不允许 null？

理解"为什么"比记住"是什么"重要得多。

---

📌 本文是「面向面试之并发编程」系列第 5 篇。上一篇讲了 AQS 的核心原理，下一篇我们来聊 CompletableFuture——异步编程的终极武器。
