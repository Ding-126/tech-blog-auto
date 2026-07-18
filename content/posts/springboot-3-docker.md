+++
title = "Spring Boot 生产实战系列三：Docker 化最佳实践——多阶段构建 + 镜像瘦身"
slug = "springboot-3-docker"
keywords = ["Docker", "容器化"]
difficulty = "实战"
target_length = 2000
series_name = "Spring Boot 生产实战"
series_number = 3
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-07-19
+++

公司去年从物理机迁移到 K8s 集群，我负责把二十多个 Spring Boot 服务全部 Docker 化。第一版 Dockerfile 写得很粗糙：`FROM openjdk:11-jdk` 直接上，打出来的镜像 800MB+，部署一台机器拖半天，CI 跑一次十几分钟。后来踩了无数坑才总结出一套靠谱的做法。

这篇就聊两个核心问题：怎么把 Spring Boot 镜像从 800MB 压到 200MB 以内，以及多阶段构建到底怎么用才不白写。

<!--more-->

## 先看一个反面教材

大部分新手写 Dockerfile 是这样的：

```dockerfile
FROM openjdk:11-jdk
COPY target/app.jar app.jar
CMD ["java", "-jar", "app.jar"]
```

有啥问题？三个：

1. **基础镜像太大**。`openjdk:11-jdk` 包含了全套 JDK 工具（javac、javap、jmap...），运行期根本用不上。这个镜像本身 500MB+。
2. **构建和运行混一起**。你需要在 Docker 外面先 mvn package，再 COPY jar 进去。构建环境不隔离，本地和 CI 行为不一致的问题早晚遇到。
3. **没有分层利用**。依赖没变化也重新 COPY 整个 jar，Docker 的 layer cache 等于没用上。

我第一次上线就吃了亏——本地打包没问题，CI 上 JDK 版本差了一个小版本，序列化出问题，回滚了一整个下午。

**个人经验**：不要相信"Dockerfile 能跑就行"，镜像大小直接决定你的发布速度和回滚成本。800MB 的镜像传到阿里云镜像仓库要 3 分钟，200MB 的只要 30 秒。你不想线上出问题的时候干等三分钟。

## 多阶段构建的正确姿势

多阶段构建不是炫技，是让你在同一个 Dockerfile 里做完构建和打包，最后只拿最小产物。

### 第一阶段：用 Maven 编译

```dockerfile
# Stage 1: build
FROM maven:3.8.6-eclipse-temurin-11 AS builder
WORKDIR /build
COPY pom.xml .
RUN mvn dependency:go-offline -B
COPY src ./src
RUN mvn package -DskipTests -B
```

`dependency:go-offline` 这步很关键。先把 pom.xml COPY 进去下载依赖，再把源码 COPY 进去编译。这样只要 pom.xml 没变，依赖那层就不会失效，CI 能省一半时间。

### 第二阶段：用 JRE 跑

```dockerfile
# Stage 2: runtime
FROM eclipse-temurin:11-jre-alpine
WORKDIR /app
COPY --from=builder /build/target/app.jar app.jar
EXPOSE 8080
CMD ["java", "-jar", "app.jar"]
```

这个基础镜像从 500MB 降到了 80MB 左右。加上 jar 包，最终镜像大概 180MB。

**踩坑记录**：别直接用 `alpine` 裸镜像然后手动装 JDK。alpine 用的 musl libc，和 glibc 有不兼容的地方，我遇到过 Elasticsearch client 连不上的问题。直接用 `*-jre-alpine` 这种官方组合镜像最稳。

## 镜像瘦身的几板斧

多阶段构建只是第一步，想压到 150MB 以内还得上手段。

### 1. jlink 定制运行时

JDK 11+ 可以用 jlink 剪裁 JRE，只保留你实际用到的模块。

```dockerfile
FROM eclipse-temurin:11-jdk-alpine AS jre-build
RUN jlink \
    --add-modules java.base,java.logging,java.sql,java.naming,java.management,java.security.jgss,java.instrument,jdk.unsupported \
    --strip-debug \
    --no-man-pages \
    --no-header-files \
    --compress=2 \
    --output /jre

FROM alpine:3.16
ENV JAVA_HOME=/jre
ENV PATH="${JAVA_HOME}/bin:${PATH}"
COPY --from=jre-build /jre $JAVA_HOME
```

这么搞完，基础运行时大概 40MB。Spring Boot 应用 + 依赖大概 50MB，总镜像 90MB 左右。

怎么知道需要哪些模块？跑一遍 `jdeps` 分析你的 jar：

```bash
jdeps --print-module-deps target/app.jar
# 输出：java.base,java.logging,java.sql,java.naming...
```

### 2. 分层提取依赖

Spring Boot 的 fat jar 把依赖和业务代码打在一起，不利于 Docker layer cache。可以用分层工具把依赖和业务分离：

```dockerfile
# 先提取分层信息
RUN java -Djarmode=layertools -jar app.jar extract

# 分层 COPY
COPY --from=builder /build/dependencies/ ./
COPY --from=builder /build/spring-boot-loader/ ./
COPY --from=builder /build/snapshot-dependencies/ ./
COPY --from=builder /build/application/ ./
```

好处是显而易见的——你改一行业务代码，只需要重新传最后一层。这在 CI 流水线里效果很明显，部署 sprint 版本时基本秒传。

### 3. 压缩层

```dockerfile
RUN docker-slim build --target your-app:latest
```

或者用 Docker 自带的 `--squash` 实验特性。不过说实话，如果前两步做完了，这一步收益不大，我一般跳过。

## 几个你一定会遇到的坑

**时区问题**：alpine 镜像默认 UTC，中国时区要手动加：

```dockerfile
RUN apk add --no-cache tzdata
ENV TZ=Asia/Shanghai
```

**内存限制**：容器里不设 JVM 参数会在高负载时 OOM：

```dockerfile
CMD ["java", "-XX:+UseContainerSupport", "-XX:MaxRAMPercentage=75.0", "-jar", "app.jar"]
```

Java 10+ 的 `UseContainerSupport` 能感知 CGroup 限制，但你得把比例设好。我一般留 25% 给操作系统和监控。

**健康检查**：K8s 里的存活探针别等 30 秒超时：

```dockerfile
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:8080/actuator/health || exit 1
```

**个人经验**：有一个问题我搞了三天——alpine 镜像里 java 进程 PID 不是 1，docker stop 发 SIGTERM 收不到。解决方案是加 `exec`：

```dockerfile
CMD exec java -jar app.jar
```

或者用 `tini` 做 init 进程。不加的话，docker stop 会变成 SIGKILL，你的优雅关闭逻辑全白写了。

## 最终版 Dockerfile

```dockerfile
FROM eclipse-temurin:11-jdk-alpine AS builder
WORKDIR /build
COPY pom.xml .
RUN mvn dependency:go-offline -B
COPY src ./src
RUN mvn package -DskipTests -B
RUN java -Djarmode=layertools -jar target/app.jar extract

FROM eclipse-temurin:11-jre-alpine
WORKDIR /app
RUN apk add --no-cache tzdata curl
ENV TZ=Asia/Shanghai

COPY --from=builder /build/dependencies/ ./
COPY --from=builder /build/spring-boot-loader/ ./
COPY --from=builder /build/snapshot-dependencies/ ./
COPY --from=builder /build/application/ ./

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:8080/actuator/health || exit 1

CMD exec java -XX:+UseContainerSupport -XX:MaxRAMPercentage=75.0 -jar app.jar
```

镜像大小：约 120MB。构建时间：首次 3 分钟，后续只改代码的话 30 秒。

从 800MB 到 120MB，压缩了 85%——不只是省磁盘空间，更重要的是部署速度和 K8s 集群的资源利用率。上线测试环境能串行改并行，CI 流水线缩短一半，回滚时间从 3 分钟变成 20 秒。这些数字才是 Docker 化真正该追求的。

---

📌 本文是「Spring Boot 生产实战」系列第 3 篇。前一篇聊了 [日志规范 + 链路追踪](/posts/springboot-2-logging)，下一篇讲配置中心与动态刷新。
