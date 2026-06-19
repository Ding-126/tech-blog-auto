# troubleshoot-2-内存泄漏

## 凌晨三点的报警

去年双十一大促压测那段时间，凌晨三点我被值班电话吵醒。运维兄弟语气很急："订单服务 OOM 了，Pod 连续重启三次，核心链路挂了。"

我爬起来打开电脑，先看了一眼监控大盘。Grafana 上的堆内存曲线像过山车——刚重启时只占 30%，然后以肉眼可见的速度往上爬，大概六小时后触顶，然后就 OOM Kill。重启又爬，反复循环。

这种"缓慢泄漏 + 周期性死亡"的模式，第一反应就是：**要么线程泄漏，要么对象泄漏**。

## 第一步：先止血，再查因

很多人一上来就 dump 堆快照、装 MAT，这不对。线上正在挂，你先要把它拉起来。

我当时的操作：

```bash
# 快速扩容，让流量分散
kubectl scale deployment order-service --replicas=6

# 加 JVM 参数，下次 OOM 自动 dump
kubectl set env deployment order-service \
  JAVA_OPTS="-XX:+HeapDumpOnOutOfMemoryError \
  -XX:HeapDumpPath=/data/dumps/ \
  -XX:MetaspaceSize=256m \
  -Xmx4g -Xms4g"
```

扩容六副本之后，单节点的压力降下来了，再触发 OOM 的时间窗口就长了，给我们留出排查空间。

加 `HeapDumpOnOutOfMemoryError` 是常规操作，但这里有个坑：**如果你没挂独立数据盘，Pod 重启后 dump 文件就丢了**。所以我提前配了 PVC 挂载，dump 文件落到持久卷上。

等了一个多小时，终于抓到了一个 dump 文件。好戏开始了。

## 第二步：用 MAT 揪出"吃内存大户"

拿到 dump 文件，下载到本地，用 Eclipse MAT 打开。

第一步不是瞎看，而是直接跑 **Leak Suspects Report**。MAT 的自动分析会告诉你最可疑的对象链条。

报告结果让我有点意外：

> Problem Suspect 1: 一个 `java.util.HashMap$Node[]` 实例占用了堆的 68%（约 2.7GB），由 `SessionManager` 持有。

点进去一看，`SessionManager` 里面有个 `ConcurrentHashMap`，存的居然是**用户的购物车快照**。每次用户在订单页操作一次，就往里塞一条，但**只有 put，没有 remove**。

## 第三步：确认泄漏点——不只看代码，要串链路

到这里，代码里的"罪魁祸首"基本锁定了。但我没急着改代码，我多做了两步验证：

**验证 1：GC Root 链路确认**

在 MAT 的 "Merge Shortest Paths to GC Roots" 里，排除所有软引用/弱引用，看一下这个 HashMap 为什么没被回收：

```
SessionManager.cartSnapshots
  └── ConcurrentHashMap (2.7GB)
       └── Entry[] (无数个 cartSnapshot 对象)
            └── cartId (String) → 都是唯一的
```

没有工具可以回收它。对象还活着，因为 `SessionManager` 是 Spring 管理的 `@Service` 单例，只要应用不挂，它就一直在。

**验证 2：数量和时间分布**

MAT 的 **Histogram** 里，我看了一下 `cartSnapshot` 对象的数量，大约 42 万个。结合压测跑了 6 个小时，算下来：

> 42 万 / (6h × 3600s) ≈ 每秒新增 20 个对象

跟 QPS 对比一下，这个服务的 QPS 高峰在 2000 左右，说明**不是每次请求都创建泄漏对象**。回头翻代码，那 20/s 对应的是"提交订单前、用户修改购物车"的场景。

这个小验证虽然没改变结论，但让我对泄漏的「严重程度」有了底——它不是灾难级的（每秒泄漏几百兆），但跑 6 小时照样撑爆 4G 堆。

## 第四步：源码修复——怎么拆这颗雷

找到代码，问题就在 `SessionManager` 里：

```java
@Component
public class SessionManager {
    private final ConcurrentHashMap<String, CartSnapshot> cartSnapshots = new ConcurrentHashMap<>();

    public void snapshotCart(String sessionId, Cart cart) {
        // 每次用户操作购物车都存一份快照
        CartSnapshot snapshot = CartSnapshot.from(cart);
        cartSnapshots.put(sessionId + ":" + System.currentTimeMillis(), snapshot);
        // ⚠️ 没有清除逻辑！
    }
}
```

这个 key 里带时间戳，每次都不一样，所以 HashMap 永远只增不减。

修复方案要考虑业务场景。这个快照是用来做"用户误操作回退"用的，业务上只保留最近 5 次就够了。我改成了 **LRU 缓存**：

```java
@Component
public class SessionManager {
    private final Cache<String, CartSnapshot> cartSnapshots = Caffeine.newBuilder()
        .maximumSize(5000)
        .expireAfterWrite(30, TimeUnit.MINUTES)
        .build();

    public void snapshotCart(String sessionId, Cart cart) {
        String key = sessionId + ":" + System.currentTimeMillis();
        cartSnapshots.put(key, CartSnapshot.from(cart));
    }

    public CartSnapshot getSnapshot(String key) {
        return cartSnapshots.getIfPresent(key);
    }
}
```

用 Caffeine 替换手写的 `ConcurrentHashMap`，限制最大 5000 个条目，超时 30 分钟自动过期。

**这里有个选择：** 为什么不用 Guava Cache？不是不能用，Caffeine 在淘汰策略的吞吐量上比 Guava 好一个数量级（基于 W-TinyLFU），这个场景 QPS 2000，Guava 也能扛，但团队内部已经统一用 Caffeine 了，保持一致更好。

## 第五步：验证修复

代码改完，我没直接上线。先在压测环境复现场景：

1. 模拟 2000 QPS 混合流量
2. 持续跑 8 小时（比之前触发 OOM 的 6 小时还多 2 小时）
3. 监控堆内存曲线

结果对比很直观：

| 指标 | 修复前 | 修复后 |
|

---

发布于：2026-06-19

原文链接：

|> 更多技术干货，欢迎关注公众号「后端实战笔记」
