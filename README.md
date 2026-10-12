# njupt-grad-safety-edu-toolkit

南京邮电大学研究生安全教育相关的自动化工具。

仓库目前**只有一个工具**：实验室安全教育平台的刷课工具，源码在 `lab_safety/`，
用 Python 3.8+ 写成，只依赖标准库（urllib + ssl），不需要装任何第三方包。
以后要接入别的平台，照着 `lab_safety/` 再建一个文件夹就行，两者互不影响。

> ⚠️ 仅供本人账号自主学习、补课使用。
> 自动刷课属于绕过平台"防挂机"限制的行为，可能被平台判定为异常，存在账号风险。
> 请自行评估，不要用于他人账号，也不要违反学校规定。本项目仅供技术学习与交流。

## 功能

对应平台：南邮实验室安全数字化教育平台（<http://10.22.192.38:9092>）。

| 命令         | 作用                                            |
| ------------ | ----------------------------------------------- |
| `config`     | 写入 Access-Token（存到 `lab_safety/config.json`） |
| `status`     | 列出全部课程：观看进度 / 完成状态 / 已答题数    |
| `learn`      | 刷课，支持倍速与并发                            |
| `exam`       | 自动考试，先收集题库答案再逐题作答提交          |
| `commitment` | 下载 / 上传《实验室安全承诺书》                 |

## 快速开始

在仓库根目录执行：

```bash
python lab_safety/cli.py config --token <你的Access-Token>
python lab_safety/cli.py status
python lab_safety/cli.py learn
python lab_safety/cli.py exam
```

Token 获取：登录 <http://10.22.192.38:9092>（校园内网）→ F12 → Application →
Local Storage → 复制 `Access-Token` 的值。

## 关于刷课耗时

平台会校验真实观看时长。上报过快不仅不计数，调用 `finish` 还会把该门课的进度
**重置为 0**，所以刷课耗时约等于 `视频总时长 ÷ 倍速 ÷ 并发门数`：

```bash
python lab_safety/cli.py learn --speed 2 --workers 4
```

倍速默认 2，实测 3 倍可用，5 倍会在接近 100% 时被平台重置。
中途中断没关系，重跑会从平台记录的进度继续。

接口细节、字段含义和排错方法见 [lab_safety/README.md](lab_safety/README.md)。

## 目录

```
.
├── README.md
├── LICENSE
└── lab_safety/
    ├── README.md             工具详细说明
    ├── api.py                平台接口与核心逻辑
    ├── cli.py                终端入口
    ├── probe.py              接口探针，字段排错用
    ├── config.example.json   配置示例
    └── tests/test_lab.py     离线测试
```

## 测试

```bash
python lab_safety/tests/test_lab.py
```

用例在本地 mock 掉 HTTP，不访问真实平台，也不需要 Token。

## 来源与许可

实验室平台部分重构自 [HClOLyw/njupt-course-auto](https://github.com/HClOLyw/njupt-course-auto)
的实验室安全教育模块，原作者通过抓包还原了平台接口；更上游是
[Scwizard/jiangsu-safety-platform-skip](https://github.com/Scwizard/jiangsu-safety-platform-skip)。

许可证：[Apache License 2.0](LICENSE)。

## 免责声明

1. 请只用于你本人的账号，用于日常学习、补课。
2. 自动化刷课、答题属于绕过平台学习与考试要求的行为，可能存在账号风险，请谨慎评估。
3. `config.json` 里的 Token 是敏感凭证，不要提交或分享。
4. 平台题目可能变化，换题后需要补充题库。
5. 本项目仅供学习交流，请遵守学校与平台的相关规定。
