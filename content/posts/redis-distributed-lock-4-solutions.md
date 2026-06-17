+++
date = '2026-06-17T10:51:11+08:00'
draft = false
title = '面向面试之 Redis 系列二：分布式锁的 4 种实现方案——从 setnx 到 Redlock'
description = 'Redis 分布式锁从 setnx 到 Redlock 的完整演进。4 种方案对比：SETNX+EXPIRE 的坑、SET NX EX 原子命令、Redisson WatchDog 自动续期、Redlock 多节点共识。附 Java 代码，面试必考。'
tags = ['Redis', '分布式锁', 'Redisson', 'Redlock', 'setnx', 'Java后端', '面试题', '进阶']
categories = ['tutorial']
source_url = ''
source_name = 'original'
difficulty = '进阶'
target_keywords = '分布式锁, Redisson, Redlock, setnx, Redis 锁, 面试题, 分布式系统'
series_name = '面向面试之 Redis'
series_number = 2
series_total = 6
+++

## 开头

聊分布式锁之前，先问一句：你项目里的分布式锁真的安全吗？

我之前面试过一个候选人，他说他们用 Redis 做分布式锁，问怎么实现的，回答"就 SETNX 加个 EXPIRE"。再问他如果 SETNX 成功但 EXPIRE 没执行怎么办？愣了一下。

这其实是最经典的面试题之一。分布式锁看着简单，坑一个比一个深。从最原始的 `SETNX` 到 Redis 官方推荐的 Redlock，这中间经历了 4 个阶段的演进。

今天我把这 4 种方案掰开揉碎了讲，每种方案的原理、代码、坑点、面试怎么答，一次性说清楚。

## 为什么不用 JVM 锁？

先解决一个基础问题：为什么不用 `synchronized` 或 `ReentrantLock`？

JVM 锁只在单进程内生效。你的应用部署了 3 个实例，用户 A 的请求打到实例 1，用户 B 的请求打到实例 2，两个实例同时执行一段需要互斥的代码——JVM 锁管不了跨进程的资源竞争。

具体的场景：秒杀扣库存、定时任务防止重复执行、分布式事务中的资源锁定。这些场景必须用分布式锁，锁的是**所有实例之间共享的资源**。

好，进入正题。

## 方案一：SETNX + EXPIRE（坑最多的方案）

这是最原始、也是最容易出事故的方案。

### 现象

你肯定也遇到过这种情况：线上突然出现大量重复的定时任务执行。明明加了锁，怎么还是重复了？

查日志发现：实例 A 执行 `SETNX lock_key "value"` 成功，拿到了锁。但是还没来得及执行 `EXPIRE lock_key 30`，进程挂了。锁永远不释放，其他实例永远拿不到锁。

### 原因

`SETNX` 和 `EXPIRE` 是两条独立的命令，不具备原子性。

```redis
> SETNX lock_key "instance_1"   # 成功返回 1
(integer) 1
> EXPIRE lock_key 30            # 命令还没执行，进程挂了
```

Redis 不会把两条命令作为一个事务处理。SETNX 成功但 EXPIRE 失败的场景下，这把锁就变成了一把**永不过期的死锁**。

### 解决方案（有条件的改进）

如果你非要用这种方案，至少把两条命令合并成一条原子命令，用 `SET` 加上 NX 和 EX 参数：

```redis
> SET lock_key "instance_1" NX EX 30
OK
```

这个命令等价于 SETNX + EXPIRE 的原子版本。要么同时成功，要么同时失败，不会出现锁不释放的问题。

但你以为这就够了？还有一个大坑——**谁解锁**的问题。

### 解错锁的灾难

我第一次用的时候也犯过这个错：解锁代码直接 `DEL lock_key`。

```java
// 错误示例：可能解掉别人的锁
if (jedis.get("lock_key").equals(myValue)) {
    jedis.del("lock_key");  // 不是原子的！
}
```

为什么有问题？进程 A 拿到锁，锁的 TTL 是 30 秒。进程 A 业务执行了 35 秒（超过锁过期时间），锁自动释放了。进程 B 拿到锁。此时进程 A 终于执行完了，执行 `DEL`——把进程 B 的锁给解了。

这就是经典的**锁超时导致误删**问题。

正确的做法是用 Lua 脚本保证"查值 + 判断 + 删除"的原子性：

```lua
-- unlock.lua：原子解锁脚本
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

```java
// Java 调用 Lua 脚本解锁
String luaScript = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end";
jedis.eval(luaScript, 
    Collections.singletonList("lock_key"), 
    Collections.singletonList(requestId));
```

这个方案能解决单机场景下的大部分问题，但它仍然有一个硬伤：**Redis 主从切换时锁会丢失**。主节点挂了，从节点还没同步锁信息，新的客户端就能轻松拿到锁。

我之前踩过这个坑。线上 Redis 主从切换，三秒之内两台实例同时写数据库，数据直接乱掉。修复花了一整晚。

## 方案二：SET NX EX + Lua 原子解锁（单实例方案）

这个方案其实是方案一的正确版本，很多小团队用这个就够了。

核心就是两点：

1. **加锁**：`SET key value NX EX ttl`（原子操作）
2. **解锁**：Lua 脚本保证"查值→比对→删除"的原子性

加锁的 Java 代码长这样：

```java
public boolean tryLock(Jedis jedis, String lockKey, String requestId, int expireSeconds) {
    // SET key value NX EX seconds 是原子操作
    String result = jedis.set(lockKey, requestId, 
        SetParams.setParams().nx().ex(expireSeconds));
    return "OK".equals(result);
}

public boolean unlock(Jedis jedis, String lockKey, String requestId) {
    String luaScript = 
        "if redis.call('get', KEYS[1]) == ARGV[1] then " +
        "  return redis.call('del', KEYS[1]) " +
        "else " +
        "  return 0 " +
        "end";
    Object result = jedis.eval(luaScript, 
        Collections.singletonList(lockKey), 
        Collections.singletonList(requestId));
    return Long.valueOf(1).equals(result);
}
```

`requestId` 用 UUID 或者 `IP:线程ID:时间戳` 的拼接，保证每个客户端持有的 value 是唯一的。解锁的时候只有 value 匹配才允许删除。

### 这个方案够吗？

分情况。

如果你的 Redis 是单节点部署（或者使用了 Redis 哨兵模式但可以接受秒级的锁丢失），这个方案够用。据我观察，国内很多日活百万级别的电商项目，用的就是这个方案。

但如果你需要强一致性的分布式锁——比如金融交易、账务系统——单实例方案不够。主从切换的那几百毫秒里，锁可能丢失。

我之前有一篇 Redis 系列一讲过五种数据类型的高级用法，那是基础。到了分布式锁这里，你得根据业务对一致性的要求选方案。

## 方案三：Redisson（生产环境最推荐）

我之前踩过那个主从切换的坑之后，就把分布式锁全换成了 Redisson。

Redisson 是 Redis 官方推荐的 Java 客户端，内置了分布式锁的全部实现。不需要你手写 Lua 脚本，API 和 JUC 的 `ReentrantLock` 几乎一样。

### 代码示例

```java
// Maven 依赖
// <dependency>
//     <groupId>org.redisson</groupId>
//     <artifactId>redisson</artifactId>
//     <version>3.27.1</version>
// </dependency>

Config config = new Config();
config.useSingleServer().setAddress("redis://127.0.0.1:6379");
RedissonClient redisson = Redisson.create(config);

RLock lock = redisson.getLock("myLock");

try {
    // 尝试加锁，最多等 10 秒，锁 30 秒后自动释放
    if (lock.tryLock(10, 30, TimeUnit.SECONDS)) {
        // 业务逻辑
        Thread.sleep(5000);
    }
} catch (InterruptedException e) {
    Thread.currentThread().interrupt();
} finally {
    // 释放锁
    lock.unlock();
}
```

### Redisson 的三大核心能力

**1. WatchDog 自动续期**

这是 Redisson 最有价值的功能。默认锁的 TTL 是 30 秒，业务没执行完怎么办？Redisson 会启动一个后台线程（WatchDog），每隔 10 秒检查一次锁是否还在持有。如果还在，就把 TTL 重新设为 30 秒。

我第一次用的时候特意测过这个机制：业务线程 sleep 60 秒，WatchDog 在第 10 秒、第 20 秒、第 30 秒分别续期，锁一直没释放。业务执行完后调用 `unlock()`，锁才真正释放。

**2. 可重入锁**

同一个线程可以多次获取同一把锁，内部用计数器 + 哈希结构实现。和 Java 的 `ReentrantLock` 语义一致。

**3. 自动 Lua 脚本托管**

加锁、续期、解锁全部封装在 Lua 脚本里，你只需要调 API。避免了手写 Lua 出 bug 的风险。

### Redisson 的缺点

Redisson 的方案和方案二本质一样——依赖单台 Redis 节点。主从切换时，如果从节点还没复制到锁信息，锁仍然会丢失。

只是 Redisson 靠 WatchDog 减少了"业务超时导致锁提前释放"的概率，但没有解决"主从切换导致锁丢失"的问题。

如果你的业务对一致性要求极高，需要方案四。

## 方案四：Redlock（Redis 官方分布式锁算法）

Redlock 是 Redis 作者 antirez 在 2015 年提出的分布式锁算法。核心思想：**多数同意**。

### 原理

不是在同一个 Redis 上加锁，而是在 N（通常 5）个独立的 Redis 节点上都加锁。只要超过半数（N/2 + 1）的节点加锁成功，就认为拿到了锁。

### 完整流程

```
客户端获取当前时间 T1
依次向 5 个 Redis 节点发送 SET NX EX 命令
统计加锁成功的节点数（≥3 就算成功）
如果成功，计算总耗时 = 当前时间 - T1
检查总耗时是否小于锁的 TTL
如果小于，锁有效；如果大于，解锁所有节点（锁已经过期了）
```

```java
// Redisson 使用 Redlock
Config config1 = new Config();
config1.useSingleServer().setAddress("redis://node1:6379");
Config config2 = new Config();
config2.useSingleServer().setAddress("redis://node2:6379");
Config config3 = new Config();
config3.useSingleServer().setAddress("redis://node3:6379");
Config config4 = new Config();
config4.useSingleServer().setAddress("redis://node4:6379");
Config config5 = new Config();
config5.useSingleServer().setAddress("redis://node5:6379");

RedissonClient redisson1 = Redisson.create(config1);
// ... 创建其他客户端

RedissonRedLock redLock = new RedissonRedLock(
    redisson1.getLock("lockKey"),
    redisson2.getLock("lockKey"),
    redisson3.getLock("lockKey"),
    redisson4.getLock("lockKey"),
    redisson5.getLock("lockKey")
);

try {
    if (redLock.tryLock(10, 30, TimeUnit.SECONDS)) {
        // 5 个节点中至少 3 个加锁成功
        // 执行业务...
    }
} finally {
    redLock.unlock();
}
```

### 争议

Redlock 其实不是没有争议的。2016 年分布式系统专家 Martin Kleppmann 发表过一篇文章批判 Redlock，指出它依赖了"时钟假设"——假设所有 Redis 节点的时钟是同步的。如果某个节点时钟发生跳跃，Redlock 的安全性就被破坏了。

antirez 也写了文章回应，争论的核心在于"分布式锁到底需要多强的一致性"。

我的建议：如果你的系统已经很复杂了（比如用了 ZK 做协调），直接用 ZooKeeper 的临时顺序节点做分布式锁。ZK 的强一致性模型天然适合这种场景。但如果你本身就在用 Redis，不想额外引入 ZK，Redlock 是你能找到的最好的 Redis 方案。

## 四种方案对比

| 方案 | 原子性 | 容错性 | 性能 | 推荐场景 |
|------|--------|--------|------|----------|
| SETNX+EXPIRE | ❌ 无 | ❌ 无 | 高 | 别用 |
| SET NX + Lua | ✅ 有 | ❌ 主从丢锁 | 高 | 中小项目，非关键路径 |
| Redisson | ✅ 有 + 自动续期 | ❌ 主从丢锁 | 高 | 绝大多数生产环境 |
| Redlock | ✅ 有 | ✅ 容忍多数节点挂 | 中（5 次网络） | 金融、账务、高一致性场景 |

## 面试常见追问

面试官问到这里，一般还会追三个问题：

**Q1：锁的 TTL 设多少合适？**

没有标准答案。得根据业务执行时间来评估。定时任务一般设 30~60 秒。我习惯初始值设 30 秒，配合 Redisson 的 WatchDog 自动续期。如果业务平均执行 5 秒，续期任务基本不会触发，也不影响性能。

**Q2：锁冲突的时候怎么处理？**

不要自旋死等。建议用"快速失败 → 异步重试 → 人工兜底"的策略。秒杀场景下 90% 的请求在 5ms 内能拿到锁，拿不到的走降级流程，不要阻塞在 `while(true)` 循环里。

**Q3：Redis 分布式锁和 ZooKeeper 锁对比？**

ZK 更重但一致性更好。Redis 锁追求**高可用**，ZK 锁追求**强一致**。Redis 加锁一次大约 1ms，ZK 因为 ZAB 协议需要 10ms~50ms。追求性能选 Redis，追求一致性选 ZK。

---

📌 本文是「面向面试之 Redis」系列第 2 篇
• 上一篇：[Redis 系列一：五种存储类型的高级用法](/posts/redis-data-types-advanced-usage/)
• 下一篇：[Redis 系列三：缓存穿透/击穿/雪崩](/posts/redis-cache-penetration-avalanche/)（待发布）
