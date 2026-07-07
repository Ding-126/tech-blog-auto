+++
title = "面向面试之并发编程系列五：并发容器——ConcurrentHashMap 从源码到面试题"
slug = "concurrency-5-concurrenthashmap"
keywords = ["ConcurrentHashMap", "并发容器"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之 并发编程"
series_number = 5
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-07-08
+++

面试 Java 中高级岗位，ConcurrentHashMap 是绕不开的题。我面过不下 30 个人，聊到这个话题时，能清楚说出 JDK 7 和 JDK 8 区别的不到一半，能讲明白 sizeCtl 的就更少了。

这篇从源码角度把 ConcurrentHashMap 拆开，顺便把面试常考的那些题一并解决。

## 为什么不用 HashMap 和 Hashtable

先说 HashMap。多线程场景下，HashMap 的 put 操作会导致扩容时形成环形链表——JDK 7 的 bug，JDK 8 虽然修复了链表死循环，但 put 的时候数据互相覆盖的问题还在。所以 HashMap 从来就不是线程安全的。

那 Hashtable 呢？它是线程安全的，但实现方式太粗暴了——所有方法都加了 synchronized，相当于给整个哈希表上了一把大锁。两个线程就算操作不同的桶，也得排队。

我见过一个老项目用的 Hashtable，压测的时候并发稍微一上来，CPU 不高但 RT 飙升。一看火焰图，全在 Hashtable 的 put 方法上等着。换成 ConcurrentHashMap 之后，QPS 翻了一倍多。

ConcurrentHashMap 就是这两者的折中——既要线程安全，又要高并发。

## JDK 7 的设计：分段锁

JDK 7 的 ConcurrentHashMap 把数据分成 16 个 Segment（默认），每个 Segment 继承 ReentrantLock，相当于 16 把锁。

```java
// 伪代码：JDK 7 结构
ConcurrentHashMap {
    Segment[] segments;  // 默认 16 个
}

Segment extends ReentrantLock {
    HashEntry[] entries;  // 每个 Segment 里自己管自己的桶
}
```

写操作的时候，先算 key 落在哪个 Segment，然后只锁这个 Segment。其他 15 个 Segment 还能正常读写。这就是分段锁——锁粒度从全表缩小到 1/16。

读操作基本不加锁，HashEntry 的 value 和 next 都用 volatile 修饰，保证可见性。

JDK 7 的问题也很明显：
- Segment 数量初始化后固定，不能动态调整
- 扩容只在单个 Segment 内进行，如果某个 Segment 数据特别多（hash 不均匀），性能会下降
- 分段锁在竞争激烈的场景下，锁开销也不小

## JDK 8 的重写：CAS + synchronized

JDK 8 把 ConcurrentHashMap 彻底重写了。抛弃了 Segment 分段锁，改用 Node 数组 + CAS + synchronized。

结构上跟 HashMap 基本一致——数组 + 链表 + 红黑树，唯一的区别是多了并发控制。

```java
// JDK 8 简化结构
transient volatile Node<K,V>[] table;
private transient volatile int sizeCtl;
```

**put 流程大概是这样的：**

1. 算 key 的 hash，找到 table 中的位置 i
2. 如果位置 i 是 null，CAS 直接放进去——无锁操作，最快路径
3. 如果位置 i 不为 null，用 synchronized 锁住链表头节点（或红黑树根节点），然后插进去
4. 如果链表长度超过 8，转红黑树
5. 检查是否需要扩容

关键点在这：**大部分情况下，ConcurrentHashMap 的 put 是无锁的**。只有发生 hash 冲突的时候才加锁，而且锁的粒度是一个桶，而不是全表或者 Segment。

之前我团队里有个同事看到源码里用了 synchronized，问我"为什么不用 ReentrantLock？synchronized 不是很重吗？"

这就是刻板印象了。JDK 8 的 synchronized 已经做了大量优化——偏向锁、轻量级锁、锁升级机制。在低竞争场景下，synchronized 的开销比 ReentrantLock 小。Doug Lea 在重构时选 synchronized，是有充分理由的。

## 几个必须知道的源码细节

**sizeCtl 这个变量的妙用**

sizeCtl 是个 int，在不同的阶段代表不同的含义：
- 负数：表示正在初始化或扩容。具体来说，-1 表示正在初始化，-(1 + 扩容线程数) 表示正在扩容
- 正数：表示扩容阈值（table.length * 0.75）

一个变量管了初始化和扩容两件事，挺巧妙的。

**扩容时的多线程协助**

ConcurrentHashMap 的扩容支持多线程协助。当某个线程发现需要扩容时，它会把 table 分成多个任务段，每个线程负责一段的迁移工作。

```java
// 核心方法：transfer()
// 每个线程从任务队列里领一个 stride，搬完再领下一个
// 全部搬完才结束
```

其他线程在 put 的时候如果发现正在扩容，也会停下来帮忙（helpTransfer）。这个机制能保证扩容不会阻塞太久。

**get 为什么不需要加锁**

get 操作完全无锁，靠的是 volatile 保证可见性。Node 的 val 和 next 都是 volatile 修饰的，所以读线程能直接看到其他线程写入的最新值。

而且 ConcurrentHashMap 的 Node 里的 key 和 hash 在初始化后就不变了，不存在读写不一致的问题。

## 高频面试题

**Q: put 流程完整说一遍**

把上面那段"hash 找位置 → CAS 插入 → 冲突加 synchronized → 链表转红黑树 → 检查扩容"说清楚就行。能顺手提一下 sizeCtl 的含义更加分。

**Q: get 为什么不用加锁？**

因为 volatile 保证了 val 和 next 的可见性，且 key 和 hash 不可变。

**Q: 1.7 和 1.8 有什么区别？**

三个核心区别：锁机制（分段锁 vs CAS+synchronized）、数据结构（Segment+HashEntry vs Node+红黑树）、扩容机制（单 Segment 扩容 vs 多线程协助扩容）。

**Q: 并发计数器怎么实现的？**

JDK 8 用 CounterCell 数组来降低计数冲突。多个线程同时加计数时，分散到不同的 CounterCell 上，最后 sum 的时候把所有 Cell 的值加起来。这其实跟 LongAdder 的思路一样。

**Q: ConcurrentHashMap 的迭代器是强一致性还是弱一致性？**

弱一致性。迭代器遍历时，如果其他线程修改了数据，迭代器不会抛 ConcurrentModificationException，但也不保证能读到最新的数据。这是为了性能做的权衡。

## 实战经验

之前我做一个本地缓存组件，用 ConcurrentHashMap 存储热点配置数据。当时想的是"CHM 线程安全，直接 put/get 就行了吧"。压测时发现问题了——某些配置失效需要重新加载时，多个线程同时发现失效，同时去加载，同时 put 进去。虽然没有数据错误，但做了很多无用功。

后来加了 computeIfAbsent：

```java
// 保证只有一个线程执行加载逻辑
configCache.computeIfAbsent(key, k -> loadConfig(k));
```

computeIfAbsent 内部用了 CAS + synchronized，保证同一个 key 只有一个线程执行加载函数，其他线程等待结果。既节省资源，又避免了缓存击穿。

另一个坑是 size() 方法。ConcurrentHashMap 的 size() 在并发下不是一个精确值，而是一个估计值。如果你需要精确的计数，建议用 LongAdder 单独维护一个计数器，或者在 compute 里做原子操作。

---

ConcurrentHashMap 是我在生产环境用得最多的并发容器。它的设计思路——CAS 兜底、锁粒度降到最低、多线程协助扩容——放到今天看依然非常值得学习。

📌 本文是「面向面试之 并发编程」系列第 5 篇，前一篇：[CompletableFuture 异步编程——告别 Callback 地狱](https://dushi.tech/posts/concurrency-4-completablefuture/)，后一篇：Java 并发工具类——CountDownLatch、CyclicBarrier、Semaphore 的全场景解析，敬请期待。
