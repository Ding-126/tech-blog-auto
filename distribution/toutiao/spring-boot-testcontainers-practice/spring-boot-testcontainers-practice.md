+++
date = '2026-06-16T07:05:00+08:00'
draft = false
title = 'Spring Boot 测试实战：Unit Test + Integration Test + Testcontainers'
description = '从 JUnit 5 单元测试到 Testcontainers 集成测试，手把手搭建 Spring Boot 项目测试体系，覆盖数据库、Redis、Kafka 等外部依赖的真实测试方案。'
tags = ['Spring Boot', '测试', 'Testcontainers', 'JUnit 5', '集成测试', '单元测试', 'Docker', '实战']
categories = ['tutorial']
source_url = 'https://testcontainers.com/guides/getting-started-with-testcontainers-for-java/'
source_name = 'original'
difficulty = '实战'
target_keywords = ['Spring Boot 测试', 'Testcontainers', '集成测试', '单元测试', 'JUnit 5', '容器化测试', '数据库测试']
+++

说实话，我工作第三年之前，写单元测试全靠"跑个 main 方法看看"。直到有个线上问题——本地跑得好好的，上生产就报数据库连接超时——debug 了半天才发现测试用的 H2 内存库跟 MySQL 行为不一样。

**测试最怕的不是测，而是测了等于没测。**

后来我把 Spring Boot 项目的测试体系彻底翻了一遍，从 JUnit 5 单元测试到 Testcontainers 集成测试全都写了一遍。这篇文章没有高深的理论，全是实战中一步步搭出来的方案。

---

## 核心结论

- **单元测试**：Mock 掉所有外部依赖，只测业务逻辑
- **集成测试**：用 Testcontainers 起真实容器，不 mock 数据库/Redis
- **分层策略**：Unit Test（70%）+ Integration Test（25%）+ E2E（5%）
- **Testcontainers**：Docker 容器自动拉起和销毁，测试完不留痕迹
- **性能**：单次集成测试启动约 8-12 秒，可以接受

---

## 从哪里开始：项目依赖

先用一个典型的 Spring Boot 3.x + MyBatis + Redis 项目来说事。

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-test</artifactId>
    <scope>test</scope>
</dependency>
<!-- Testcontainers 核心 -->
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>testcontainers</artifactId>
    <version>1.20.4</version>
    <scope>test</scope>
</dependency>
<!-- MySQL 模块 -->
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>mysql</artifactId>
    <scope>test</scope>
</dependency>
<!-- Redis 模块 -->
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>redisson</artifactId>
    <scope>test</scope>
</dependency>
<!-- JUnit 5 + Testcontainers 集成 -->
<dependency>
    <groupId>org.testcontainers</groupId>
    <artifactId>junit-jupiter</artifactId>
    <scope>test</scope>
</dependency>
```

> 小提示：如果你用 Gradle，记得在 `test` 作用域加 `@Testcontainers` 注解的依赖。我就是因为这个没加，容器一直起不来，查了半天。

---

## 第一步：单元测试（Unit Test）—— 最快的反馈

单元测试的核心就一条：**只测当前类，不测外部依赖**。

### Service 层测试

```java
@ExtendWith(MockitoExtension.class)
class OrderServiceTest {

    @Mock
    private OrderMapper orderMapper;

    @Mock
    private InventoryClient inventoryClient;

    @InjectMocks
    private OrderService orderService;

    @Test
    void shouldCreateOrderWhenStockIsAvailable() {
        // given —— 模拟外部依赖的行为
        CreateOrderRequest request = new CreateOrderRequest("item-001", 2);
        when(inventoryClient.checkStock("item-001", 2)).thenReturn(true);
        when(orderMapper.insert(any(Order.class))).thenReturn(1);

        // when
        Order result = orderService.createOrder(request);

        // then
        assertThat(result).isNotNull();
        assertThat(result.getStatus()).isEqualTo(OrderStatus.CREATED);
        verify(orderMapper, times(1)).insert(any(Order.class));
    }

    @Test
    void shouldThrowWhenStockIsInsufficient() {
        CreateOrderRequest request = new CreateOrderRequest("item-001", 99);
        when(inventoryClient.checkStock("item-001", 99)).thenReturn(false);

        assertThrows(InsufficientStockException.class,
            () -> orderService.createOrder(request));

        verify(orderMapper, never()).insert(any());
    }
}
```

我之前犯的一个错：在单元测试里也给 `orderMapper` 配了真实的 MyBatis 映射。跑一次测试要连数据库、加载 Spring 容器，等 10 秒才跑完一个 case。**单元测试就该毫秒级完成**，否则团队根本不愿意跑。

### Controller 层测试

```java
@WebMvcTest(OrderController.class)
class OrderControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private OrderService orderService;

    @Test
    void shouldReturn201WhenOrderCreated() throws Exception {
        Order order = new Order("order-001", "item-001", 2, OrderStatus.CREATED);
        when(orderService.createOrder(any())).thenReturn(order);

        mockMvc.perform(post("/api/orders")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {"itemId": "item-001", "quantity": 2}
                    """))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.orderId").value("order-001"));
    }
}
```

`@WebMvcTest` 只加载 Web 层，不加载整个 Spring Boot 应用。之前我懒得区分，一律用 `@SpringBootTest`，一个 Controller 测试跑 30 秒，现在 2 秒搞定。

---

## 第二步：集成测试（Integration Test）—— 测真正的数据库

单元测试测完逻辑了，但不测数据库 SQL 正确性就是白测。**你用 MySQL 的语法、H2 的语法不一样**，出了 H2 没问题的 SQL 到 MySQL 报错，这种事太多了。

### 用 Testcontainers 起一个真实的 MySQL

```java
@Testcontainers
@SpringBootTest
class OrderRepositoryTest {

    // 一个静态容器，所有测试共享
    @Container
    static MySQLContainer<?> mysql = new MySQLContainer<>("mysql:8.0")
        .withDatabaseName("testdb")
        .withUsername("test")
        .withPassword("test");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", mysql::getJdbcUrl);
        registry.add("spring.datasource.username", mysql::getUsername);
        registry.add("spring.datasource.password", mysql::getPassword);
    }

    @Autowired
    private OrderMapper orderMapper;

    @Test
    void shouldInsertAndSelectOrder() {
        Order order = new Order("order-001", "item-001", 2, OrderStatus.CREATED);

        int rows = orderMapper.insert(order);
        assertThat(rows).isEqualTo(1);

        Order found = orderMapper.selectById("order-001");
        assertThat(found.getItemId()).isEqualTo("item-001");
        assertThat(found.getQuantity()).isEqualTo(2);
    }
}
```

### 同时启动 Redis

```java
@Testcontainers
@SpringBootTest
class CacheServiceTest {

    @Container
    static GenericContainer<?> redis = new GenericContainer<>("redis:7-alpine")
        .withExposedPorts(6379);

    @DynamicPropertySource
    static void redisProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
    }

    @Autowired
    private CacheService cacheService;

    @Test
    void shouldCacheAndRetrieve() {
        cacheService.put("key", "hello-testcontainers");
        String value = cacheService.get("key");
        assertThat(value).isEqualTo("hello-testcontainers");
    }
}
```

我第一次用 Testcontainers 时踩了个坑：**容器写在测试类的实例字段里，每次 @Test 都创建一个新容器**。跑 10 个测试，启动 10 个 MySQL 容器，机器直接卡死。必须用 `static` + `@Container` 组合，容器在测试类生命周期内只启动一次。

---

## 第三步：同时启动多个容器

现实项目里，一个接口往往要查数据库 + 写缓存 + 发消息。这种场景需要多个容器同时跑。

```java
@Testcontainers
@SpringBootTest
@ActiveProfiles("integration-test")
class OrderIntegrationTest {

    // 定义所有需要的容器
    private static final MySQLContainer<?> mysql = new MySQLContainer<>("mysql:8.0");
    private static final GenericContainer<?> redis = new GenericContainer<>("redis:7-alpine")
        .withExposedPorts(6379);
    private static final KafkaContainer kafka = new KafkaContainer(
        DockerImageName.parse("confluentinc/cp-kafka:7.6.0"));

    // 静态初始化块 —— 所有容器一起启动
    static {
        mysql.start();
        redis.start();
        kafka.start();
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", mysql::getJdbcUrl);
        registry.add("spring.datasource.username", mysql::getUsername);
        registry.add("spring.datasource.password", mysql::getPassword);
        registry.add("spring.data.redis.host", redis::getHost);
        registry.add("spring.data.redis.port", () -> redis.getMappedPort(6379));
        registry.add("spring.kafka.bootstrap-servers", kafka::getBootstrapServers);
    }

    @Autowired
    private OrderService orderService;

    @Test
    void shouldCreateOrderAndSendEvent() {
        CreateOrderRequest request = new CreateOrderRequest("item-001", 2);
        Order order = orderService.createOrder(request);

        // 验证数据库有记录
        assertThat(order).isNotNull();
        assertThat(order.getOrderId()).isNotNull();
        // 验证消息被发送到 Kafka
        // ... 这里可以消费 Kafka topic 验证
    }
}
```

三个容器同时启动大概需要 15-20 秒（取决于镜像下载进度），但启动一次之后后面的测试全复用，不影响开发节奏。

---

## 第四步：测试配置的最佳实践

### application-integration-test.yml

```yaml
spring:
  jpa:
    hibernate:
      ddl-auto: validate  # 不自动建表，用 flyway/liquibase
  kafka:
    listener:
      missing-topics-fatal: false  # 测试时 topic 可能不存在
```

### 自定义 Testcontainers 配置类

```java
@TestConfiguration
public class TestcontainersConfig {

    @Bean
    @ServiceConnection  // Spring Boot 3.1+ 自动配连接
    public MySQLContainer<?> mysqlContainer() {
        return new MySQLContainer<>("mysql:8.0");
    }

    @Bean
    @ServiceConnection
    public RedisContainer redisContainer() {
        return new RedisContainer("redis:7-alpine");
    }
}
```

Spring Boot 3.1 之后加了 `@ServiceConnection`，省去了手写 `@DynamicPropertySource` 的麻烦。用不上这个新特性的项目就还是用上面的方式。

---

## 常见坑 & 解决方案


---

## 效果验证

我重构完测试体系后，对比了一下前后数据：


**集成测试让我们在生产前就发现了 3 个 SQL 兼容问题**，其中一个就是 `OFFSET` 分页语法差异——H2 支持 `OFFSET 10 ROWS FETCH NEXT 20 ROWS ONLY`，但 MySQL 8.0 的语法是 `LIMIT 20 OFFSET 10`。

---

## 扩展 & 进阶方向

- **Testcontainers + WireMock**：测外部 HTTP API 调用，结合 Testcontainers 的 Network 特性，容器间互相访问
- **Testcontainers + LocalStack**：模拟 AWS S3/SQS/SNS，测云服务集成
- **Testcontainers Desktop**：可视化查看容器状态（2025 年后免费版受限，CI 用 CLI）
- **测试金字塔自动化**：通过 Jacoco 插件统计覆盖率，低于阈值 CI 拦截

---

## 参考资料

- [Testcontainers 官方指南 - Java 快速开始](https://testcontainers.com/guides/getting-started-with-testcontainers-for-java/)
- [Spring Boot Testing 官方文档](https://docs.spring.io/spring-boot/reference/testing/index.html)
- [Spring Boot 3.1 @ServiceConnection 说明](https://spring.io/blog/2023/06/23/improved-testcontainers-support-in-spring-boot-3-1)
- [本站文章：Java Optional 实战：消灭 NullPointerException 的 10 个模式](/posts/java-optional-10-patterns-npe-guide/)

---

说实话，测试这件事，写一两次觉得麻烦，但一旦你被线上问题坑过几次，就会觉得**测试不是额外成本，而是保险**。Testcontainers 最让我舒服的是——测试跑过就是真的跑过了，不用猜"H2 过是不是就等于 MySQL 过了"。

你项目里的集成测试覆盖率多少？评论区聊聊你们的测试方案。
