# 风格图鉴长时间加载 —— 真因是启动序列踩了 TDZ（V2.30）

## 现象

用户原话：「风格图鉴连接的时间过长」「风格图鉴长时间加载」。
选到「风格图鉴」模块后，门控一直显示 **「正在连接图鉴服务…」**，图始终不出来。

## 症状层证据（都是实测，不是推断）

对着出问题的实例（PID 2144，V2.28 部署版）打：

| 观察点 | 结果 | 说明 |
| --- | --- | --- |
| `8793 /api/version` | 200，2.4 ms | 图鉴服务活着、应答极快 |
| `8793 /api/focus` | `{"seq": 0, "alive": false}` | 图鉴**页**从没在浏览器里跑起来过 |
| `8793 /api/data` | 61.5 ms / 6.36 MB | 服务端不是瓶颈 |
| `8793 /?embed=1` | 47.5 ms / 6.47 MB | 同上 |

再用**真浏览器**打开 `http://127.0.0.1:8790/`（localStorage 里
`comfybatch-module='atlas'`），等 42 秒后读 DOM 与 resource timing：

- `#atlasGateText` 仍是 `正在连接图鉴服务…`，`#atlasFrame.src` 为空；
- `performance.getEntriesByType('resource')` 里**只有 app.css / app.js**
  —— 那条跨端口探测请求根本没发生；
- 图鉴服务端日志里也没有任何 `origin=http://127.0.0.1:8790` 的记录。

也就是说：**卡住不是"等很久"，是那条请求压根没被发出去。**

## 真因

`src/app.js` 顶层第 1410 行（改之前）就会执行启动序列：

```js
goModule(localStorage.getItem('comfybatch-module')||'gen');
```

localStorage 里存的是 `atlas` 时，它转到 `ensureAtlas()`，而
`ensureAtlas()` 第一步就是 `_atlasStopWaiting()`：

```js
function _atlasStopWaiting(){
  if(_atlasRetryTimer){clearTimeout(_atlasRetryTimer);_atlasRetryTimer=0;}
}
```

`_atlasRetryTimer` 是**顶层 `let`，声明在 2392 行**——脚本求值还没走到那里，
读它就是暂时性死区（TDZ）。在浏览器里直接调出来看：

```
ensureAtlas: ReferenceError: Cannot access '_atlasRetryTimer' before initialization
goModule:    ReferenceError: Cannot access '_atlasRetryTimer' before initialization
atlasRetry:  ReferenceError: Cannot access '_atlasTries' before initialization
```

要命的是**这是顶层语句**：异常会中断整份脚本的求值，后面的全都不再执行。
于是故障远不止"图鉴不出来"：

- 门控停在 `index.html` 里的初始文案（`#atlasGateText` 的 HTML 初值就是这句），
  永远不会被改写；
- 探测请求一个都没发出；
- 启动序列剩下的 `refreshInventory()` / `poll(true)` / `initReviewDialogs()`
  全部没执行，`connectEvents()`（2327 行）和后面 `const BADGE_CLASS`、
  `let viewerIndex`、`const ATLAS_PROBE` 等一律没求值 —— **页面看着渲染出来了，
  其实整页是死的**（不轮询、不扫资源、模块标签和审图按钮都没绑上）；
- 而且**每次重载都一样**：localStorage 里那格还是 `atlas`，下一轮启动撞同一行。

旁证：文件顶部 58 行那条注释，记的就是**同一个坑**，之前已经为
`MODULES / currentModule / _atlasFrameReady / _compareFrameReady` 踩过一次，
把它们提前到了文件前部。图鉴这一段后来又长出了 `_atlasRetryTimer` 等声明，
于是同一个入口被重新踩中。

核对过 V2.28 打包进 exe 的 `app.js`：`goModule(...)` 在 1410 行、
`let _atlasRetryTimer=0,_atlasTries=0;` 在 2392 行，**结构完全相同**。所以这不是
某一版新引入的缺陷，而是从内嵌图鉴那一版起就一直在。

## 修法

**把启动序列整体移到文件末尾。** 不做"把某个变量往前挪"的补丁 —— 那个补丁
只会修好这一次，下一段新代码还会撞上同一个入口。放到末尾之后，任何顶层声明
都已经求值完成，这一整类 TDZ 坑从结构上就不存在了。

规则由 `tests/test_page_assets.py` 的 `BootOrderTests` 钉住：启动语句之后不允许
再出现顶层声明。用这条规则回扫 V2.28 的 app.js，它会报出 9 条越界声明
（含 `_atlasRetryTimer`）—— 说明这条测试真的抓得住，不是装饰。

## 顺带纠正：一个被推翻的假设

排查过程中曾经认定「前端 `fetch(...,{cache:'no-store'})` 会触发 CORS 预检，
而图鉴服务答不出 OPTIONS（501、无 `Access-Control-*` 头），于是 fetch 被拦掉」
—— 代码注释、`CorsTests` 的文档和一条测试都照这个写了。

**用真浏览器实测把它推翻了。** 起了一对本地服务端（页面 8910、模拟旧图鉴 8911，
后者不实现 `do_OPTIONS`），拿 Chromium 打：

```
B GET /api/version Origin=http://127.0.0.1:8910 Cache-Control=no-cache Pragma=no-cache
A REPORT 1_old_cache_no_store = RESOLVED 9ms {"ok":true,"build_id":"experiment"}
```

`cache:'no-store'` 确实会让浏览器补上 `Cache-Control: no-cache` / `Pragma: no-cache`，
但**不会**发 OPTIONS 预检 —— cache mode 自己加的头不算"作者自定义头"。
那条 fetch 9ms 就正常 resolve 了。

所以：

- 前端改成时间戳参数破缓存（`?` + `Date.now()`）**保留**：理由是把请求保持在
  「简单请求」这一档，不必依赖服务端答得出预检，而不是"否则会断"。
- 探测加 `AbortController` 4 秒超时**保留**：没有超时的 fetch 一旦挂住，提示语
  会永远停在「正在连接…」，用户分不清是慢还是卡死。这次能一眼看出"请求没发出"
  正是靠顺手接上的请求日志。
- `do_OPTIONS`（图鉴服务答预检）**保留**：8793 是被 8790 跨端口调用的 API，
  哪天有人加一个自定义头，预检就真的会发出来；答不出预检的服务就是个隐患。
  但它的定位是**加固**，不是本次故障的修复 —— 注释与测试文档都照实改了。
- `_note()` 请求日志**保留**：排查这次问题最缺的证据就是「浏览器到底发过什么」，
  没有它就只能靠猜（而猜错了）。

## 验证

- 全量测试：见 HANDOFF。
- 真浏览器端到端：见 HANDOFF「验证记录」。

## 教训

1. **"卡住"要先分清是"没发出"还是"发了没回"。** 服务端日志 + resource timing
   一列，就能把 90% 的猜测砍掉；没有请求日志时，我猜了两轮都猜错方向。
2. **顶层语句抛异常会整份脚本停摆**，但页面照样渲染 —— 症状会伪装成
   "某个功能慢/不出来"，实际是整页都死了。看到"只有一个模块坏"要留个心眼。
3. **注释里的因果链要能指回一次实测**。这次把"预检导致"写进注释和测试，
   看代码的人（包括后来的我）会当事实接受；实测一跑才发现是错的。
   推断可以写，但必须标成推断。
