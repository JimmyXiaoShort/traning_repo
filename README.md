# 培训：从零写一个「石头剪刀布」决策网关

给大一新生的编程训练。目标是让每个人都能独立写出一个**转发网关**：
把一行 JSON 请求转发给大模型，把大模型返回的**任意文本**整理成一行**严格合法**的 JSON 输出，
并且在网络抖动、上游报错、模型胡说、输入损坏时都不崩。


## 目录

```
training_repo/
├── README.md      ← 你正在看的文件
├── skeleton/      ← 你的作业：残缺网关（架构 + 脚手架 + 10 个 TODO + 93 个测试）
└── example/       ← 参考实现：完整网关（同样 93 个测试全绿）
```

两个目录结构完全一样：

```
gateway/
├── config.py       配置（脚手架，不用改）
├── prompts.py      预设提示词（脚手架，不用改）
├── json_guard.py   ★ JSON 容错：把任意文本整理成 dict
├── rules.py        ★ 字段校验、枚举归一化、本地兜底
├── client.py       ★ HTTP 转发：鉴权、重试、只取 content
├── app.py          ★ 编排：一行进一行出，永不抛异常
└── game.py         对局与裁判（脚手架，不用改）
run_gateway.py      CLI：JSON 进 / JSON 出
play_game.py        和本地对手（或 DeepSeek）玩一局
mock_llm_server.py  本地假上游，故意返回各种坏数据
selfcheck.py        列出未实现的 TODO + 跑测试
tests/              验收测试（就是你的评分标准）
```

## 协议

| | 内容 |
| --- | --- |
| 输入（每行） | `{"time": "<本轮时间>", "history": ["rock", "paper"]}`，`history` 可选 |
| 输出（每行） | `{"time": "<与输入一致>", "option": "rock\|scissor\|paper"}`，只允许这两个键 |
| 硬性不变式 | 一行进、一行出；任何异常都不允许影响输出，永远是合法 JSON |


```bash
export DEEPSEEK_API_KEY=sk-xxx
cd example && python play_game.py --rounds 10 --strategy counter
```


## 怎么算通过

| 项 | 标准 |
| --- | --- |
| 自动测试 | `python selfcheck.py` 显示 **93 passed** 且无 TODO 残留 |
| 手工场景 | 离线、假上游、真实 API 三条命令都能输出合法 JSON |
| 口头答辩 | 能讲清「三级容错」「哪些错误该重试」「time 为什么以请求为准」 |
| 代码卫生 | 不吞异常、不用 `eval`、不硬编码 API Key |

## AI 辅助编程规范

* 可以用 AI 写代码、解释报错、生成测试用例——这是工作方式，不是作弊；
* **但提交的每一行都要能解释**，并且必须自己跑过测试；AI 说"应该没问题"不算验证；
* 提示词要给上下文：贴协议、贴失败用例、贴你的思路，而不是"帮我写个网关"；
* 不许把 `example/` 整目录拷进 `skeleton/`：验收时会现场改需求（例如新增一种出拳），
  抄来的代码改不动；
* API Key 只从环境变量读，任何情况下不要写进代码或提交到仓库。

## 一条命令检查自己

```bash
cd skeleton && python selfcheck.py
```
