+++
date = '2026-06-18T14:00:00+08:00'
draft = false
title = '面向面试之 Redis 系列七（番外）：缓存与数据库一致性——4 种方案从简单到可靠'
description = '缓存和数据库双写不一致是面试高频题，也是线上最常见的坑。本文用场景+代码+方案对比，从最简单的 Cache Aside 到最可靠的 Canal 监听 Binlog，一次讲清楚。'
tags = ['Redis', '缓存一致性', '双写一致性', 'Canal', 'MySQL', '缓存设计', 'Java后端', '面试题']
categories = ['tutorial']
source_url = ''
source_name = 'original'
difficulty = '实战'
target_keywords = '缓存一致性, 双写一致性, 缓存与数据库, Canal, 最终一致性'
series_name = '面向面试之 Redis'
series_number = 7
series_total = 8
+++

## 开头

说实话，我第一次遇到缓存和数据库数据不一致的时候，整个人是懵的。

用户修改了自己的昵称，数据库里已经更新了，但页面刷新了好几次还是旧的。查了半天发现是缓存没更新，手动清了 Redis 的 key 才恢复。领导问我"为什么会出现这种问题"，我支支吾吾说"缓存更新顺序有问题"——其实当时自己都没搞明白。

后来深入研究才发现，**缓存和数据库之间从来就没有天然的一致性**。不管你怎么设计，只要同时读写一个数据，就可能有时间差导致不一致。

今天这篇把缓存与数据库一致性的 4 种常见方案从头到尾讲透，从最简单的到最可靠的，每个方案的坑和适用场景都说清楚。

## 核心结论先行

- **Cache Aside Pattern**：最通用方案，读的时候先查缓存再查 DB，写的时候先更新 DB 再删缓存
- **延迟双删**：在 Cache Aside 基础上加一个延迟删除，降低并发写时的不一致概率
- **消息队列最终一致性**：写 DB → 发消息 → 消费消息删缓存，引入重试机制确保最终一致
- **Canal + Binlog**：最可靠方案，监听 MySQL Binlog 变更，异步同步到 Redis，对业务代码零侵入

## 为什么缓存和数据库会不一致？

先搞清楚问题本质。

正常流程应该是这样的：

```
用户修改昵称 → 更新数据库 → 更新或删除缓存 → 下次读取时缓存刷新
```

但只要涉及两个存储系统，就一定有时间窗口：

```
线程A：更新 DB → （还没来得及删缓存）
线程B：读缓存 → 读到旧数据
```

这就是不一致。时间窗口再短，只要存在就有可能触发。

常见的有三种场景：


其中**最经典也最容易踩的坑是"先删缓存再更新 DB"**。我第一次做缓存更新时就掉进去了。

## 方案一：Cache Aside Pattern（最通用）

### 怎么做的

读：

```java
// 1. 先查缓存
String value = redis.get(key);
if (value != null) {
    return value;  // 缓存命中，直接返回
}
// 2. 缓存没命中，查数据库
value = db.query(key);
// 3. 写回缓存
redis.set(key, value);
return value;
```

写：

```java
// 1. 更新数据库
db.update(data);
// 2. 删除缓存（不是更新缓存）
redis.del(key);
```

### 为什么是删缓存而不是更新缓存？

这就是关键所在。如果你更新缓存：

```
线程A：更新 DB → 更新缓存为值A
线程B：更早的请求，更新缓存为值B（比A晚执行，把A覆盖了）
```

结果缓存里是旧值 B，DB 里是新值 A——不一致了。

**删缓存的话，下次读取时缓存 MISS，自然会去 DB 拿最新的值。**

### 有什么问题

Cache Aside 在单线程场景下完全没问题。但在高并发下：

```
线程A：更新 DB → 删缓存（删得慢）
线程B：读 MISS → 从 DB 读旧数据 → 写回缓存
```

线程B 把旧数据写进了缓存，之后一直读到旧的。这个时间窗口很短，但有。

## 方案二：延迟双删（Delayed Double Delete）

### 怎么做的

在 Cache Aside 的基础上，删完缓存后等几百毫秒再删一次：

```java
// 1. 更新数据库
db.update(data);
// 2. 第一次删缓存
redis.del(key);
// 3. 等待一段时间（500ms-1s）
Thread.sleep(500);
// 4. 第二次删缓存
redis.del(key);
```

### 原理

第二次删除覆盖了那个"读 MISS 写回旧缓存"的时间窗口。500ms 的延迟足够让绝大多数并发读操作完成。

### 适用场景

- 并发量不是特别大（QPS < 5000）
- 可以容忍短暂的不一致（500ms 内读到旧数据）
- 不希望引入额外的组件（消息队列、Canal）

之前我参与的一个电商项目用的就是延迟双删。用户修改收货地址后，大约 300ms 内可能看到旧地址，之后就对了。产品经理说 300ms 可以接受。

### 这个方案的坑

- 延迟时间要选对。太短覆盖不了并发窗口，太长影响体验。一般 500ms-1s 比较合理
- 如果第二次删除失败了，还是会不一致——所以最好配合重试机制
- 这不是强一致方案，是最终一致

## 方案三：消息队列最终一致性

### 怎么做的

把"删缓存"这个动作从同步变成异步，通过消息队列保证一定会执行：

```java
// 写流程
public void updateData(data) {
    // 1. 更新数据库
    db.update(data);
    // 2. 发消息到 MQ（包含需要删除的 key）
    mq.send(new CacheDeleteMessage(key));
}

// 消费者
@RabbitListener(queues = "cache-delete")
public void handleCacheDelete(CacheDeleteMessage msg) {
    try {
        redis.del(msg.getKey());
    } catch (Exception e) {
        // 删除失败，重新投递消息（MQ 自带重试）
        throw new AmqpRejectAndDontRequeueException(e);
    }
}
```

### 好处

- 重试机制：MQ 自带重试，不像方案二删失败了就失败
- 解耦合：写 DB 和删缓存不在同一个事务里，不会因为 Redis 超时拖慢 DB 操作
- 可追踪：消息记录了每次删除的日志，出问题能回溯

### 不足

- 引入了 MQ，架构复杂度增加
- 消息可能有延迟（几十毫秒到几秒不等）
- 极端情况下消息积压，不一致时间会拉长

## 方案四：Canal + Binlog（最可靠，零侵入）

### 怎么做的

这是目前生产环境我见过最可靠的方案。原理很简单：

1. MySQL 主库的 Binlog 记录了所有数据变更
2. Canal 伪装成 MySQL 从库，实时读取 Binlog
3. Canal 将变更推送到 MQ 或直接调用删除缓存的接口
4. 业务代码完全不需要处理缓存——只要写 DB 就行

```
业务代码 → 更新 DB
              ↓
         MySQL Binlog
              ↓
          Canal 监听
              ↓
         MQ / HTTP 回调
              ↓
          删除 Redis 缓存
```

### 代码层面

业务代码只需要正常写数据库：

```java
// 业务代码完全不用管缓存
public void updateNickname(Long userId, String newName) {
    userMapper.updateNickname(userId, newName);
    // 缓存的事？不用管，Canal 会处理
}
```

Canal 配置：

```properties
# canal.properties
canal.destinations = example
example.mysql.host = 127.0.0.1
example.mysql.port = 3306
example.mysql.username = canal
example.mysql.password = canal
example.mysql.slaveId = 1234
```

### 优势

- **零业务侵入**：已有的代码不需要任何修改
- **最可靠**：Binlog 是 MySQL 原生机制，不会丢数据
- **异步解耦**：不影响主流程性能
- **支持所有变更**：不只是代码层面的更新，手动修改数据库、数据迁移等操作也能感知

### 不足

- 部署运维成本高（需要 Canal 服务端 + 客户端）
- 数据变更到缓存更新有秒级延迟
- 增加了系统组件，出了问题排查链路变长

我之前在一个用户量比较大的社交平台用过 Canal 方案。线上跑了一年多没出过缓存不一致的问题。Binlog 监听 + 重试机制，基本做到了最终一致性 99.99%。

## 完整对比


## 效果验证

分享一个我验证不一致方案的测试脚本思路：

```java
// 开两个线程
ExecutorService pool = Executors.newFixedThreadPool(2);

// 线程1：不断地更新数据
pool.submit(() -> {
    for (int i = 0; i < 1000; i++) {
        db.update("key", i);
    }
});

// 线程2：不断地读缓存
pool.submit(() -> {
    for (int i = 0; i < 1000; i++) {
        String cached = redis.get("key");
        String dbVal = db.query("key");
        if (!cached.equals(dbVal)) {
            System.out.println("不一致！缓存=" + cached + " DB=" + dbVal);
        }
    }
});
```

用这个脚本跑不同方案，你就能直观看到不一致的频次和时间窗口。

## 扩展 & 进阶方向

- **读写锁 + 缓存**：强一致性方案（性能换一致性），适合金额类场景
- **Redis 分布式锁 + 双删**：在延迟双删上加锁，进一步缩小时间窗口
- **Canal + 自定义注解**：通过注解指定缓存 key 规则，让 Canal 客户端自动拼 key 删缓存

## 参考资料

- [Canal 官方文档](https://github.com/alibaba/canal)
- [Redis 事务与管道](https://redis.io/docs/manual/transactions/)
- [Cache Aside Pattern (Martin Fowler)](https://martinfowler.com/bliki/CacheAside.html)

---

📌 本文是「面向面试之 Redis」系列第 7 篇（番外）  
• 上一篇：[Redis 系列六：Redis 内存淘汰策略与 LRU 算法实现原理](/posts/redis-6-内存淘汰-lru/)  
• 下一篇：[Redis 系列八（番外）：大 Key 排查与性能调优——线上实战记录](/posts/redis-8-大Key排查与性能调优/)（待发布）
