## 面试重点：LRU 算法——Redis 的实现和教科书不一样的

LRU（Least Recently Used）是最常用的淘汰策略，但很多面试者在"手撕 LRU"这道题上翻车。

先搞清楚标准 LRU 是什么。

### 教科书版 LRU

标准 LRU 需要维护一个访问有序链表：

- 每次访问一个 key，把它移到链表头部
- 链表尾部就是最近最少访问的 key
- 内存不够时，淘汰尾部

这个实现很直观，但有代价：每次访问都要移动节点，Redis 的单线程模型扛不住这个开销。

### Redis 的近似 LRU

Redis 没用标准的 LRU，而是用了 **近似 LRU**（Approximated LRU）。

它的做法是：**不维护全局链表，而是随机采样一批 key，淘汰其中最旧的**。

具体来说：

1. 每个 key 记录一个 `lru` 字段（24 bits），存的是最近一次访问的时间戳（精度到秒，但不是完整时间戳，而是对某个基准值的差值）
2. 内存不够时，调用 `evict.c` 的淘汰逻辑
3. 从数据库里随机取 N 个 key（默认 5 个，由 `maxmemory-samples` 控制）
4. 淘汰这 N 个里 `lru` 值最小的

```c
// 伪代码示意
samples = 5  // maxmemory-samples
best_key = NULL
best_idle = 0

for i = 0 to samples:
    key = random_key_from_db()
    idle = current_time - key.lru
    if idle > best_idle:
        best_idle = idle
        best_key = key

evict(best_key)
```

这个做法有几个明显的好处：

- **O(1) 开销**：每次淘汰就取 5 个 key，不遍历全库
- **够用**：采样数越大越接近标准 LRU，但 5 个已经能覆盖 80% 的场景

你可以在 `redis.conf` 里调整 `maxmemory-samples`。默认 5，调到 10 会更精确，但 CPU 开销翻倍。线上我一般保持 5，除非你的 Redis 里 key 的冷热温差特别大。

### 近似 LRU 和标准 LRU 的差距有多大？

Redis 官方做过 benchmark：采样 5 个的情况下，近似 LRU 的淘汰准确率大约是标准 LRU 的 90% 左右。采样 10 个能到 95%。对于绝大多数缓存场景来说，这个精度完全够用——本来缓存淘汰就是个概率问题，没必要追求绝对精确。

## LFU 策略：4.0 之后的新选择

LFU（Least Frequently Used）是 4.0 引入的，淘汰的是"最不经常使用"的 key，而不是"最近最少使用"的。

什么场景用 LFU？举个真实的例子：

有一个推荐系统的缓存，每天零点有个定时任务大批量写入新的推荐结果，旧的结果很快就被覆盖了。按 LRU 的逻辑，前一天下午用户频繁访问的热门推荐——虽然累计访问了几万次——只要午夜新数据一涌入，就会被挤掉。而实际上这些数据可能第二天上午还有人看。

LFU 解决的就是这个问题：**它看的是累计访问频率，不是最后访问时间**。

Redis 的 LFU 实现也挺巧妙。它和 LRU 共用同一个 `lru` 字段（24 bits，没错，字段名字叫 lru，但 LFU 模式下存的不是时间戳了）。LFU 模式下把这个 24 bits 拆成两部分：

- 高 16 bits：上次衰减时间（以分钟为单位）
- 低 8 bits：访问频率计数器（线性增长很慢，需要经过一个对数因子来调整）

频率计数器不是简单的"访问一次 +1"，因为这样高频 key 的计数会增长到溢出。Redis 用了 **概率递增（MORRIS COUNTER）** 的方式：访问次数越多，计数器增长的概率越低。

```
// 通俗理解
第 1 次访问：加 1   的概率 100%
第 10 次访问：加 1  的概率 50%
第 100 次访问：加 1 的概率 10%
```

这样设计是为了让低频和高频区分开，而不让高频 key 的计数无限膨胀。

LFU 还有个衰减机制：如果某个 key 很久没被访问了，它的频率计数会随时间慢慢衰减。默认衰减速度是 1 分钟衰减一半（可配置）。

## 生产实践：几个踩过的坑

### 坑一：只设 maxmemory 不设策略

见过好几次了，配置里只写了 `maxmemory 2gb`，没写 `maxmemory-policy`。默认 `noeviction`，内存满了直接报错，线上事故就这么来的。

正确做法：

```
maxmemory 2gb
maxmemory-policy allkeys-lru
```

### 坑二：volatile-lru 和 allkeys-lru 搞混

如果你用 volatile-lru，但你的 key 有很多没设过期时间——那 Redis 删无可删，最后还是 `OOM`。典型的场景：用了 `SET` 不带 `EXPIRE`。

我一般直接推荐 allkeys-lru，省心。除非你明确知道有些 key 不能删（比如分布式锁的 key，虽然理论上过期了会被删）。

### 坑三：maxmemory-samples 调太高

有人追求"精确"把 `maxmemory-samples` 调到 50。结果内存淘汰的 CPU 消耗飙升，慢查询增多。对大多数场景来说 5-10 就够了。

### 坑四：忽略 INFO 里的淘汰指标

```bash
redis-cli info stats | grep evicted
```

`evicted_keys` 这个指标能告诉你每秒淘汰了多少 key。如果长期大于 0，说明你的内存配小了，或者缓存命中率在下降。这个指标应该加到监控报警里——当每秒淘汰数超过阈值时自动告警。

## 面试官可能会追问的问题

### 问：maxmemory 设为 0 是什么意思？

64 位系统下为 0 表示不限制内存，直到系统 OOM。32 位系统下为 0 表示最大 3GB。生产环境一定要手动设置。

### 问：淘汰策略可以动态改吗？

可以，不用重启：

```bash
redis-cli CONFIG SET maxmemory-policy allkeys-lru
redis-cli CONFIG SET maxmemory 4gb
```

但注意：从 `volatile-*` 切到 `allkeys-*`，可能会导致大量 key 被立即淘汰——因为之前只针对有过期时间的 key，现在针对全部了。

### 问：淘汰 key 会不会阻塞 Redis？

会的。Redis 是单线程，淘汰过程中要随机采样、计算、删除，整个过程阻塞其他命令。如果一次淘汰太多 key（比如从空内存瞬间写入大量数据），会导致明显的延迟毛刺。这也是为什么建议留 20% 内存余量——给淘汰过程一点缓冲空间。

## 总结

回到开头那个面试题：Redis 的 key 过期了，什么时候被删？

答案不是"到时间就删"，而是：

1. **惰性删除**：访问时发现过期了再删
2. **定期删除**：每隔 100ms 随机抽查一批过期 key 来删
3. **内存淘汰**：上面两个都搞不定了，内存快满了，用淘汰策略强行腾空间

这套组合拳保证了 Redis 在内存和 CPU 之间找到了一个平衡点。没有完美的方案，只有适合场景的方案。

LRU 在绝大多数情况下够用。如果你有特殊场景——比如某类 key 访问频率极高但峰值过后迅速冷却——可以考虑 LFU。选对策略，配好样本数，然后监控 `evicted_keys`，这就够了。

---

**本文是「面向面试之 Redis」系列第 6 篇**。本系列共 6 篇，到此完结。前面 5 篇：
- [五种数据类型的高级用法](https://tech-blog-auto.vercel.app/posts/redis-data-types-advanced-usage/)
- [分布式锁的 4 种实现方案](https://tech-blog-auto.vercel.app/posts/redis-distributed-lock-4-solutions/)
- [缓存穿透/击穿/雪崩](https://tech-blog-auto.vercel.app/posts/redis-cache-penetration-avalanche/)
- [Redis 持久化 RDB vs AOF](https://tech-blog-auto.vercel.app/posts/redis-rdb-aof-persistence/)
- [Redis 集群方案对比](https://tech-blog-auto.vercel.app/posts/redis-5-集群方案/)

---

**原文链接**: 
