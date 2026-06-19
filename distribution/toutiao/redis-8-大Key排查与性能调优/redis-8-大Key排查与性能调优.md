## 开头

有一次周五晚上，我正在家吃饭，告警群突然响了——"Redis 响应延迟超过 3 秒"。

我筷子一放就开电脑。登录 Redis 一看，`info commandstats` 里 `SMEMBERS` 命令的平均耗时从 0.5ms 飙升到了 1200ms，涨了 2400 倍。

赶紧 `SMEMBERS` 那个 key 一看——**这个 Set 里有 680 万个元素**，占用内存 1.2GB。

这就是典型的大 Key 问题。后来花了两个晚上重构，改成 Hash 分片存储，延迟降回了 0.8ms。

今天用这个真实案例，把大 Key 和热 Key 的排查、定位、根治方法一次讲清楚。

## 核心结论先行

- **大 Key**：单个 key 的值过大（String > 10KB 或集合 > 5000 元素）→ 阻塞 Redis、网络带宽打满
- **热 Key**：某个 key 被超高频率访问 → CPU 飙高、单节点成为瓶颈
- **排查工具**：`redis-cli --bigkeys` 扫描 + `INFO commandstats` 定位慢命令 + `MONITOR` 采样
- **方案**：大 Key 分片/拆分，热 Key 本地缓存 + 读写分离

## 先看现象

那天晚上的现象是这样的：

```bash
# 1. 先看延迟
redis-cli -h r-xxx.redis.rds.aliyuncs.com --latency -i 1
min: 0.4ms, max: 4821ms, avg: 23.7ms
```

平均 24ms，最大 4.8 秒。正常 Redis 延迟应该在 1ms 以内。

```bash
# 2. 看哪个命令最慢
redis-cli INFO commandstats | head -20

# cmdstat_smembers:calls=2387,usec=2891765,usec_per_call=1211.45
# cmdstat_hgetall:calls=15673,usec=1872341,usec_per_call=119.47
# cmdstat_get:calls=89234,usec=892340,usec_per_call=10.00
```

`SMEMBERS` 每次调用平均 1.2 秒，这肯定有问题。问题就是这个命令操作的 key 太大了。

我还遇到过另一种场景：热 Key。有次做秒杀活动，一个商品 key 每秒被访问 5 万次，那个 Redis 节点的 CPU 直接飙到 95%。这就是热 Key 的问题——单个节点扛不住。

## 大 Key 的 3 种形态

| 类型 | 特征 | 典型场景 | 影响 |
|------|------|---------|------|
| **大 String** | value > 10KB | 缓存大 JSON、序列化对象 | 读写慢、带宽高 |
| **大 Set/ZSet/List** | 元素 > 5000 | 未分页的关注列表、粉丝列表 | SMEMBERS/LRANGE 阻塞 |
| **大 Hash** | field > 10000 | 海量用户属性未拆分 | HGETALL 耗时高 |

## 怎么排查大 Key

### 方法一：--bigkeys（最常用）

```bash
redis-cli --bigkeys -i 0.1

# 输出示例
# Biggest Set   found so far 'user:follow:10086' with 6800000 members
# Biggest String found so far 'session:token:abc' with 5242880 bytes
# Biggest Hash  found so far 'product:202406' with 50000 fields
```

`-i 0.1` 表示每扫描 100 个 key 暂停 0.1 秒，避免影响线上。如果你是阿里云 Redis，这个命令可能被禁用，可以用 `scan` 手动遍历。

### 方法二：DEBUG OBJECT（精确查看）

```bash
redis-cli DEBUG OBJECT user:follow:10086
# Value at:0x7f8e1c0 refcount:1 encoding:hashtable serializedlength:2841971 lru:10827902 lru_seconds_idle:6
```

`serializedlength: 2841971` 表示序列化后 2.8MB，实际内存占用可能更大。

### 方法三：MEMORY USAGE（精确内存）

```bash
redis-cli MEMORY USAGE user:follow:10086
(integer) 1283456712  # 约 1.2GB
```

**注意**：`MEMORY USAGE` 本身会遍历整个 key，大 Key 上执行这个命令也会阻塞，谨慎使用。

## 大 Key 怎么根治

### 方案一：拆分（最推荐）

把一个大 Set/Hash 拆成多个小 key。比如上面的 680 万粉丝，按粉丝 ID 哈希分到 100 个 key 里：

```java
public class FollowService {
    private static final int BUCKET_SIZE = 100;
    
    public String buildKey(Long userId) {
        return "user:follow:" + userId + ":" + (userId % BUCKET_SIZE);
    }
    
    public Set<Long> getFollows(Long userId) {
        Set<Long> all = new HashSet<>();
        for (int i = 0; i < BUCKET_SIZE; i++) {
            String key = "user:follow:" + userId + ":" + i;
            Set<Long> part = redis.sMembers(key);
            all.addAll(part);
        }
        return all;
    }
}
```

每个 key 约 6.8 万元素，`SMEMBERS` 耗时从 1.2 秒降到 15ms。代价是查询时要合并 100 个 key，但 100 次 15ms 的网络开销也就 1.5 秒——比原来 1.2 秒阻塞单次好很多，而且不会阻塞 Redis。

### 方案二：冷热分离

把大 Key 里的数据按访问频率分离：

- 热数据（最近 7 天的）放在 Redis
- 冷数据（7 天前的）放到 MySQL/Tair 等后端存储

```java
public List<String> getMessages(Long userId, int page, int size) {
    String hotKey = "msg:hot:" + userId;
    // 先查 Redis 热数据
    List<String> hot = redis.lRange(hotKey, 0, -1);
    if (page * size < hot.size()) {
        return hot.subList(page * size, Math.min((page+1)*size, hot.size()));
    }
    // 热数据不够，查冷存储
    return coldDB.queryMessages(userId, page, size);
}
```

### 方案三：压缩

如果是大 String，可以压缩后再存：

```java
// 写入时压缩
String json = objectMapper.writeValueAsString(data);
byte[] compressed = gzip.compress(json.getBytes());
redis.set(key, compressed);

// 读取时解压
byte[] compressed = redis.get(key);
String json = new String(gzip.decompress(compressed));
Data data = objectMapper.readValue(json);
```

实测 JSON 压缩率能到 5:1 到 10:1，对大 JSON 场景很有效。

## 热 Key 怎么排查

热 Key 的表现是某些 key 的 QPS 异常高：

```bash
# 用 MONITOR 采样 10 秒，统计 key 访问频率
redis-cli MONITOR | head -100000 | awk '{print $4}' | sort | uniq -c | sort -rn | head -10

# 输出
# 45213 "GET" "product:123456"
# 23451 "GET" "product:789012"
# 8921 "SET" "session:token:xxx"
```

这个 `product:123456` 每秒 4500 次 GET，就是热 Key。

**更安全的做法**：用 `redis-cli --hotkeys`（Redis 6.0+）：

```bash
redis-cli --hotkeys
```

注意 `MONITOR` 在高并发下会消耗大量 CPU，可能导致 Redis 延迟升高。线上谨慎使用。

## 热 Key 怎么解决

### 方案一：本地缓存

```java
@Cacheable(value = "product", key = "#id", localCache = true)
public Product getProduct(Long id) {
    return productMapper.selectById(id);
}
```

本地缓存（Caffeine/LoadingCache）扛掉 90% 以上的读请求，只有少量 MISS 才穿透到 Redis。我之前那个秒杀场景，加了本地缓存后 Redis QPS 从 5 万降到了 3000，CPU 从 95% 降到了 20%。

### 方案二：读写分离

把读请求分散到从库：

```
写请求 → Master
读请求 → Slave1 / Slave2 / Slave3（轮询）
```

Redis 的主从复制是异步的，从库可能读到稍旧的数据。但对大多数读多写少的场景来说，可以接受。

### 方案三：key 散列

在 key 上加随机后缀，让热 key 分散到多个节点。比如秒杀商品 key `product:123456` 可以拆成：

```java
// 读的时候随机选一个副本
int replica = ThreadLocalRandom.current().nextInt(10);
String key = "product:123456:" + replica;
String value = redis.get(key);
if (value == null) {
    // MISS 时从 DB 加载
    value = loadFromDB(123456);
    // 写到所有副本
    for (int i = 0; i < 10; i++) {
        redis.setex("product:123456:" + i, 300, value);
    }
}
```

**注意**：这个方案适用于 Redis Cluster，对单个 Redis 实例没用——数据都在同一台机器上。

## 完整排查流程

我一般按这个步骤走：

```
收到告警（延迟高 / CPU 高）
    ↓
1. redis-cli --latency 确认延迟
    ↓
2. INFO commandstats 定位慢命令
    ↓
3. 根据慢命令判断是大 Key（SMEMBERS/HGETALL/LRANGE）还是热 Key（某命令调用量异常高）
    ↓
   ├─ 大 Key → --bigkeys 定位具体 key
   │           → 分拆 / 冷热分离 / 压缩
   │
   └─ 热 Key → MONITOR 采样确认 key
               → 本地缓存 / 读写分离 / key 散列
    ↓
4. 验证：再次 --latency 确认延迟回到正常水平
```

## 扩展 & 进阶方向

- **阿里云 Redis 的 bigkey 和 hotkey 诊断**：云 Redis 自带大 Key 和热 Key 诊断功能，在控制台开启即可
- **Redis 7.0 的 Function 代替 Lua**：可以在服务端做复杂的数据分片逻辑，减少网络开销
- **Redis 慢日志**：`SLOWLOG GET 100` 查看最近 100 条慢查询，定位问题很快

## 参考资料

- [Redis 大 Key 最佳实践](https://redis.io/docs/management/optimization/latency/)
- [Redis Big Keys 扫描](https://redis.io/commands/scan/#the--bigkeys-option)
- [阿里云 Redis 诊断与优化](https://help.aliyun.com/document_detail/26355.html)

---

📌 本文是「面向面试之 Redis」系列第 8 篇（番外，完结篇）  
• 上一篇：[Redis 系列七（番外）：缓存与数据库一致性——4 种方案从简单到可靠](/posts/redis-7-缓存与数据库一致/)  
• 🎉 Redis 系列全部完成！共 6 篇正片 + 2 篇番外