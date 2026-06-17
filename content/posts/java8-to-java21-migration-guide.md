+++
date = '2026-06-15T23:50:00+08:00'
draft = false
title = '从 Java 8 到 Java 21：新版特性实用迁移指南'
tags = ['Java', 'Java 21', '迁移', 'Spring Boot']
+++

说实话，我公司那个 Java 8 项目跑了快 5 年了，一直没人敢动。去年领导说"明年 Java 8 要停商业支持了，必须升"，全组面面相觑——谁都不想接这个活。

但真正动手之后发现，**从 Java 8 升到 Java 21 没有想象中那么可怕**。这篇文章我把迁移过程中最实用的新特性整理出来，全是实战经验，不是官方文档复述。

---

## 核心结论

先说结论，省得你们看到一半：

- **迁移路线**：Java 8 → Java 11（过渡） → Java 17（LTS） → Java 21（最新 LTS）
- **最需要改的**：模块化（`module-info.java`）、移除的 API（`finalize()`、`javax.*`→`jakarta.*`）
- **改动量**：一个中等规模的 Spring Boot 项目，纯代码改造大约 2-3 天
- **最大收益**：Virtual Threads（虚拟线程）、Record、Pattern Matching、ZGC

---

## 第一步：从 Java 8 到 Java 11

Java 9 到 11 这三年变了太多。最核心的几个：

### 1. 模块化（JPMS）

这是迁移中最容易出问题的点。

```java
// Java 8 时代：随便 import
import com.sun.net.ssl.internal.ssl.Provider;
// Java 9+：模块化后，内部 API 被封了
// ❌ 编译报错：package com.sun.net.ssl.internal.ssl is not visible
```

**解决方案**：检查项目中所有 `com.sun.*`、`sun.misc.*` 的引用，替换成公开 API。

```java
// 改前
import sun.misc.BASE64Encoder;
// 改后
import java.util.Base64;
Base64.getEncoder().encodeToString(bytes);
```

我遇到过的一个坑：有个老项目用了 `sun.reflect.Reflection.getCallerClass()`，Java 9 直接封了。换成 `StackWalker` 搞定。

### 2. 移除的 API

| Java 8 | Java 11+ 替代 |
|--------|-------------|
| `javax.xml.bind.*` (JAXB) | Maven 加依赖或换 JSON |
| `javax.annotation.PostConstruct` | 还在但别用了 |
| `finalize()` | `Cleaner` 或 `AutoCloseable` |
| `Thread.destroy()`/`stop()` | 早就废弃了，删掉 |

我之前维护的一个支付项目，JAXB 用了 30 多个地方。迁移当天我写了个脚本批量替换，花了半天。

### 3. HTTP Client（替代古老 URLConnection）

```java
// Java 11+ 原生 HTTP Client
HttpClient client = HttpClient.newHttpClient();
HttpRequest request = HttpRequest.newBuilder()
    .uri(URI.create("https://api.example.com/data"))
    .header("Accept", "application/json")
    .GET()
    .build();
HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString());
```

比起 `HttpURLConnection` 那套，这 API 舒服太多了。而且支持 HTTP/2 和 WebSocket。

---

## 第二步：从 Java 11 到 Java 17

Java 17 是 LTS，很多实用特性到位了。

### 4. 密封类（Sealed Class）

```java
// Java 17
public sealed class Shape permits Circle, Rectangle, Triangle {
    // ...
}
public final class Circle extends Shape { /* ... */ }
public final class Rectangle extends Shape { /* ... */ }
public final class Triangle extends Shape { /* ... */ }
```

说白了就是**告诉编译器"这个接口就这几个实现类"**。做领域模型设计的时候特别好用。

举个例子，我们有个支付系统，支付方式就三种：微信、支付宝、银行卡。用 sealed class 之后，switch 不用写 default 分支了。

### 5. 模式匹配（Pattern Matching for instanceof）

```java
// Java 8 写法
if (obj instanceof String) {
    String s = (String) obj;
    System.out.println(s.length());
}
// Java 17 写法
if (obj instanceof String s) {
    System.out.println(s.length());
}
```

看着改动不大，但写多了省不少样板代码。配合 switch 更香：

```java
// Java 17+
return switch (obj) {
    case String s -> "字符串: " + s.length();
    case Integer i -> "数字: " + i;
    case null -> "null!";
    default -> "其他";
};
```

### 6. Record（数据载体终极形态）

```java
// 一行顶几十行
public record User(Long id, String name, String email) {}
```

自动生成：构造函数、getter、`equals()`、`hashCode()`、`toString()`。

我之前写 DTO 类最烦的就是写 `equals` 和 `hashCode`，有 Record 之后，数据类全是 Record，代码量少了一半。JSON 序列化（Jackson 和 FastJSON 2.0）都支持。

---

## 第三步：从 Java 17 到 Java 21

Java 21 是 2023 年发布的最新 LTS，也是最值得升级的版本。

### 7. 虚拟线程（Virtual Threads）—— 最大的亮点

```java
// Java 21：百万级并发不是梦
try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    for (int i = 0; i < 10_000; i++) {
        executor.submit(() -> {
            // 每个任务一个虚拟线程
            handleRequest();
        });
    }
}
```

**虚拟线程不是让代码跑更快，而是让线程更轻量。** 一个虚拟线程占用约几 KB，一个平台线程约 1MB。10 万个虚拟线程 = 10 万个平台线程 ≈ 100GB vs 几百 MB。

Spring Boot 3.x + Tomcat 10.1 已经支持虚拟线程。配置一行：

```properties
# application.properties
spring.threads.virtual.enabled=true
```

我们上线之后，之前动不动 200 线程池打满的场景再也没出现了。

**但要注意**：虚拟线程里别用 `synchronized`（会 pin 住载体线程），用 `ReentrantLock` 替代。

```java
// ❌ 在虚拟线程里
synchronized (lock) { ... }

// ✅ 改成
private final Lock lock = new ReentrantLock();
lock.lock();
try { ... } finally { lock.unlock(); }
```

### 8. 字符串模板（String Templates，Preview）

```java
// Java 21（Preview）
String name = "Java";
String message = STR."Welcome to \{name}!";
// → "Welcome to Java!"
```

比拼字符串方便多了，还自动处理转义。还在 preview 阶段，生产环境等 Java 23/24 稳定再说。

### 9. 结构化并发（Structured Concurrency，Preview）

```java
// 传统写法：需要自己管理线程池和异常
Future<String> user = executor.submit(() -> fetchUser());
Future<Integer> order = executor.submit(() -> fetchOrder());
String u = user.get();  // 需要处理 InterruptedException
int o = order.get();

// Java 21（Preview）
try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {
    Future<String> user = scope.fork(() -> fetchUser());
    Future<Integer> order = scope.fork(() -> fetchOrder());
    scope.join();
    scope.throwIfFailed();
    return new Response(user.resultNow(), order.resultNow());
}
```

出错时自动取消所有子任务，生命周期清晰。Preview 特性，预计 Java 24+ 稳定。

---

## Spring Boot 迁移注意

如果你的项目用 Spring Boot，迁移顺序要跟 Spring Boot 版本同步：

| Spring Boot | 最低 Java | 推荐 Java |
|-------------|-----------|-----------|
| 2.x (维护期) | 8 | 11 或 17 |
| 3.0 - 3.1 | 17 | 17 |
| 3.2+ | 17 | 21 |

**最大的坑**：Spring Boot 3.x 把 `javax.*` 换成了 `jakarta.*`。

```xml
<!-- 改前 -->
<dependency>
    <groupId>javax.servlet</groupId>
    <artifactId>javax.servlet-api</artifactId>
</dependency>

<!-- 改后 -->
<dependency>
    <groupId>jakarta.servlet</groupId>
    <artifactId>jakarta.servlet-api</artifactId>
</dependency>
```

所有 `import javax.*` 要改成 `import jakarta.*`。这个改动量取决于你项目有多少第三方依赖。好消息是大部分主流库都支持了。

---

## 迁移 Checklist

```java
[ ] 检查 JDK 版本：java -version
[ ] 检查框架兼容性：Spring Boot / MyBatis / Netty 等
[ ] 替换 javax.* → jakarta.*（如果升 Spring Boot 3.x）
[ ] 移除 com.sun.* / sun.misc.* 引用
[ ] 替换废弃 API：finalize → Cleaner
[ ] 模块化：添加 module-info.java（可选）
[ ] 单元测试跑一遍：mvn test
[ ] 集成测试：接口 + 压力测试
[ ] 灰度发布：先一台机器验证
```

---

## 总结

说实话，从 Java 8 升到 Java 21 没有想象中那么恐怖。**核心改动就三块**：模块化封了内部 API、javax 换 jakarta、几个废弃方法要替换。真正的收益是 Virtual Threads 和 Record，这两项在日常开发中样样都能用到。

如果你也在计划迁移，建议**先升到 Java 17 跑一段时间**，稳定后再冲 Java 21。不建议从 8 直接跳到 21，中间隔着太多变化，排查问题会很头疼。

你公司用哪个 Java 版本？有迁移计划吗？留言说说你们的迁移故事。
