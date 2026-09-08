# 故障10：BGP邻居状态异常 — 诊断决策树

> 标识: bgp-neighbor-abnormal | 分类: 路由协议类

## 根因1：BGP邻居数量超限

对等体数量超过设备规格上限，新邻居无法建立。

```
display bgp peer
display bgp all summary
```

## 根因2：路由故障

到对端 Loopback 的路由不可达，TCP 179 无法建立。底层下钻见 common.md §3.8。

## 根因3：BGP配置异常

AS 号、对等体地址、update-source 配置不一致。

## 根因4：告警

设备存在与 BGP 相关的活动告警。
