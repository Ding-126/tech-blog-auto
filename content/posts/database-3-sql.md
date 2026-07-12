+++
title = "面向面试之数据库系列三：SQL 优化——从慢查询到索引设计的完整链路"
slug = "database-3-sql"
keywords = ["SQL 优化", "索引设计"]
difficulty = "进阶"
target_length = 2500
series_name = "面向面试之 数据库"
series_number = 3
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-07-13
+++

面试问 SQL 优化，大部分人能答出"加索引"。再问怎么加，就变成了"看 where 后面的字段"——这个答案太粗糙了，面试官听完不会给你加分。

我工作第三年的时候接手过一个线上慢查询，一条 SQL 跑了 7 秒，加了索引之后变到 5.8 秒。那 1 秒的提升等于没加——后来发现我加的索引根本没被用上。那是第一次意识到，SQL 优化不是拍脑袋"这里加个索引"，而是有完整链路可循的。

这篇不讲玄学，讲方法论。

<!--more-->

## 第一步：慢查询日志——你得先知道谁慢

很多人优化是"感觉某个接口慢了"然后去看代码，找到对应 SQL 就开始分析。这不叫优化，这叫碰运气。

真正有效的方式：**打开慢查询日志**。

```sql
-- 查看慢查询是否开启
SHOW VARIABLES LIKE 'slow_query%';
SHOW VARIABLES LIKE 'long_query_time';

-- 设置阈值（建议 1 秒，线上如果太严可以设 2 秒）
SET long_query_time = 1;
SET slow_query_log = ON;
```

我服务过的一家电商公司，上线前 DBA 统一把慢查询阈值设到 0.5 秒。第一周扫出来 200 多条慢 SQL，大部分是联表查询没用索引、分页用了 `OFFSET` 很大的值、或者是 datetime 字段上用了函数导致索引失效。

**优化前必须先量化**。没有慢查询日志，你就是在猜。

面试官问到这，你能说出"先开慢查询日志定位问题"——这个起手式就说明你有实战经验。

## 第二步：EXPLAIN——别只会看 type 那一列

拿到慢 SQL 之后，第一件事就是 EXPLAIN。

```sql
EXPLAIN SELECT * FROM orders WHERE status = 1 ORDER BY create_time DESC LIMIT 10;
```

大多数人只盯着 `type` 看：`ALL` 不行，`ref` 可以，`const` 最好。这没错，但不够。

**面试中能让你跟别人拉开差距的，是下面这几列：**

### rows + filtered

- `rows`：MySQL 预估要扫描的行数
- `filtered`：经过条件过滤后剩下的百分比

如果 `rows = 100000` 但 `filtered = 0.1`，说明索引没有精准定位数据，MySQL 扫了 10 万行才筛出 100 行。这种情况大概率是**索引设计不合理**——比如建了单列索引，但查询条件是多列的 AND。

### Extra

这里藏着最多信息：
- `Using index`：覆盖索引，不用回表——这是最佳状态
- `Using index condition`：索引下推（ICP），MySQL 5.6+ 特性，部分过滤在引擎层做
- `Using where; Using index`：索引覆盖 + 额外过滤
- `Using temporary`：用了临时表，常见于 GROUP BY 或 DISTINCT
- `Using filesort`：文件排序，数据量大时非常慢

**我自己的经验：看到 `Using temporary` 或 `Using filesort`，就值得停下来重新审视 SQL。**

有一次我优化一个报表查询，`EXPLAIN` 显示 `Using temporary; Using filesort`，查了 6 秒。把 `ORDER BY` 字段改成索引的一部分之后，`Extra` 变成了 `Using index`，耗时降到 0.02 秒——300 倍的差距。

## 第三步：索引设计——别在低基数字段上建索引

这是一个容易被新手忽略的坑。

**基数（Cardinality）** 指一个字段有多少个不同的值。

```sql
SHOW INDEX FROM users;
-- 看 Cardinality 列
```

如果表有 100 万行，Cardinality 是 2（比如 status 字段只有 0 和 1），那这个索引的区分度极差。MySQL 用这个索引查，还是得扫 50 万行，还不如全表扫描快——全表扫描走顺序 IO，用索引反而要反复回表，**性能更差**。

所以优化原则第一条：**优先在高基数字段上建索引**。

| 字段 | 基数 | 是否适合索引 |
|------|------|:---:|
| id | 100% | ✅ |
| order_no | 极高 | ✅ |
| user_id | 高 | ✅ |
| status | 2~5 | ❌ |
| gender | 2 | ❌ |
| is_deleted | 2 | ❌ |

第二个原则：**复合索引的"最左前缀"不是死规则，是有逻辑的。**

很多人背了"复合索引最左前缀原则"，但不知道怎么用。其实很简单——**把区分度最高的字段放在最左边**。

举个例子，索引 `(city, status, created_at)`：
- 查询 `WHERE city = '北京'` → 能用到索引
- 查询 `WHERE city = '北京' AND status = 1` → 能用到索引
- 查询 `WHERE status = 1` → **用不了**，跳过了最左列

这不是 MySQL 故意为难你。B+Tree 先按第一个字段排序，再按第二个、第三个。跳过第一个就等于在一本电话簿里按姓的笔画找人的名——完全不匹配。

## 第四步：常见反模式——面试官最喜欢问的坑

### 1. 函数包裹索引字段

```sql
-- 索引失效 ❌
SELECT * FROM orders WHERE DATE(create_time) = '2026-01-01';

-- 正确写法 ✅
SELECT * FROM orders WHERE create_time >= '2026-01-01' AND create_time < '2026-01-02';
```

MySQL 的索引存的是字段的原始值，不是 `DATE()` 函数的计算结果。对字段做任何函数操作（`DATE()`、`LEFT()`、`+0`）都会导致索引失效。面试官十次问索引失效，八次会提这个。

### 2. 隐式类型转换

```sql
-- phone 字段是 VARCHAR，传了数字
SELECT * FROM users WHERE phone = 13800138000;  -- 索引失效 ❌

SELECT * FROM users WHERE phone = '13800138000';  -- 正常 ✅
```

MySQL 内部会把 `phone` 转成数字做比较，实际上等于对字段做了 `CAST(phone AS int)`，索引失效。

### 3. 分页越往后越慢

```sql
-- 第 100 页，性能开始崩 ❌
SELECT * FROM orders WHERE status = 1 ORDER BY id LIMIT 10 OFFSET 990;

-- 改进：用上一页的最后一条记录 ✅
SELECT * FROM orders WHERE status = 1 AND id > 990 ORDER BY id LIMIT 10;
```

`LIMIT 990, 10` 不是只读 10 行——MySQL 要扫 1000 行再扔掉前 990 行。越往后越慢。对于瀑布流或分页的场景，用游标分页替代偏移量分页，差距肉眼可见。

## 第五步：Covering Index——能不回表就别回表

InnoDB 的非聚簇索引叶子节点存的是主键值。查询回到主键索引取整行数据，这个过程叫**回表**。

如果能在一棵索引树里拿到所有需要的字段，就不需要回表。这叫**覆盖索引**（Covering Index）。

```sql
-- 假设索引 (status, create_time)
-- ❌ 要回表拿 amount
SELECT amount FROM orders WHERE status = 1 ORDER BY create_time;

-- ✅ 索引里包含了所有字段，不用回表
-- 可以建复合索引 (status, create_time, amount)
```

覆盖索引是性能优化里**性价比最高**的手段。不需要改表结构，不需要改 SQL，只需要在现有索引上加几个字段。

我之前帮一个团队优化统计报表的查询，原来 3.2 秒，加完覆盖索引变成 0.008 秒。改写就是 `ALTER TABLE ... ADD INDEX ...` 一句话的事。

## 面试总结：完整的优化链路

如果你的面试官问你"你怎么做 SQL 优化"，照着这个链路答，层次感就有了：

1. **定位**：开慢查询日志，找出真正的慢 SQL
2. **分析**：EXPLAIN 看 type/rows/Extra，确认瓶颈
3. **索引**：高基数优先，复合索引的最左前缀按区分度排
4. **检查反模式**：函数包裹、隐式转换、分页偏移量
5. **覆盖索引**：能不回表就不回表

这五步走完，90% 的慢查询都能解决。剩下 10% 走的是 SQL 改写、分表、走 ES 之类的架构级方案——那就是另外一个话题了。

📌 本文是「面向面试之 数据库」系列第 3 篇。上一篇聊了事务隔离级别与 MVCC，下一篇聊线上 SQL 死锁分析。
