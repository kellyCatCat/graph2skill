# IP网络故障诊断 — BGP场景

## 引用通用规范

本文件依赖以下通用规范，诊断时请一并加载：

| 文件 | 内容 |
| --- | --- |
| common.md | 采集规范、执行约束、诊断流程、自检清单、追问流程、28类共享基础设施故障 |

## 1. 触发条件

BGP邻居状态异常。当 BGP 对等体无法建立、频繁振荡、或邻居状态非 Established 时触发本场景诊断。

### 1.1 诊断执行顺序（强制遵循，不可跳步）

- [ ] Step 1: 确认为 BGP 邻居状态异常 → 进入 §2
- [ ] Step 2: 执行 common.md §3.0 强制前置采集（配置变更 + 拓扑变化日志）
- [ ] Step 3: 进入 §2 决策树 / reference 文件
- [ ] Step 4: 按决策树逐层下钻，根因迭代到底层
- [ ] Step 5: 输出诊断结论 + common.md §4 验证追问

⚠️ Step 2 是全局前置步骤，无论什么故障类型都必须执行。跳过 Step 2 的诊断结论视为无效。

## 2. BGP邻居状态异常

### 2.1 故障序号10：BGP邻居状态异常

标识: bgp-neighbor-abnormal | 分类: 路由协议类 | 根因: 4类

| 编号 | 根因名称 | 英文标识 | 触发特征 |
| --- | --- | --- | --- |
| 1 | BGP邻居数量超限 | Number of BGP neighbors exceeds the limit | 见reference决策树 |
| 2 | 路由故障 | Routing Failure | 见reference决策树 |
| 3 | BGP配置异常 | Abnormal BGP Configuration | 见reference决策树 |
| 4 | 告警 | Alarm | 见reference决策树 |

→ 诊断决策树与详细根因分析详见：reference/fault-10-bgp-neighbor.md

## 3. 根因迭代到底层

BGP邻居Down的根因可能涉及底层基础设施故障。当根因指向以下方向时，跳转到 common.md 对应章节继续下钻：

| BGP 根因方向 | 跳转至 common.md 章节 | 故障类型 |
| --- | --- | --- |
| 接口DOWN/物理层异常 | §3.1 | 物理端口故障 |
| 设备不可达 | §3.2 | 设备离线故障 |
| 路由不可达 | §3.8 | OSPF状态异常 |
| 光模块/光功率异常 | §3.4, §3.5, §3.18, §3.19 | 光模块/光功率故障 |
| 链路异常 | §3.6 | 链路故障 |
| 硬件异常 | §3.16, §3.17, §3.21 | 单板/子卡/风扇故障 |
| BFD会话Down | §3.11 | BFD会话异常 |
| L3VPN相关 | §3.20 | L3VPN业务故障 |
| EVPN相关 | §3.30 | EVPN故障 |
| Tunnel/LDP/MPLS相关 | §3.23, §3.25, §3.26 | Tunnel/LDP/MPLS故障 |
| SRv6相关 | §3.27 | SRv6 TE Policy故障 |

## 4. 诊断完成

诊断完成后，请执行 common.md §3.31 自检清单和 §4 诊断后追问流程。

### 4.1 统计文件

BGP邻居状态异常统计文件路径：statistics/bgp-neighbor-abnormal-stats.json
