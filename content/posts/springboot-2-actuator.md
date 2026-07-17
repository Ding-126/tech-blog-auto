+++
title = "Spring Boot 生产实战系列二：Actuator + Prometheus + Grafana 监控体系搭建"
slug = "springboot-2-actuator"
keywords = ["Actuator", "监控", "Prometheus"]
difficulty = "实战"
target_length = 2500
series_name = "Spring Boot 生产实战"
series_number = 2
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-07-18
+++

上周跟朋友聊天，他说上线半年的项目，CPU 飙到 90% 了才发现——因为没有监控。我说你这不是代码问题，是裸奔。

线上系统没有监控，等于黑灯瞎火开车。这篇直接搭一套 Actuator + Prometheus + Grafana，从 Spring Boot 应用里把指标扒出来，可视化、告警一步到位。

## 先看我们要搭什么

三个组件各管一摊：

- **Actuator**：Spring Boot 自带的"体检科"，暴露 /health、/metrics、/threaddump 这些端点
- **Prometheus**：时序数据库 + 采集器，每隔几秒去你的应用拉一次数据
- **Grafana**：可视化面板，把 Prometheus 里的数据画成 Dashboard

示意图很简单：App（暴露 /actuator/prometheus）← Prometheus（定时 scrape）← Grafana（画图 + 告警）

## 第一步：Actuator 暴露 Prometheus 指标

Actuator 默认只开 /health 和 /info，要把 Prometheus 端点也打开。

```xml
<!-- pom.xml -->
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

配置文件：

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  metrics:
    tags:
      application: ${spring.application.name}
```

重启应用，访问 `http://localhost:8080/actuator/prometheus`，如果看到一堆 `jvm_`、`tomcat_`、`http_` 开头的指标文本，说明 Actuator 已经吐数据了。

这里有个容易踩的坑：**千万别把 `include: "*"` 放到生产环境**。我见过有人图省事暴露了所有端点，结果 /env 直接把数据库密码 leak 了。只开你需要的。

## 第二步：Prometheus 配置抓取

Prometheus 用 YAML 配置文件告诉它去哪抓、多久抓一次。

先写一个 `prometheus.yml`：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'spring-boot-app'
    metrics_path: '/actuator/prometheus'
    static_configs:
      - targets: ['localhost:8080']
        labels:
          instance: 'my-service'
```

`scrape_interval: 15s` 的意思是每 15 秒去你的应用拉一次指标。别设太短，5s 的话没啥必要，还会给应用增加不必要的压力。

启动 Prometheus：

```bash
docker run -d --name prometheus \
  -p 9090:9090 \
  -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus
```

打开 `http://localhost:9090/targets`，看 State 是不是 UP。如果是 DOWN，检查网络或路径对不对。

插一句我的经验：**一开始用 Docker 跑 Prometheus 和 Grafana 就够了，先别在 K8s 里上 Operator**。Operator 学习成本高，出了问题你都不知道是 Operator 配错了还是应用有问题。单体 Docker 先跑通，后面再迁移。

## 第三步：Grafana 接 Prometheus 数据源

```bash
docker run -d --name grafana \
  -p 3000:3000 \
  grafana/grafana
```

浏览器打开 `http://localhost:3000`，默认账号 admin/admin。

登录之后：
1. 点左侧齿轮图标 → **Data Sources** → **Add data source**
2. 选 **Prometheus**
3. URL 填 `http://localhost:9090`
4. 点 **Save & Test**，看到绿色提示就对了

仪表盘我用的是 **JVM (Micrometer)** 模板，ID 是 4701：

1. 左侧 `+` → **Import**
2. 填 4701 → **Load**
3. 选你刚配的 Prometheus 数据源 → **Import**

不到一分钟，JVM 堆内存、GC 次数、线程数、CPU 使用率全出来了。

## 第四步：加几个真正有用的指标

Actuator + Micrometer 自带很多指标，但真正该盯的是这几个：

### JVM 内存

```
jvm_memory_used_bytes{area="heap"}
```

盯着堆内存使用率。如果稳步上升从不下降，大概率是内存泄露。配合 `jvm_gc_pause_seconds` 一起看——GC 频繁且暂停时间长，说明堆需要扩容或对象分配有问题。

### HTTP 请求

```
http_server_requests_seconds_sum / http_server_requests_seconds_count
```

这是平均响应时间。我习惯把它拆到接口级别看——如果某一个接口的 P99 比其他接口高一截，优先排查它。

### 线程状态

```
jvm_threads_states_threads{state="BLOCKED"}
```

正常情况 BLOCKED 线程应该是个位数。如果持续有几十个线程 BLOCKED，说明有锁竞争或者死锁。这是线上问题排查的第一步信号。

### 数据库连接池

```
hikaricp_connections_active
hikaricp_connections_idle
hikaricp_connections_pending
```

如果 `pending` 持续大于 0，说明连接池不够用。不是调大 max 就完事——先查是不是慢 SQL 把连接占了太久。

## 第五步：告警别等用户发现

Prometheus 自带的 Alertmanager 可以配置告警规则。先写规则文件：

```yaml
groups:
  - name: spring-boot-alerts
    rules:
      - alert: HighErrorRate
        expr: rate(http_server_requests_seconds_count{status=~"5.*"}[5m]) > 0.05
        for: 3m
        annotations:
          summary: "5xx 错误率超过 5%"
```

再在 `prometheus.yml` 里引用它：

```yaml
rule_files:
  - "alerts.yml"
```

我不建议一开始就上太多告警规则。先配 3 条最关键的：**错误率过高、堆内存占用超 90%、线程 BLOCKED 过多**。跑两周，看看噪音大不大，再慢慢加。告警疲劳比没有告警更可怕。

## 生产环境还要注意什么

**端口安全**。Actuator 端点别暴露到公网。要么内网隔离，要么用 Spring Security 加一层认证：

```yaml
management:
  server:
    port: 8081
  endpoints:
    web:
      base-path: /internal/actuator
```

把管理端口单独拆出来，跟业务端口分开。Nginx 只转发 8080，8081 只允许内网访问。

**数据保留**。Prometheus 默认本地存 15 天。如果磁盘不大，15 天也够排查近期问题。Long-term 存储可以用 Thanos，但那是 100 人以上团队才需要操心的事。别过早优化。

**自己加业务指标**。Actuator 自带的是通用指标。真正有用的是业务指标——比如"今日订单量"、"支付成功率"。用 Micrometer 的 `MeterRegistry` 自定义：

```java
@RestController
public class OrderController {
    private final Counter orderCounter;

    public OrderController(MeterRegistry registry) {
        this.orderCounter = Counter.builder("orders.created")
            .tag("region", "cn")
            .register(registry);
    }

    @PostMapping("/orders")
    public Order createOrder(@RequestBody Order order) {
        orderCounter.increment();
        // ...
    }
}
```

这些业务指标配合 Grafana 做实时大盘，比看日志效率高太多了。

## 这套搭完能干什么

拿我之前的经历举例子：有次线上服务每半小时卡顿一次，持续 10 秒。查日志看不出原因。后来在 Grafana 上看 JVM GC 面板，发现 Full GC 每 30 分钟触发一次，暂停时间 8 秒。原因是堆只有 2GB，流量大了之后老年代涨得太快。把堆调到 4GB，GC 参数改成 G1，问题消失。

没有监控的话，你连问题方向都找不到。

📌 本文是「Spring Boot 生产实战」系列第 2 篇。下一篇讲日志怎么做到排查问题时随手可取——而不是在服务器上翻半天文件。
