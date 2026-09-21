# 石头剪刀布决策网关 · 学生任务书（skeleton）

> 目标：写一个**转发网关**——把一行 JSON 请求转发给大模型，再把大模型返回的**任意文本**
> 整理成**一行严格合法的 JSON** 输出。ROS2、机器人、比赛内容都不涉及，你只需要处理
> 「HTTP 转发 + JSON 容错 + 兜底」。

## 0. 先跑起来看看（5 分钟）

```bash
pip install -r requirements.txt

# 看还剩多少没实现（一开始会列出 10 个）
python selfcheck.py

# 离线模式：不调用任何 API，只用本地兜底出拳，先感受一下协议
echo '{"time": "2026-01-01T00:00:00+00:00"}' | python run_gateway.py --offline
# {"time":"2026-01-01T00:00:00+00:00","option":"rock"}

# 对着本地假上游玩一局（不花钱、结果可复现）
python mock_llm_server.py --port 8801 &
python play_game.py --rounds 6 --api-base-url http://127.0.0.1:8801 --api-key test
```

## 1. 协议（唯一要背下来的东西）

| | 内容 |
| --- | --- |
| 输入 | 每行一个 JSON 对象：`{"time": "<本轮时间>", "history": ["rock", "paper"]}`，`history` 可选 |
| 输出 | 每行一个 JSON 对象：`{"time": "<与输入一致>", "option": "rock\|scissor\|paper"}`，**只允许这两个键** |
| 不变式 | **一行进，一行出**；坏输入、上游挂掉、模型胡说，都必须回一行合法 JSON，绝不抛异常 |

胜负：`rock` 赢 `scissor`，`scissor` 赢 `paper`，`paper` 赢 `rock`。

## 2. 你的 TODO 清单（共 10 个函数）

按依赖顺序做，每做完一个就跑一次测试：

```bash
python -m pytest tests -q
# 或者直接
python selfcheck.py
```

| # | 文件 | 函数 | 干什么 | 对应测试 |
| --- | --- | --- | --- | --- |
| 1 | `gateway/json_guard.py` | `strip_code_fence` | 剥掉 ` ```json ... ``` ` 围栏 | `test_strip_code_fence_*`（5 个） |
| 2 | `gateway/json_guard.py` | `find_first_json_object` | 字符串感知地抠出配平的 `{...}` | `test_find_first_json_object_*`（6 个） |
| 3 | `gateway/json_guard.py` | `repair_json_text` | 修尾逗号/单引号/注释/裸换行/缺括号 | `test_repair_*`（9 个） |
| 4 | `gateway/rules.py` | `normalize_option` | 把 `ROCK` / `Scissors` / `石头` 拉回枚举 | `test_normalize_option_*` |
| 5 | `gateway/rules.py` | `validate_decision` | 校验模型输出；time 以请求为准 | `test_validate_decision_*` |
| 6 | `gateway/rules.py` | `fallback_decision` | 本地兜底出拳（反制对手上一手） | `test_fallback_decision_*` |
| 7 | `gateway/client.py` | `build_body` | 组装 Chat Completions 请求体 | `test_body_*` |
| 8 | `gateway/client.py` | `decide` | 发送 + 分类重试（该重试的重试，不该重试的立刻失败） | `test_decide_*` |
| 9 | `gateway/client.py` | `extract_content` | 从响应里只取 `content`，丢掉思维链 | `test_extract_content_*` |
| 10 | `gateway/app.py` | `handle_request` | 把上面全部串起来，保证永不抛异常 | `test_handle_request_*`、`test_run_*` |

每个函数的 docstring 里都写了**实现提示**，照着做即可。没被列出的函数都是脚手架，不用改。

## 3. 验收标准

1. `python selfcheck.py` 输出「所有 TODO 都实现完了」且 **93 个测试全部通过**；
2. 下面三条命令都能跑通，且输出每行都是合法 JSON、`option` 只在三个取值里：

```bash
# a) 离线兜底
printf '%s\n' '{"time":"t1"}' '{"time":"t2","history":["rock"]}' | python run_gateway.py --offline --quiet

# b) 坏上游也不许崩（假上游会依次返回围栏/废话/非法取值/空回复……）
python mock_llm_server.py --port 8801 &
python run_gateway.py --api-base-url http://127.0.0.1:8801 --api-key test --input samples/requests.jsonl --quiet

# c) 完整对局
python play_game.py --rounds 10 --api-base-url http://127.0.0.1:8801 --api-key test --quiet
```

3. 能对着代码讲清楚这三件事（助教会问）：
   * 为什么容错要分「剥围栏 / 括号配对 / 文本修复」三级？只用 `json.loads` 会怎样？
   * 哪些错误要重试，哪些必须立刻失败？为什么？
   * 为什么输出里的 `time` 用请求里的，而不是模型返回的？

## 4. 常见坑（踩过就懂了）

* `json.loads` 遇到单引号、尾随逗号、`None` 一律抛异常——所以才需要 `repair_json_text`；
* **不要用 `eval` 或 `ast.literal_eval` 去"救" JSON**：模型输出是不可信输入，
  `eval` 等于把执行权交出去；修复必须是文本级的、可解释的；
* 括号配对必须"字符串感知"：`{"reason": "a } b"}` 里的 `}` 不是对象结束；
* `except Exception: pass` 是最危险的写法：可以兜底，但一定要把原因记进日志/说明列表；
* `content` 为空、只返回 `reasoning_content`（思维链）、`finish_reason=length`
  是三种不同的失败，别混成一个错误；
* 说话的内容（`history`）和模型自己写的 `time` 都是**不可信**的，网关要做最终裁决；
* 坏输入不要转发给上游：既浪费钱，又可能把脏数据带进日志。

## 5. AI 辅助编程规范

允许（也鼓励）用 AI 帮忙，但必须做到：

1. **先自己想 15 分钟**再问 AI，问的时候贴报错和你的思路，而不是"帮我写一个网关"；
2. AI 给的代码必须**逐行读懂**再提交——助教随时可能指着任意一行让你解释；
3. 每改一次就跑 `python selfcheck.py`，让测试告诉你对不对，而不是让 AI 告诉你对不对；
4. 直接抄 `../example` 会被发现：最终要能现场改需求（例如"再加一个 `lizard/spock` 变体"）。

## 6. 卡住了怎么办

1. 看测试：`python -m pytest tests/test_json_guard.py -q -x`，报错信息里就有期望值；
2. 看 `../example` 里**同一个函数的 docstring**（先只看文档，不要看实现）；
3. 实在不行再看实现，然后**关掉文件自己重写一遍**。

## 7. 做完之后可以再试试（扩展题）

* 给 `GatewayConfig` 加一个 `deadline`：上游超过 N 秒没回就放弃并兜底；
* 支持一次读入多行、并发调用（注意别把顺序搞乱，也不要打爆限流）；
* 把 `history` 从"对手历史"扩展成"双方历史"，并让提示词利用它；
* 增加一个指标：统计 `ok / repaired / fallback` 的比例并打印成一行报告。
