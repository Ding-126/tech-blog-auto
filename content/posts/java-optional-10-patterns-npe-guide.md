+++
date = '2026-06-15T16:00:00+08:00'
draft = false
title = 'Java Optional 实战：消灭 NullPointerException 的 10 个模式（附代码）'
description = 'Java Optional 的 10 个实战模式：从基础用法到高级技巧，消灭 NullPointerException。每个模式都有完整代码示例和踩坑记录，适合 1-5 年经验的 Java 开发者。'
tags = ['Java', 'Optional', 'NullPointerException', '函数式编程', '代码规范', '实战教程', 'Java 8', '编程技巧']
categories = ['tutorial']
source_url = 'https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/Optional.html'
source_name = 'original'
difficulty = '入门'
target_keywords = ['Java Optional', 'Optional 使用', 'NPE 防护', 'Java 空指针', 'Optional 实战']
+++

说实话，做了这么多年 Java，最让我烦的不是业务逻辑有多复杂，而是**每写一行代码都要担心会不会报 NPE**。

你肯定也写过这种代码：

```java
if (user != null) {
    Address address = user.getAddress();
    if (address != null) {
        String city = address.getCity();
        if (city != null) {
            System.out.println(city.toUpperCase());
        }
    }
}
```

层层 if，代码又臭又长，还容易漏判。

Java 8 引入了 Optional，就是来解决这个问题的。但我也见过很多人把它用歪了——`.get()` 直接调用、用 Optional 做字段类型、甚至用 Optional 来写更长的代码……

今天我把工作中真正好用的 **10 个 Optional 模式** 整理出来，全是实战经验。

---

## 核心结论

- Optional 不是用来消灭所有 if，而是**消灭嵌套 if**
- **禁止**：把 Optional 设为类字段、方法参数、序列化对象
- **推荐**：只用做方法返回值，告诉调用者"可能为空"
- 配合 Stream API，Optional 能写出非常优雅的链式代码
- 核心原则：**能提前 return 就别包 Optional**

---

## 10 个实战模式

### 模式 1：创建 Optional 的正确姿势

```java
// ✅ 推荐：明确知道可能为空
Optional<String> opt = Optional.ofNullable(getName());

// ✅ 推荐：确定不为空（极少用）
Optional<String> opt = Optional.of("hello");  // 传 null 会抛 NPE

// ❌ 不推荐：用 Optional 包装已知非空值
Optional<String> opt = Optional.ofNullable(someMethod());  // 如果 someMethod 明确不会 null，不需要包
```

> 小经验：我见过有人把每一个方法返回值都包一层 Optional，反而让代码更难读了。只在**调用者可能忘记判空**的地方用。

### 模式 2：安全取值（最常用）

```java
// ❌ 老方式
if (user != null) {
    String name = user.getName();
}

// ✅ Optional 方式
Optional.ofNullable(user)
        .map(User::getName)
        .ifPresent(name -> System.out.println(name));
```

### 模式 3：提供默认值

```java
// ❌ 三目运算，可读性差
String name = user != null ? user.getName() : "默认用户";

// ✅ Optional + orElse
String name = Optional.ofNullable(user)
                      .map(User::getName)
                      .orElse("默认用户");

// ✅ 如果默认值需要计算（延迟执行）
String name = Optional.ofNullable(user)
                      .map(User::getName)
                      .orElseGet(() -> fetchDefaultName());
```

`orElse` 和 `orElseGet` 的区别：orElse 不管用不用都创建对象，orElseGet 是懒加载。如果默认值是简单的字符串常量，用 orElse 就行。

### 模式 4：找不到就抛异常

```java
// 从配置中心查配置，查不到就别默默处理了
String timeout = Optional.ofNullable(config.get("timeout"))
                         .orElseThrow(() -> new ConfigException("timeout 未配置"));
```

### 模式 5：链式取值（消灭多层 if）

```java
// ❌ 多层 if（最开始的例子）
if (user != null) {
    Address address = user.getAddress();
    if (address != null) {
        String city = address.getCity();
        if (city != null) {
            System.out.println(city.toUpperCase());
        }
    }
}

// ✅ 一行搞定
Optional.ofNullable(user)
        .map(User::getAddress)
        .map(Address::getCity)
        .map(String::toUpperCase)
        .ifPresent(System.out::println);
```

我第一次重构这种代码时，把 20 行的嵌套 if 改成了 4 行。同事 review 时说"这代码看着清爽多了"。

### 模式 6：过滤条件

```java
// 只处理长度大于 3 的用户名
Optional.ofNullable(user)
        .map(User::getName)
        .filter(name -> name.length() > 3)
        .ifPresent(this::processUser);
```

### 模式 7：Optional + Stream 配合

```java
// 从用户列表中找出第一个有效邮箱
List<User> users = getUsers();
String email = users.stream()
    .map(User::getEmail)
    .filter(Optional::isPresent)
    .map(Optional::get)
    .findFirst()
    .orElse("default@email.com");

// Java 9+ 有更简洁的写法（见模式 10）
```

### 模式 8：List 为空的处理

```java
// ❌ 容易漏判
List<String> list = getList();
for (String item : list) {  // list 可能为 null，炸了
    ...
}

// ✅ 安全方式
List<String> list = Optional.ofNullable(getList()).orElse(Collections.emptyList());
list.forEach(item -> ...);
```

### 模式 9：多个 Optional 组合

```java
// 从多个数据源查配置，按优先级返回
Optional<String> local = getLocalConfig("timeout");
Optional<String> remote = getRemoteConfig("timeout");
Optional<String> fallback = Optional.of("3000");

String timeout = Stream.of(local, remote, fallback)
    .filter(Optional::isPresent)
    .map(Optional::get)
    .findFirst()
    .get();
```

### 模式 10：Java 9+ 新方法

```java
// or() - 如果为空，返回另一个 Optional
Optional<String> opt = Optional.ofNullable(getName())
    .or(() -> Optional.ofNullable(getBackupName()))
    .or(() -> Optional.of("默认名"));

// ifPresentOrElse() - 非空做 A，为空做 B
Optional.ofNullable(user)
    .ifPresentOrElse(
        u -> processUser(u),
        () -> logger.warn("用户为空，跳过处理")
    );

// stream() - Optional 转 Stream（配合 flatMap 神器）
List<User> users = getUsers();
users.stream()
    .map(User::getEmail)  // 返回 Optional<String>
    .flatMap(Optional::stream)  // Java 9+ 直接转 Stream
    .collect(Collectors.toList());
```

---

## 常见误区（我踩过的坑）

### 误区 1：用 Optional 做字段类型

```java
// ❌ 千万别
public class User {
    private Optional<String> name;  // Optional 不可序列化！！
}

// ✅ 正确做法
public class User {
    private String name;
    public Optional<String> getName() {
        return Optional.ofNullable(name);
    }
}
```

### 误区 2：直接用 .get()

```java
// ❌ 危险
Optional<String> opt = Optional.ofNullable(getName());
String name = opt.get();  // 为空直接抛 NoSuchElementException

// ✅ 安全
String name = opt.orElse("默认");
```

### 误区 3：用 Optional 做方法参数

```java
// ❌ 让调用者困惑
public void process(Optional<String> name) {
    // 调用时：process(Optional.of("xxx")); 多此一举
}

// ✅ 直接用重载或默认值
public void process(String name) {
    // ...
}
public void process() {
    process("默认");
}
```

---

## 速查表

| 场景 | 推荐写法 |
|------|----------|
| 安全取值 | `Optional.ofNullable(x).map(Foo::getBar)` |
| 提供默认值 | `.orElse(defaultValue)` |
| 懒加载默认值 | `.orElseGet(() -> compute())` |
| 为空抛异常 | `.orElseThrow(() -> new Ex())` |
| 过滤条件 | `.filter(x -> condition)` |
| 链式取值 | `.map(A::getB).map(B::getC)` |
| 有值则执行 | `.ifPresent(x -> doSomething(x))` |
| 有值/无值都处理 | `.ifPresentOrElse(x -> doA(), () -> doB())` |

---

## 参考资料

- [Java Optional 官方文档](https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/util/Optional.html)
- [Oracle 官方教程：Optional](https://docs.oracle.com/javase/tutorial/java/advanced/optional.html)

---

你平时写 Java 最常遇到的 NPE 场景是什么？有没有哪次被 NPE 坑到加班？评论区说说你的故事。


 
 
 
 
 
 
 
 
 
 
