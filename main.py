"""
MyCGC 监控 App 主界面（Kivy）。

功能：
- 显示第1档：实时 CGC / WGDC 数量与比例
- 设置刷新间隔分钟数（需求一）
- 设置第2档（低阈值）、第3档（高阈值）（需求二）
- 启动/停止后台前台服务（需求三，息屏也监控）
- 请求通知权限 & 引导用户关闭电池优化，避免系统杀掉后台服务
"""
import os
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.text import LabelBase, DEFAULT_FONT
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.graphics import Color, Rectangle

from config_store import load_config, save_config
from monitor_core import fetch_amounts_and_ratio

SERVICE_ENTRY_NAME = "monitor"  # 对应 buildozer.spec 里 services = monitor:service/main.py

# ---------------------------------------------------------------------------
# 注册中文字体为默认字体。必须在任何 Label/Button/TextInput 创建之前执行，
# 否则中文会因为默认的 Roboto 字体没有中文字形而显示成方块乱码。
# 字体文件需要你自己下载放到 assets/fonts/NotoSansSC-Regular.ttf
# （Google Noto Sans SC，免费商用：https://fonts.google.com/noto/specimen/Noto+Sans+SC）
# ---------------------------------------------------------------------------
_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "NotoSansSC-Regular.ttf")
if os.path.exists(_FONT_PATH):
    LabelBase.register(DEFAULT_FONT, _FONT_PATH)
else:
    print(f"[WARN] 中文字体文件不存在: {_FONT_PATH}，中文会显示为方块乱码！")

# 统一配色，暗色主题
BG_COLOR = (0.07, 0.07, 0.09, 1)
CARD_COLOR = (0.14, 0.14, 0.17, 1)
ACCENT_COLOR = (0.20, 0.55, 0.95, 1)
TEXT_COLOR = (0.92, 0.92, 0.92, 1)


def _wrap_label(label):
    """让 Label 根据自身宽度自动换行，而不是超出高度溢出到相邻控件上。"""
    label.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
    label.bind(texture_size=lambda w, *_: setattr(w, "height", max(w.texture_size[1] + dp(12), dp(40))))


class SectionLabel(Label):
    """带背景色的小标题，用来分隔每个设置区块，比纯文字更容易区分层级。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(40))
        kwargs.setdefault("font_size", "15sp")
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "middle")
        kwargs.setdefault("color", TEXT_COLOR)
        kwargs.setdefault("padding", (dp(10), 0))
        super().__init__(**kwargs)
        _wrap_label(self)
        with self.canvas.before:
            Color(*CARD_COLOR)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_bg, size=self._update_bg)

    def _update_bg(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size


def make_input(text, input_filter):
    ti = TextInput(
        text=text,
        input_filter=input_filter,
        multiline=False,
        size_hint_y=None,
        height=dp(52),
        font_size="20sp",
        padding=(dp(12), dp(12)),
    )
    return ti


def make_button(text, accent=False):
    btn = Button(
        text=text,
        size_hint_y=None,
        height=dp(56),
        font_size="16sp",
        background_normal="",
        background_color=ACCENT_COLOR if accent else CARD_COLOR,
        color=(1, 1, 1, 1),
    )
    return btn


class MyCGCApp(App):
    def build(self):
        self.cfg = load_config()
        self.title = "MyCGC 监控"

        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))
        with root.canvas.before:
            Color(*BG_COLOR)
            self._root_bg = Rectangle(pos=root.pos, size=root.size)
        root.bind(pos=lambda w, *_: setattr(self._root_bg, "pos", w.pos))
        root.bind(size=lambda w, *_: setattr(self._root_bg, "size", w.size))

        # ---- 顶部状态卡片 ----
        self.status_label = Label(
            text="尚未刷新",
            font_size="17sp",
            size_hint_y=None,
            height=dp(110),
            halign="left",
            valign="top",
            color=TEXT_COLOR,
            padding=(dp(12), dp(12)),
        )
        self.status_label.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
        with self.status_label.canvas.before:
            Color(*CARD_COLOR)
            self._status_bg = Rectangle(pos=self.status_label.pos, size=self.status_label.size)
        self.status_label.bind(pos=lambda w, *_: setattr(self._status_bg, "pos", w.pos))
        self.status_label.bind(size=lambda w, *_: setattr(self._status_bg, "size", w.size))
        root.add_widget(self.status_label)

        # ---- 中间设置表单（可滚动，占满剩余空间） ----
        form = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        form.bind(minimum_height=form.setter("height"))

        form.add_widget(SectionLabel(text="需求一：刷新间隔（分钟，可任意设置）"))
        self.interval_input = make_input(str(self.cfg.get("interval_minutes", 5)), "int")
        form.add_widget(self.interval_input)

        form.add_widget(SectionLabel(text="需求二 · 第2档低阈值：实时比例 < 此值 → 提醒 GDC NOW LOW!"))
        self.low_input = make_input(str(self.cfg.get("low_ratio", 90)), "float")
        form.add_widget(self.low_input)

        form.add_widget(SectionLabel(text="需求二 · 第3档高阈值：实时比例 > 此值 → 提醒 GDC NOW HAGH!"))
        self.high_input = make_input(str(self.cfg.get("high_ratio", 110)), "float")
        form.add_widget(self.high_input)

        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(form)
        root.add_widget(scroll)

        # ---- 底部按钮区 ----
        btn_row1 = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(10))
        save_btn = make_button("保存设置", accent=True)
        save_btn.bind(on_press=self.save_settings)
        refresh_btn = make_button("立即刷新一次")
        refresh_btn.bind(on_press=self.manual_refresh)
        btn_row1.add_widget(save_btn)
        btn_row1.add_widget(refresh_btn)
        root.add_widget(btn_row1)

        btn_row2 = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(10))
        start_btn = make_button("启动后台监控", accent=True)
        start_btn.bind(on_press=self.start_service)
        stop_btn = make_button("停止后台监控")
        stop_btn.bind(on_press=self.stop_service)
        btn_row2.add_widget(start_btn)
        btn_row2.add_widget(stop_btn)
        root.add_widget(btn_row2)

        battery_btn = make_button("关闭电池优化（息屏监控必须点这个）")
        battery_btn.bind(on_press=self.request_ignore_battery_optimization)
        root.add_widget(battery_btn)

        # 国产手机系统(MIUI/ColorOS/OriginOS/EMUI/One UI等)都有一套自己的
        # "自启动管理/后台耗电管理"，跟上面标准的电池优化白名单是两码事——
        # 不点这个，退出App或息屏后系统还是会把后台进程强杀掉。
        autostart_btn = make_button("打开自启动/后台运行管理（小米华为OPPOvivo三星必点）")
        autostart_btn.bind(on_press=self.open_oem_autostart_settings)
        root.add_widget(autostart_btn)

        log_btn = make_button("查看后台运行日志")
        log_btn.bind(on_press=self.show_log)
        root.add_widget(log_btn)

        self.request_runtime_permissions()
        Clock.schedule_interval(self.refresh_status_label, 5)

        # 如果字体文件不存在，在界面上提示用户
        if not os.path.exists(_FONT_PATH):
            Clock.schedule_once(lambda dt: self.set_status_text("警告：未发现中文字体文件\n请确保 assets/fonts/ 目录下有字体文件"), 1)
            
        return root

    # ---------- 权限相关 ----------
    def request_runtime_permissions(self):
        try:
            from android.permissions import request_permissions, Permission
            perms = [Permission.INTERNET]
            for name in ("POST_NOTIFICATIONS",):
                p = getattr(Permission, name, None)
                if p:
                    perms.append(p)
            request_permissions(perms)
        except Exception:
            pass

    def request_ignore_battery_optimization(self, instance):
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            Settings = autoclass("android.provider.Settings")
            Uri = autoclass("android.net.Uri")
            activity = PythonActivity.mActivity
            intent = Intent()
            intent.setAction(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
            intent.setData(Uri.parse("package:" + activity.getPackageName()))
            activity.startActivity(intent)
        except Exception as e:
            self.status_label.text = f"请求电池优化白名单失败: {e}"

    def open_oem_autostart_settings(self, instance):
        """
        国产手机厂商(小米/华为/OPPO/vivo/三星/魅族等)在 AOSP 标准的
        "电池优化白名单"之外，还各自维护一套"自启动管理/后台运行管理"，
        这一层系统级限制会在退出App或息屏一段时间后强杀我们的后台服务，
        且完全绕过 START_STICKY 和 前台服务 —— 这是导致"退出App就不刷新、
        不提醒"最常见的原因，且无法通过 App 代码彻底避免，只能引导用户
        去系统设置里手动放行。这里按厂商自动跳转到对应设置页，
        不同 ROM 版本的 Activity 类名不完全一样，所以每个厂商准备多个
        候选，依次尝试，都失败就退回到应用详情页，用户手动找相关开关。
        """
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            ComponentName = autoclass("android.content.ComponentName")
            Build = autoclass("android.os.Build")
            Settings = autoclass("android.provider.Settings")
            Uri = autoclass("android.net.Uri")

            activity = PythonActivity.mActivity
            pkg = activity.getPackageName()
            manufacturer = str(Build.MANUFACTURER).lower()

            candidates = []
            if "xiaomi" in manufacturer:
                candidates = [
                    ("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity"),
                ]
            elif "huawei" in manufacturer or "honor" in manufacturer:
                candidates = [
                    ("com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity"),
                    ("com.huawei.systemmanager", "com.huawei.systemmanager.optimize.process.ProtectActivity"),
                    ("com.huawei.systemmanager", "com.huawei.systemmanager.appcontrol.activity.StartupAppControlActivity"),
                ]
            elif "oppo" in manufacturer:
                candidates = [
                    ("com.coloros.safecenter", "com.coloros.safecenter.permission.startup.StartupAppListActivity"),
                    ("com.coloros.safecenter", "com.coloros.safecenter.startupapp.StartupAppListActivity"),
                    ("com.oppo.safe", "com.oppo.safe.permission.startup.StartupAppListActivity"),
                ]
            elif "vivo" in manufacturer:
                candidates = [
                    ("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"),
                    ("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"),
                    ("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager"),
                ]
            elif "samsung" in manufacturer:
                candidates = [
                    ("com.samsung.android.lool", "com.samsung.android.sm.ui.battery.BatteryActivity"),
                ]
            elif "meizu" in manufacturer:
                candidates = [
                    ("com.meizu.safe", "com.meizu.safe.security.SHOW_APPSEC"),
                ]
            elif "oneplus" in manufacturer:
                candidates = [
                    ("com.oneplus.security", "com.oneplus.security.chainlaunch.view.ChainLaunchAppListActivity"),
                ]

            opened = False
            for pkg_name, cls_name in candidates:
                try:
                    intent = Intent()
                    intent.setComponent(ComponentName(pkg_name, cls_name))
                    activity.startActivity(intent)
                    opened = True
                    self.status_label.text = "已打开系统自启动/后台管理设置，请手动允许本App后台运行和自启动"
                    break
                except Exception:
                    continue

            if not opened:
                # 兜底：至少打开本App的应用详情页，用户手动找"电池/自启动/后台运行"相关开关
                intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                intent.setData(Uri.parse("package:" + pkg))
                activity.startActivity(intent)
                self.status_label.text = "未识别到本机型专属设置页，已打开应用详情页，请手动查找电池/自启动相关权限"
        except Exception as e:
            self.status_label.text = f"打开自启动设置失败: {e}"

    # ---------- 设置 ----------
    def save_settings(self, instance):
        try:
            # 更新内存中的配置
            self.cfg["interval_minutes"] = int(self.interval_input.text)
            self.cfg["low_ratio"] = float(self.low_input.text)
            self.cfg["high_ratio"] = float(self.high_input.text)
            # 保存到文件
            save_config(self.cfg)
            # 重新加载一次以确保同步（虽然 self.cfg 已经更新，但这样更稳妥）
            self.cfg = load_config()
            self.status_label.text = "设置已保存"
        except Exception as e:
            self.status_label.text = f"保存失败: {e}"

    # ---------- 服务启停 ----------
    # 重要修复：之前这里手动 new 一个 Intent 只 setClassName，完全没有携带
    # p4a 的 PythonService 启动所必需的 extras（androidPrivate/androidArgument/
    # serviceEntrypoint/pythonHome/pythonPath/serviceStartAsForeground 等）。
    # PythonService.onStartCommand() 第一步就是 intent.getExtras().getString(...)，
    # extras 为 null 会直接空指针崩溃 —— 服务在 Python 代码执行前就已经死亡，
    # 这就是"日志文件为空、息屏收不到通知、后台不刷新"三个问题的共同根因。
    # 正确做法是调用 buildozer 自动生成的 Service{Name} 类自带的 start()/stop()
    # 静态方法，它会自动把这些 extras 都填好。
    _WATCHDOG_REQUEST_CODE = 9527

    def _get_service_class(self, activity):
        from jnius import autoclass
        service_class = activity.getPackageName() + ".Service" + SERVICE_ENTRY_NAME.capitalize()
        return autoclass(service_class)

    def _schedule_watchdog_alarm(self, activity):
        """
        额外的自愈保险：不管系统(尤其是国产ROM的省电策略)有没有把我们的
        前台服务强杀掉，每隔一段时间用 AlarmManager 唤醒设备并重新调用
        ServiceXxx.start()。已经确认 p4a 生成的 onStartCommand() 是幂等的：
        服务如果还活着，重复调用只是安全地 no-op；服务如果已经被杀，
        这次调用就会把它重新拉起来。相当于给 START_STICKY 加了双保险。
        """
        try:
            from jnius import autoclass
            Context = autoclass("android.content.Context")
            AlarmManager = autoclass("android.app.AlarmManager")
            PendingIntent = autoclass("android.app.PendingIntent")
            Build_VERSION = autoclass("android.os.Build$VERSION")
            System = autoclass("java.lang.System")

            ServiceClass = self._get_service_class(activity)
            watchdog_intent = ServiceClass.getDefaultIntent(
                activity, "", "MyCGC 后台监控", "正在监控 CGC/WGDC 比例", ""
            )

            flags = PendingIntent.FLAG_UPDATE_CURRENT
            if Build_VERSION.SDK_INT >= 23:
                flags = flags | PendingIntent.FLAG_IMMUTABLE

            pending_intent = PendingIntent.getService(
                activity, self._WATCHDOG_REQUEST_CODE, watchdog_intent, flags
            )

            alarm_manager = activity.getSystemService(Context.ALARM_SERVICE)
            interval = AlarmManager.INTERVAL_FIFTEEN_MINUTES
            first_time = System.currentTimeMillis() + interval
            # setRepeating 是"非精确"的重复闹钟，不需要 SCHEDULE_EXACT_ALARM 权限，
            # 配合已经申请的电池优化白名单，一般 Doze 打盹期间也能按时(或稍延迟)唤醒。
            alarm_manager.setRepeating(AlarmManager.RTC_WAKEUP, first_time, interval, pending_intent)
        except Exception as e:
            print("watchdog alarm schedule failed:", e)

    def _cancel_watchdog_alarm(self, activity):
        try:
            from jnius import autoclass
            Context = autoclass("android.content.Context")
            AlarmManager = autoclass("android.app.AlarmManager")
            PendingIntent = autoclass("android.app.PendingIntent")
            Build_VERSION = autoclass("android.os.Build$VERSION")

            ServiceClass = self._get_service_class(activity)
            watchdog_intent = ServiceClass.getDefaultIntent(
                activity, "", "MyCGC 后台监控", "正在监控 CGC/WGDC 比例", ""
            )
            flags = PendingIntent.FLAG_UPDATE_CURRENT
            if Build_VERSION.SDK_INT >= 23:
                flags = flags | PendingIntent.FLAG_IMMUTABLE

            pending_intent = PendingIntent.getService(
                activity, self._WATCHDOG_REQUEST_CODE, watchdog_intent, flags
            )
            alarm_manager = activity.getSystemService(Context.ALARM_SERVICE)
            alarm_manager.cancel(pending_intent)
            pending_intent.cancel()
        except Exception as e:
            print("watchdog alarm cancel failed:", e)

    def start_service(self, instance):
        self.save_settings(instance)
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            ServiceClass = self._get_service_class(activity)
            ServiceClass.start(activity, "", "MyCGC 后台监控", "正在监控 CGC/WGDC 比例", "")
            self._schedule_watchdog_alarm(activity)

            self.cfg["service_running"] = True
            save_config(self.cfg)
            self.status_label.text = "后台监控已启动 (请务必也点一下下方的自启动/后台管理设置)"
        except Exception as e:
            self.status_label.text = f"启动失败: {e}"

    def stop_service(self, instance):
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            ServiceClass = self._get_service_class(activity)
            ServiceClass.stop(activity)
            self._cancel_watchdog_alarm(activity)

            self.cfg["service_running"] = False
            save_config(self.cfg)
            self.status_label.text = "后台监控已停止"
        except Exception as e:
            self.status_label.text = f"停止失败: {e}"

    # ---------- 手动刷新 / 状态展示 ----------
    def manual_refresh(self, instance):
        self.status_label.text = "刷新中..."
        threading.Thread(target=self._do_manual_refresh, daemon=True).start()

    def _do_manual_refresh(self):
        try:
            cgc, wgdc, ratio = fetch_amounts_and_ratio(self.cfg)
            self.cfg["last_cgc"] = cgc
            self.cfg["last_wgdc"] = wgdc
            self.cfg["last_ratio"] = ratio
            self.cfg["last_update"] = time.time()
            save_config(self.cfg)
            # 刷新完成后，强制 UI 线程立即更新一次标签
            Clock.schedule_once(lambda dt: self.refresh_status_label(0))
        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            print("manual refresh error:", err_msg)
            Clock.schedule_once(lambda dt: self.set_status_text(f"刷新失败: {str(e)}"))

    def set_status_text(self, text):
        self.status_label.text = text

    def show_log(self, instance):
        from config_store import CONFIG_DIR
        log_path = os.path.join(CONFIG_DIR, "service.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                # 只显示最后 20 行
                self.status_label.text = "".join(lines[-20:])
            except Exception as e:
                self.status_label.text = f"读取日志失败: {e}"
        else:
            self.status_label.text = "暂无运行日志，请先启动监控"

    def refresh_status_label(self, dt):
        cfg = load_config()
        ratio = cfg.get("last_ratio")
        cgc = cfg.get("last_cgc")
        wgdc = cfg.get("last_wgdc")
        ts = cfg.get("last_update")
        if ratio is not None and cgc is not None and wgdc is not None:
            t_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "-"
            self.status_label.text = (
                f"CGC: {cgc:.4f}   WGDC: {wgdc:.4f}\n"
                f"实时比例(第1档): {ratio:.4f}\n"
                f"更新于 {t_str}"
            )


if __name__ == "__main__":
    MyCGCApp().run()
