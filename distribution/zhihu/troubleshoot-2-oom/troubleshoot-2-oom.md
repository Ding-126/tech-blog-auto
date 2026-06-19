# troubleshoot-2-oom

线上 OOM 是最让人头疼的问题之一。服务器挂了、接口超时、用户过来说打不开页面了——而你连它在哪都还不知道。

上个月我们线上就出了一次。不是什么高并发场景，就是一笔正常的支付回调，跑着跑着内存就爆了。

这篇文章记录完整的排查过程：从告警收到、Heap Dump 导出、MAT 分析，到定位到具体代码行、修复、复盘。全程思路，不止工具。

## 告警来了：内存使用率 95%

那天下午两点，告警群弹出消息：

> [告警] 支付服务 POD pay-svc-7f9b6c 内存使用率 95%

第一反应是流量突增。看了一眼监控——QPS 正常，和昨天差不多。那就不是流量的问题，大概率是内存泄漏。

内存泄漏的特点就是：**系统运行越久，内存占用越高，最终 OOM 被 kill**。如果是流量高峰导致的内存飙升，降下来之后内存也会降。而内存泄漏不会。

我们看一下现在的内存情况：

```bash
# 进 POD
kubectl exec -it pay-svc-7f9b6c -- sh

# 看进程
ps aux --sort=-%mem | head -5

# 用 jstat 看堆内存
jstat -gcutil 1 1000 5
```

输出显示老年代（Old Gen）使用率已经 92%，而且 Full GC 的频率从正常的每小时一次变成了每 10 分钟一次——典型的内存泄漏信号。

## 导出 Heap Dump

确认是内存泄漏后，第一步是导出堆转储文件。注意：生产环境导出 Heap Dump 会导致应用暂停（STW），要确认业务低峰期再做。

我们当时选了凌晨 2 点操作：

```bash
# 先看进程 PID
jps -l

# 导出 Heap Dump（PID 12345）
jmap -dump:live,format=b,file=/tmp/heap-20260618.hprof 12345
```

`-dump:live` 参数会先触发一次 Full GC，只保留存活对象，这样导出的文件更小、分析起来更容易。但这个操作本身也会 STW，大堆可能几十秒。

导出完把文件拷出来：

```bash
kubectl cp pay-svc-7f9b6c:/tmp/heap-20260618.hprof ./heap-dump.hprof
```

这个文件 800 多 MB，传了快两分钟。**建议先 gzip 再传**，压缩率很好：

```bash
gzip /tmp/heap-20260618.hprof
# 800MB → 120MB
```

## 用 MAT 分析堆转储

Heap Dump 拿到了，怎么分析？最常用的工具是 Eclipse MAT（Memory Analyzer Tool）。下载地址: https://eclipse.dev/mat/

也可以用命令行版（头大时用）:

```bash
# 安装 MAT 命令行版
brew install mat

# 分析
ParseHeapDump.sh heap-dump.hprof
```

但我建议还是用 GUI——可视化的 Leak Suspects Report 能直接告诉你「最可能的问题在哪里」。

导入 Heap Dump 后，MAT 会自动生成一份 **Leak Suspects Report**，点开看：

```
Problem Suspect 1:
The class "java.util.HashMap$Node" loaded by "system class loader"
occupies 387,418,968 (45.6%) bytes of total heap.

The thread "http-nio-8080-exec-15" is at:
  java.util.HashMap.putVal(HashMap.java:xxx)
  com.dudu.pay.service.CallbackProcessor.saveCallback(CallbackProcessor.java:42)
```

45% 的堆被一个 HashMap 占了！点进去看详情，发现这个 HashMap 有 280 万个 entry，key 是 String 类型，value 是 CallbackRecord 对象。

280 万个！正常的支付回调记录不应该留这么多在内存里，应该写进数据库后就释放引用。

## 定位到代码

顺着 MAT 的指引，我打开了 `CallbackProcessor.java`：

```java
@Component
public class CallbackProcessor {
    // 缓存所有未处理的回调记录
    private Map<String, CallbackRecord> pendingCallbacks = new HashMap<>();

    public void saveCallback(String orderId, CallbackRecord record) {
        pendingCallbacks.put(orderId, record);
        // 写数据库
        callbackRepository.save(record);
    }

    public void processCallback(String orderId) {
        CallbackRecord record = pendingCallbacks.get(orderId);
        if (record != null && record.isProcessed()) {
            // 处理完成，但！没有 remove！
            // pendingCallbacks.remove(orderId);  // 这一行被注释掉了
        }
    }
}
```

看到没？`processCallback` 方法处理完回调记录后，应该从 `pendingCallbacks` 里 remove 掉，但对应的代码被注释了。我查了 git blame——是两周前一次重构时，同事不小心把这一行删了。

结果就是：每笔支付回调都往 HashMap 里 put，但永远不 remove。运行两周，280 万条记录，堆撑爆。

修复就一行：

```java
public void processCallback(String orderId) {
    CallbackRecord record = pendingCallbacks.get(orderId);
    if (record != null && record.isProcessed()) {
        pendingCallbacks.remove(orderId);  // 加上这一行
        // 后续处理逻辑...
    }
}
```

但即便是修好了，这个设计也有问题——HashMap 作为缓存，万一处理失败永远不 remove，还是会泄漏。更好的做法是**换用 Guava Cache 或 Caffeine，设置最大容量和过期时间**：

```java
LoadingCache<String, CallbackRecord> pendingCallbacks = Caffeine.newBuilder()
    .maximumSize(10000)
    .expireAfterWrite(30, TimeUnit.MINUTES)
    .build(key -> callbackRepository.findById(key));
```

这样就算漏了 remove，缓存也会自动过期淘汰，不会撑爆内存。

## 修完上线，观察指标

修复后灰度上线，观察三件事：

1. **老年代使用率**：应该从 92% 缓慢下降，稳定在 30-40%
2. **Full GC 频率**：应该从每 10 分钟恢复到每小时 1 次
3. **`pendingCallbacks` 数量**：可以通过 Arthas 或 JMX 监控，应该稳定在几百

```bash
# Arthas 看 Map 大小
ognl -c com.dudu.pay.service '@callbackProcessor@pendingCallbacks.size()'
```

观察了 24 小时，老年代稳定在 35%，Full GC 每 50 分钟一次，恢复正常。

## 复盘：为什么这个 Bug 活了两周？

几个原因叠加：

- **代码 Review 不够细**：重构 PR 删了一行代码，reviewer 没注意到。养成习惯：**删了 put 的地方检查有没有对应的 remove**
- **没有自动化检测**：没有加 Pod 内存水位线告警的自动化触发。如果内存 80% 时自动 dump 一份堆，发现能提前两周
- **HashMap 当缓存用**是最常见的内存泄漏模式之一。工具推荐：**AliJava 规约插件**能扫出这类问题

**我的建议：**
1. 所有 Map<String, X> 类型的类成员变量，上线前问自己「这个 Map 会有多大？什么时候清理？」
2. Caffeine 代替 HashMap 做缓存，强制设置上限
3. MAT 分析不是学会了才用，是遇到问题再学——先把 MAT 下载好，省得到时候手忙脚乱

---

📌 本文是「线上问题排查」系列第 2 篇。下一篇聊：一次诡异的 Connection Reset 排查——从网络层到应用层，一层层剥开。

---

发布于：2026-06-19

原文链接：

|> 更多技术干货，欢迎关注公众号「后端实战笔记」
