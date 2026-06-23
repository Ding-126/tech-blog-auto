+++
title = "面向面试之 JVM 系列三：类加载机制——双亲委派模型与 Tomcat 为何要打破它"
slug = "jvm-3-tomcat"
keywords = ["类加载", "双亲委派", "Tomcat"]
difficulty = "进阶"
target_length = 2000
series_name = "面向面试之 JVM"
series_number = 3
series_total = 5
draft = false
categories = ["tutorial"]
date = 2026-06-24
+++

Java 类加载机制是面试里的常客，几乎每场高级一点的面试都会问到。大部分人能说出双亲委派模型的概念，但问到"Tomcat 为什么要打破它"，就卡住了。

今天把这两个问题串起来讲清楚。

## 先搞清楚：类加载器有哪些

JVM 自带了三个类加载器：

- **启动类加载器（Bootstrap ClassLoader）**——加载 `rt.jar`、`java.lang.*` 这些核心类。C++ 实现，Java 里拿不到它的引用。
- **扩展类加载器（Extension ClassLoader）**——加载 `jre/lib/ext` 目录下的类。
- **应用类加载器（Application ClassLoader）**——加载 `classpath` 上的类，我们自己写的代码默认由它加载。

还有个东西叫"自定义类加载器"，继承 `ClassLoader` 重写 `findClass` 就行。我们平时写代码基本不会自己写类加载器，但框架里到处都是。

## 双亲委派模型：一句话说清楚

**当一个类加载器收到加载请求时，它不会自己先加载，而是委托给父加载器去尝试，每一层都往上推，直到 Bootstrap ClassLoader。父加载器能加载就它加载，加载不了才往下退。**

伪代码长这样：

```java
protected Class<?> loadClass(String name, boolean resolve) {
    // 1. 先检查自己有没有加载过
    Class<?> c = findLoadedClass(name);
    if (c == null) {
        try {
            // 2. 让父加载器先试试
            if (parent != null) {
                c = parent.loadClass(name, false);
            } else {
                c = findBootstrapClassOrNull(name);
            }
        } catch (ClassNotFoundException e) {
            // 3. 父加载器加载不了，自己来
            c = findClass(name);
        }
    }
    return c;
}
```

翻译成人话：**你先上，你不行我再上。**

### 为什么这么设计？

两个核心原因。

**第一，安全。** 防止你写一个 `java.lang.String` 替换掉 JDK 自带的。双亲委派保证核心 API 永远由 Bootstrap ClassLoader 加载，你写的同名类根本没机会被加载。

**第二，避免重复加载。** 同一个类在 JVM 里只会被加载一次，所有类加载器共用这个结果。没有双亲委派，每个类加载器都自己加载一次，内存里就有多份相同的类，类型比较全挂。

## 面试重点：什么情况下需要打破双亲委派？

**当你想让子加载器先加载类，或者想让同一个类被不同加载器各加载一份时，就需要打破。**

典型场景：

1. **JDBC 的 SPI 机制**——DriverManager 是 Bootstrap ClassLoader 加载的，但它要调用各个数据库厂商的实现类。那些类在 classpath 上，Bootstrap ClassLoader 加载不了。所以 JDK 搞了个 `ThreadContextClassLoader`，让驱动类由应用类加载器加载。这叫"逆向委托"。

2. **Tomcat 容器**——这是最常见的面试题场景，重点展开说。

## Tomcat 为什么要打破双亲委派？

Tomcat 不是"打破"那么简单，它搞了一套自己的加载规则。

先说痛点。Tomcat 要同时部署多个 Web 应用，每个应用有自己的依赖。**如果都用默认的双亲委派，A 应用用 Spring 5.0，B 应用用 Spring 6.0，类会被加载成同一份，冲突是迟早的事。**

Tomcat 的做法是给每个 Web 应用一个独立的 WebAppClassLoader，加载逻辑变了：

**1. 先检查自己有没有加载过。**
**2. 尝试用自己的 findClass 加载当前应用的类。**
**3. 如果找不到，才委托给父加载器。**

也就是把双亲委派的顺序倒过来了：**先自己找，找不到再往上问。**

### 有没有例外？

有。JVM 核心类（`java.*`）不走这个流程——它们仍然由 Bootstrap ClassLoader 先加载。Tomcat 不会傻到让你自己加载 `java.lang.String`。

### 这个设计解决了什么问题

我说的直白点：**隔离。**

你部署 10 个应用上去，每个有自己的 classloader。应用 A 挂了，类加载器被回收，不影响应用 B。如果都用同一个类加载器，一个应用加载了某个类的静态变量炸了，所有应用跟着遭殃。

我在某家公司做过一次线上排查，当时是多个应用部署在同一个 Tomcat 实例里，其中一个应用引入了旧版本的 Guava，另一个用的新版。两个版本里某个类的行为完全不同，但被同一个类加载器加载了，结果就是那个应用时灵时不灵。查了三天才发现是类冲突——如果当时用的是独立的 WebAppClassLoader，这问题根本不会出现。

## 说说 OSGi 的极端做法

Tomcat 还算是温和的打破，OSGi 更激进——每个模块（Bundle）完全独立，类加载全靠声明导入导出，完全没有双亲委派可言。好处是模块化做到极致，坏处是复杂到大多数团队玩不转。我见过一个项目用了 OSGi，最后连开发人员自己都搞不清楚哪个类从哪里加载的。

面试里提到 OSGi 会让面试官觉得你见识广，但别深入展开——大多数面试官自己也没真用过。

## 如何自定义一个类加载器？

面试偶尔会让写一个。模板长这样：

```java
public class MyClassLoader extends ClassLoader {
    @Override
    protected Class<?> findClass(String name) {
        // 读取字节码
        byte[] bytes = loadClassFromFile(name);
        // 定义类
        return defineClass(name, bytes, 0, bytes.length);
    }
}
```

注意是重写 `findClass`，不是 `loadClass`。如果你重写 `loadClass`，相当于把整个双亲委派流程重新定义了——那就是真正的"打破"了。

## 总结一下面试话术

面试被问到"双亲委派"时，按这个顺序回答，基本能拿满分：

1. 先说概念：类加载器的层级关系 + 向上委托的流程
2. 说设计目的：安全 + 避免重复加载
3. 说怎么打破：重写 `loadClass` 或通过线程上下文类加载器
4. 举实际例子：JDBC SPI、Tomcat WebAppClassLoader
5. 说为什么要有这些打破：类隔离、版本冲突

最后加一句：**"双亲委派是 JVM 的默认行为，框架需要时可以打破它，但没有绝对正确的方式，取决于你要解决什么问题。"**

这是我面试别人时最想听到的答案——有理解、有实践、不背书。

---

📌 本文是「面向面试之 JVM」系列第 3 篇，下一篇讲 JVM 内存模型与对象的"一生"。
