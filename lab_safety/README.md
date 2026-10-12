# lab_safety —— 实验室安全教育平台工具

针对**南邮实验室安全数字化教育平台**（Web: <http://10.22.192.38:9092>，
后端 API: `http://10.22.192.38:9090/jeecg-boot`，JeecgBoot 框架）的终端工具，
支持**刷课 / 查进度 / 自动考试 / 承诺书**四件事。仅依赖 Python 标准库。

> ⚠️ 仅供本人账号学习 / 补课使用。自动化刷课属于绕过平台学习要求的行为，请自行评估风险。

---

## 1. 安装与配置

无需安装依赖，Python 3.8+ 即可。**在仓库根目录**执行：

```bash
python lab_safety/cli.py config --token <你的Access-Token>
```

Token 获取：登录 <http://10.22.192.38:9092>（校园内网）→ F12 → Application →
Local Storage → 复制 `Access-Token` 的值。

该命令会把 token 写入 `lab_safety/config.json`。Token 也可通过环境变量提供，优先级：

```
--token 参数  >  环境变量 LAB_TOKEN  >  lab_safety/config.json
```

---

## 2. 命令

| 命令                           | 说明                                               |
| ------------------------------ | -------------------------------------------------- |
| `config --token X`             | 写入 / 更新配置（`--base`、`--web` 可改 API 地址） |
| `status`                       | 列出全部课程：观看进度 / 完成状态 / 已答题数       |
| `learn`                        | 刷全部未完成课程                                   |
| `exam`                         | 自动考试（先收集题库答案再作答提交）               |
| `commitment`                   | 下载《实验室安全承诺书》模板                       |
| `commitment --upload 照片.jpg` | 上传已签字承诺书并绑定到账号                       |

### learn 的选项

| 选项              | 默认 | 说明                                                                            |
| ----------------- | ---- | ------------------------------------------------------------------------------- |
| `--speed N`       | 2    | 播放倍速。平台按真实时长校验，实测 **2~3 倍可正常刷完**，5 倍会被拒绝并重置进度 |
| `--workers N`     | 1    | 并发课程数。不同课程计时互不影响，可多门并行缩短总时长                          |
| `--course 关键字` | —    | 只刷名称包含该关键字的课程（子串匹配，如 `消防`）                               |
| `--fast`          | 关   | 调试用：不做真实等待，通常**无法**真正完成，仅供排查                            |
| `--token <token>` | —    | 临时用另一个账号                                                                |

```bash
# 后台挂机：2 倍速，4 门并发，日志落盘
nohup python lab_safety/cli.py learn --speed 2 --workers 4 > learn.log 2>&1 &
```

刷课中途 `Ctrl+C` 或关窗口都没关系——**断点续刷**：每门课会从平台已记录的百分比继续。

---

## 3. 关键行为（重要）

**平台不认一次性上报的进度，它只计入与"真实耗时"相当的部分。**

- 必须**按节拍分段上报**：每 10 秒视频上报一次，并真实等待相应秒数
  （倍速 2 → 实际等 5 秒；倍速 3 → 实际等 3.3 秒）。
- 上报过快 → 进度被拒绝；此时调 `finish` 还会把**该课进度重置为 0**。
  这正是"题目答完了、视频还是 0%"的原因。
- 字段坑：请求体 `finishRate` 里的 `watchDuration` 是**累计观看秒数**，
  而 `myCourseList` 返回行里的 `watchDuration` / `learnRate` 是**百分比**，同名不同义。
- 观看进度到 100% 时平台会自动置 `isFinish=1`；之后调用 `finish` 不会重置。
- 倍速下若平台未认可（课程未被标记完成），`api.py` 会**自动回退到 1 倍速重刷一次**。

因此：**总耗时 ≈ Σ(视频时长) ÷ 倍速 ÷ 并发门数**。

### 考试与承诺书

考试前必须在平台上传本人签字的《实验室安全承诺书》（平台强制要求）：

```bash
python lab_safety/cli.py commitment                          # 下载模板
# 打印 → 签字 → 拍照
python lab_safety/cli.py commitment --upload 签字照片.jpg      # 上传并绑定
python lab_safety/cli.py exam                                # 自动考试
```

`exam` 会先通过练习接口收集题库正确答案（约 3300+ 题），再逐题作答提交；
**试卷里出现题库外的题目时会中止提交**，避免答错丢分。

---

## 4. 目录说明

| 文件                  | 作用                                                                 |
| --------------------- | -------------------------------------------------------------------- |
| `api.py`              | 平台接口封装与核心逻辑（`LabApi` 类），刷课 / 考试 / 题库 / 承诺书   |
| `cli.py`              | 终端入口，线性脚本，按 `sys.argv` 分发子命令                         |
| `probe.py`            | 联调探针：用真实 token 打印各接口原始返回，用于排错与字段校准        |
| `config.example.json` | 配置示例（复制为 `config.json` 也可，或直接用 `cli.py config` 生成） |
| `tests/test_lab.py`   | 离线端到端测试（mock HTTP）                                          |

---

## 5. 接口速查

| 用途                  | 接口                                                                         |
| --------------------- | ---------------------------------------------------------------------------- |
| 我的课程              | `GET /jcedutec/courseSource/myCourseList`                                    |
| 课程详情              | `GET /jcedutec/courseSource/queryById`                                       |
| 课程弹题              | `GET /jcedutec/courseSource/queryCourseQuestionRelaByMainId`                 |
| 上报进度              | `POST /jcedutec/courseSource/finishRate`（body: `{id, watchDuration}`，秒）  |
| 弹题作答              | `POST /jcedutec/courseSource/submitAnswer`                                   |
| 标记完成              | `POST /jcedutec/courseSource/finish`                                         |
| 题库（含答案）        | `GET /questions/queryListByType`                                             |
| 我的考试              | `GET /jcedutec/exam/myExamList`                                              |
| 开始 / 提交考试       | `POST /jcedutec/exam/startExam`、`POST /jcedutec/exam/submitExam/{recordId}` |
| 个人信息 / 承诺书绑定 | `GET /students/queryMyInfo`、`POST /students/updateMyInfo`                   |

认证方式：请求头 `X-Access-Token`。

> 注：`finishRate` 只接受 POST + JSON（form 表单返回 500，GET 返回 405）。

---

## 6. 测试

```bash
python lab_safety/tests/test_lab.py
```

全部用例 mock 掉 HTTP 层，不访问真实网络，也不需要 Token。覆盖刷课（含倍速回退）、
题库收集、考试提交与题库外中止、配置读写、承诺书上传流程。

---

## 7. 排错

- **提示"未找到 token"**：先跑 `config --token ...`，或 `export LAB_TOKEN=...`。
- **课程一直 0%**：确认倍速没设太高（用 `--speed 2` 试），以及中途别手动在网页端打开该课。
- **接口字段变了**：跑 `python lab_safety/probe.py` 打印真实返回，据此调整 `api.py`。
- **课程全部显示已完成但仍想重刷**：平台侧状态即真相，工具只刷 `isFinish != 1` 的课程。
