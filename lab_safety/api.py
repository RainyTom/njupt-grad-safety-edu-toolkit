# -*- coding: utf-8 -*-
"""
api.py —— 南邮实验室安全数字化教育平台接口封装
========================================================
面向 http://10.22.192.38:9092（后端 API: http://10.22.192.38:9090/jeecg-boot）
的自动化助手核心逻辑，仅依赖 Python 标准库。

功能：
  1) 课程学习：列出我的课程 -> 看详情/嵌入式题目 -> 分段上报观看进度(finishRate)
     -> 时间点弹题作答(submitAnswer) -> 看完标记完成(finish)
  2) 考试：列出我的考试 -> 开始(startExam) -> 按答案作答 -> 提交(submitExam)
  3) 答案收集：通过"练习接口"的题目列表直接读取 correctAnswer，构建题库答案映射；
     课程嵌入式题目优先查题库，查不到则报错/跳过，避免盲目答错。

⚠️ 仅供本人账号自主学习使用，请自行评估平台风险。
"""

import json
import math
import os
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request

LAB_API_BASE = "http://10.22.192.38:9090/jeecg-boot"
LAB_WEB_BASE = "http://10.22.192.38:9092"

if getattr(sys, "frozen", False):
    # PyInstaller onefile：config.json 与 exe 同目录，保证保存能持久化
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# 默认值
DEFAULT_INTERVAL = 6  # 每个知识点之间等待秒数（防风控）
DEFAULT_REPORT_STEP = 10  # 进度上报步长（与网页端每 10 秒一致）
# 默认播放倍速：平台只计入与真实耗时相当的部分，实测 2~3 倍可正常刷完，
# 过高（如 5 倍）会被平台拒绝并重置进度。
DEFAULT_SPEED = 2.0

# 忽略 SSL 校验（平台证书自签/链不完整）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class LabApiError(Exception):
    """实验室平台 API 业务错误（带用户可读的中文提示）。"""

    pass


class LabApi:
    def __init__(
        self,
        token="",
        base=LAB_API_BASE,
        web=LAB_WEB_BASE,
        interval_between=DEFAULT_INTERVAL,
        log_callback=None,
        speed=DEFAULT_SPEED,
    ):
        self.token = (token or "").strip()
        self.base = (base or LAB_API_BASE).rstrip("/")
        self.web = (web or LAB_WEB_BASE).rstrip("/")
        self.interval_between = int(interval_between or DEFAULT_INTERVAL)
        # 答案库：questionsId -> 答案。单选/判断为 "A"，多选为 ["A","C"] 或 "A,C"
        self.answer_bank = {}
        # 题干 -> 答案 索引（题库与课程题目 id 可能不同，用题干兜底匹配）
        self._stem_index = {}
        self.fast = False
        # 上报间隔的真实等待比例：1.0 = 按真实播放时长等待（平台要求）；
        # 测试时可置 0，跳过等待但仍保持分段上报。
        self.sleep_scale = 1.0
        # 播放倍速：1.0 = 正常速度。>1 时上报间隔相应缩短（平台可能拒绝，
        # 一旦未完成会自动回退到 1 倍速重刷）。默认 2.0。
        self.speed = float(speed or DEFAULT_SPEED)
        self._log_cb = log_callback

    # ---------------- 日志 ----------------
    def log(self, msg, level="info"):
        if self._log_cb:
            self._log_cb(level, str(msg))
        else:
            print(msg)

    def set_fast(self, fast):
        self.fast = bool(fast)

    # ---------------- 配置加载/保存 ----------------
    @classmethod
    def load_config(cls):
        cfg = {
            "lab_token": "",
            "lab_base": LAB_API_BASE,
            "lab_web": LAB_WEB_BASE,
            "lab_speed": DEFAULT_SPEED,
        }
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                cfg.update({k: v for k, v in data.items() if k in cfg})
            except Exception:
                pass
        return cfg

    @staticmethod
    def save_config(cfg):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(
                {
                    k: cfg.get(k)
                    for k in ("lab_token", "lab_base", "lab_web", "lab_speed")
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    # ---------------- HTTP 封装 ----------------
    def _headers(self, base):
        for name, val in (("X-Access-Token", self.token),):
            if isinstance(val, str) and any(ord(c) > 127 for c in val):
                raise LabApiError(
                    "请求头 %s 包含非 ASCII 字符，请检查是否把说明文字填成了凭证" % name
                )
        return {
            "X-Access-Token": self.token,
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
        }

    def _request(self, method, path, params=None, data=None, base=None):
        """发送请求并解析 JSON。base 默认走后端 API，也可传 self.web 访问前端静态。
        网络异常自动重试（3 次退避），最终失败抛 LabApiError。"""
        import urllib.error

        if not self.token:
            raise LabApiError(
                "未提供实验室平台 Access-Token，请先在【参数设置】中填写。"
            )
        base = (base or self.base).rstrip("/")
        url = base + path
        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        body = None
        headers = self._headers(base)
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        last_err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, context=_SSL_CTX, timeout=30) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"success": True, "raw": raw[:500]}
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt < 3:
                    wait = 5 * (attempt + 1)
                    self.log(
                        "  网络异常(%s)，%d 秒后重试 (%d/3)..."
                        % (e, wait, attempt + 1),
                        "warn",
                    )
                    time.sleep(wait)
        raise LabApiError("网络请求失败（已重试 3 次）: %s" % last_err)

    def api_get(self, path, params=None, base=None):
        return self._request("GET", path, params=params or {}, base=base)

    def api_post(self, path, data=None, base=None):
        return self._request("POST", path, data=data or {}, base=base)

    @staticmethod
    def _check(res, what):
        if not isinstance(res, dict):
            raise LabApiError("%s返回异常: %s" % (what, res))
        if not res.get("success"):
            raise LabApiError(
                "%s失败: %s" % (what, res.get("message", res.get("code", res)))
            )

    @staticmethod
    def _rows(res):
        """兼容 result 为 dict{records...} 或 list 两种返回。"""
        result = res.get("result")
        if isinstance(result, dict):
            return (
                result.get("records") or result.get("rows") or result.get("list") or []
            )
        if isinstance(result, list):
            return result
        return []

    # ---------------- 课程 ----------------
    def my_courses(self, name=None, page=1, page_size=200):
        """列出我的课程。row 字段：id/name/watchDuration%/donum/total/unCorrectNum/isFinish。"""
        params = {"pageNo": page, "pageSize": page_size}
        if name:
            params["name"] = name
        res = self.api_get("/jcedutec/courseSource/myCourseList", params)
        self._check(res, "获取课程列表")
        return self._rows(res)

    def course_detail(self, cid):
        """课程详情（视频地址 url、名称、简介等）。"""
        res = self.api_get("/jcedutec/courseSource/queryById", {"id": cid})
        self._check(res, "获取课程详情")
        return res.get("result") or {}

    def course_questions(self, cid):
        """课程嵌入式题目（弹题）。字段：id/stem/kind/optiona-d/ejectTime/可能含 correctAnswer。"""
        res = self.api_get(
            "/jcedutec/courseSource/queryCourseQuestionRelaByMainId", {"id": cid}
        )
        self._check(res, "获取课程题目")
        return self._rows(res)

    def update_visits(self, cid):
        """记录访问课程。"""
        res = self.api_post("/jcedutec/courseSource/updateVisits", {"id": cid})
        if not res.get("success"):
            self.log("  记录访问失败: %s" % res.get("message", res), "warn")
        return bool(res.get("success"))

    def report_finish_rate(self, cid, watch_duration):
        """上报观看进度（秒）。网页端每 10s 上报一次。"""
        return self.api_post(
            "/jcedutec/courseSource/finishRate",
            {"id": cid, "watchDuration": int(watch_duration)},
        )

    def submit_answer(self, cid, question_id, option):
        """作答课程弹题。option：单选为 "A"，多选为 ["A","C"]。"""
        return self.api_post(
            "/jcedutec/courseSource/submitAnswer",
            {"id": cid, "questionId": question_id, "option": option},
        )

    def finish_course(self, cid):
        """标记课程完成。"""
        return self.api_post("/jcedutec/courseSource/finish", {"id": cid})

    def uncorrect_by_course(self, cid):
        """课程错题（含正确答案 correctAnswer / 解析 analysis）。"""
        res = self.api_get("/students/queryUnCorrectByCourseId", {"id": cid})
        self._check(res, "获取课程错题")
        return self._rows(res)

    # ---------------- 题库 / 练习（带正确答案） ----------------
    def question_types(self):
        """题型列表。row 字段：id/name/.../count 或 questionNum。"""
        res = self.api_get("/jcedutec/questionType/queryCountList", {})
        self._check(res, "获取题型")
        return self._rows(res)

    def questions_by_type(self, type_ids, start_num=1, end_num=500):
        """按题型取题目（练习接口，返回含 correctAnswer）。"""
        params = {
            "ids": ",".join(str(t) for t in type_ids),
            "startNum": start_num,
            "endNum": end_num,
        }
        res = self.api_get("/questions/queryListByType", params)
        self._check(res, "获取题库题目")
        return self._rows(res)

    def harvest_answer_bank(self):
        """从练习接口收集全部题目的正确答案 -> self.answer_bank。

        bank: qid -> "A"（或 "A,C" 多选）。收集后返回题目总数。
        """
        self.log("开始收集题库答案（练习接口）...")
        types = self.question_types()
        if not types:
            self.log("练习接口未返回题型，跳过答案收集。", "warn")
            return 0
        bank = {}
        total = 0
        for t in types:
            tid = t.get("id")
            if not tid:
                continue
            tname = t.get("name", tid)
            # 数量字段名做兼容探测
            cnt = t.get("count") or t.get("questionNum") or t.get("num") or 0
            try:
                cnt = int(cnt)
            except (TypeError, ValueError):
                cnt = 0
            # ids 可能是单个 id 的字符串/数字，也可能是逗号分隔列表
            got = self.questions_by_type([tid], 1, cnt if cnt else 500)
            for q in got:
                qid = q.get("id") or q.get("questionsId")
                ans = q.get("correctAnswer")
                if qid and ans:
                    bank[str(qid)] = self._norm_answer(ans)
                    total += 1
                    stem = (q.get("stem") or "").strip()
                    if stem:
                        self._stem_index.setdefault(stem, self._norm_answer(ans))
            self.log("  题型[%s] 收录 %d 题" % (tname, len(got)), "info")
        self.answer_bank.update(bank)
        self.log("题库答案收集完成：共 %d 题。" % len(bank), "success")
        return len(bank)

    @staticmethod
    def _norm_answer(ans):
        """把 correctAnswer 规范成 "A" 或 ["A","C"]；无法判定的原样保留。"""
        if isinstance(ans, list):
            return sorted(str(a).strip() for a in ans if str(a).strip())
        if isinstance(ans, (int, float)):
            return str(ans)
        s = str(ans or "").strip().upper()
        if not s:
            return ""
        # 多选 "A,B,C" / "A B C" -> ["A","C"] 列表
        parts = [
            p.strip()
            for p in s.replace("，", ",").replace(" ", ",").split(",")
            if p.strip()
        ]
        if len(parts) > 1:
            return sorted(parts)
        # 单选/判断：单个字母原样；含标点或无逗号的多选组合也原样
        return s

    def lookup_answer(self, question):
        """查找一题的答案：先按 id 查库，再按题干匹配。找不到返回 None。"""
        qid = str(question.get("id") or question.get("questionsId") or "")
        if qid and qid in self.answer_bank:
            return self.answer_bank[qid]
        stem = (question.get("stem") or "").strip()
        if stem and stem in self._stem_index:
            return self._stem_index[stem]
        return None

    # ---------------- 考试 ----------------
    def my_exams(self):
        """我的考试列表。row 字段：id/examNo/examName/startTime/endTime/examTime/qualifiedScore/score。"""
        res = self.api_get("/jcedutec/exam/myExamList", {})
        self._check(res, "获取考试列表")
        return self._rows(res)

    def start_exam(self, exam_id, exam_type="1"):
        """开始考试。网页端固定传 type="1"（正式考试）。
        返回 {recordId, records:[题目...], examTime, startTime}。"""
        data = {"id": exam_id}
        if exam_type is not None:
            data["type"] = exam_type
        res = self.api_post("/jcedutec/exam/startExam", data)
        self._check(res, "开始考试")
        result = res.get("result") or {}
        return {
            "recordId": result.get("id"),
            "records": result.get("records") or result.get("questionList") or [],
            "examTime": result.get("examTime"),
            "startTime": result.get("startTime"),
        }

    def submit_exam(self, record_id, answers):
        """提交考试。answers: {questionId: "A" 或 ["A","C"]}。"""
        res = self.api_post("/jcedutec/exam/submitExam/%s" % record_id, answers)
        self._check(res, "提交考试")
        return res

    def exam_uncorrect(self, exam_id, exam_type=None):
        """考试错题（含 correctAnswer / analysis）。"""
        params = {"id": exam_id}
        if exam_type is not None:
            params["type"] = exam_type
        res = self.api_get("/jcedutec/exam/unCorrect", params)
        self._check(res, "获取考试错题")
        return self._rows(res)

    # ---------------- 个人信息 / 承诺书 ----------------
    def my_info(self):
        res = self.api_get("/students/queryMyInfo", {})
        self._check(res, "获取个人信息")
        return res.get("result") or {}

    def commitment_ready(self):
        """承诺书是否已上传。"""
        info = self.my_info()
        return bool((info.get("commitmentPath") or "").strip())

    def download_commitment(self, save_path=None):
        """下载实验室安全承诺书模板（doc），返回保存路径。"""
        import urllib.request as u

        url = self.base + "/sys/common/static/temp/commitment.doc"
        path = save_path or os.path.join(SCRIPT_DIR, "实验室安全承诺书.doc")
        req = u.Request(url, headers=self._headers(self.base))
        with u.urlopen(req, context=_SSL_CTX, timeout=20) as resp:
            data = resp.read()
        with open(path, "wb") as f:
            f.write(data)
        return path

    def upload_commitment(self, file_path, field="file"):
        """上传已签字的承诺书照片/扫描件并绑定到账号。

        两步：1) multipart 上传到 /sys/common/upload -> 得到 url；
              2) POST /students/updateMyInfo 写入 commitmentPath。
        """
        import mimetypes
        import uuid

        if not os.path.isfile(file_path):
            raise LabApiError("承诺书文件不存在: %s" % file_path)
        # ---- 1) 上传文件 ----
        boundary = "----DSH" + uuid.uuid4().hex
        with open(file_path, "rb") as f:
            file_data = f.read()
        fname = os.path.basename(file_path)
        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        parts = []
        parts.append(("--%s\r\n" % boundary).encode("utf-8"))
        parts.append(
            (
                'Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (field, fname.replace('"', ""))
            ).encode("utf-8")
        )
        parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode("utf-8"))
        parts.append(file_data)
        parts.append(b"\r\n")
        parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
        body = b"".join(parts)
        headers = self._headers(self.base)
        headers["Content-Type"] = "multipart/form-data; boundary=%s" % boundary
        req = urllib.request.Request(
            self.base + "/sys/common/upload", data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, context=_SSL_CTX, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            up = json.loads(raw)
        except ValueError:
            raise LabApiError("承诺书上传接口返回异常: %s" % raw[:200])
        if not up.get("success"):
            raise LabApiError("上传承诺书失败: %s" % up.get("message", up))
        r = up.get("result") or {}
        path = r.get("path") or r.get("url")
        if not path:
            raise LabApiError("上传成功但未返回文件路径: %s" % str(r)[:200])
        self.log("  上传成功: %s" % path, "success")
        # ---- 2) 绑定到账号 ----
        res = self.api_post("/students/updateMyInfo", {"commitmentPath": path})
        self._check(res, "绑定承诺书")
        self.log("承诺书已绑定到账号。", "success")
        return True

    # ---------------- 自动刷课 ----------------
    def _target_seconds(self, detail, questions):
        """估算该课程需要上报的观看时长（秒）。

        优先取课程自带 duration 字段；否则用最后一题的 ejectTime 作为下限；
        再回退到默认 60 秒。
        """
        for key in (
            "duration",
            "watchDuration",
            "studyTime",
            "playTime",
            "videoDuration",
        ):
            v = detail.get(key)
            try:
                v = float(v) if v not in (None, "", "null") else 0
            except (TypeError, ValueError):
                v = 0
            if v and v > 0:
                return max(int(v), 60)
        last = 0
        for q in questions:
            try:
                last = max(last, int(q.get("ejectTime") or 0))
            except (TypeError, ValueError):
                pass
        if last > 0:
            return last + 60
        return 60

    def _answer_for(self, question):
        """解析一题的答案，找不到返回 None。返回格式："A" 或 ["A","C"]。"""
        # 1) 题目自带正确答案（联调后若返回则直接用）
        raw = question.get("correctAnswer")
        if raw not in (None, ""):
            return self._norm_answer(raw)
        # 2) 题库 id 匹配
        qid = str(question.get("id") or question.get("questionsId") or "")
        if qid and qid in self.answer_bank:
            return self.answer_bank[qid]
        # 3) 题干匹配
        stem = (question.get("stem") or "").strip()
        if stem and stem in self._stem_index:
            return self._stem_index[stem]
        return None

    @staticmethod
    def _num(*vals):
        """取第一个能转成正数的值，否则 0。"""
        for v in vals:
            try:
                v = float(v) if v not in (None, "", "null") else 0
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                return v
        return 0

    @staticmethod
    def _eject_time(q):
        """弹题弹出时间（秒）。"""
        try:
            return float(q.get("ejectTime") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _answer_question(self, cid, q):
        """作答一道课程弹题。返回是否作答成功。"""
        ans = self._answer_for(q)
        stem = (q.get("stem") or "")[:50]
        if ans is None:
            self.log("     [题 %s] %s" % (q.get("id"), stem), "info")
            self.log("       !! 题库中无该题答案，跳过作答（可事后补充）", "warn")
            return False
        try:
            r = self.submit_answer(cid, q.get("id"), ans)
            if r.get("success"):
                self.log(
                    "     [答对] %s -> %s"
                    % (stem, ans if isinstance(ans, str) else ",".join(ans)),
                    "success",
                )
                return True
            self.log("     [作答失败] %s: %s" % (stem, r.get("message", r)), "error")
        except LabApiError as e:
            self.log("     [作答异常] %s: %s" % (stem, e), "error")
        return False

    def _course_row(self, cid):
        """从课程列表中取某一门课的最新行，取不到返回 {}。"""
        for c in self.my_courses():
            if c.get("id") == cid:
                return c
        return {}

    @staticmethod
    def _is_finish(row):
        return str((row or {}).get("isFinish")) in ("1", "true", "True")

    def learn_one_course(self, cid, course_name="", row=None, speed=None, retry=True):
        """刷单个课程：上报进度 + 到时间点答弹题 + 标记完成。

        平台会按“真实观看时长”校验上报值：必须分段上报（每 DEFAULT_REPORT_STEP 秒一次），
        且平台只计入与真实耗时相当的部分；因此等待时长 = 步长 / speed。
        speed=1 为正常速度（已验证可真正完成）；speed>1 时上报更快，若平台拒绝
        （结束仍未完成）会自动回退到 1 倍速重刷一次。
        row 为 my_courses 的课程行（可选），用于读取已看百分比以便接着刷。
        """
        spd = float(speed or self.speed or DEFAULT_SPEED)
        name = course_name or cid
        self.log("")
        self.log("== 课程: %s (id=%s)" % (name, cid))
        self.update_visits(cid)

        detail = {}
        try:
            detail = self.course_detail(cid)
        except LabApiError as e:
            self.log("   详情获取失败: %s（继续刷课）" % e, "warn")

        try:
            questions = self.course_questions(cid)
        except LabApiError as e:
            self.log("   题目获取失败: %s" % e, "warn")
            questions = []
        questions.sort(key=self._eject_time)
        if questions:
            self.log("   嵌入式题目 %d 道" % len(questions), "info")

        # 1. 真实视频时长（秒）：详情 duration -> 列表行 duration -> 按弹题时间点估算
        row = row or {}
        target = int(
            self._num(
                detail.get("duration"),
                detail.get("second"),
                row.get("duration"),
                row.get("second"),
            )
        )
        if target <= 0:
            target = self._target_seconds(detail, questions)
        if self.fast:
            target = max(target, 60)

        # 2. 接着上次的进度刷（列表行 watchDuration 是已看百分比，0-100）
        cur = 0
        pct = self._num(row.get("watchDuration"))
        if 0 < pct < 98:
            cur = int(target * pct / 100.0)
            self.log("   从已看 %d%%（约 %d 秒）继续" % (int(pct), cur), "info")

        step = DEFAULT_REPORT_STEP
        if self.fast or self.sleep_scale <= 0:
            wait = 0
        else:
            wait = max(0.2, step * self.sleep_scale / max(spd, 0.05))
        self.log(
            "   目标观看时长 %d 秒，每 %d 秒视频上报一次%s%s"
            % (
                target,
                step,
                "，倍速 %.1fx" % spd if spd != 1 else "",
                "（极速模式不等待）" if self.fast else "",
            )
        )

        # 3. 播放循环：到时间点作答弹题，然后上报进度并真实等待
        qi = 0
        while cur < target:
            cur = min(target, cur + step)
            while qi < len(questions) and self._eject_time(questions[qi]) <= cur:
                self._answer_question(cid, questions[qi])
                qi += 1
            try:
                r = self.report_finish_rate(cid, cur)
                if not r.get("success"):
                    self.log(
                        "   进度上报失败(%ds): %s" % (cur, r.get("message", r)), "warn"
                    )
            except LabApiError as e:
                self.log("   进度上报异常(%ds): %s" % (cur, e), "error")
            time.sleep(wait)
        # 补答视频结束后仍未作答的弹题
        while qi < len(questions):
            self._answer_question(cid, questions[qi])
            qi += 1

        # 4. 标记完成
        try:
            r = self.finish_course(cid)
        except LabApiError as e:
            self.log("   完成标记异常: %s" % e, "error")
            return False
        if not r.get("success"):
            self.log("   完成标记失败: %s" % r.get("message", r), "error")
            return False

        # 5. 倍速下平台可能拒绝上报，复查一次；未完成则回退 1 倍速重刷
        if self.fast or spd == 1.0:
            self.log("   [完成] 课程已标记完成", "success")
            return True
        row2 = self._course_row(cid)
        if self._is_finish(row2):
            self.log("   [完成] 课程已标记完成（已按 %.1fx 刷完）" % spd, "success")
            return True
        if retry:
            self.log(
                "   倍速 %.1fx 未通过平台校验（平台只计入与真实耗时相当的部分），"
                "改为 1 倍速重刷本课..." % spd,
                "warn",
            )
            return self.learn_one_course(
                cid, course_name, row=row2, speed=1.0, retry=False
            )
        self.log("   注意：平台仍显示未完成，可稍后重跑（会从当前进度继续）。", "warn")
        return False

    def learn_all(self, fast=False, workers=1, name=None, speed=None):
        """遍历我的课程全部刷完。

        fast=False 按真实播放时长等待（平台要求，能真正刷完但耗时）；
        workers 为并发课程数（默认 1，串行；不同课程计时互不影响，可并行）；
        name 非空时只刷名称包含该关键字的课程；
        speed 为播放倍速（默认 2.0；>1 上报更快，平台若不接受会自动回退 1 倍速）。
        返回 (接口成功数, 未完成总数, 失败课程名列表)。
        """
        self.fast = fast
        self.speed = float(speed or DEFAULT_SPEED)
        self.log("=" * 60)
        courses = self.my_courses()
        if name:
            courses = [c for c in courses if name in (c.get("name") or "")]
            if not courses:
                self.log("没有名称包含 %r 的课程。" % name, "warn")
                return 0, 0, []
        if not courses:
            self.log("我的课程列表为空（可能 token 无效或未绑定课程）。", "warn")
            return 0, 0, []
        unfinished = [
            c for c in courses if str(c.get("isFinish")) not in ("1", "true", "True")
        ]
        self.log("共 %d 门课程，未完成 %d 门" % (len(courses), len(unfinished)))
        if not unfinished:
            return 0, 0, []

        total_sec = sum(
            int(self._num(c.get("duration"), c.get("second")) or 60) for c in unfinished
        )
        n = max(1, int(workers or 1))
        self.log(
            "视频总时长 %d 分 %d 秒；%d 路并发%s，预计约 %d 分 %d 秒"
            % (
                total_sec // 60,
                total_sec % 60,
                n,
                "、%.1fx 倍速" % self.speed if self.speed != 1 else "",
                total_sec // self.speed / 60 / n,
                (total_sec / self.speed / n) % 60,
            )
        )

        done = 0
        failed = []
        lock = threading.Lock()
        queue = list(unfinished)

        def worker():
            nonlocal done
            while True:
                with lock:
                    if not queue:
                        return
                    c = queue.pop(0)
                    idx = len(unfinished) - len(queue)
                self.log("[%d/%d] 开始 %s" % (idx, len(unfinished), c.get("name")))
                try:
                    ok = self.learn_one_course(
                        c.get("id"), c.get("name"), row=c, speed=self.speed
                    )
                except Exception as e:  # noqa: BLE001 - 单课失败不中断整体
                    self.log("  [异常] %s: %s" % (c.get("name"), e), "error")
                    ok = False
                with lock:
                    if ok:
                        done += 1
                    else:
                        failed.append(c.get("name"))

        if n > 1:
            threads = []
            for _ in range(min(n, len(unfinished))):
                t = threading.Thread(target=worker)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()
        else:
            worker()

        # 全部结束后核对一次（只发一次请求）
        self.log("")
        self.log("=" * 60)
        try:
            left = [
                c
                for c in self.my_courses()
                if str(c.get("isFinish")) not in ("1", "true", "True")
            ]
            if not left:
                self.log("刷课完成：全部课程均已标记完成。", "success")
            else:
                self.log(
                    "刷课结束：接口成功 %d 门，平台仍显示 %d 门未完成（平台可能在后台校验，"
                    "稍后重跑本命令即可继续）:" % (done, len(left)),
                    "warn",
                )
                for c in left[:20]:
                    self.log(
                        "   - %s（进度 %s%%）" % (c.get("name"), c.get("learnRate")),
                        "warn",
                    )
        except LabApiError as e:
            self.log("刷课结束：接口成功 %d 门（最终核对失败: %s）" % (done, e), "warn")
        if failed:
            self.log("失败课程: %s" % ", ".join(str(x) for x in failed), "warn")
        return done, len(unfinished), failed

    # ---------------- 自动考试 ----------------
    def run_exam(self, exam_id=None, exam_type="1"):
        """自动完成一场考试。返回结果字典。"""
        if not self.commitment_ready():
            raise LabApiError(
                "请先在【实验室安全】页下载并上传已签字的《实验室安全承诺书》，"
                "之后才能开始考试。"
            )
        eid = exam_id
        if not eid:
            exams = self.my_exams()
            if not exams:
                return {"submitted": False, "reason": "无可参加的考试"}
            eid = exams[0].get("id")
            self.log(
                "未指定考试 ID，自动选择第一场: %s" % exams[0].get("examName", eid)
            )
        self.log("开始自动考试 (examId=%s)" % eid)
        st = self.start_exam(eid, exam_type)
        records = st["records"]
        self.log("考试记录 id=%s，题目 %d 道" % (st["recordId"], len(records)))
        if not st["recordId"] or not records:
            return {"submitted": False, "reason": "考试无可提交记录/题目"}

        answers = {}
        missing = []
        for q in records:
            ans = self._answer_for(q)
            qid = q.get("id") or q.get("questionsId")
            if ans is None:
                missing.append(q)
                continue
            answers[str(qid)] = ans
            self.log(
                "  [%s] %s -> %s"
                % (
                    qid,
                    (q.get("stem") or "")[:40],
                    ans if isinstance(ans, str) else "".join(ans),
                )
            )
        if missing:
            self.log("")
            self.log(
                "[!] %d 道题不在题库内，为避免答错丢分，本次【不提交】："
                % len(missing),
                "warn",
            )
            for q in missing:
                self.log("   【%s】%s" % (q.get("kind"), q.get("stem")), "warn")
            return {
                "submitted": False,
                "filled": len(answers),
                "total": len(records),
                "unanswered": missing,
            }
        resp = self.submit_exam(st["recordId"], answers)
        self.log("提交完成：已答 %d / %d 题" % (len(answers), len(records)), "success")
        return {
            "submitted": True,
            "filled": len(answers),
            "total": len(records),
            "response": resp,
        }

    def run_all_exams(self, exam_type="1"):
        """把我已有的考试全部提交一遍。返回 (成功数, 总数)。"""
        if not self.commitment_ready():
            raise LabApiError(
                "请先下载并上传已签字的《实验室安全承诺书》，之后才能开始考试。"
            )
        exams = self.my_exams()
        if not exams:
            self.log("没有可参加的考试。", "warn")
            return 0, 0
        ok = 0
        for i, ex in enumerate(exams, 1):
            self.log("")
            self.log(
                "[考试 %d/%d] %s (合格分 %s)"
                % (i, len(exams), ex.get("examName"), ex.get("qualifiedScore"))
            )
            try:
                r = self.run_exam(ex.get("id"), exam_type)
                if r.get("submitted"):
                    ok += 1
            except LabApiError as e:
                self.log("  考试失败: %s" % e, "error")
        self.log("考试处理完成：成功 %d / %d" % (ok, len(exams)), "success")
        return ok, len(exams)


def load_config():
    return LabApi.load_config()


if __name__ == "__main__":
    # 只做单元测试验证，见 e2e_lab_test.py
    print("api loaded OK")
