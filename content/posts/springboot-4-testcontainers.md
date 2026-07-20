+++
title = "Spring Boot 生产实战系列四：Testcontainers 集成测试——告别 H2 内存数据库"
slug = "springboot-4-testcontainers"
keywords = ["Testcontainers", "集成测试"]
difficulty = "实战"
target_length = 2500
series_name = "Spring Boot 生产实战"
series_number = 4
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-07-20
+++

我刚工作那几年，团队里写集成测试的标配姿势是：搞个 H2 内存数据库，启动时跑一遍 SQL 脚本，测完一扔，干净利落。

当时觉得这方案绝了——不用装 MySQL，CI 里直接跑，多快啊。

直到线上出了几次事故，我才发现 H2 这玩意儿根本不能当 MySQL 用。最典型的一次，一个字段在 MySQL 里用 `json` 类型，H2 不认，测试里我们用 `varchar` 存 JSON 字符串，所有测试全绿。上线后 MySQL 的 JSON 函数抛了一堆异常。

从那以后，我再也不敢拿 H2 做集成测试了。如果你还在这么干，这篇文章就是写给你的。

## 问题出在哪？

H2 的问题不是它不好，而是它**不是 MySQL**。

哪怕你用 `MODE=MySQL` 启动 H2，也只是模拟了 MySQL 的一部分行为。下面这几个坑我全踩过：

- **语法差异**：H2 不支持 MySQL 的 `JSON_EXTRACT`、`STRAIGHT_JOIN`、`ON DUPLICATE KEY UPDATE` 的部分写法
- **函数行为不一致**：`GROUP_CONCAT` 在 H2 和 MySQL 里的默认长度限制不同
- **类型映射问题**：MySQL 的 `TINYINT(1)` 默认转成 `BOOLEAN`，H2 不会
- **锁机制不同**：MySQL 的 `SELECT ... FOR UPDATE` 加锁行为跟 H2 不完全一样，并发测试基本是白测

说白了，用 H2 测 MySQL 兼容性，就像用电动车练手动挡——大概架子能看，真上路就露馅。

## Testcontainers 是什么？

简单说，Testcontainers 是一个 Java 库，能在跑测试的时候用 Docker 拉起真正的中间件实例：MySQL、Redis、Kafka、Elasticsearch……测完自动销毁。

你需要的是 Docker，不是模拟器。跑的是真正的 MySQL，不是号称兼容 MySQL 的 H2。

我从 Testcontainers 1.15 版本开始用，到现在项目里几乎所有集成测试都靠它跑。

## Spring Boot 里怎么用

### 1. 加依赖

Maven：

```xml
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>testcontainers</artifactId>
    <scope>test</scope>
</dependency>
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>mysql</artifactId>
    <scope>test</scope>
</dependency>
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>junit-jupiter</artifactId>
    <scope>test</scope>
</dependency>
```

如果你用 Gradle，对应的依赖差不多，就不贴了。

### 2. 写一个基础容器配置

先定义一个基类，所有集成测试继承它：

```java
@Testcontainers
public abstract class IntegrationTestBase {

    @Container
    static MySQLContainer<?> mysql = new MySQLContainer<>("mysql:8.0.33")
        .withDatabaseName("testdb")
        .withUsername("test")
        .withPassword("test");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", mysql::getJdbcUrl);
        registry.add("spring.datasource.username", mysql::getUsername);
        registry.add("spring.datasource.password", mysql::getPassword);
        registry.add("spring.datasource.driver-class-name", mysql::getDriverClassName);
    }
}
```

`@DynamicPropertySource` 会把容器的连接信息动态注入 Spring 的 `DataSource` 配置，测试类不需要改任何配置。

### 3. 写测试类

```java
@SpringBootTest
class UserRepositoryTest extends IntegrationTestBase {

    @Autowired
    private UserRepository userRepository;

    @Test
    void shouldSaveAndFindUser() {
        User user = new User("张三", "zhangsan@example.com");
        userRepository.save(user);

        Optional<User> found = userRepository.findByEmail("zhangsan@example.com");
        assertThat(found).isPresent();
        assertThat(found.get().getName()).isEqualTo("张三");
    }
}
```

没了，就是这么简单。数据库操作跑的是真实 MySQL，SQL 行为跟线上一致。

## 几个踩过坑的实战经验

### 1. 容器启动策略：共用实例

默认每个测试类里声明 `@Container` 会启动一个独立的 MySQL 容器。如果你的测试类超过 10 个，CI 里 Docker 资源直接爆炸。

我踩过这个坑。解决方案是把容器声明为 `static` 并加上 `@Container`，这样所有测试类共享一个容器实例：

```java
@Container
static MySQLContainer<?> mysql = new MySQLContainer<>("mysql:8.0.33");
```

或者直接放在基类里，所有集成测试都继承它。整个测试套件只启动一次容器，速度快很多。

### 2. Flyway 迁移和测试数据分离

很多人用一个 `data.sql` 既做 schema 迁移又塞测试数据，这是埋坑。

正确的做法是：

- Flyway / Liquibase 管理 schema 迁移（有版本号，可追溯）
- 测试数据用 `@Sql` 注解或者 Builder 模式在测试里构造

```java
@Test
@Sql("/sql/user-test-data.sql")
void shouldReturnActiveUsers() {
    // 测试数据在 user-test-data.sql 里
}
```

这样迁移脚本和生产一致，测试数据按需加载，互不干扰。

### 3. 不要用固定端口

```java
// ❌ 不要这样
new MySQLContainer<>("mysql:8.0.33")
    .withFixedExposedPort(3306, 3306);
```

固定端口会导致两个问题：一是本机 MySQL 占用 3306 时冲突，二是并行跑测试时端口争用。

Testcontainers 默认随机分配端口，这才是正确用法。

### 4. CI 里缓存镜像

Testcontainers 每次跑测试都要拉镜像，没有的话 CI 跑一次 5 分钟起步。

GitHub Actions 里可以这样缓存 Docker 镜像：

```yaml
- name: Cache Docker images
  uses: ScribeMD/docker-cache@0.5.0
  with:
    key: docker-${{ hashFiles('**/pom.xml') }}
```

或者提前 pull 镜像：

```bash
docker pull mysql:8.0.33
```

我第一次配 CI 的时候没做缓存，每个 PR 跑 8 分钟，被同事吐槽了好久。

### 5. 选对镜像版本，跟生产一致

```java
// ✅ 跟生产一致
new MySQLContainer<>("mysql:8.0.33");

// ❌ 默认 5.7，跟生产对不上
new MySQLContainer<>();
```

还有一个细节：指定 `mysql:8.0.33` 而不是 `mysql:8`。后者是个浮动 tag，哪天镜像更新了，你的测试可能突然挂掉。固定到小版本号，可控。

## 不只是 MySQL

Testcontainers 不止能测数据库。我现在项目里用得最多的几个模块：

- **MySQL / PostgreSQL**：数据访问层测试
- **Redis**：缓存逻辑，测试 `@Cacheable` 和过期策略
- **Kafka**：消息发送和消费的端到端测试
- **Elasticsearch**：搜索功能的集成测试

每个都是 `GenericContainer` 配置启动参数和等待策略，写法基本一致。

比如测 Redis：

```java
@Container
static GenericContainer<?> redis = new GenericContainer<>("redis:7-alpine")
    .withExposedPorts(6379);
```

一行代码拉起 Redis，测完自动清掉，不用自己管理实例。

## 性能怎么样？

这是很多人第一反应：起容器是不是很慢？

第一次启动确实慢，因为要拉镜像。但是后续运行很快，Docker 层有缓存。实测 20 个测试类，共用容器实例，跑完大概 40 秒。H2 大概 15 秒，多花 25 秒买的是"线上的 SQL 行为一致"——这交易我觉得值。

我团队去年把所有集成测试从 H2 迁移到 Testcontainers，线上因 SQL 兼容性导致的事故直接降为零。一个 bug 没修，只是换了测试方式。

## 总结

一句话：**集成测试要用生产一样的数据库，而不是长得像的模拟品。**

H2 做单元测试查几行数据没问题，但涉及 SQL 执行计划、函数行为、锁机制的验证，必须上 Testcontainers。

迁移门槛很低：加依赖 → 写基类 → 改测试，一个下午就能搞定。你踩过的那些 H2 的坑，值得花这一个下午。

📌 本文是「Spring Boot 生产实战」系列第 4 篇。前 3 篇讲了配置管理、日志、异常处理，下一篇聊聊生产监控与告警，尽量不走弯路。
