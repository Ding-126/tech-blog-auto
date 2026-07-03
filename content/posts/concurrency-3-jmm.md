+++
title = "面向面试之并发编程系列三：Java 内存模型（JMM）与 volatile 的可见性秘密"
slug = "concurrency-3-jmm"
keywords = ["JMM", "volatile", "可见性"]
difficulty = "进阶"
target_length = 2000
series_name = "面向面试之 并发编程"
series_number = 3
series_total = 6
draft = false
categories = ["tutorial"]
date = 2026-07-04
+++

## 一段让你后背发凉的 Bug

有次周四晚上，同事找我排查一个奇怪的问题：一个标记变量用了 `boolean` 类型，主线程改了值，子线程死活看不到。

```java
class TaskRunner {
    private boolean running = true;
    
    public void run() {
        new Thread(() -> {
            while (running) {
                // 执行业务逻辑
            }
        }).start();
    }
    
    public void stop() {
        this.running = false;
    }
}
```

代码读三遍都没毛病。但 `stop()` 调了之后，子线程继续跑，像没听见一样。

这就是**可见性**问题——一个线程的修改，另一个线程看不见。

这个坑我踩过一次之后，再也不敢裸用普通变量做线程间通信了。

## 从 CPU 到 JMM：到底是谁看不见

要理解为什么看不见，先看硬件层面。

现代 CPU 有多级缓存：

```
CPU 内核 → L1/L2 缓存 → 主内存
```

线程 A 改了 `running = false`，这个修改可能先落在 L1 缓存里，还没来得及写回主内存。线程 B 读 `running` 的时候，读的是自己缓存里的副本——还是 `true`。

这就是可见性问题的根因：**每个线程有自己的缓存副本，没有机制保证即时同步**。

Java 内存模型（JMM）就是为解决这个问题而生的。它定义了一套规则，规定了线程之间如何通过主内存来共享数据。

JMM 的核心抽象就两句话：

1. **所有共享变量存在主内存中**
2. **每个线程有自己的工作内存（可以理解为 CPU 缓存的抽象）**

线程不能直接操作主内存的变量，必须先把变量复制到自己的工作内存，操作完再刷回主内存。

所以问题很明显：**刷回主内存的时间点是不确定的**。

## volatile 到底干了什么

继续看 `volatile` 关键字。它做的事情比大多数人以为的要多。

给变量加上 `volatile` 之后：

```java
private volatile boolean running = true;
```

修改 `running` 的线程，会把新值**强制立即写入主内存**。
读取 `running` 的线程，每次**强制从主内存重新读取**。

也就是说，`volatile` 干了三件事：

1. **禁止指令重排序**：编译器不会把 `volatile` 变量的读写操作乱序排列
2. **保证可见性**：写操作立即刷主存，读操作每次都从主存拿
3. **不保证原子性**：这个后面细讲

加了这个关键字，刚才那段代码问题就解决了。

但你知道面试官最喜欢问什么吗？——**"volatile 能替代 synchronized 吗？"**

不行。因为 volatile 只保证可见性和有序性，不保证原子性。

```java
private volatile int count = 0;

// 两个线程同时执行
count++;
```

你以为是两次加 `1` 变成 `2`，但 `count++` 实际上是三步操作：读取 → 加 1 → 写入。如果两个线程同时读取到 `0`，都加 `1`，最后写回 `1`——丢了 `1`。

这就是原子性问题，`volatile` 管不了，得用 `synchronized` 或 `AtomicInteger`。

## happens-before 原则：面试必杀技

JMM 最核心的设计是 **happens-before** 原则。理解了这个，面试这块基本稳了。

简单说：如果操作 A happens-before 操作 B，那么 A 的结果对 B 是可见的。

官方定义了 8 条规则，我挑最重要的几条说：

**1. 程序次序规则**
同一个线程里，写在前面的代码 happens-before 后面的代码。

**2. volatile 变量规则**
对一个 volatile 变量的写操作，happens-before 后续对这个变量的读操作。

这就解释了 volatile 为什么能保证可见性。

**3. 锁规则**
对一个锁的解锁（unlock）happens-before 后续对这个锁的加锁（lock）。

**4. 传递性**
如果 A happens-before B，B happens-before C，那么 A happens-before C。

这三条规则组合起来，基本上能推导出所有并发场景下的可见性结论。

## 双检锁单例：一个 volatile 的经典战场

来看看最经典的面试题——双重检查锁定的单例模式。

```java
public class Singleton {
    private static volatile Singleton instance;
    
    private Singleton() {}
    
    public static Singleton getInstance() {
        if (instance == null) {
            synchronized (Singleton.class) {
                if (instance == null) {
                    instance = new Singleton();
                }
            }
        }
        return instance;
    }
}
```

如果不加 `volatile`，`instance = new Singleton()` 这行代码，在 JVM 层面有三个步骤：

1. 分配内存空间
2. 初始化对象（调用构造方法）
3. 将引用赋值给变量

问题是，**JIT 编译器可能把 2 和 3 重排序**——先赋值再初始化。这时候另一个线程进来，发现 `instance != null`，直接返回了一个还没初始化完成的对象。

加了 `volatile`，禁止了构造方法前后的重排序，保证赋值之前对象一定是初始化完的。

## 我的一般原则

日常开发中，什么时候用 `volatile`？

**适合的场景**：
- 状态标记变量（`running`、`shutdown` 这种）
- 一次性发布（单次赋值且后续只读）
- 写入不依赖当前值的场景

**不要用 volatile 的场景**：
- 需要原子性（`count++` 类操作）
- 多变量之间存在不变式约束
- 你需要在一次操作里完成读-改-写

第一条踩过坑之后，我固定了一个习惯：**任何被多个线程访问的共享变量，要么 volatile、要么 AtomicXXX、要么加锁。从来不裸用。**

## 小结

JMM 是 Java 并发编程的底层基础。理解它不只是为了面试——日常写多线程代码时，面对"为什么我看不到别人改的值"这种问题，你不会两眼一抹黑。

本文只讲了可见性和 volatile，下篇聊 `synchronized` 的底层原理和锁升级机制，这个在面试里出镜率也极高。

📌 本文是「面向面试之 并发编程」系列第 3 篇，共 6 篇。
