# 面向面试之 Redis 系列三：缓存穿透/击穿/雪崩——别再搞混了，一次讲清楚

## 开头

说实话，我刚工作那会儿也分不清这三个"缓存兄弟"——穿透、击穿、雪崩。

面试被问的时候脑子里一团浆糊，只知道"都是缓存出问题了"。更惨的是，有一次线上真的发生了缓存雪崩，我看着告警群里的报错刷屏，手忙脚乱地重启服务，领导在旁边盯着，那滋味真不好受。

后来我花了一整个周末，把这三个概念的原理、场景、解决方案彻底梳理了一遍。从那以后不管是面试还是线上问题排查，都没再翻过车。

今天我把这套理解框架分享给你。**只要你记住一个口诀，这辈子都不会再搞混。**

## 先记一个口诀

这三个词听起来像兄弟，但问题本质完全不同：

> **穿透查不存在，击穿扛不住热 key，雪崩一批同时挂。**

- 缓存**穿透**：查的数据在 DB 和缓存里**都不存在**，每次请求都穿过缓存打到数据库。
- 缓存**击穿**：某个**热 key** 突然过期，大量并发请求直接打到数据库。
- 缓存**雪崩**：大量 key 在**同一时间过期**（或者 Redis 直接宕机），请求全涌向数据库。

记住了吗？接下来一个一个拆解。

## 一、缓存穿透

### 现象

你的 Redis 里没有数据，MySQL 里也没有数据。但是这个 key 的查询请求源源不断地打进来。

每次请求都穿过缓存直击数据库，数据库扛不住就挂了。

典型的场景：恶意攻击者伪造了一批不存在的用户 ID 请求你的接口。比如 ID = -1、ID = 999999999，这些数据缓存里肯定没有，数据库里当然也没有。

还有一种更隐蔽的情况：用户刚注册了一个账号，数据还没同步到缓存层，这时候有请求查这个新用户的个人信息——缓存没命中，数据库也没有，空转一圈。

### 怎么发生的？

流程很简单，你看一眼就知道问题在哪：

```
请求 → 查 Redis（未命中）→ 查 MySQL（未命中）→ 返回空（不回写）
第二次同样的请求 → 查 Redis（未命中）→ 查 MySQL（未命中）→ 返回空（不回写）
第三次 → 循环...
```

1. 请求到了，先查 Redis，没命中。
2. 接着查数据库，也没查到。
3. 返回空结果，**但是没有回写缓存**。
4. 下一个同样的请求来了，重复 1-3。
5. 假设某个恶意操作的 QPS 是 10000，DB 瞬间被打爆。

这里的根因是：**缓存没有缓存"空结果"**，导致不存在的数据每次都要穿透到数据库。

### 解决方案一：缓存空对象

既然查不到就不缓存，那我们反着来——**查不到也缓存，只不过缓存一个空值**。

```java
public String getUserInfo(String userId) {
    // 1. 查缓存
    String cacheValue = redisTemplate.opsForValue().get("user:" + userId);
    if (cacheValue != null) {
        // 缓存中有值，直接返回（可能是空对象标记 "NULL"）
        return cacheValue;
    }
    
    // 2. 缓存未命中，查数据库
    String dbValue = userMapper.getUserById(userId);
    
    if (dbValue == null) {
        // 3. 数据库也没有，缓存一个空对象，设置短过期时间
        //    防止恶意 key 长期占用内存
        redisTemplate.opsForValue()
            .set("user:" + userId, "NULL", 60, TimeUnit.SECONDS);
        return null;
    }
    
    // 4. 数据库有数据，写入缓存
    redisTemplate.opsForValue()
        .set("user:" + userId, dbValue, 3600, TimeUnit.SECONDS);
    return dbValue;
}
```

**优点**：实现简单，改动成本低，适合 key 数量有限、恶意攻击不算猛烈的场景。

**缺点**：如果攻击者伪造大量不同的 key，你的缓存会塞满"空对象"，白白浪费内存。而且这些空 key 的过期时间很短，一过期又要重新查数据库——形成了另一种形式的"周期性穿透"。

### 解决方案二：布隆过滤器（Bloom Filter）

布隆过滤器像一个"存在性检查员"——在查缓存之前先问它：这个 key 可能存在吗？

如果布隆过滤器说"不存在"，那就 100% 不存在，直接拒绝，不用查缓存也不用查数据库。如果它说"可能存在"，才继续后面的流程。

```java
public class BloomFilterCache {
    // 布隆过滤器预计插入 100 万条数据，误判率 1%
    private BloomFilter<String> bloomFilter = 
        BloomFilter.create(
            Funnels.stringFunnel(Charset.forName("UTF-8")),
            1000000, 0.01
        );
    
    @PostConstruct
    public void init() {
        // 启动时从数据库加载所有合法 key 到布隆过滤器
        List<String> allUserIds = userMapper.getAllUserIds();
        allUserIds.forEach(id -> bloomFilter.put("user:" + id));
    }
    
    public String getUserById(String userId) {
        String cacheKey = "user:" + userId;
        
        // 1. 布隆过滤器判断是否存在
        if (!bloomFilter.mightContain(cacheKey)) {
            // 一定不存在，直接返回
            System.out.println("布隆过滤器拦截非法请求: " + cacheKey);
            return null;
        }
        
        // 2. 查缓存
        String cacheValue = redisTemplate.opsForValue().get(cacheKey);
        if (cacheValue != null) {
            return cacheValue;
        }
        
        // 3. 查数据库
        String dbValue = userMapper.getUserById(userId);
        if (dbValue != null) {
            redisTemplate.opsForValue()
                .set(cacheKey, dbValue, 3600, TimeUnit.SECONDS);
        }
        return dbValue;
    }
}
```

**小提示**：布隆过滤器有**误判率**——它可能会说某个 key "可能存在"，但实际上不存在。但**它绝不会把存在说成不存在**。所以"不存在"的判断是 100% 可信的。

这个特性非常有用：我们可以放心地对"不存在"的判决策略做拦截，而不必担心误伤合法请求。

**推荐的做法**：空对象 + 布隆过滤器配合使用。布隆过滤器拦住大部分非法请求，空对象兜住布隆过滤器误判的那一小部分漏网之鱼。

## 二、缓存击穿

### 现象

一个**热 key**（比如微博热搜第一名、双十一的某个爆款商品），缓存里本来有，突然过期了。

这时候成千上万个请求同时来查这个 key，全都发现缓存里没数据，一窝蜂地涌向数据库。

数据库：你们是商量好的吗？

### 和穿透的区别

这是最容易搞混的两个概念，我一句话帮你区分：

- **穿透**：数据根本不存在，缓存和数据库都没有。
- **击穿**：数据是存在的，只是缓存恰好过期了，大家一瞬间同时来查。

所以穿透打的是"假数据"，击穿打的是"真数据但缓存过期了"。

### 怎么发生的？

热 key 的过期时间到了，Redis 自动删除了它。还没来得及回写新缓存，大量请求就涌进来了。

时间线是这样的：

```
T1 时刻：热 key "hot_item_1001" 缓存有效，返回正常
T2 时刻：热 key 过期，Redis 删除该 key
T3 时刻：1000 个请求同时到达，全都没命中缓存
T4 时刻：1000 个请求全部去打数据库
T5 时刻：数据库 CPU 100%，连接池耗尽，服务开始超时报错
```

### 解决方案一：互斥锁（Mutex Key）

让第一个线程去查数据库回写缓存，其他线程等着。等缓存写好了，后面的线程直接从缓存拿。

```java
public String getHotData(String key) {
    // 1. 查缓存
    String value = redisTemplate.opsForValue().get(key);
    if (value != null) {
        return value;
    }
    
    // 2. 缓存未命中，尝试获取分布式锁
    //    setIfAbsent 就是 SETNX，10 秒自动释放防止死锁
    String lockKey = "lock:" + key;
    Boolean locked = redisTemplate.opsForValue()
        .setIfAbsent(lockKey, "locked", 10, TimeUnit.SECONDS);
    
    if (Boolean.TRUE.equals(locked)) {
        try {
            // 3. 拿到锁，再次查缓存（双重检查，防止等待期间已有线程更新了缓存）
            value = redisTemplate.opsForValue().get(key);
            if (value != null) {
                return value;
            }
            
            // 4. 查数据库（只有一个线程会执行这里）
            value = database.query(key);
            
            // 5. 写回缓存
            redisTemplate.opsForValue()
                .set(key, value, 3600, TimeUnit.SECONDS);
        } finally {
            // 6. 释放锁
            redisTemplate.delete(lockKey);
        }
    } else {
        // 7. 没拿到锁，自旋等待 50ms 后重试
        try {
            Thread.sleep(50);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        return getHotData(key); // 递归重试
    }
    
    return value;
}
```

**优点**：保证只有一个线程查数据库，数据库压力完全可控。

**缺点**：有锁就有等待，会降低接口的吞吐量。而且如果拿到锁的线程在回写缓存之前挂了，锁没释放，就死锁了——所以**锁一定要设置过期时间**。

在实际工业级项目中，推荐用 Redisson 的 `RLock`，它内置了看门狗自动续期机制，比你手写 SETNX 安全得多。

### 解决方案二：逻辑过期

给缓存数据设置一个**逻辑过期时间**，而不是 Redis 的 TTL。

Redis 里这个 key 永不过期，但数据对象里带一个 `expireTime` 字段。每次读取时检查逻辑时间，如果快过期了，异步线程去刷新缓存。

```java
@Data
public class HotCacheData {
    private String data;          // 实际数据
    private long expireTime;      // 逻辑过期时间戳
}

public class LogicExpireService {
    private Executor asyncPool = Executors.newFixedThreadPool(5);
    
    public String getHotData(String key) {
        // 1. 查缓存（key 永不过期）
        HotCacheData cacheData = 
            (HotCacheData) redisTemplate.opsForValue().get(key);
        
        if (cacheData == null) {
            // 缓存还没初始化（第一次加载或者 Redis 重启），
            // 必须同步查数据库
            return loadFromDBAndCache(key);
        }
        
        // 2. 判断逻辑是否过期
        if (cacheData.getExpireTime() > System.currentTimeMillis()) {
            // 未过期，直接返回旧数据
            return cacheData.getData();
        }
        
        // 3. 逻辑过期了：异步刷新缓存
        //    当前线程仍然返回旧数据，保证接口不卡顿
        asyncPool.submit(() -> loadFromDBAndCache(key));
        
        return cacheData.getData();
    }
    
    private String loadFromDBAndCache(String key) {
        String data = database.query(key);
        HotCacheData cacheData = new HotCacheData();
        cacheData.setData(data);
        cacheData.setExpireTime(System.currentTimeMillis() + 3600_000);
        redisTemplate.opsForValue().set(key, cacheData);
        return data;
    }
}
```

**核心思想**：**用短暂的数据不一致，换取系统的可用性。**

即使当前用户看到的是 1 秒前的数据，也比接口超时或 500 强太多了。

**适用场景**：微博热搜、秒杀商品详情页、排行榜等对一致性要求不高但并发量巨大的场景。

## 三、缓存雪崩

### 现象

缓存雪崩比前两个都猛，因为它不是单个 key 的问题，而是大面积失效。

不是单个 key 挂了，而是一大片 key 同时过期，或者 Redis 本身就挂了。所有请求直接打到数据库，数据库瞬间被击穿。

想象一下，你的电商网站凌晨 0 点有秒杀活动，所有的商品 key 都设置了相同的过期时间——凌晨 0 点。0 点一到，所有 key 同时过期，数据库被秒成渣。

### 怎么发生的？

最常见的原因有三个：

**原因 1：统一过期时间**

开发图方便，所有缓存 key 都写死了 3600 秒过期。如果它们是在同一时刻写入的，那也会在同一时刻过期。

```bash
# 这种写法就是定时炸弹
SET product:1001 "{...}" EX 3600
SET product:1002 "{...}" EX 3600
SET product:1003 "{...}" EX 3600
# ... 10000 个商品都在同一秒过期
```

**原因 2：Redis 实例宕机**

缓存服务不可用了，所有请求直接穿透到数据库层。

**原因 3：缓存服务重启**

发布部署的时候，Redis 缓存被清空，还没来得及预热，流量就涌进来了。

### 解决方案一：过期时间加随机值

这是最便宜、最高效的方案。给每个 key 的过期时间加一个随机偏移值，让它们的过期时间错开。

```bash
# 错误示范：所有 key 同一秒过期
SET product:1001 "{...}" EX 3600

# 正确做法：基础时间 + 随机偏移
SET product:1001 "{...}" EX $((3600 + RANDOM % 600))
```

```java
// Java 实现
public void setProductCache(String productId, String data) {
    int baseExpire = 3600;  // 基础过期时间（秒）
    int randomOffset = new Random().nextInt(600);  // 随机偏移 0-600 秒
    
    redisTemplate.opsForValue()
        .set("product:" + productId, data, 
             baseExpire + randomOffset, TimeUnit.SECONDS);
}
```

加了这个随机值，10000 个 key 的过期时间会均匀分布在 3600-4200 秒之间，不会出现"集体过期"的情况。这个改动只需要一行代码，但效果立竿见影。

### 解决方案二：多级缓存

用本地缓存（Caffeine / Guava Cache）挡在第一层，Redis 在第二层，数据库在第三层。

即使 Redis 挂了，本地缓存还能扛住一部分流量，不至于让数据库裸奔。

```java
@Component
public class MultiLevelCache {
    // 一级缓存：Caffeine 本地缓存
    // 存储最近 10000 个热点数据，5 分钟自动过期
    private Cache<String, String> localCache = Caffeine.newBuilder()
        .maximumSize(10000)
        .expireAfterWrite(5, TimeUnit.MINUTES)
        .recordStats()
        .build();
    
    @Autowired
    private RedisTemplate<String, String> redisTemplate;
    
    public String get(String key) {
        // 1. 查本地缓存（毫秒级，无网络开销）
        String localValue = localCache.getIfPresent(key);
        if (localValue != null) {
            return localValue;
        }
        
        // 2. 查 Redis（有网络开销，但比查数据库快）
        String redisValue = redisTemplate.opsForValue().get(key);
        if (redisValue != null) {
            // 回填本地缓存，下次请求直接走本地
            localCache.put(key, redisValue);
            return redisValue;
        }
        
        // 3. 查数据库（最慢，最昂贵的操作）
        String dbValue = database.query(key);
        if (dbValue != null) {
            redisTemplate.opsForValue()
                .set(key, dbValue, 3600, TimeUnit.SECONDS);
            localCache.put(key, dbValue);
        }
        return dbValue;
    }
}
```

多级缓存的核心价值是**把流量层层拦截**：本地缓存挡 40% 的请求，Redis 挡 50%，只有 10% 落到数据库。就算 Redis 全挂了，还有 40% 的请求被本地缓存接住，服务不至于完全不可用。

### 解决方案三：Redis 高可用架构

针对"Redis 挂了"这种情况，本地缓存只能缓解，不能根治。要彻底解决，得上高可用架构：

- **主从 + 哨兵（Sentinel）**：主节点挂了，哨兵自动把从节点升级为主节点，秒级切换。
- **Redis Cluster**：数据分片存储，部分节点挂了不影响整体。
- **缓存预热（Cache Warming）**：系统上线前预先加载热点数据到缓存。可以用一个定时任务，在业务低峰期把数据库中的热点数据刷到 Redis。

```java
@Component
public class CacheWarmUp {
    
    @Autowired
    private RedisTemplate<String, String> redisTemplate;
    
    @PostConstruct
    public void warmUp() {
        // 系统启动后，预先加载热点商品到缓存
        List<HotProduct> hotProducts = productMapper.getTop1000HotProducts();
        
        for (HotProduct product : hotProducts) {
            String key = "product:" + product.getId();
            String value = JSON.toJSONString(product);
            
            // 设置随机过期时间，避免同时过期
            int expire = 3600 + new Random().nextInt(600);
            redisTemplate.opsForValue()
                .set(key, value, expire, TimeUnit.SECONDS);
        }
        
        System.out.println("缓存预热完成，共加载 " + hotProducts.size() + " 个热点商品");
    }
}
```

## 四、面试高频追问

面试官问完这三个概念后，通常不会轻易放过你。以下是我被问过的高频追加问题：

**Q1：缓存穿透、击穿、雪崩同时发生了怎么办？**

先按影响范围排序：雪崩 > 击穿 > 穿透。先加限流兜底（比如 Sentinel 或 Guava RateLimiter），确保数据库不会被打死，然后逐个排查根因。

限流不是万能的，但它能在你定位问题的时候给系统争取时间。

**Q2：布隆过滤器支持删除操作吗？**

标准的布隆过滤器不支持删除。因为多个元素会共享同一个 bit 位，删除一个元素可能会把别个元素的标记也清掉。

如果要支持删除，需要用**计数布隆过滤器（Counting Bloom Filter）**，每个 bit 位变成一个计数器。代价是内存占用会大几倍。

**Q3：互斥锁方案会不会死锁？**

会。如果拿到锁的线程在回写缓存之前抛异常退出了，锁没释放，后面所有线程都拿不到锁，一直在自旋等待，这就是死锁。

**解决方案**：一定要给锁设置过期时间。更推荐直接用 Redisson 的 `RLock`，它内置了看门狗（WatchDog）自动续期机制，比你手写 SETNX + EXPIRE 安全得多。

**Q4：热 key 怎么提前发现？**

- 用 Redis 4.0+ 的 `redis-cli --hotkeys` 扫描命令。
- 在业务层统计访问频率，维护一个本地的 Top N 热 key 列表。
- 用 Redis 的 Object idle time 命令判断 key 的活跃度。

发现热 key 之后，可以手动对这些 key 设置更长的过期时间，或者主动延迟它们的过期。

## 五、总结场景对比表

我用一个简单的场景对比表帮你加深记忆：

| 概念 | 说法 | 影响范围 | 核心方案 |
|------|------|---------|---------|
| 缓存穿透 | 数据不存在 | 单个非法 key | 布隆过滤器 + 空对象 |
| 缓存击穿 | 热 key 过期 | 单个热点 key | 互斥锁 + 逻辑过期 |
| 缓存雪崩 | 大量 key 同时过期 | 大量 key 或全库 | 随机过期 + 多级缓存 + 高可用 |

## 写在最后

这三个概念面试必考，线上必遇。再复习一下那个口诀：

> **穿透查不存在，击穿扛不住热 key，雪崩一批同时挂。**

- **缓存穿透**：数据在 DB 和缓存都不存在 → 布隆过滤器 + 缓存空对象
- **缓存击穿**：热 key 过期，并发请求打爆 DB → 互斥锁 + 逻辑过期
- **缓存雪崩**：大批 key 同时过期或 Redis 挂了 → 随机过期时间 + 多级缓存 + 高可用架构

面试的时候，先把口诀说出来，让面试官知道你有清晰的理解框架。然后逐个展开讲现象、讲原因、讲代码、讲方案。这套东西聊 15 分钟完全没问题。

更重要的是，下一次线上再出缓存问题，你不会慌了——因为你已经知道问题出在哪，也知道该怎么修。

---

📌 本文是「面向面试之 Redis」系列第 3 篇  
• 上一篇：[Redis 系列二：分布式锁的 4 种实现方案](/posts/redis-distributed-lock-4-solutions/)  
• 下一篇：[Redis 系列四：Redis 持久化——RDB vs AOF](/posts/redis-rdb-aof-persistence/)（待发布）

---

发布于：2026-06-17

原文链接：

|> 更多技术干货，欢迎关注公众号「后端实战笔记」

