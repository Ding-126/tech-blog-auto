+++
title = "面向面试之并发编程系列二：synchronized 和 Lock 底层原理——别再只回答"锁升级"了"
slug = "concurrency-2-synchronized"
keywords = ["synchronized", "AQS", "锁"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之 并发编程"
series_number = 2
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-07-03
+++

得承认一个现实：Java 并发面试问了十年的 synchronized 和 Lock 区别，大部分人还在背"锁升级"三个字。

面试官问：说说 synchronized 底层实现？

答：无锁 → 偏向锁 → 轻量级锁 → 重量级锁。

然后呢？没然后了。就像你只会说"Spring 是 IOC 容器"一样——对，但不够，面试官想听的是**他怎么做到的**。

这篇我把 synchronized 和 AQS 底层扒一遍，不废话，直接干活。

## 一、synchronized：不是你想的那么简单

### 1.1 字节码层面的实现

先看一个最简单的例子：

```java
public class SyncDemo {
    public synchronized void method() {
        System.out.println("hello");
    }
    
    public void block() {
        synchronized (this) {
            System.out.println("world");
        }
    }
}
```

用 `javap -v` 反编译看看：

- **同步方法**：flags 里多了 `ACC_SYNCHRONIZED`，JVM 执行时检查这个标识，有就获取 monitor。
- **同步代码块**：字节码里插入了 `monitorenter` 和 `monitorexit` 两条指令，注意 exit 会出现两次——正常执行一次，异常时再执行一次，保证锁一定释放。

这是字节码层面，再往下走，monitor 的实现依赖于操作系统的 Mutex Lock（重量级锁），但 JVM 做了大量优化，这就是"锁升级"的由来。

### 1.2 锁升级到底怎么升的？

HotSpot VM 里，每个对象有一个 **对象头（mark word）**，最低 2 位用来表示锁状态：

- **01 无锁 / 偏向锁**：再细分看第 3 位，1 是偏向锁，0 是无锁
- **00 轻量级锁**
- **10 重量级锁**
- **11 GC 标记**

面试加分点来了：锁升级是**单向的、不可逆的**。只能从偏向锁一路升到重量级锁，不会降级。为什么？因为一旦发生竞争，说明这个对象确实被多线程争抢了，没必要再降回去浪费时间。

我遇到过一个问题：项目用了偏向锁，结果线上压测时大量线程阻塞。原因很简单——偏向锁在**高竞争场景下**反而增加了撤销成本。JDK 15 之后默认禁用了偏向锁，你们可以想想为什么。

### 1.3 重量级锁：ObjectMonitor 内部结构

这是 synchronized 最后兜底的实现。每个 Java 对象关联一个 ObjectMonitor，核心结构：

```
ObjectMonitor {
    _owner          // 当前持有锁的线程
    _EntryList      // 等待获取锁的线程队列
    _WaitSet        // 调用 wait() 的线程队列
    _recursions     // 重入计数
}
```

流程：线程尝试 CAS 设置 _owner 为自己 → 失败则进入 _EntryList 阻塞 → 调用 wait() 进入 _WaitSet → notify() 回到 _EntryList 重新竞争。

这段自己画一下流程图，面试时手撕这个比背"锁升级"要高一个档次。

## 二、Lock（AQS）：你每天都在用，但可能没理解精髓

ReentrantLock、CountDownLatch、Semaphore，底层都是 **AbstractQueuedSynchronizer（AQS）**。

### 2.1 AQS 核心数据结构

```java
abstract class AbstractQueuedSynchronizer {
    volatile int state;              // 同步状态，0 表示无锁
    volatile Node head;              // CLH 队列头
    volatile Node tail;              // CLH 队列尾
}
```

state 的语义由子类定义：
- ReentrantLock：state 表示持有锁的次数（0=无锁，1=持有，N=重入 N 次）
- Semaphore：state 表示剩余许可数
- CountDownLatch：state 表示计数器的值

### 2.2 CLH 队列：不是普通的队列

AQS 的等待队列是 **CLH（Craig, Landin, Hagersten）锁**的变种，本质是一个双向链表。

前驱节点释放锁后，自旋检查前驱状态，而不是自己反复轮询。好处：**缓存友好**。每个线程只在自己的前驱节点上自旋，不会像自旋锁那样所有线程同时访问同一个变量，避免了缓存一致性风暴。

说实话，CLH 的名字面试时不用非背出来，但**双向队列 + 前驱通知**的机制一定要讲清楚。

### 2.3 独占模式获取锁流程（以 ReentrantLock 为例）

加锁核心逻辑：

1. `tryAcquire(arg)`：尝试 CAS 修改 state，成功直接返回
2. 失败 → `addWaiter(Node.EXCLUSIVE)`：把当前线程包装成 Node 加入队列尾部
3. `acquireQueued(node, arg)`：在队列里自旋或挂起
   - 如果自己是第二个节点，再试一次 CAS 获取锁
   - 失败检查前驱状态：前驱是 SIGNAL 就挂起；前驱是 CANCELLED 就跳过；否则设置前驱为 SIGNAL

释放锁：
1. `tryRelease(arg)`：state 减到 0，设置 exclusiveOwnerThread 为 null
2. 唤醒后继节点：`unparkSuccessor(h)`

### 2.4 一个我踩过的坑：tryLock 的公平性问题

```java
// 你以为这是公平的？
lock.tryLock(100, TimeUnit.MILLISECONDS) 
```

错！`tryLock` 默认是**非公平的**，即使你创建的是公平锁。源码里 `tryLock` 直接调用 `nonfairTryAcquire`。这意味着它上来就会 CAS 抢一把，抢不到才排队。

我当时排查一个"某线程长时间拿不到锁"的问题，就是因为一个线程用 `tryLock` 反复插队，导致排队等锁的线程活活饿死。方案：要么不用 `tryLock`，要么自己加排队逻辑。

## 三、面试题实战

### Q1：synchronized 和 ReentrantLock 怎么选？

我的建议：
- 不需要高级功能（超时、中断、多个 Condition），**优先 synchronized**
- 需要尝试获取锁、定时等待、公平锁，用 ReentrantLock
- JDK 版本越新，synchronized 优化的越好，性能差距基本没有了

### Q2：synchronized 是公平锁还是非公平锁？

**非公平的**。新来的线程可以和队列里的线程一起抢锁。原因是避免线程挂起和唤醒的开销——公平锁意味着每个线程都要先 park/unpark，这个成本比 CAS 高。

### Q3：说说偏向锁为什么被废弃？

JDK 15 默认禁用了偏向锁，JDK 21 正式移除了。原因：偏向锁的撤销逻辑复杂，在高并发应用中反而降低了性能。偏向锁的代码量和维护成本超过了它的收益。

## 总结

synchronized 是 JVM 级别的锁，通过对象头和 monitor 实现，适用于锁竞争不激烈的场景。

Lock 是 API 级别的锁，基于 AQS 实现，提供了更灵活的锁控制。

两者底层用的其实都是 CAS + park/unpark。

真正的加分项，不是背结论，而是能把 **mark word → 锁升级 → ObjectMonitor → AQS → CLH 队列**这条链路讲通。

📌 本文是「面向面试之 并发编程」系列第 2 篇

[系列索引]
1. [线程池核心参数与拒绝策略面试全解](https://...) ← 占位
2. [synchronized 和 Lock 底层原理](https://...) ← 当前
3. volatile 与 JMM
4. ThreadLocal 内存泄漏与弱引用
5. CAS 与 ABA 问题
6. 并发容器选型与最佳实践

