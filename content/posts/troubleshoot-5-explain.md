+++
title = "线上问题排查系列五：线上慢 SQL 从发现到根治——一个完整的 DBA 排查流程"
slug = "troubleshoot-5-explain"
keywords = ["慢 SQL", "索引优化", "EXPLAIN"]
difficulty = "实战"
target_length = 2500
series_name = "线上问题排查"
series_number = 5
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-06-22
+++

上周四晚上十一点，我刚洗完澡准备睡觉，手机一震——报警群又炸了。

"订单查询接口耗时 12 秒，P0 故障！"

我打开电脑，看一眼日志，不用猜，慢 SQL 又来了。这不是第一次了，在上一家公司做电商的时候，几乎每个月都要跟慢 SQL 打交道。今天就把我这些年排查慢 SQL 的经验完整走一遍，**从发现到根治，一个步骤都不跳过**。

## 第一步：先确认是不是 SQL 的问题

慢不一定是 SQL 慢。也可能是网络抖动、应用线程阻塞、GC 停顿。别上来就怼数据库，先确认。

怎么看？在应用日志里搜接口响应时间，找到具体哪个方法慢。如果方法里只调了一个数据查询接口，那大概率是 SQL 的问题。如果方法里调了外部 HTTP 接口，先看看是不是第三方超时了。

**我的经验**：曾经有一次排查了俩小时，最后发现是 Redis 连接池满了，跟 SQL 半毛钱关系没有。浪费时间。

确认是 SQL 慢之后，进入下一步。

## 第二步：找出具体是哪条 SQL

慢查询日志（slow query log）是最好的入口。

```sql
-- 开启慢查询日志（生产慎用在线开启，建议配置文件中配好）
SET GLOBAL slow_query_log = 'ON';
-- 设置慢查询阈值，单位秒
SET GLOBAL long_query_time = 1;
-- 查看慢查询日志文件路径
SHOW VARIABLES LIKE 'slow_query_log_file';
```

如果不想动数据库配置，还有一个更轻量的办法——查 `performance_schema`：

```sql
SELECT DIGEST_TEXT, COUNT_STAR, AVG_TIMER_WAIT/1000000000 AS avg_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE AVG_TIMER_WAIT/1000000000 > 1000
ORDER BY AVG_TIMER_WAIT DESC
LIMIT 10;
```

这个查的是历史聚合，不需要专门开日志，对生产环境友好很多。

找到了慢 SQL，比如：

```sql
SELECT * FROM orders WHERE user_id = 12345 ORDER BY create_time DESC;
```

别急着优化，先看看它到底慢在哪。

## 第三步：EXPLAIN 是唯一的诊断工具

MySQL 的 EXPLAIN 是最重要的武器，没有之一。

```sql
EXPLAIN SELECT * FROM orders WHERE user_id = 12345 ORDER BY create_time DESC;
```

输出关键字段要看懂：

| 字段 | 含义 | 好的信号 | 坏的信号 |
|------|------|----------|----------|
| type | 访问方式 | const, ref, range | ALL（全表扫） |
| possible_keys | 可能用到的索引 | 有值 | NULL（没索引） |
| key | 实际使用的索引 | 有值 | NULL（没用上） |
| rows | 扫描行数估计 | 小 | 几十万+ |
| Extra | 额外信息 | Using index | Using filesort, Using temporary |

**说人话**：`rows` 就是这条 SQL 要翻多少行数据。如果 orders 表有 1000 万行，`rows` 显示 500 万，那这条 SQL 一秒钟跑完算我输。

上面那条 SQL 的问题一般是两个之一：
1. `user_id` 没有索引 → type = ALL
2. 有索引但 `ORDER BY create_time` 导致 filesort → Extra 里有 "Using filesort"

**个人经验**：有一次我排查一条跑了 30 秒的 SQL，`EXPLAIN` 出来 `rows` 显示 800 万，但其实实际只返回 10 条数据。这种就是典型的"翻了 800 万行只找到 10 行"，正确索引能把 800 万变成几十。

## 第四步：对症下药——三类慢 SQL 的根治方案

### 类型一：全表扫描（type = ALL）

直接建索引。但别无脑建，看查询条件：

```sql
-- 这条 SQL
SELECT order_id, amount, status FROM orders WHERE user_id = 12345;

-- 正确索引：覆盖 user_id 查询条件
ALTER TABLE orders ADD INDEX idx_user_id (user_id);

-- 更好：覆盖索引，避免回表
ALTER TABLE orders ADD INDEX idx_user_id_covering (user_id, order_id, amount, status);
```

覆盖索引的意思是——索引里已经包含了你要查的所有字段，MySQL 不需要回主表再查一次。查询速度能翻倍。

### 类型二：排序导致 filesort（Extra = Using filesort）

ORDER BY 的字段不在索引里，MySQL 只好把数据拉到内存排序，数据量大就慢。

```sql
-- 这条 SQL
SELECT * FROM orders WHERE user_id = 12345 ORDER BY create_time DESC;

-- 解决方案：建联合索引，查询字段在前，排序字段在后
ALTER TABLE orders ADD INDEX idx_user_id_create_time (user_id, create_time);
```

**注意顺序**：最左前缀原则。`(user_id, create_time)` 能同时覆盖 WHERE 和 ORDER BY。

### 类型三：Join 慢（NLJ 循环太多）

```sql
SELECT * FROM orders o LEFT JOIN order_items oi ON o.id = oi.order_id WHERE o.user_id = 12345;
```

这种问题很简单：**被驱动表的关联字段一定要有索引**。上面的 SQL，`order_items.order_id` 必须加索引，否则每查一条 orders 记录，order_items 都要全表扫一次。

## 第五步：极致优化——索引之外的手段

有时候索引加完了还是慢，那就不是索引的问题了。

**1. 数据量太大，考虑分库分表**

单表超过 500 万行，即使有索引，B+ 树深度也会增加，IO 次数跟着涨。这时候走分库分表（ShardingSphere、MyCat）或者 TiDB 这类分布式数据库。

**2. SQL 写法有问题，改查询逻辑**

别在 WHERE 条件里对索引字段做函数操作：

```sql
-- 慢！使用了函数，索引失效
SELECT * FROM orders WHERE DATE(create_time) = '2026-06-22';

-- 快！范围查询，索引生效
SELECT * FROM orders WHERE create_time >= '2026-06-22' AND create_time < '2026-06-23';
```

**3. 业务上不需要实时数据，加缓存**

一条统计类的 SQL 跑了 5 秒，业务说"可以接受 30 秒的延迟"——那就用 Redis 或本地缓存兜住。MySQL 不需要扛所有流量。

**个人经验**：我们有个报表接口，每天凌晨跑一次聚合查询写入 Redis，白天用户看的全是缓存数据。原来这条 SQL 每次跑 8 秒，改成缓存后接口响应 2 毫秒。不是 SQL 变快了，是不查了。

## 第六步：建立防御体系，不再重复踩坑

单次排查解决不了根本问题。要上制度：

- **慢查询监控告警**：任何 SQL 超过 1 秒，直接报 P3 告警。超过 5 秒报 P1。别等人发现。
- **SQL Review 上流程**：每次发版前 review SQL 变更，`EXPLAIN` 必须贴出来，`rows` 超过 1 万的不能上线。
- **定期巡检**：每周跑一次慢查询报告，看看有没有"新生"的慢 SQL。新索引上线后观察一周，确认效果。

我在上一家公司就靠这三条，把线上慢 SQL 数量从每月 30+ 降到了个位数。不是能力变强了，是流程堵住了漏洞。

## 总结一下排查流程

慢 SQL 排查没什么玄学，记住五个步骤：

1. **确认**是 SQL 的问题而不是其他组件的问题
2. **定位**具体是哪条 SQL
3. **诊断**用 EXPLAIN 看全表扫还是 filesort
4. **治疗**加索引 / 改写法 / 加缓存 / 分库分表
5. **防御**慢查询告警 + SQL Review + 定期巡检

这五个步骤走一遍，90% 的慢 SQL 都能搞定。剩下 10% 是架构问题，需要从数据存储层面重新设计，那就是另外一个故事了。

📌 本文是「线上问题排查」系列第 5 篇（共 5 篇）。本系列到此完结，从线程池、死锁、内存、网络到慢 SQL，覆盖了线上最常遇到的五类问题。希望能帮你在下次接到报警的时候，少走弯路。
