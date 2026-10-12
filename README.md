# 南邮研究生安全教育工具包（njupt-grad-safety-edu-toolkit）

针对南京邮电大学安全教育相关平台的**终端工具集合**。纯 Python 标准库实现
（urllib + ssl），无需安装任何第三方依赖，Python 3.8+ 直接运行。

> ⚠️ **仅供本人账号自主学习 / 补课使用。**
> 自动操作为绕过平台"防挂机 / 防作弊"限制的行为，可能被平台判定为异常、存在账号风险。
> 请自行评估，切勿用于批量处理他人账号或违反学校规定。本项目仅用于技术学习与交流。

---

## 🧰 包含的工具

| 工具                      | 对应平台                                     | 功能                              |
| ------------------------- | -------------------------------------------- | --------------------------------- |
| [lab_safety](lab_safety/) | 南邮实验室安全数字化教育平台（10.22.192.38） | 刷课 / 查进度 / 自动考试 / 承诺书 |

---

## 🚀 快速开始

```bash
# 1. 写入实验室平台 Access-Token（会生成 lab_safety/config.json）
python lab_safety/cli.py config --token <你的Access-Token>

# 2. 查看课程进度
python lab_safety/cli.py status

# 3. 刷课（默认 2 倍速、1 门串行）
python lab_safety/cli.py learn

# 4. 自动考试（需先上传签字承诺书）
python lab_safety/cli.py exam
```

> 平台按**真实观看时长**校验进度，所以刷课耗时 ≈ 视频时长 ÷ 倍速 ÷ 并发门数。
> 详见 [lab_safety/README.md](lab_safety/README.md)。

---

## 🗂 目录结构

```
.
├── README.md                 # 本文件：工具包总览
├── LICENSE                   # Apache License 2.0
└── lab_safety/               # 工具一：实验室安全教育平台
    ├── README.md             # 该工具的详细说明
    ├── api.py                # 平台接口封装（刷课 / 考试 / 题库 / 承诺书）
    ├── cli.py                # 终端入口（config / status / learn / exam / commitment）
    ├── probe.py              # 接口联调探针（字段排错用）
    ├── config.example.json   # 配置示例
    └── tests/
        └── test_lab.py       # 离线测试（mock HTTP，无需真实网络）
```

---

## ➕ 新增工具约定

为便于后续扩展，**一个工具一个文件夹**，内部保持统一结构：

```
<工具名>/
├── README.md          # 该工具的使用说明
├── api.py             # 平台接口与核心逻辑
├── cli.py             # 终端入口（线性脚本，从 sys.argv 分发子命令）
├── config.example.json
└── tests/
    └── test_<工具名>.py
```

- 配置写在各自文件夹的 `config.json`（已加入 `.gitignore`），工具之间互不干扰。
- 只用标准库，不引入第三方依赖。
- 测试一律 mock HTTP，`python <工具名>/tests/... ` 即可离线跑通。

---

## ✅ 测试

```bash
python lab_safety/tests/test_lab.py
```

全部用例在本地 mock 网络层，不需要真实 Token，也不会向平台发请求。

---

## 🙏 致谢与许可

- 实验室平台部分重构自 [HClOLyw/njupt-course-auto](https://github.com/HClOLyw/njupt-course-auto)
  的实验室安全教育模块（原作者通过抓包还原了平台接口）。
- 上游上游：[Scwizard/jiangsu-safety-platform-skip](https://github.com/Scwizard/jiangsu-safety-platform-skip)
- 许可证：[Apache License 2.0](LICENSE)

---

## ⚠️ 免责声明

1. 请**只用于你本人的账号**，用于日常学习、补课。
2. 自动化刷课 / 答题属于**绕过平台学习与考试要求**的行为，可能存在账号风险，请谨慎评估。
3. `config.json` 里的 token 是敏感凭证，**请勿把填写了真实 token 的文件提交或上传到任何仓库**。
4. 平台题目**可能变化**；换题后请补充题库再重试。
5. 本项目仅供学习交流，请遵守学校与平台的有关规定。
