+++
title = "Kafka 实战系列一：从零搭建——Topic、分区、副本，一次搞清楚"
slug = "kafka-1-kafka"
keywords = ["Kafka", "Topic", "分区"]
difficulty = "入门"
target_length = 2000
series_name = "Kafka 入门到生产"
series_number = 1
series_total = 4
draft = false
categories = ["tutorial"]
writing_mode = "tutorial"
date = 2026-11-01
+++

2021 年冬天，我第一次在生产环境建 Kafka Topic。

运维给了我一条命令，我照着敲下去：

```bash
kafka-topics.sh --bootstrap-server kafka:9092 --create \
  --topic order-event --partitions 3 --replication-factor 1
```

Topic 建起来了，消息也发出去了，压测没问题，我还挺得意。

三天后一个 broker 重启，`order-event` 里积压的消息全没了。

后来复盘的时候我才明白：**我不是搞错了三个配置项，我是压根没把这三个概念当成一套设计约束。** 分区数决定你这套系统能扛多大并发，副本数决定它会不会丢数据，而这两件事在建 Topic 的那一秒就定死了。

这篇就从零搭一套能跑的 Kafka，把 Topic、分区、副本这三个东西讲透。不背概念，只讲它们各自管什么、参数怎么定、定错了会死在哪。

<!--more-->

## 先把三者的关系摆清楚

一句话概括：

| 概念 | 是谁在管 | 决定什么 | 改不回来？ |
|---|---|---|---|
| Topic | 业务 | 消息的逻辑分类 | 可以改名，但消费端全得改 |
| Partition | Kafka | 并行度上限、顺序性边界 | 只能加，不能减 |
| Replica | 运维 | 数据会不会丢 | 可以调，但调之前数据已经在裸奔 |

再补一句更准确的：**Topic 是给业务看的名字，Partition 是 Kafka 干活的单位，Replica 是运维的保险单。**

业务侧说"我要一个订单事件的 Topic"，Kafka 拆成 3 个 Partition 分给 3 台机器并行写，每份 Partition 再复制 3 份散在不同 broker 上。这三层是嵌套的，不是并列的。

很多教程把这三个概念并列着讲，讲完了你还是不知道 `--partitions 3` 该填几。原因就在这里——它们是嵌套关系，得先搞明白谁约束谁。

## 搭一套能折腾的环境

练手别用单机单节点，否则你永远看不到副本和领导者切换。三节点，docker compose 一把起，KRaft 模式（3.3 之后不再需要 ZooKeeper）：

```yaml
x-kafka: &kafka
  image: bitnami/kafka:3.7
  environment: &env
    KAFKA_CFG_PROCESS_ROLES: broker,controller
    KAFKA_CFG_CONTROLLER_LISTENER_NAMES: CONTROLLER
    KAFKA_CFG_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093
    KAFKA_CFG_INTER_BROKER_LISTENER_NAME: PLAINTEXT
    KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: 1@kafka1:9093,2@kafka2:9093,3@kafka3:9093
    ALLOW_PLAINTEXT_LISTENER: "yes"

services:
  kafka1:
    <<: *kafka
    environment:
      <<: *env
      KAFKA_CFG_NODE_ID: 1
      KAFKA_CFG_ADVERTISED_LISTENERS: PLAINTEXT://localhost:19092
    ports: ["19092:9092"]
  kafka2:
    <<: *kafka
    environment:
      <<: *env
      KAFKA_CFG_NODE_ID: 2
      KAFKA_CFG_ADVERTISED_LISTENERS: PLAINTEXT://localhost:19093
    ports: ["19093:9092"]
  kafka3:
    <<: *kafka
    environment:
      <<: *env
      KAFKA_CFG_NODE_ID: 3
      KAFKA_CFG_ADVERTISED_LISTENERS: PLAINTEXT://localhost:19094
    ports: ["19094:9092"]
```

`docker compose up -d` 之后，用 19092/19093/19094 任意一个都能作为 bootstrap server 连进去。**注意 `ADVERTISED_LISTENERS` 一定要写客户端能访问的地址**，写成容器名 `kafka1:9092` 你在宿主机上就永远连不上——这是新手第一个卡点，别问我是怎么知道的。

## 建 Topic：那两个参数是唯一的决策

```bash
kafka-topics.sh --bootstrap-server localhost:19092 --create \
  --topic order-event \
  --partitions 3 \
  --replication-factor 3 \
  --config min.insync.replicas=2
```

建完先看一眼：

```bash
kafka-topics.sh --bootstrap-server localhost:19092 --describe --topic order-event
```

你会看到每个 Partition 带一个 Leader 和一组 Replicas、Isr。这就是 Kafka 的核心状态表，出任何问题都是先看这张表。`Isr` 里少于副本数，说明有 broker 掉队了，后面细说。

**分区数只能加不能减**，这是硬约束。所以现在的建议是：宁可一开始估大一点。分区多的成本远小于分区不够——加到 100 个分区的开销，顶多是多几个打开的文件句柄；而分区不够导致吞吐上不去，你只能重建 Topic 然后迁移数据。

## 分区数到底怎么定

两个约束，取大的那个：

**1. 吞吐约束**：单分区写入实测大概 10~50 MB/s（跟批大小、压缩、盘类型强相关）。目标写入 200 MB/s，那至少 4 个分区，留一倍冗余就 8 个。

**2. 消费并发约束**：消费者组内**一个分区只能被一个消费者消费**。所以分区数 = 你这个 Topic 未来最大的消费并发上限。你预估三年后要 20 个消费者实例，现在就别建 3 个分区，否则第 4 个消费者启动后会一直空转——它分不到分区。

还有一个坑，只有踩过才知道：**分区数变了，key 的顺序性就断了。** Kafka 用 `hash(key) % 分区数` 决定消息去哪个分区，分区数从 3 改成 6，同一个订单 ID 的 hash 取模结果全变了，之前落在 P0 的消息现在可能去 P3，同一笔订单的创建、支付、发货三条消息，顺序乱了。

订单、支付这类强顺序业务，要么分区数一次定死不再动，要么别用 key 分区，改成业务侧排序。**没有第三种选择。**

## 副本：acks 和 min.insync.replicas 是绑在一起的

副本这块，参数看着有三个，其实是两组联动。

- `replication-factor`：副本存几份（建 Topic 时定，可以后来加）
- `min.insync.replicas`（minISR）：写成功至少要有几个副本确认落盘
- 生产端的 `acks`：0 不等、1 等 leader、all 等所有 ISR

组合起来是这样的：

| acks | minISR | 结果 |
|---|---|---|
| 0 | 任意 | 发出去就不管，broker 挂了必丢 |
| 1 | 任意 | leader 落盘就返回，leader 挂掉且未同步 -> **丢** |
| all | 1 | 只要 leader 一个副本活着就能写，但那个副本挂了数据还在别的节点上，等它重启就回来了 |
| all | 2 | 主流生产配置，能容忍 1 台 broker 挂掉不丢不阻塞 |

**最常见的错误是 `acks=all` 配 `replication-factor=2` 配 `minISR=2`。** 看着挺严谨，实际是：挂一台 broker，可用副本只剩 1 个，小于 minISR=2，这个 Topic 直接变成不可写，整个链路卡死。你想要的"高可用"，配出来的是"一碰就停"。

正确姿势：三副本 + `minISR=2` + `acks=all`。挂一台，还剩 2 个副本，够 minISR，继续写；挂两台，写入被拒，但那是你该知道的事实，不是意外。

我刚换到这套配置时还遇到一件事：单个 broker 重启，客户端报了一堆 `NOT_ENOUGH_REPLICAS`，几秒后自己恢复了。那几秒的报错不是 bug，是 Kafka 在保护你——**它在告诉你"现在写进来我保证不了不丢"**。重试策略配好、幂等生产者打开，这几秒的事就过去了；要是硬把 minISR 调成 1 图个安静，出事那天才知道安静有多贵。

## 上线前必跑的四条验证

配置建完不算完，得验证它在故障下真的按你想的工作：

```bash
# 1. 确认副本分布没扎堆（同一分区的副本不能在同一 broker）
kafka-topics.sh --bootstrap-server localhost:19092 --describe --topic order-event

# 2. 杀掉一个 broker，看 ISR 收缩
docker stop kafka3
kafka-topics.sh --bootstrap-server localhost:19093 --describe --topic order-event

# 3. 在缺一个副本的情况下继续写，确认没被阻塞
kafka-console-producer.sh --bootstrap-server localhost:19093 --topic order-event

# 4. 把 broker 拉起来，看 ISR 恢复、再看消费者积压
docker start kafka3
kafka-consumer-groups.sh --bootstrap-server localhost:19092 --describe --group order-consumer
```

第 4 条最容易被跳过。ISR 恢复了不代表消费者跟上了——如果 producer 一直在写而 consumer 卡住，你会看到 lag 一路涨，这时候问题在消费侧，跟副本没关系。**排查 Kafka 问题的顺序永远是：先看 leader/ISR，再看 lag，最后看业务代码。**

## 写在最后

这一篇把地基打完了。回头看 2021 年那次事故，代价其实不是丢了消息，是我在建 Topic 的那一分钟里，把"能跑"当成了"能扛"。

下一篇聊生产者：消息怎么保证不重不丢，幂等和事务到底解决的是哪个问题。

📌 本文是「Kafka 入门到生产」系列第 1 篇。
