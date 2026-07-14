+++
title = "面向面试之数据库系列五：MySQL 主从复制与高可用——Binlog 实战"
slug = "database-5-binlog"
keywords = ["主从复制", "Binlog", "高可用"]
difficulty = "进阶"
target_length = 2000
series_name = "面向面试之 数据库"
series_number = 5
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-07-15
+++

前天帮同事看一个线上问题：**主库写入正常，从库延迟了 20 分钟，业务那边报表一直出不来**。上去一看，`Seconds_Behind_Master` 一万多，Binlog 文件堆了一整块盘。

这种场景面试里问得特别多——**MySQL 主从复制 + Binlog 高可用**。而且你会发现，面试官问的点和实际线上踩的坑，基本是同一套东西。这篇就把那些绕不开的问题串一遍。

## Binlog 就是个"操作流水账"

Binlog（Binary Log）说白了就是 MySQL 的变更日志。谁改了哪条数据、什么时候改的、改之前什么样、改之后什么样——全记在里面。

三种格式，面试必问：

- **STATEMENT**：记 SQL 语句本身。优点是日志小，缺点是某些函数（比如 `NOW()`、`UUID()`）在主从执行结果不一样
- **ROW**：记每一行数据的变化。精确、一致性好，缺点就是日志量大
- **MIXED**：MySQL 自己判断，普通的 SQL 用 STATEMENT 记，有歧义的地方自动切 ROW

我个人的建议：**线上无脑用 ROW**。STATEMENT 在复杂 SQL 下出过太多诡异问题，省那点磁盘空间不值得。

## 主从复制到底怎么跑的

这是面试手绘图题，流程其实就三步：

1. **主库写 Binlog**：事务提交时，把变更记到本地的 Binlog 文件里
2. **从库拉 Binlog**：从库的 I/O 线程连上主库，把 Binlog 内容拉下来，写到自己的 Relay Log（中继日志）
3. **从库回放**：SQL 线程读 Relay Log，一条一条执行

画个简化版就是：`主库写入 → Binlog → 从库 I/O 线程 → Relay Log → SQL 线程 → 从库数据`

面试官接下来会追问：**如果从库的 SQL 线程挂了会怎样？**

答：I/O 线程照样拉数据，积在 Relay Log 里。SQL 线程恢复后继续追。这就是为什么从库 Binlog 文件会堆积——消费速度跟不上生产速度。

**如果主库挂了会怎样？**

这就进入了高可用的范畴。

## 三种常见的高可用方案

### 1. 主从切换（手动或 MHA）

最传统的方式：监控发现主库挂了，选一个从库 `CHANGE MASTER TO` 把它提升为主库，业务改连接地址。

坑在哪？**数据一致性**。如果主库没来得及把最后一个 Binlog 传给从库，切换后就会丢数据。解决方案是半同步复制——主库等至少一个从库确认收到 Binlog，才给客户端返回"写入成功"。

我之前踩过一个坑：MHA 自动切换后，从库比主库少了几条订单数据，财务对账怎么都对不平。后来加了个 Binlog 补偿脚本，切之前先比对主从的 `gtid_executed`，差异补上再切。

### 2. 半同步复制（Semi-Sync Replication）

MySQL 5.5 引入，5.7 完善。核心就一句话：**主库等一个从库 ACK 再提交**。

```
主库：准备提交事务 → 写 Binlog → 等从库 ACK → 提交完成
```

代价是写入延迟变大。如果从库一直不回 ACK，超时后 MySQL 会自动降级为异步复制，保证业务不受影响。

### 3. Group Replication / MGR

MySQL 5.7.17 出的组复制，多节点都能写，内部走 Paxos 协议的一致性。但部署和维护成本高，中小厂用得少。

## Binlog 实战：从库延迟怎么查

回到开头的场景。从库延迟 20 分钟，排查链路上来三板斧：

**第一板斧：看 `SHOW SLAVE STATUS\G`**

重点关注三个字段：
- `Seconds_Behind_Master`：延时秒数（这个值不绝对精确，但够用）
- `Slave_IO_Running`：I/O 线程状态
- `Slave_SQL_Running`：SQL 线程状态

**第二板斧：查 I/O 线程的瓶颈**

```
SHOW PROCESSLIST;
```

看 I/O 线程是正常的"Reading master binlog"还是卡在"Connecting to master"。如果网络有问题，I/O 线程连不上主库，Relay Log 没有新数据，从库永远追不上。

**第三板斧：查大事务**

`SHOW BINLOG EVENTS` 看当前在回放的是什么 SQL。很多时候延迟不是因为并发不够，而是**一个大事务在跑**——比如业务写了条 `UPDATE` 影响了几百万行，从库要一条一条回放，自然慢。

我遇到过最离谱的一次：某活动上线后，运营同学在 MySQL 客户端直接点了"全量更新"，一个事务跑了 40 分钟，所有从库全部卡死。从那以后我在代码里强制加了 `max_execution_time` 和 WHERE 必须有索引检查。

## GTID：现代复制的基石

GTID（Global Transaction Identifier）是 MySQL 5.6 引入的，给每个事务分配一个全局唯一 ID。

好处是**切换时不用手动指定 Binlog 文件名和位置**了。以前 `CHANGE MASTER TO` 要写 `master_log_file` 和 `master_log_pos`，GTID 模式下只要 `MASTER_AUTO_POSITION=1` 就行了。

为什么很多面试官喜欢问 GTID？因为**它是判断你对 MySQL 复制理解深不深的分水岭**。能说出 GTID 解决了什么痛点、什么场景下必须用 GTID——基本可以认定这个人动手做过运维。

## 总结：面试里怎么答这题

如果面试官让你"讲讲 MySQL 主从复制和高可用"，建议按这个逻辑组织：

1. **Binlog 是什么**：三种格式，推荐 ROW
2. **复制流程**：主库 Binlog → 从库 I/O 线程 → Relay Log → SQL 线程
3. **延迟问题**：从库延迟排查三板斧
4. **高可用方案**：主从切换、半同步、MGR，各自的取舍
5. **GTID**：自动定位位置，简化切换

数据层的高可用没有银弹。半同步 + 自动切换 + 定期演练，对于大部分业务场景已经够用。别被那些复杂方案唬住——先把三板斧玩熟，比什么都强。

📌 本文是「面向面试之 数据库」系列第 5 篇。前 4 篇讲了索引、事务、锁、SQL 优化和分库分表，这最后一篇聊聊主从与高可用。系列已完结，共 5 篇。
