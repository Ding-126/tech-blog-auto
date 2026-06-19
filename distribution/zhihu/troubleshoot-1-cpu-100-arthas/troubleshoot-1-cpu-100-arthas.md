# 

上周五下午四点，我正划水等下班，手机突然震个不停——钉钉报警群里炸了。

线上两台应用服务器的 CPU 同时飙到 100%，用户反馈页面打不开，接口响应从 50ms 涨到了 8 秒。

实话实说，那一刻确实慌了。但干了这么多年 Java，总得有点吃饭的本事。深呼吸，打开终端，开始排查。

这期就聊聊线上 CPU 100% 的完整排查流程，我尽量写成你也碰得到的样子，而不是教科书那种"情况一出现请执行步骤 A"。

## 第一步：确认现象，别被监控骗了

监控显示 CPU 100%，但别急着上工具。先问自己三个问题：

1. **是所有核都 100%，还是某几个核？** — 这个决定了是整体负载问题还是某个线程卡死。
2. **持续多久了？** — 如果是突刺（spike），可能是 GC 引起的，等它自己恢复就行。
3. **最近半小时上过线吗？** — 很多 CPU 问题就是新代码搞出来的。

我当时看了下：4 核 CPU，全部跑满，持续了将近 10 分钟，最近一次上线是昨天晚上的小版本。

基本锁定是代码问题了。

## 第二步：top 看全局，定位异常进程

```bash
top -c
```

按 CPU 排序（大写 P），一眼就能看出哪个进程在吃资源。

我的输出长这样：

```
PID   USER      PR  NI  VIRT    RES    SHR   S  %CPU  %MEM
28765 appuser   20  0  4.2g    1.1g   12m    S  380%  7.5
```

好家伙，Java 进程 28765 占了 380% 的 CPU，说明它用了将近 4 个核。没跑了，就是它。

这时候你可能会想直接重启。**别急，重启就丢证据了。** 先抓现场数据。

## 第三步：top -H 看线程，找到罪魁祸首

同样的 top，加个 `-H` 参数就能看到进程里的线程级别 CPU 占用：

```bash
top -H -p 28765
```

按 CPU 排序，找到那些 CPU 占用异常的线程 ID：

```
PID   USER      PR  NI  VIRT    RES    SHR   S  %CPU  %MEM
28786 appuser   20  0  4.2g    1.1g   12m    R   70%  7.5
28793 appuser   20  0  4.2g    1.1g   12m    R   65%  7.5
28797 appuser   20  0  4.2g    1.1g   12m    R   62%  7.5
28801 appuser   20  0  4.2g    1.1g   12m    R   60%  7.5
```

四个线程各占 60-70%，加起来把 CPU 干满了。把这几个线程 ID 记下来，转成十六进制：

```bash
printf '%x\n' 28786 28793 28797 28801
```

输出 `7072`、`7079`、`707d`、`7081`，后面要用。

## 第四步：用 Arthas，别再用 jstack 了

以前我遇到 CPU 问题，第一反应就是 jstack 抓堆栈。但 jstack 有两个问题：

- 它抓的是瞬间快照，抓不住的线程可能又跑过去了
- 你得自己算线程 ID、做进制转换、再 grep，贼麻烦

**Arthas 就不一样了。** 它能把 CPU 最热的线程直接排好序、打印堆栈，一行命令搞定。

```bash
# 进入 Arthas Console
curl -O https://arthas.aliyun.com/arthas-boot.jar
java -jar arthas-boot.jar 28765
```

选择进程后，直接输入：

```bash
thread -n 5
```

这命令的意思是：**按 CPU 占用排行，打印前 5 个线程的堆栈。** 一步到位。

输出长这样：

```
"http-nio-8080-exec-12" Id=28786 cpuUsage=70% prio=5
    at com.dudu.service.OrderService.calculatePrice(OrderService.java:152)
    at com.dudu.service.OrderService.applyPromotion(OrderService.java:98)
    at com.dudu.controller.OrderController.submitOrder(OrderController.java:45)
    ...
```

看到 `OrderService.calculatePrice` 这个方法占用了 70% 的 CPU，问题范围一下就缩小了。

> 个人经验：Arthas 这个东西，早学早享受。别等到线上出事了才去翻文档。我大概花了一个周末把常用命令过了一遍，之后每次排查平均省 30 分钟。

## 第五步：读堆栈，不要读代码

很多人到了这一步就开始看源码了。别走弯路——**先看堆栈，确认逻辑路径，再看具体实现。**

我用 `thread 28786` 看了第 28786 号线程的完整堆栈：

```
"http-nio-8080-exec-12" Id=28786 cpuUsage=70% prio=5
    at com.dudu.service.OrderService.calculatePrice(OrderService.java:152)
    at com.dudu.service.OrderService.applyPromotion(OrderService.java:98)
    at com.dudu.service.OrderService.submitOrder(OrderService.java:45)
    at com.dudu.controller.OrderController.submitOrder(OrderController.java:35)
    at java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1149)
    at java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:624)
```

走的是 `submitOrder → applyPromotion → calculatePrice` 这个链路。再去看 `calculatePrice` 第 152 行：

```java
// OrderService.java:152
while (promotions.hasNext()) {
    Promotion prom = promotions.next();
    // 这里有个复杂计算，每个订单循环上千次
    BigDecimal discount = calculatePromotionDiscount(prom, orderItems);
    ...
}
```

这一看就明白了——`promotions` 是个大集合，每次请求进来都要全量遍历计算。昨晚上线的新逻辑加了个新的促销计算规则，复杂度从 O(n) 变成了 O(n²)。

## 第六步：修还是不修？这是个问题

找到原因只是第一步。接下来需要决策：

- **立即回滚？** 如果问题严重，先回滚保证业务可用
- **热修复？** 如果找到了明确的性能瓶颈，可以打个补丁直接上线
- **本地复现再修？** 如果业务还能忍，赶紧本地压测复现，彻底修好再上线

我当时选了方案二。原因是：

1. 问题范围清晰——就是那个循环
2. 修复很简单——加个缓存，把促销计算结果缓存在 Redis 里，同一个订单不重复计算
3. 验证快——改一行代码，加个 `@Cacheable` 注解就行

```java
// 修复后
@Cacheable(value = "promotion-cache", key = "#orderId")
public BigDecimal calculatePrice(Long orderId, List<OrderItem> orderItems) {
    // ...
}
```

修完上线后，CPU 从 100% 瞬间降到 20%。四个线程的 CPU 占用变成了各 3-5%。

> 个人经验：线上问题修复的黄金法则是"最小改动，最快见效"。不要想着重构，不要想着完美。先止血，再治本。那次我本来想重构整个促销模块，被 leader 拦住了——"改一行能解决的事，别改一百行。"

## 总结一下 CPU 100% 排查流程

| 步骤 | 命令 | 作用 |
|

---

发布于：2026-06-19

原文链接：

|> 更多技术干货，欢迎关注公众号「后端实战笔记」

