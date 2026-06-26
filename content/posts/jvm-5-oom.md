+++
title = "面向面试之 JVM 系列五：OOM 场景大全——从堆溢出到元空间溢出"
slug = "jvm-5-oom"
keywords = ["OOM", "内存溢出", "排查"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之 JVM"
series_number = 5
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-06-26
+++

面试八股背了一堆，一到 OOM 的问题就卡壳？

不怪你。OOM 这个知识点太碎了——堆溢出、栈溢出、元空间、直接内存，面试官随便挑一个都能把你问住。

这篇把 JVM 里常见的 OOM 场景全盘过一遍，每个场景我会讲三样东西：报错长什么样、为什么会发生、怎么复现和排查。系列收尾，不留死角。

## 一、Java heap space——堆空间溢出

**报错信息**

```
java.lang.OutOfMemoryError: Java heap space
```

**原因**

堆里放不下新对象了。要么是对象太大（一次性加载了超大文件/图片），要么是对象只进不出（内存泄漏，List 里 add 了不 remove）。

**我踩过的坑**

之前做报表系统，有个定时任务每天凌晨导出全量数据。某天半夜报警，一看堆直冲 4G。查代码发现是查询不分页，几百万行全拉到内存里用 stream 处理。对象本身不大，但量太大。

**排查**

1. `jmap -histo:live <pid>` 看哪些类占内存最多
2. 用 `jhat` 或 MAT 分析 heap dump（-XX:+HeapDumpOnOutOfMemoryError）
3. 重点看 byte[]、char[]、Object[] 这些基础数组类型——哪个类产生了大量数组，基本就是元凶

**面试题要点**

- 和 Xmx、Xms 参数有关
- 问"怎么调"是虚的，问"怎么查"才是实的
- 大对象直接进入老年代（-XX:PretenureSizeThreshold）这个知识点可以顺带提

## 二、GC overhead limit exceeded——GC 太努力了

**报错**

```
java.lang.OutOfMemoryError: GC overhead limit exceeded
```

**原因**

JVM 花了超过 98% 的时间做 GC，但每次回收回来的堆空间不到 2%。死循环式 GC，应用程序基本停摆。

**和 Java heap space 的区别**

- Java heap space：堆满了，对象分配不出去，直接抛
- GC overhead limit：堆没满，但 GC 已经累垮了，JVM 觉得没必要再硬撑

**亲身经历**

曾经有个 ES 集群频繁 Full GC，节点每隔几分钟就断连。我一度以为堆不够大，把堆从 8G 调到 16G，结果更频繁了——Full GC 扫描范围更大。

后来看 GC 日志才发现，GC 每次回收量几乎为零，说明大部分对象都是存活的。这才是 GC overhead limit exceeded 的典型信号——不是堆太小，是存活对象太多且无法回收。

**排查**

- 用 `jstat -gcutil <pid> 1000` 看 GC 频率和耗时
- 重点关注 FGC/YGC 比值，如果 FGC 一秒一次，那多半是内存泄漏
- 加 `-XX:+PrintGCDetails -XX:+PrintGCDateStamps` 看 GC 日志

## 三、Metaspace——元空间溢出

**报错**

```
java.lang.OutOfMemoryError: Metaspace
```

**原因**

类的元数据放不下了。每个类加载到 JVM 时，它的结构信息（方法、字段、注解）会占用一块元空间内存。当动态生成大量类又没卸载时，就会溢出。

**什么场景会触发**

- CGLib / 动态代理疯狂生成代理类
- JSP 页面热加载（老项目常见）
- 频繁的 Lambda 表达式在大循环里构建

**一个典型反面案例**

我朋友公司的支付网关，上线后跑了 8 小时就 OOM。堆只用了 500M，但 Metaspace 飙到 1G+。

查代码发现他们用了 Groovy 脚本引擎来做规则热更新，每次规则变更就 `GroovyClassLoader.parseClass()` 一遍，旧的 ClassLoader 没释放——Metaspace 上的类元数据越积越多，直到撑爆。

**面试题要点**

- JDK8 之前叫 PermGen，JDK8 之后改为 Metaspace，默认是系统内存上限
- 可以用 `-XX:MaxMetaspaceSize` 限制
- 面试追问：PermGen 和 Metaspace 的区别？答：PermGen 是堆的一部分，有固定上限；Metaspace 在本地内存，默认只受系统内存限制

## 四、unable to create new native thread——线程数超限

**报错**

```
java.lang.OutOfMemoryError: unable to create new native thread
```

**原因**

操作系统层面的线程上限到了，JVM 没法再创建新线程。注意：这个不是堆内存问题，是系统资源问题。

**三大限制因素**

- OS 层面：`ulimit -u` 限制用户进程数
- 系统层面：`/proc/sys/kernel/threads-max` 限制全局线程数
- 内存层面：每个线程有自己的栈（-Xss），线程太多即使堆没用完，栈也会占满虚拟内存

**排查思路**

- `ps -eLf | grep java | wc -l` 看当前线程数
- `ulimit -a` 看系统限制
- `jstack <pid>` 看线程都在干什么（是业务线程还是 GC 线程、等待线程）

**OS 级别怎么查**

```
cat /proc/sys/kernel/threads-max
cat /proc/sys/vm/max_map_count
```

如果业务确实需要上万线程，那架构设计就有问题了——这个量级该用线程池或协程。

## 五、Direct buffer memory——直接内存溢出

**报错**

```
java.lang.OutOfMemoryError: Direct buffer memory
```

**原因**

NIO 的 DirectByteBuffer 分配了过多直接内存（堆外）。直接内存不走堆，所以 `-Xmx` 管不到它，但受 `-XX:MaxDirectMemorySize` 限制。

**谁在用直接内存**

- Netty
- NIO（FileChannel.map / ByteBuffer.allocateDirect）
- 序列化框架（Kryo、Protobuf 在某些配置下）

**踩坑故事**

接手过一个 IM 系统，高峰期 OOM。堆内存很正常，3G 堆只用了 1.5G。但 `top` 看 RES 占用 6G+，明显不对劲。

最后定位到是 Netty 的 ByteBuf 没有 release，直接内存泄漏了。Netty 的引用计数器（refCnt）为 0 才会回收，但代码里漏调了 release()，导致每个连接都多占了几十 KB 直接内存，连上一万路就是几百 MB。

**排查手段**

- `-XX:MaxDirectMemorySize` 设个合理上限（默认等于 Xmx）
- 用 `pmap <pid>` 看进程内存分布
- Netty 自带 `io.netty.leakDetectionLevel=paranoid` 泄漏检测

## 六、Stack overflow——栈溢出

**报错**

```
java.lang.StackOverflowError
```

**原因**

线程的调用栈太深了。方法调用在栈帧里压入，当方法递归太深或死循环调用时，栈帧把栈空间撑爆了。

**注意和 OOM 的区别**

StackOverflowError 是 Error 不是 OutOfMemoryError，但面试常把它和 OOM 放一起问，因为它也是内存区域的溢出。

**什么场景最常见**

- 递归没有终止条件
- JSON 序列化循环引用（A 引用 B，B 引用 A）
- ORM 懒加载导致的死循环（toString 里又调了关联对象）

**快速定位**

报错信息会直接打出调用栈，看最顶层就是炸掉的地方：

```
at com.example.User.toString(User.java:10)
at com.example.Order.toString(Order.java:20)
at com.example.User.toString(User.java:10)
...重复几百行
```

看到重复的行号，基本就是递归或循环调用。

## 七、Requested array size exceeds VM limit

**报错**

```
java.lang.OutOfMemoryError: Requested array size exceeds VM limit
```

**原因**

你试图创建一个超大的数组。不同平台上的限制不同，但大多数 JVM 实现里数组长度不能超过 `Integer.MAX_VALUE - 2`（约 21 亿）。

**实际场景**

做过数据导出的都知道，如果查询不分页，一次性 list 转 array 就可能触发这个。虽然代码是 List，但底层 toArray 会尝试分配一个足够大的连续内存块，内存碎片多的时候即使堆还有空间，也分配不出来。

**面试角度**

这个错误比较小众，但面试官问"Java 数组最大长度"的时候指的就是它。能答出来说明你读过 G1 源码或看过 JVM 规范。

## 八、总结——OOM 排查三板斧

不管什么类型的 OOM，排查套路就三句话：

1. **看报错信息**——堆溢出 / 元空间 / 直接内存 / 线程，报错里写得清清楚楚
2. **看 GC 日志**——`-XX:+PrintGCDetails -XX:+PrintGCDateStamps` 是必加参数
3. **看内存分布**——`jmap` + `jstat` + `top` 三板斧，外加 heap dump 分析

最终极的防守是上线前做好测试。压测跑一跑，OOM 的场景提前暴露，比线上报警强一百倍。

📌 本文是「面向面试之 JVM」系列第 5 篇，也是最终篇。前四篇讲了类加载、内存模型、GC 调优、性能分析，加上这篇 OOM 大全，JVM 面试的知识体系就够用了。
