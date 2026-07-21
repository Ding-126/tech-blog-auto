+++
title = "Spring Boot 生产实战系列五：生产就绪——配置中心、日志、健康检查全方案"
slug = "springboot-5-生产就绪"
keywords = ["配置中心", "日志", "健康检查"]
difficulty = "实战"
target_length = 2000
series_name = "Spring Boot 生产实战"
series_number = 5
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-07-21
+++

前两篇我们聊了 Docker 化部署和 Testcontainers 集成测试，到这一步你的 Spring Boot 应用在开发环境已经没啥大问题了。但你懂的，**开发环境跑得欢，不代表生产环境稳得住**。

我之前带过一个项目，代码写得挺漂亮，CI/CD 也配好了，结果一上线就被运维投诉——日志打不出来、配置改不了、健康检查挂了也不知道。搞了一周才把坑填完。

今天这篇是系列最后一期，不讲花活儿，就聊三个你把应用扔进生产环境之前必须搞定的事：**配置中心、日志方案、健康检查**。

## 一、别把配置硬编码在 application.yml 里

我刚入行那会儿干过这种蠢事：数据库密码直接写在 `application-prod.yml` 里，还传到 Git 仓库。后来被老大骂了一顿，才去了解配置中心。

生产环境跟开发环境最大的区别是：**你得在不停机的前提下改配置**。比如数据库切主库、限流阈值调整、某个功能开关打开——这些事在开发环境直接改 yml 重启就完了，生产环境可不行。

### 常见的配置中心方案

现实点说，国内用得最多的是两个：

**Apollo（携程开源）**：功能最全，界面最友好，配置变更历史、灰度发布、权限管理都有。适合中大型团队。我们团队用了三年，最满意的点是配置变更可追溯——出了事故能查到谁改了什么。

**Nacos（阿里开源）**：现在的新项目基本都在用，因为如果你的微服务体系是 Spring Cloud Alibaba，Nacos 本来就做注册中心，配置中心是附带的，省一个中间件。

我个人的选择建议是：**新项目用 Nacos，存量项目用 Apollo**。不要为了省事把配置写在 `application-prod.yml` 里带进镜像——镜像一旦构建，改配置就得重新构建，这叫生产事故前置。

### Spring Boot 集成 Nacos 配置中心

代码量很少，三步搞定。

第一步，加依赖：

```xml
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-nacos-config</artifactId>
</dependency>
```

第二步，`bootstrap.yml`（注意不是 application.yml）里配 Nacos 地址：

```yaml
spring:
  application:
    name: my-service
  cloud:
    nacos:
      config:
        server-addr: 127.0.0.1:8848
        file-extension: yaml
```

第三步，在 Nacos 控制台创建配置，dataId 为 `my-service.yaml`。之后注入 `@Value` 或者 `@ConfigurationProperties` 就跟读本地文件一样。配置改了之后 Nacos 会自动推送到应用，不用重启。

一个小坑：**`@RefreshScope` 别滥用**。`@RefreshScope` 会让 Bean 在配置刷新时重建，如果一个 Bean 里引了十几个配置，重建开销不小。建议只给真正需要动态变更的配置类加上，静态配置走普通 Bean 就行。

## 二、日志——你在线上唯一的眼睛

说难听点，**线上出了问题，你百分之八十的线索都在日志里**。日志打不好，等于闭着眼睛修 Bug。

### 别用 Spring Boot 默认的日志配置

Spring Boot 默认用 Logback，控制台输出的格式挺漂亮，但生产环境需要三样东西：

1. **按天滚动 + 保留天数**——你不想日志文件撑爆磁盘
2. **结构化日志格式**——方便接入 ELK 或 Loki
3. **链路追踪 ID**——微服务下把一次请求串起来

这是我用了好几年的一套配置，直接贴 `logback-spring.xml` 的核心部分：

```xml
<appender name="FILE" class="ch.qos.logback.core.rolling.RollingFileAppender">
    <file>/var/log/my-service/app.log</file>
    <rollingPolicy class="ch.qos.logback.core.rolling.TimeBasedRollingPolicy">
        <fileNamePattern>/var/log/my-service/app.%d{yyyy-MM-dd}.%i.log</fileNamePattern>
        <maxHistory>30</maxHistory>
        <totalSizeCap>10GB</totalSizeCap>
        <timeBasedFileNamingAndTriggeringPolicy class="ch.qos.logback.core.rolling.SizeAndTimeBasedFNATP">
            <maxFileSize>500MB</maxFileSize>
        </timeBasedFileNamingAndTriggeringPolicy>
    </rollingPolicy>
    <encoder>
        <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} [%X{traceId}] - %msg%n</pattern>
    </encoder>
</appender>
```

解释几个点：

- `maxHistory=30` + `totalSizeCap=10GB`：双保险。光配保留天数可能不够——某天日志量暴增，30 天能写出 50GB。加上容量限制更稳。
- `[%X{traceId}]`：从 MDC 拿链路 ID。配合 Sleuth 或 SkyWalking 的 Agent，每条日志都能追溯到一次请求。
- 每个文件最多 500MB，避免单文件过大导致日志采集工具处理不过来。

### 日志级别是个学问

我刚带团队的时候定了个规矩，后来发现特别管用：

- 线上默认 **INFO**。WARN 和 ERROR 走告警通道。
- 某个接口排查问题时，用 `logging.level.com.yourpackage=DEBUG` 临时调回去——**记得调回来**。我见过有人 DEBUG 开了一个月，磁盘直接写爆。
- **别用 `log.debug` 打敏感信息**。用户手机号、身份证、密码——这些东西不该在任何日志级别出现。

## 三、健康检查——别等报警了才知道服务挂了

Spring Boot Actuator 自带了一个 `/actuator/health` 端点，默认返回简单的 JSON。但生产环境要的不是这个——你需要的是**让监控系统和 K8s 知道你的服务到底健不健康**。

### 自定义健康指标

默认的健康检查只检查数据库连接、Redis 连接这些基础资源。但在生产环境，**你的服务可能数据库连着，但业务已经废了**。

举个例子，你的服务依赖上游一个 API，如果上游挂了，你的健康检查应该告诉外面"我不健康"——这样负载均衡器就不会把流量打到你这台半残的机器上。

写一个自定义指标很简单：

```java
@Component
public class UpstreamApiHealthIndicator implements HealthIndicator {

    @Override
    public Health health() {
        try {
            // 检查上游 API 是否可用
            boolean isUp = checkUpstreamApi();
            if (isUp) {
                return Health.up().withDetail("upstream", "available").build();
            }
            return Health.down().withDetail("upstream", "unavailable").build();
        } catch (Exception e) {
            return Health.down(e).build();
        }
    }
}
```

然后在 `application.yml` 里把健康检查端点暴露出来：

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info
  endpoint:
    health:
      show-details: when-authorized
      show-components: when-authorized
```

### K8s 存活探针和就绪探针

如果你的应用部署在 K8s 上，Actuator 提供了两个专门的端点：

- **`/actuator/health/liveness`**：存活探针。应用还活着吗？如果挂了，K8s 会重启 Pod。
- **`/actuator/health/readiness`**：就绪探针。应用能接收流量吗？如果不能，K8s 会从 Service 里摘掉它。

在 K8s 的 Deployment 里这样配：

```yaml
livenessProbe:
  httpGet:
    path: /actuator/health/liveness
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 10
readinessProbe:
  httpGet:
    path: /actuator/health/readiness
    port: 8080
  initialDelaySeconds: 20
  periodSeconds: 5
```

**一个我踩过的坑**：第一次配 K8s 探针时我把存活探针和就绪探针配成了一样的路径，结果应用启动慢了点，存活探针在就绪之前就把 Pod 重启了。存活探针的 `initialDelaySeconds` 一定要给够，不然你的 Pod 会陷入"启动→被杀→重启→被杀"的死循环。

### 信息端点泄露问题

最后说个安全小细节。`/actuator/info` 默认是空的，很多人就放着不管了。但你如果配了 `management.endpoint.health.show-details=always`，外网就能看到你的数据库连接池状态、磁盘剩余空间——这些信息对攻击者来说就是地图。

我的做法是：`show-details=when-authorized`，然后再用 Spring Security 把 Actuator 端点限制在内网 IP 段。

---

## 写在最后

写到这篇，「Spring Boot 生产实战」系列就结束了。

五篇文章从配置管理聊到 Docker 部署，从集成测试聊到生产就绪。说实话，这些都不是什么高深的技术，但每一件都是我在线上踩过坑之后才真正重视起来的。

**开发环境的代码决定你能不能跑，生产环境的基建决定你能跑多远。**

📌 本文是「Spring Boot 生产实战」系列第 5 篇（完结篇）。
