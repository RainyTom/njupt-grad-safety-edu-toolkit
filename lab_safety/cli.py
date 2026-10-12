# -*- coding: utf-8 -*-
# cli.py —— 实验室安全教育平台终端工具（刷课 / 查进度 / 考试 / 承诺书）
# ==========================================================================
# 用法（在仓库根目录执行）：
#   python lab_safety/cli.py config --token <Access-Token>   把 token 写进 lab_safety/config.json
#   python lab_safety/cli.py status                          看每门课的进度
#   python lab_safety/cli.py learn                           刷全部未完成课程（默认 2 倍速，1 门串行）
#   python lab_safety/cli.py learn --workers 3               3 门并发（更快，但请求更频繁）
#   python lab_safety/cli.py learn --course 消防              只刷名称含“消防”的课
#   python lab_safety/cli.py learn --speed 3                3 倍速（默认 2；过高平台会拒绝）
#   python lab_safety/cli.py learn --token <token>           临时用另一个账号
#   python lab_safety/cli.py exam                            自动考试
#   python lab_safety/cli.py commitment                      下载承诺书模板
#   python lab_safety/cli.py commitment --upload 照片.jpg     上传已签字的承诺书
#
# 【重要】平台按“真实观看时长”校验进度上报：
#   - 必须按节拍分段上报，并真实等待相应秒数，否则进度会被平台拒绝，
#     调 finish 时还会把该课进度重置为 0；
#   - 所以单门课耗时 ≈ 视频时长 ÷ 倍速，用 --workers 并发多门课来缩短总时间。
# token 读取顺序：--token 参数 -> 环境变量 LAB_TOKEN -> config.json 的 lab_token

import io
import json
import os
import sys

# 1. 中文日志能在终端正常输出；把本目录加入模块搜索路径
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api
from api import LabApi, LabApiError

CONFIG_PATH = api.CONFIG_PATH
HELP = "python lab_safety/cli.py"

# 2. 参数解析（不用 argparse，保持直白）
argv = sys.argv[1:]
cmd = argv[0] if argv else "help"


def opt(name, default=None):
    # 取 --name value 形式的值
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return default


arg_token = opt("--token")
arg_workers = int(opt("--workers", 1))
arg_course = opt("--course")
arg_upload = opt("--upload")
arg_speed = float(opt("--speed", 0) or 0)
arg_fast = "--fast" in argv

# 3. 日志
LEVEL = {"info": "[i]", "success": "[+]", "warn": "[!]", "error": "[-]"}


def log(msg, level="info"):
    print("%s %s" % (LEVEL.get(level, "[i]"), msg), flush=True)


# 4. config 命令：把 token 等写进 config.json（不需要 token 也能跑）
if cmd == "config":
    data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    if arg_token:
        data["lab_token"] = arg_token
    if opt("--base"):
        data["lab_base"] = opt("--base")
    if opt("--web"):
        data["lab_web"] = opt("--web")
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log("已写入 %s" % CONFIG_PATH, "success")
    log("lab_token = %s...（前 12 位）" % (data.get("lab_token") or "")[:12], "info")
    sys.exit(0)

# 5. help
if cmd in ("help", "-h", "--help"):
    print("南邮实验室安全教育平台 终端工具")
    print("")
    print("  %s config --token <Access-Token>   把 token 写进 config.json" % HELP)
    print("  %s status                          查看课程进度" % HELP)
    print("  %s learn [--speed N] [--workers N] 刷全部未完成课程" % HELP)
    print("  %s learn --course 消防              只刷名称含“消防”的课程" % HELP)
    print("  %s exam                            自动考试" % HELP)
    print("  %s commitment                      下载承诺书模板" % HELP)
    print("  %s commitment --upload 照片.jpg     上传已签字承诺书" % HELP)
    print("")
    print("说明：平台按真实观看时长校验进度，默认 2 倍速刷课（改 config.json 的")
    print("      lab_speed 或加 --speed）；倍速过高（如 5 倍）平台会拒绝并重置进度。")
    sys.exit(0)

# 6. 取 token 并建 api
token = (arg_token or os.environ.get("LAB_TOKEN") or "").strip()
if not token:
    token = (LabApi.load_config().get("lab_token") or "").strip()
if not token:
    log("未找到 token", "error")
    log("设置方式（任选其一）：", "info")
    log("  %s config --token <你的Access-Token>" % HELP, "info")
    log("  export LAB_TOKEN=<你的Access-Token>", "info")
    sys.exit(2)
api = LabApi(token=token, log_callback=lambda lv, tx: log(tx, lv))
# 播放倍速：--speed 优先，否则用 config.json 的 lab_speed，最后默认 2.0
speed = arg_speed or float(LabApi.load_config().get("lab_speed") or 2.0)

# 7. status：查看进度
if cmd == "status":
    courses = api.my_courses()
    if not courses:
        log("课程列表为空（token 可能无效，或该账号未绑定课程）", "warn")
        sys.exit(1)
    done = sum(1 for c in courses if str(c.get("isFinish")) in ("1", "true", "True"))
    log("共 %d 门课，已完成 %d，未完成 %d" % (len(courses), done, len(courses) - done))
    print("")
    print("  %-4s %-38s %7s %8s %8s" % ("#", "课程", "进度", "状态", "弹题"))
    print("  " + "-" * 66)
    for i, c in enumerate(courses, 1):
        nm = (c.get("name") or "")[:36]
        rate = c.get("learnRate") or c.get("watchDuration") or "0"
        fin = "已完成" if str(c.get("isFinish")) in ("1", "true", "True") else "未完成"
        q = "%s/%s" % (c.get("donum") or 0, c.get("total") or 0)
        print("  %-4d %-38s %6s%% %8s %8s" % (i, nm, rate, fin, q))
    sys.exit(0)

# 8. learn：刷课
if cmd == "learn":
    preview = api.my_courses()
    if arg_course:
        preview = [c for c in preview if arg_course in (c.get("name") or "")]
        if not preview:
            log("没有名称含 %r 的课程" % arg_course, "error")
            sys.exit(1)
    left = [c for c in preview if str(c.get("isFinish")) not in ("1", "true", "True")]
    if not left:
        log("这些课程都已完成，无需刷课", "success")
        sys.exit(0)
    sec = sum(int(float(c.get("duration") or c.get("second") or 60)) for c in left)
    workers = max(1, min(arg_workers, len(left)))
    log("待刷 %d 门，视频总时长约 %d 分 %d 秒" % (len(left), sec // 60, sec % 60))
    log(
        "倍速 %.1fx，并发 %d 路，预计耗时约 %d 分 %d 秒"
        % (speed, workers, sec / speed / workers / 60, (sec / speed / workers) % 60)
    )
    if arg_fast:
        log("--fast 会跳过等待：平台校验真实时长，一般不会真正刷完，仅供调试", "warn")
    try:
        done, total, failed = api.learn_all(
            fast=arg_fast, workers=workers, name=arg_course, speed=speed
        )
    except LabApiError as e:
        log("刷课失败: %s" % e, "error")
        sys.exit(1)
    log("本次调用成功 %d 门 / 共 %d 门待刷" % (done, total), "success")
    if failed:
        log("失败课程: %s" % "，".join(str(x) for x in failed), "warn")
    log("可执行 %s status 复查平台最终状态" % HELP, "info")
    sys.exit(0)

# 9. exam：自动考试
if cmd == "exam":
    if not api.commitment_ready():
        log("尚未上传已签字的《实验室安全承诺书》，无法考试", "error")
        log(" 下载模板: %s commitment" % HELP, "info")
        log(" 上传签字: %s commitment --upload 照片.jpg" % HELP, "info")
        sys.exit(1)
    log("正在收集题库答案...")
    log("题库收录 %d 题" % api.harvest_answer_bank())
    try:
        ok, total = api.run_all_exams()
    except LabApiError as e:
        log("考试失败: %s" % e, "error")
        sys.exit(1)
    log("考试完成：成功 %d / %d" % (ok, total), "success")
    sys.exit(0)

# 10. commitment：承诺书
if cmd == "commitment":
    if arg_upload:
        if not os.path.isfile(arg_upload):
            log("文件不存在: %s" % arg_upload, "error")
            sys.exit(1)
        api.upload_commitment(arg_upload)
        log("承诺书已上传并绑定到账号", "success")
    else:
        log("模板已下载: %s" % api.download_commitment(), "success")
        log(
            "打印签字后执行: %s commitment --upload 你的照片.jpg" % HELP, "info"
        )
    sys.exit(0)

# 11. 未知命令
log("未知命令: %s" % cmd, "error")
print("可用命令：config | status | learn | exam | commitment")
sys.exit(2)
