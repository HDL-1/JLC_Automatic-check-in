import requests
import json
import time
import random
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime, timedelta
from requests.exceptions import RequestException
from collections import defaultdict
import urllib.parse

# ============== 配置区域 ==============

COOKIE_LIST = [
    "账号cookie"
    #更换对应cookie就可以更换账号自动签到
    #"账号2"
    #"账号3"
]

SEND_KEY_LIST = [
    "推送的key"    #方糖KEY
    #"账号2推送微信"
    #"账号3推送微信"
]

# 签到时间配置（24小时制）
SIGN_HOUR = 7    #签到小时
SIGN_MINUTE = 0  #签到分钟

# 重试配置
RETRY_INTERVAL = 300  # 5分钟 = 300秒
MAX_RETRY = 3  # 最大重试次数

# OSHWHUB 接口配置
BASE_URL = "https://oshwhub.com"
SIGN_URL = f"{BASE_URL}/api/users/signIn"  # 正确的签到接口
USER_INFO_URL = f"{BASE_URL}/api/users/signInRecord"  # 用户信息查询接口


# ============== 核心功能类 ==============

class OSHWHubAutoSign:
    def __init__(self, gui=None):
        self.gui = gui
        self.sign_status = {}  # 记录每个账号的签到状态
        self.is_running = True
        self.today_sign_done = False
        self.last_sign_date = None  # 记录上次签到日期

    def log(self, message, level="INFO"):
        """输出日志到GUI和控制台"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_msg = f"[{timestamp}] [{level}] {message}"
        print(log_msg)
        if self.gui:
            self.gui.add_log(message, level)

    def mask_account(self, account):
        """隐藏部分账号信息"""
        if len(account) >= 4:
            return account[:2] + '****' + account[-2:]
        return '****'

    def send_msg_by_server(self, send_key, title, content):
        """Server酱推送"""
        push_url = f'https://sctapi.ftqq.com/{send_key.strip()}.send'
        data = {'text': title, 'desp': content}
        try:
            response = requests.post(push_url, data=data, timeout=10)
            return response.json()
        except RequestException as e:
            self.log(f"推送失败: {e}", "ERROR")
            return None

    def build_verify_url(self):
        """构建用户信息验证 URL（查询最近30天签到记录）"""
        end_time = int(time.time() * 1000) + 24 * 60 * 60 * 1000   # 明天
        start_time = end_time - 30 * 24 * 60 * 60 * 1000           # 30天前
        return f"{USER_INFO_URL}?startTime={start_time}&endTime={end_time}"

    def get_csrf_token(self, cookie_str):
        """从 Cookie 中提取 CSRF Token"""
        try:
            for item in cookie_str.split(';'):
                item = item.strip()
                if item.startswith('oshwhub_csrf='):
                    # URL 解码
                    csrf = urllib.parse.unquote(item.split('=', 1)[1])
                    return csrf
        except Exception as e:
            self.log(f"提取 CSRF 失败: {e}", "WARN")
        return None

    def sign_single_account(self, cookie_str, retry_count=0):
        """单个账号签到，失败自动重试"""
        headers = {
            'Cookie': cookie_str,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Content-Type': 'application/json;charset=UTF-8',  # JSON格式
            'Origin': 'https://oshwhub.com',
            'Referer': 'https://oshwhub.com/sign_in',
            'X-Requested-With': 'XMLHttpRequest',
        }

        # 添加 CSRF Token 到 Header
        csrf_token = self.get_csrf_token(cookie_str)
        if csrf_token:
            headers['X-CSRF-Token'] = csrf_token

        try:
            # 1. 先获取用户信息验证 Cookie 有效（使用 signInRecord 接口）
            self.log("验证用户信息...", "DEBUG")

            # 构建查询时间范围（最近30天）
            verify_url = self.build_verify_url()
            user_response = requests.get(verify_url, headers=headers, timeout=10)

            # 特别处理 401 错误
            if user_response.status_code == 401:
                self.log("Cookie 已失效（401），请重新登录 OSHWHUB 获取", "ERROR")
                self.log("步骤：浏览器访问 oshwhub.com → 登录 → F12 → Network → 复制 Cookie", "INFO")
                return {
                    'success': False,
                    'message': 'Cookie已失效，请重新获取',
                    'need_refresh_cookie': True
                }

            user_response.raise_for_status()

            # 解析用户信息
            user_code = "unknown"
            user_nickname = "unknown"

            try:
                user_result = user_response.json()
                if user_result.get('success') and user_result.get('data'):
                    records = user_result['data']
                    if records and len(records) > 0:
                        first_record = records[0]
                        # 尝试多种可能的字段名
                        user_code = first_record.get('customerCode') or first_record.get('customer_code') or 'unknown'
                        user_nickname = first_record.get('nickname') or first_record.get(
                            'userName') or first_record.get('username') or 'unknown'
            except:
                pass

            # 如果还是unknown，从Cookie中提取
            if user_code == 'unknown':
                for item in cookie_str.split(';'):
                    if 'jlc_customer_code=' in item:
                        user_code = item.split('=', 1)[1].strip()
                        user_nickname = user_code  # 用code作为昵称
                        break

            masked_code = self.mask_account(user_code)
            self.log(f"用户验证成功: {user_nickname} ({masked_code})", "INFO")

            # 2. 执行签到（POST 请求，带时间戳）
            self.log(f"账号 {masked_code} 开始签到...", "INFO")

            # 构建请求体：{ "_t": 毫秒时间戳 }
            payload = {
                "_t": int(time.time() * 1000)
            }

            sign_response = requests.post(SIGN_URL, headers=headers, json=payload, timeout=10)  # ← 使用 json=payload
            sign_response.raise_for_status()
            sign_result = sign_response.json()

            # 解析签到结果
            if sign_result.get('success'):
                # 获取连续签到天数等信息
                data = sign_result.get('data', {})
                check_in_days = data.get('checkInDays') or data.get('check_in_days', 0)
                reward = data.get('reward', '无')

                self.log(f"账号 {masked_code} 签到成功，连续签到 {check_in_days} 天，奖励: {reward}", "SUCCESS")
                return {
                    'success': True,
                    'code': masked_code,
                    'nickname': user_nickname,
                    'message': f'签到成功，连续{check_in_days}天，奖励:{reward}',
                    'check_in_days': check_in_days,
                    'is_already': False
                }
            else:
                message = sign_result.get('message', '未知错误')
                if '已经签到' in message or '已签到' in message or '重复' in message or 'already' in message.lower():
                    self.log(f"账号 {masked_code} 今日已签到", "INFO")
                    return {
                        'success': True,
                        'code': masked_code,
                        'nickname': user_nickname,
                        'message': '今日已签到',
                        'is_already': True
                    }
                else:
                    raise Exception(f"签到失败: {message}")

        except Exception as e:
            self.log(f"签到异常: {e}", "ERROR")

            # 自动重试
            if retry_count < MAX_RETRY:
                self.log(f"{RETRY_INTERVAL // 60}分钟后第{retry_count + 1}次重试...", "WARN")
                time.sleep(RETRY_INTERVAL)
                return self.sign_single_account(cookie_str, retry_count + 1)
            else:
                self.log(f"账号签到失败，已达最大重试次数", "ERROR")
                return {
                    'success': False,
                    'message': str(e),
                    'is_already': False
                }

    def wait_until_sign_time(self):
        """等待到签到时间，使用绝对时间检查避免睡眠问题"""
        self.last_sign_date = None

        while self.is_running:
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')

            # 睡眠恢复检测
            if self.last_sign_date != today:
                self.today_sign_done = False
                self.last_sign_date = today

                if now.hour > SIGN_HOUR or (now.hour == SIGN_HOUR and now.minute >= SIGN_MINUTE):
                    self.log(f"检测到已过签到时间（当前 {now.strftime('%H:%M')}），立即执行...", "WARN")
                    self.run_sign_task()
                    continue

            # 计算下次签到时间
            target = now.replace(hour=SIGN_HOUR, minute=SIGN_MINUTE, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)

            wait_seconds = (target - now).total_seconds()
            self.log(f"下次签到: {target.strftime('%Y-%m-%d %H:%M:%S')}", "INFO")

            # 每秒检查实际时间
            while self.is_running:
                now = datetime.now()
                remaining = (target - now).total_seconds()

                if self.gui:
                    self.gui.update_countdown(int(remaining))

                if now >= target:
                    break

                time.sleep(1)

            if not self.is_running:
                break

            self.run_sign_task()
    def run_sign_task(self):
        """执行签到任务"""
        self.log("=" * 50, "INFO")
        self.log(f"开始执行 {datetime.now().strftime('%Y-%m-%d')} OSHWHUB 签到任务", "INFO")

        # 更新GUI状态为执行中
        if self.gui:
            self.gui.update_status("执行中...", "orange")

        CookieList = [c.strip() for c in COOKIE_LIST if c.strip()]
        SendKeyList = [k.strip() for k in SEND_KEY_LIST if k.strip()]

        if not CookieList or not SendKeyList:
            self.log("配置错误：COOKIE或SENDKEY为空", "ERROR")
            if self.gui:
                self.gui.update_status("配置错误", "red")
            return False

        # 确保长度一致
        min_length = min(len(CookieList), len(SendKeyList))
        results = []
        all_success = True

        for i in range(min_length):
            cookie = CookieList[i]
            send_key = SendKeyList[i]

            self.log(f"处理第 {i + 1}/{min_length} 个账号...", "INFO")
            result = self.sign_single_account(cookie)
            results.append((send_key, result))

            if not result['success']:
                all_success = False

            # 账号间延迟
            if i < min_length - 1:
                time.sleep(random.randint(3, 8))

        # 推送通知
        self.send_notifications(results)

        self.today_sign_done = all_success
        self.log(f"签到任务完成，今日签到状态: {'成功' if all_success else '部分失败'}", "INFO")

        # 更新GUI状态
        if self.gui:
            status_color = "green" if all_success else "red"
            status_text = "已完成" if all_success else "部分失败"
            self.gui.update_status(status_text, status_color)

        return all_success

    def send_notifications(self, results):
        """发送推送通知"""
        # 按SendKey分组
        groups = defaultdict(list)
        for send_key, result in results:
            groups[send_key].append(result)

        for send_key, group_results in groups.items():
            success_results = [r for r in group_results if r['success']]

            if not success_results:
                continue

            # 构建通知内容
            content_lines = []
            for r in success_results:
                nickname = r.get('nickname', r['code'])
                if r.get('is_already'):
                    content_lines.append(f"⏭️ {nickname}: {r['message']}")
                else:
                    content_lines.append(f"✅ {nickname}: {r['message']}")

            content = "\n\n".join(content_lines)

            # 发送
            response = self.send_msg_by_server(send_key, "OSHWHUB 签到成功", content)
            if response and response.get('code') == 0:
                self.log(f"通知发送成功", "SUCCESS")
            else:
                self.log(f"通知发送失败", "ERROR")


# ============== GUI界面 ==============

class SignGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("OSHWHUB 自动签到脚本 青竹斋")
        self.root.geometry("700x550")
        self.root.minsize(600, 450)

        # 设置样式
        self.style = ttk.Style()
        self.style.configure('Title.TLabel', font=('Microsoft YaHei', 16, 'bold'))
        self.style.configure('Subtitle.TLabel', font=('Microsoft YaHei', 10))
        self.style.configure('Status.TLabel', font=('Microsoft YaHei', 11))

        # 创建界面
        self.create_widgets()

        # 启动签到器
        self.signer = OSHWHubAutoSign(self)
        self.running = True

        # 启动后台线程
        self.sign_thread = threading.Thread(target=self.signer.wait_until_sign_time, daemon=True)
        self.sign_thread.start()

        # 启动时间更新定时器
        self.update_current_time()

        self.add_log("程序启动成功，等待 OSHWHUB 签到时间...", "INFO")

    def create_widgets(self):
        """创建界面元素"""
        # 主容器
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题区域
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(title_frame, text="OSHWHUB 自动签到系统",
                  style='Title.TLabel').pack()
        ttk.Label(title_frame, text=f"每日 {SIGN_HOUR:02d}:{SIGN_MINUTE:02d} 自动签到，失败自动重试",
                  style='Subtitle.TLabel').pack()

        # 状态区域
        status_frame = ttk.LabelFrame(main_frame, text="当前状态", padding="10")
        status_frame.pack(fill=tk.X, pady=5)

        # 使用网格布局对齐
        status_inner = ttk.Frame(status_frame)
        status_inner.pack(fill=tk.X)

        ttk.Label(status_inner, text="今日日期:",
                  style='Status.TLabel').grid(row=0, column=0, sticky=tk.W, padx=5)
        self.date_label = ttk.Label(status_inner, text="--",
                                    style='Status.TLabel')
        self.date_label.grid(row=0, column=1, sticky=tk.W, padx=5)

        ttk.Label(status_inner, text="当前时间:",
                  style='Status.TLabel').grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.time_label = ttk.Label(status_inner, text="--",
                                    style='Status.TLabel')
        self.time_label.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(status_inner, text="签到状态:",
                  style='Status.TLabel').grid(row=2, column=0, sticky=tk.W, padx=5)
        self.status_label = ttk.Label(status_inner, text="等待中",
                                      style='Status.TLabel', foreground='blue')
        self.status_label.grid(row=2, column=1, sticky=tk.W, padx=5)

        ttk.Label(status_inner, text="倒计时:",
                  style='Status.TLabel').grid(row=3, column=0, sticky=tk.W, padx=5, pady=5)
        self.countdown_label = ttk.Label(status_inner, text="计算中...",
                                         style='Status.TLabel', foreground='green')
        self.countdown_label.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)

        # 日志区域
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # 日志文本框
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=12,
            state='disabled',
            font=('Consolas', 10),
            wrap=tk.WORD
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 配置日志标签颜色
        self.log_text.tag_config("timestamp", foreground="gray")
        self.log_text.tag_config("INFO", foreground="black")
        self.log_text.tag_config("SUCCESS", foreground="green")
        self.log_text.tag_config("WARN", foreground="orange")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("DEBUG", foreground="gray")

        # 按钮区域
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=10)

        ttk.Button(btn_frame, text="立即签到",
                   command=self.manual_sign, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="测试Cookie",
                   command=self.test_cookie, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="最小化",
                   command=self.minimize, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="退出程序",
                   command=self.on_close, width=12).pack(side=tk.RIGHT, padx=5)

        # 协议关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def update_current_time(self):
        """更新当前时间显示"""
        now = datetime.now()
        self.date_label.config(text=now.strftime("%Y-%m-%d"))
        self.time_label.config(text=now.strftime("%H:%M:%S"))

        # 每秒更新一次
        self.root.after(1000, self.update_current_time)

    def add_log(self, message, level="INFO"):
        """添加日志到文本框（线程安全）"""
        self.root.after(0, self._add_log_safe, message, level)

    def _add_log_safe(self, message, level):
        """实际添加日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")

        self.log_text.configure(state='normal')
        self.log_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.log_text.insert(tk.END, f"{message}\n", level)
        self.log_text.see(tk.END)
        self.log_text.configure(state='disabled')

    def update_countdown(self, seconds):
        """更新倒计时显示"""

        def _update():
            if seconds <= 0:
                self.countdown_label.config(text="即将执行...", foreground="orange")
                return

            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60

            time_str = f"{hours:02d}:{minutes:02d}:{secs:02d}"
            self.countdown_label.config(text=time_str)

        self.root.after(0, _update)

    def update_status(self, status, color="blue"):
        """更新状态标签"""
        self.root.after(0, lambda: self.status_label.config(
            text=status,
            foreground=color
        ))

    def manual_sign(self):
        """手动触发签到"""
        self.add_log("手动触发 OSHWHUB 签到...", "INFO")
        threading.Thread(target=self.signer.run_sign_task, daemon=True).start()

    def test_cookie(self):
        """测试Cookie有效性"""
        self.add_log("测试 OSHWHUB Cookie 中...", "INFO")
        threading.Thread(target=self._do_test_cookie, daemon=True).start()

    def _do_test_cookie(self):
        """实际测试逻辑"""
        try:
            cookie = COOKIE_LIST[0]
            headers = {
                'Cookie': cookie,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Referer': 'https://oshwhub.com/sign_in',
            }

            # 使用 signInRecord 接口测试
            end_time = int(time.time() * 1000) + 24 * 60 * 60 * 1000
            start_time = end_time - 30 * 24 * 60 * 60 * 1000
            test_url = f"{USER_INFO_URL}?startTime={start_time}&endTime={end_time}"

            response = requests.get(test_url, headers=headers, timeout=10)

            if response.status_code == 401:
                self.add_log("Cookie 已失效（401），请重新登录 OSHWHUB", "ERROR")
                return

            result = response.json()

            if result.get('success'):
                data = result.get('data', [])
                if data and len(data) > 0:
                    first = data[0]
                    code = first.get('customerCode', 'unknown')
                    nickname = first.get('nickname', 'unknown')
                    self.add_log(f"Cookie 有效！用户: {nickname} ({code[:2]}****{code[-2:]})", "SUCCESS")
                else:
                    self.add_log("Cookie 有效，但暂无签到记录", "SUCCESS")
            else:
                self.add_log(f"Cookie 可能失效: {result.get('message')}", "ERROR")
        except Exception as e:
            self.add_log(f"测试失败: {e}", "ERROR")

    def minimize(self):
        """最小化窗口"""
        self.root.iconify()
        self.add_log("程序已最小化，后台继续运行...", "INFO")

    def on_close(self):
        """关闭程序"""
        self.running = False
        self.signer.is_running = False
        self.add_log("程序正在关闭...", "INFO")
        self.root.after(500, self.root.destroy)


# ============== 启动入口 ==============

def main():
    """主函数"""
    if len(sys.argv) > 1 and sys.argv[1] == '--cli':
        # 命令行模式
        print("运行命令行模式（无GUI）...")
        signer = OSHWHubAutoSign()
        signer.wait_until_sign_time()
    else:
        # GUI模式
        root = tk.Tk()
        app = SignGUI(root)
        root.mainloop()


if __name__ == '__main__':
    main()